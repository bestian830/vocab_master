"""
APScheduler 定时任务：定期扫描 next_review <= now 的词汇并推送复习消息
支持用户时区设置，只在本地时间指定窗口内推送
"""
import logging
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    # Python 3.8 兼容（backport）
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from config import SCHEDULER_INTERVAL_MINUTES
from database.client import get_all_due_users, get_due_vocab, get_user_settings, get_expiring_subscriptions
from core.quiz import build_quiz
from bot.handlers.commands import _send_quiz

logger = logging.getLogger(__name__)


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
    遍历所有有到期词汇的用户，每人推送一道复习题。
    跳过不在提醒时间窗口内的用户。
    """
    users = get_all_due_users()
    logger.info("调度器: 扫描到 %d 个用户有到期词汇", len(users))

    for user in users:
        telegram_id = user["telegram_id"]
        try:
            # 检查用户是否开启了推送
            settings = get_user_settings(telegram_id)
            if not settings.get("remind_enabled", True):
                logger.debug("用户 %s 已关闭推送，跳过", telegram_id)
                continue

            # 检查用户本地时间是否在提醒窗口内
            if not _is_in_remind_window(telegram_id):
                logger.debug("用户 %s 不在提醒时间窗口内，跳过", telegram_id)
                continue

            question = await build_quiz(telegram_id)
            if not question:
                continue

            # 通过 lambda 将 bot.send_message 包装为统一的 send_fn 接口
            send_fn = lambda text, **kwargs: bot.send_message(
                chat_id=telegram_id, text=text, **kwargs
            )
            await _send_quiz(send_fn, question)
            logger.info("已推送复习题给用户 %s", telegram_id)
        except Exception as exc:
            logger.error("推送给用户 %s 失败: %s", telegram_id, exc)


async def _remind_expiring_subscriptions(bot: Bot) -> None:
    """每日检查 3 天内到期的订阅并发送提醒"""
    users = get_expiring_subscriptions(within_days=3)
    logger.info("订阅到期检查: %d 位用户将在 3 天内到期", len(users))
    for user in users:
        telegram_id = user["telegram_id"]
        expires_str = user["expires_at"]
        try:
            dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            date_label = dt.strftime("%Y-%m-%d")
            await bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"⏰ 您的 Pro 订阅将于 *{date_label}* 到期（3 天内）。\n"
                    f"发送 `/activate 激活码` 续订，继续享受无限词库。"
                ),
                parse_mode="Markdown",
            )
            logger.info("已发送到期提醒给用户 %s", telegram_id)
        except Exception as exc:
            logger.error("发送到期提醒失败 (user=%s): %s", telegram_id, exc)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    创建并返回调度器（未启动）。
    在 main.py 中调用 scheduler.start() 启动。
    """
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
    logger.info(
        "调度器已配置，每 %d 分钟扫描一次到期复习，每天 UTC 9:00 检查订阅到期",
        SCHEDULER_INTERVAL_MINUTES,
    )
    return scheduler
