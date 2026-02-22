"""
APScheduler 定时任务：定期扫描 next_review <= now 的词汇并推送复习消息
支持用户时区设置，只在本地时间指定窗口内推送
支持多语言：按 (telegram_id, target_language) 对分别推送
"""
import logging
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    # Python 3.8 兼容（backport）
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from telegram.ext import Application

from config import SCHEDULER_INTERVAL_MINUTES
from database.client import (
    get_all_due_users, get_due_vocab, get_user_settings,
    get_expiring_subscriptions,
)
from core.quiz import build_quiz
from bot.handlers.commands import _send_quiz

logger = logging.getLogger(__name__)

# 超时阈值：session 状态超过此秒数无操作则自动清除
SESSION_TIMEOUT = 3600  # 1 小时

# 单题超时：15 分钟内同一用户不重复推题；超时后自动关闭旧题按钮
QUIZ_PENDING_TIMEOUT = 15 * 60  # 15 分钟

# 模块级字典：记录调度器已推送但用户尚未回答的 quiz 消息信息
# 格式: {telegram_id: (message_id, timestamp)}，用于防止同一用户被重复推送
_pending_quiz: dict[str, tuple[int, float]] = {}


def _is_in_remind_window(telegram_id: str) -> bool:
    """
    判断当前时刻是否在用户设置的推送时间窗口内。
    若用户未设置或时区无效，默认允许推送（UTC 全天）。
    """
    try:
        settings = get_user_settings(telegram_id)
        tz_str = settings.get("timezone", "UTC")
        remind_start = settings.get("remind_start", 8)
        remind_end = settings.get("remind_end", 22)

        tz = ZoneInfo(tz_str)
        now_local = datetime.now(timezone.utc).astimezone(tz)
        current_hour = now_local.hour
        return remind_start <= current_hour < remind_end
    except (ZoneInfoNotFoundError, Exception) as exc:
        logger.warning("时区判断失败 (user=%s): %s，跳过窗口检查", telegram_id, exc)
        return True  # 判断失败时默认允许推送


async def _push_reviews(bot: Bot) -> None:
    """
    遍历所有有到期词汇的 (用户, 语言) 对，每对推送一道对应语言的复习题。
    跳过不在提醒时间窗口内或已关闭推送的用户。
    """
    user_lang_pairs = get_all_due_users()
    logger.info("调度器: 扫描到 %d 个 (用户, 语言) 对有到期词汇", len(user_lang_pairs))

    for item in user_lang_pairs:
        telegram_id = item["telegram_id"]
        target_language = item.get("target_language", "en")
        try:
            # 若用户有未回答的 quiz，判断是否超时
            if telegram_id in _pending_quiz:
                msg_id, ts = _pending_quiz[telegram_id]
                if time.time() - ts < QUIZ_PENDING_TIMEOUT:
                    # 未超时：跳过本轮推送，避免重复刷屏
                    logger.debug("用户 %s 有未回答的 quiz（msg_id=%s），跳过本次推送",
                                 telegram_id, msg_id)
                    continue
                # 超时：静默移除旧题按钮后清除记录，本轮可继续推新题
                logger.info("用户 %s 的 pending_quiz 超时（msg_id=%s），清除并推新题", telegram_id, msg_id)
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=telegram_id, message_id=msg_id, reply_markup=None
                    )
                except Exception:
                    pass
                del _pending_quiz[telegram_id]

            # 检查用户是否开启了推送
            settings = get_user_settings(telegram_id)
            if not settings.get("remind_enabled", True):
                logger.debug("用户 %s 已关闭推送，跳过", telegram_id)
                continue

            # 检查用户本地时间是否在提醒窗口内
            if not _is_in_remind_window(telegram_id):
                logger.debug("用户 %s 不在提醒时间窗口内，跳过", telegram_id)
                continue

            # 读取用户母语，用于题目文案语言及干扰项语言
            native_language = settings.get("native_language", "zh")

            # 生成对应语言的复习题（调度器无 context，禁用 generation 造句题）
            question = await build_quiz(
                telegram_id, target_language=target_language,
                native_language=native_language, allow_generation=False,
            )
            if not question:
                continue
            # 通过 lambda 将 bot.send_message 包装为统一的 send_fn 接口
            send_fn = lambda text, **kwargs: bot.send_message(
                chat_id=telegram_id, text=text, **kwargs
            )
            sent_msg = await _send_quiz(send_fn, question, lang=native_language)
            # 记录已推送的 message_id 和时间戳，防止下一个调度周期重复推送
            if sent_msg is not None:
                _pending_quiz[telegram_id] = (sent_msg.message_id, time.time())
            logger.info(
                "已推送复习题给用户 %s（语言: %s）",
                telegram_id, target_language
            )
        except Exception as exc:
            logger.error(
                "推送给用户 %s（语言: %s）失败: %s",
                telegram_id, target_language, exc
            )


async def _remind_expiring_subscriptions(bot: Bot) -> None:
    """每日检查 3 天内到期的订阅并发送提醒，文案使用用户母语"""
    from bot.i18n import t_async
    users = get_expiring_subscriptions(within_days=3)
    logger.info("订阅到期检查: %d 位用户将在 3 天内到期", len(users))
    for user in users:
        telegram_id = user["telegram_id"]
        expires_str = user["expires_at"]
        try:
            dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            date_label = dt.strftime("%Y-%m-%d")
            # 读取用户母语，用对应语言发送提醒
            settings = get_user_settings(telegram_id)
            lang = settings.get("native_language", "zh")
            text = await t_async("expiry_reminder", lang, date=date_label)
            await bot.send_message(
                chat_id=telegram_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info("已发送到期提醒给用户 %s (lang=%s)", telegram_id, lang)
        except Exception as exc:
            logger.error("发送到期提醒失败 (user=%s): %s", telegram_id, exc)


async def _cleanup_stale_sessions(app: Application) -> None:
    """
    定期扫描 app.user_data，静默清除超时的交互状态并移除过期消息的按钮。
    清除条件：距上次设置超过 SESSION_TIMEOUT 秒且用户无任何操作。
    """
    now = time.time()
    for user_id, user_data in list(app.user_data.items()):

        # ① active_session 超时（/review 或 /practice 会话）
        if "active_session" in user_data:
            ts = user_data.get("active_session_ts", now)
            if now - ts > SESSION_TIMEOUT:
                last_msg_id = user_data.get("last_quiz_msg_id")
                if last_msg_id:
                    try:
                        await app.bot.edit_message_reply_markup(
                            chat_id=user_id, message_id=last_msg_id, reply_markup=None
                        )
                    except Exception:
                        pass
                user_data.pop("active_session", None)
                user_data.pop("active_session_ts", None)
                user_data.pop("practice_queue", None)
                user_data.pop("last_quiz_msg_id", None)
                user_data.pop("pending_generation", None)
                logger.info("用户 %s 的 active_session 超时，已静默清除", user_id)

        # ② pending_edit 超时（词义编辑等待用户输入，无按钮可移除）
        if "pending_edit" in user_data:
            ts = user_data.get("pending_edit_ts", now)
            if now - ts > SESSION_TIMEOUT:
                user_data.pop("pending_edit", None)
                user_data.pop("pending_edit_ts", None)
                logger.info("用户 %s 的 pending_edit 超时，已静默清除", user_id)

    # ③ 单题超时（调度器推送但用户 15 分钟未作答）→ 移除按钮并清除记录
    now_ts = time.time()
    for tid, (msg_id, ts) in list(_pending_quiz.items()):
        if now_ts - ts > QUIZ_PENDING_TIMEOUT:
            try:
                await app.bot.edit_message_reply_markup(
                    chat_id=tid, message_id=msg_id, reply_markup=None
                )
            except Exception:
                pass
            del _pending_quiz[tid]
            logger.info("单题超时清理: 用户 %s msg_id=%s", tid, msg_id)


def setup_scheduler(app: Application) -> AsyncIOScheduler:
    """
    创建并返回调度器（未启动）。接受 Application 对象以便 cleanup job 访问 user_data。
    在 main.py 中调用 scheduler.start() 启动。
    """
    bot = app.bot  # 现有 job 继续用 bot
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _push_reviews,
        trigger="interval",
        minutes=SCHEDULER_INTERVAL_MINUTES,
        args=[bot],
        id="review_reminder",
        replace_existing=True,
    )
    # 每天 UTC 9:00 检查 3 天内到期的订阅并发送提醒
    scheduler.add_job(
        _remind_expiring_subscriptions,
        trigger="cron",
        hour=9,
        minute=0,
        args=[bot],
        id="subscription_expiry_reminder",
        replace_existing=True,
    )
    # 每 10 分钟扫描一次超时的交互会话，静默清除
    scheduler.add_job(
        _cleanup_stale_sessions,
        trigger="interval",
        minutes=10,
        args=[app],
        id="session_cleanup",
        replace_existing=True,
    )
    logger.info(
        "调度器已配置，每 %d 分钟扫描一次到期复习，每天 UTC 9:00 检查订阅到期，每 10 分钟清理超时会话",
        SCHEDULER_INTERVAL_MINUTES,
    )
    return scheduler
