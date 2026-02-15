"""
Telegram Bot 命令处理器：/start, /vocab, /review, /search, /export, /timezone
"""
import io
import csv
import math
import logging
import html as html_lib

from telegram import Update
from telegram.ext import ContextTypes

from datetime import datetime, timezone

from database.client import (
    get_vocab_list, count_vocab, get_due_vocab,
    is_pro, get_subscription, get_today_add_count,
    activate_code, generate_codes, extend_subscription,
    get_level_distribution, get_vocab_by_word,
    get_all_vocab, get_user_settings, set_user_timezone,
    get_streak, check_db_connection,
    get_all_user_ids, get_admin_stats, get_vocab_detail,
    has_user_settings,
)
from config import ADMIN_TELEGRAM_ID, FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from core.quiz import build_quiz
from core.sm2 import level_description
from bot.keyboards import quiz_keyboard, vocab_page_keyboard, delete_confirm_keyboard, timezone_keyboard, vocab_detail_keyboard, settings_panel_keyboard

logger = logging.getLogger(__name__)

PAGE_SIZE = 10

# 帮助文本（/start 和 /help 共用）
_HELP_TEXT = (
    "*命令列表：*\n"
    "/vocab — 查看你的词库\n"
    "/review — 立即开始复习\n"
    "/practice — 自由练习（不计入进度）\n"
    "/search <词> — 搜索词库\n"
    "/export — 导出词库为 CSV\n"
    "/stats — 学习统计\n"
    "/streak — 连续学习天数\n"
    "/update <词> — 编辑词汇的词性/释义/例句\n"
    "/delete <词> — 从词库删除单词\n"
    "/timezone — 设置复习提醒时区\n"
    "/settings — 通知设置（时段/开关）\n"
    "/plan — 查看订阅状态\n"
    "/activate <码> — 激活订阅\n"
    "/help — 显示此帮助信息"
)


def _review_countdown(next_review_str: str | None, level: int) -> str:
    """根据 next_review 时间和级别返回倒计时文本，level 7 表示已掌握不显示"""
    if level >= 7:
        return ""
    if not next_review_str:
        return ""
    try:
        # Supabase 返回的 ISO 格式可能带 Z 或 +00:00
        dt = datetime.fromisoformat(next_review_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff_days = (dt.date() - now.date()).days
        if dt <= now:
            return " ⚡到期"
        elif diff_days == 0:
            return " ↻今日"
        else:
            return f" ↻{diff_days}天"
    except Exception:
        return ""


def _vocab_line(r: dict) -> str:
    """将单条词汇记录格式化为词库列表的一行，紧凑斜体元数据"""
    pos_tag = f"[{r['pos']}] " if r.get("pos") else ""
    level = r["level"]
    countdown = _review_countdown(r.get("next_review"), level)
    # level 7 显示已掌握，到期显示 ⚡，否则显示 Lv + 倒计时
    if level >= 7:
        meta = "_✓ 已掌握_"
    elif countdown:
        meta = f"_Lv{level} ·{countdown}_"
    else:
        meta = f"_Lv{level}_"
    return f"• *{r['word']}* {pos_tag}— {r['definition']}   {meta}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — 欢迎消息 + 使用说明；新用户自动引导时区设置"""
    text = (
        "👋 欢迎使用 *Vocab Master*！\n\n"
        "我能帮你记住英文单词，使用艾宾浩斯遗忘曲线自动安排复习。\n\n"
        "*使用方法：*\n"
        "• 直接发送单词或词组（如 `devastated`）\n"
        "• 发送含目标词的句子（如 `I was utterly devastated`）\n"
        "• 发送中文词语（如 `苹果`），我会找到对应英文\n\n"
        + _HELP_TEXT
    )
    await update.message.reply_text(text, parse_mode="Markdown")

    # 新用户自动弹出时区设置（老用户不重复打扰）
    telegram_id = str(update.effective_user.id)
    if not has_user_settings(telegram_id):
        await update.message.reply_text(
            "🌏 先设置时区，以便正确安排复习提醒：",
            reply_markup=timezone_keyboard(),
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — 显示命令列表"""
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/streak — 查看连续学习天数"""
    telegram_id = str(update.effective_user.id)
    streak_days, last_date = get_streak(telegram_id)

    today_reviewed = 0
    total_reviewed = 0
    try:
        from database.client import get_client
        db = get_client()
        # 统计今日复习数（review_count 有增量的词汇无法直接获取，用今日新增的近似）
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        # 统计今日到期词汇中 review_count > 0 的记录作为今日复习近似
        rows = (
            db.table("vocab_records")
            .select("review_count")
            .eq("telegram_id", telegram_id)
            .execute()
            .data
        )
        total_reviewed = sum(r.get("review_count", 0) for r in rows) if rows else 0
    except Exception:
        pass

    # 连续天数描述
    if streak_days == 0:
        streak_text = "0 天（尚未开始复习）"
    elif streak_days == 1:
        streak_text = "1 天 🌱"
    elif streak_days < 7:
        streak_text = f"{streak_days} 天 📈"
    elif streak_days < 30:
        streak_text = f"{streak_days} 天 🔥"
    else:
        streak_text = f"{streak_days} 天 🏆"

    text = (
        f"🔥 *连续学习：{streak_text}*\n\n"
        f"📊 累计总复习：{total_reviewed} 次"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/practice — 直接进入练习模式，不受 next_review 限制"""
    telegram_id = str(update.effective_user.id)

    # 防止多开：有活跃会话时拦截
    active = context.user_data.get("active_session")
    if active:
        mode_label = "复习" if active == "review" else "练习"
        await update.message.reply_text(
            f"⏳ 你正在进行{mode_label}，先完成当前题目吧～\n"
            f"（点击题目下方的「结束」按钮可提前结束）"
        )
        return

    total = count_vocab(telegram_id)
    if total == 0:
        await update.message.reply_text("词库还是空的～先发送单词积累吧！")
        return
    context.user_data["active_session"] = "practice"
    await update.message.reply_text("🎮 进入练习模式（答题不计入复习进度）…")
    question = await build_quiz(telegram_id, practice_mode=True)
    if not question:
        context.user_data.pop("active_session", None)
        await update.message.reply_text("生成练习题时出错，请稍后重试。")
        return
    await _send_quiz(update.message.reply_text, question)


async def cmd_vocab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vocab — 分页展示用户词库"""
    telegram_id = str(update.effective_user.id)

    # 解析页码参数（/vocab 2 → 第2页）
    page = 0
    if context.args:
        try:
            page = max(0, int(context.args[0]) - 1)
        except ValueError:
            pass

    total = count_vocab(telegram_id)
    if total == 0:
        await update.message.reply_text(
            "你的词库还是空的～\n发送任意单词开始积累吧！"
        )
        return

    total_pages = math.ceil(total / PAGE_SIZE)
    page = min(page, total_pages - 1)

    records = get_vocab_list(telegram_id, page=page, page_size=PAGE_SIZE)

    # 正文只显示标题行，词汇详情通过按钮弹窗展示
    text = f"📚 *你的词库* ({page + 1}/{total_pages} 页，共 {total} 词)\n点击单词按钮查看详情"
    keyboard = vocab_page_keyboard(page, total_pages, records)

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/review — 手动触发一道复习题"""
    telegram_id = str(update.effective_user.id)

    # 防止多开：有活跃会话时拦截
    active = context.user_data.get("active_session")
    if active:
        mode_label = "复习" if active == "review" else "练习"
        await update.message.reply_text(
            f"⏳ 你正在进行{mode_label}，先完成当前题目吧～\n"
            f"（点击题目下方的「结束」按钮可提前结束）"
        )
        return

    # 检查是否有到期词汇
    due_list = get_due_vocab(telegram_id)
    if not due_list:
        total = count_vocab(telegram_id)
        if total == 0:
            await update.message.reply_text(
                "你的词库还是空的～\n发送任意单词开始积累吧！"
            )
            return
        # 有词汇但无到期 → 自动进入练习模式
        context.user_data["active_session"] = "practice"
        await update.message.reply_text(
            "⏳ 当前无到期词汇，进入练习模式（答题不计入复习进度）…"
        )
        question = await build_quiz(telegram_id, practice_mode=True)
        if not question:
            context.user_data.pop("active_session", None)
            await update.message.reply_text("生成复习题时出错，请稍后重试。")
            return
        await _send_quiz(update.message.reply_text, question)
        return

    # 生成正式复习题
    context.user_data["active_session"] = "review"
    await update.message.reply_text("⏳ 正在生成复习题…")
    question = await build_quiz(telegram_id)

    if not question:
        context.user_data.pop("active_session", None)
        await update.message.reply_text("生成复习题时出错，请稍后重试。")
        return

    await _send_quiz(update.message.reply_text, question)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — 个人学习统计"""
    telegram_id = str(update.effective_user.id)

    total = count_vocab(telegram_id)
    today_added = get_today_add_count(telegram_id)
    due_count = len(get_due_vocab(telegram_id))
    dist = get_level_distribution(telegram_id)

    # 级别标签：进度条 + 级别名
    level_labels = [
        ("入门", 0), ("初级", 1), ("初级+", 2),
        ("中级", 3), ("中级+", 4), ("高级", 5),
        ("精通", 6), ("已掌握", 7),
    ]

    lines = [
        "📊 *学习统计*\n",
        f"📚 总词数：{total} 词",
        f"➕ 今日新增：{today_added} 词",
        f"⚡ 待复习：{due_count} 词\n",
        "*级别分布：*",
    ]

    for label, lvl in level_labels:
        count = dist.get(lvl, 0)
        if count == 0:
            continue
        # 固定 8 格宽度，用 ▓░ 显示比例，避免全实心黑块
        filled = max(1, round(count / total * 8)) if total > 0 else 0
        ratio_bar = "▓" * filled + "░" * (8 - filled)
        pct = round(count / total * 100) if total > 0 else 0
        lines.append(f"Lv{lvl} {label:<4}  {count:>4}词  {ratio_bar}  {pct}%")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete <word> — 从词库中删除指定单词；含逗号时批量删除"""
    from database.client import delete_vocab_by_word as _delete_by_word

    telegram_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "用法：`/delete <单词>`\n例如：`/delete devastated`\n"
            "批量删除：`/delete organ, flock`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args).strip()

    # 先查整体短语是否存在
    word = raw
    records = get_vocab_by_word(telegram_id, word)

    # 整体短语不存在 + 多个 token → 批量删除每个独立词，不需要确认
    if not records and len(context.args) > 1:
        # 支持逗号或空格分隔：先按逗号拆，没有逗号则按空格拆
        if "," in raw:
            tokens = [t.strip() for t in raw.split(",") if t.strip()]
        else:
            tokens = context.args  # 每个 arg 作为独立词/短语
        lines = ["🗑️ *批量删除结果：*"]
        for token in tokens:
            count = _delete_by_word(telegram_id, token)
            if count > 0:
                lines.append(f"✅ {token} — 已删除 {count} 条")
            else:
                lines.append(f"❌ {token} — 词库中未找到")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if not records:
        await update.message.reply_text(f"词库中找不到「{word}」，请检查拼写。")
        return

    if len(records) == 1:
        r = records[0]
        pos_tag = f"[{r['pos']}] " if r.get("pos") else ""
        text = (
            f"确认删除以下词汇？\n\n"
            f"*{r['word']}* {pos_tag}— {r['definition']}"
        )
    else:
        text = f"「{records[0]['word']}」有 {len(records)} 个释义，请选择要删除的条目："

    keyboard = delete_confirm_keyboard(records)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/activate <code> — 用户激活订阅码"""
    telegram_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "用法：`/activate 激活码`\n例如：`/activate ABCD1234`",
            parse_mode="Markdown",
        )
        return

    code = context.args[0].strip().upper()
    success, msg = activate_code(telegram_id, code)

    if success:
        await update.message.reply_text(
            f"🎉 *订阅激活成功！*\n\n"
            f"到期日期：*{msg}*\n\n"
            f"现在你可以无限添加词汇，尽情学习吧！",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ 激活失败：{msg}")


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/plan — 查看当前订阅状态"""
    telegram_id = str(update.effective_user.id)

    pro = is_pro(telegram_id)
    total = count_vocab(telegram_id)
    today_count = get_today_add_count(telegram_id)

    if pro:
        expires = get_subscription(telegram_id)
        remaining = (expires - datetime.now(timezone.utc)).days
        expires_str = expires.strftime("%Y-%m-%d")
        plan_line = (
            f"💎 *Pro 用户*\n"
            f"到期日期：{expires_str}（剩余 {remaining} 天）"
        )
        limit_line = "无添加限制"
    else:
        plan_line = (
            f"🆓 *免费用户*\n"
            f"词库上限：{FREE_WORD_LIMIT} 词，每日上限：{FREE_DAILY_LIMIT} 词"
        )
        limit_line = (
            f"词库剩余：{max(0, FREE_WORD_LIMIT - total)} 词\n"
            f"今日剩余：{max(0, FREE_DAILY_LIMIT - today_count)} 词"
        )

    text = (
        f"{plan_line}\n\n"
        f"📊 *词库统计*\n"
        f"总词数：{total} 词\n"
        f"今日新增：{today_count} 词\n"
        f"{limit_line}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_gencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gencode <天数> <数量> — 仅管理员：生成激活码"""
    telegram_id = str(update.effective_user.id)

    # 权限检查
    if not ADMIN_TELEGRAM_ID or telegram_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ 权限不足。")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "用法：`/gencode <天数> <数量>`\n例如：`/gencode 30 3`",
            parse_mode="Markdown",
        )
        return

    try:
        days = int(context.args[0])
        qty = int(context.args[1])
        if days <= 0 or qty <= 0 or qty > 50:
            raise ValueError
    except ValueError:
        await update.message.reply_text("参数错误：天数和数量须为正整数，数量最多 50。")
        return

    try:
        codes = generate_codes(days, qty)
    except RuntimeError as exc:
        # 表不存在时给出建表提示
        sql = (
            "CREATE TABLE IF NOT EXISTS activation_codes (\n"
            "  code          text        PRIMARY KEY,\n"
            "  duration_days int         NOT NULL,\n"
            "  used_by       text,\n"
            "  used_at       timestamptz,\n"
            "  created_at    timestamptz NOT NULL DEFAULT now()\n"
            ");"
        )
        await update.message.reply_text(
            f"⚠️ *激活码功能需要先建表*\n\n"
            f"请在 Supabase SQL Editor 执行以下 SQL，然后重试：\n\n"
            f"```sql\n{sql}\n```",
            parse_mode="Markdown",
        )
        return

    code_list = "\n".join(f"`{c}`" for c in codes)
    await update.message.reply_text(
        f"✅ 已生成 *{qty}* 个 *{days}* 天激活码：\n\n{code_list}",
        parse_mode="Markdown",
    )


async def cmd_extend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/extend <telegram_id> <天数> — 仅管理员：直接为用户续期"""
    caller_id = str(update.effective_user.id)

    # 权限检查
    if not ADMIN_TELEGRAM_ID or caller_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ 权限不足。")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "用法：`/extend <telegram_id> <天数>`\n例如：`/extend 123456789 30`",
            parse_mode="Markdown",
        )
        return

    target_id = context.args[0].strip()
    try:
        days = int(context.args[1])
        if days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("参数错误：天数须为正整数。")
        return

    new_date = extend_subscription(target_id, days)
    await update.message.reply_text(
        f"✅ 已为用户 `{target_id}` 续期 *{days}* 天，到期日：*{new_date}*",
        parse_mode="Markdown",
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/search <词> — 在词库中搜索单词"""
    telegram_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "用法：`/search <单词>`\n例如：`/search devastated`",
            parse_mode="Markdown",
        )
        return

    word = " ".join(context.args).strip()
    records = get_vocab_by_word(telegram_id, word)

    if not records:
        await update.message.reply_text(f"词库中未找到「{word}」，请检查拼写。")
        return

    lines = [f"🔍 *搜索结果：{word}*\n"]
    for r in records:
        pos_tag = f"[{r['pos']}] " if r.get("pos") else ""
        level = r["level"]
        if level >= 7:
            meta = "✓ 已掌握"
        else:
            meta = f"Lv{level}"
        lines.append(f"• *{r['word']}* {pos_tag}— {r['definition']}   _{meta}_")
        # 若有例句则附加显示
        if r.get("context"):
            lines.append(f"  _📝 {r['context']}_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/export — 导出全部词库为 CSV 文件"""
    telegram_id = str(update.effective_user.id)

    records = get_all_vocab(telegram_id)
    if not records:
        await update.message.reply_text("词库为空，尚无词汇可导出。")
        return

    # 用 StringIO 在内存中生成 CSV，避免磁盘读写
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["word", "pos", "definition", "context", "level", "next_review", "created_at"])
    for r in records:
        writer.writerow([
            r.get("word", ""),
            r.get("pos", ""),
            r.get("definition", ""),
            r.get("context", ""),
            r.get("level", 0),
            r.get("next_review", ""),
            r.get("created_at", ""),
        ])

    # 转为字节流发送
    buf.seek(0)
    file_bytes = buf.getvalue().encode("utf-8-sig")  # utf-8-sig 方便 Excel 识别中文

    await update.message.reply_document(
        document=io.BytesIO(file_bytes),
        filename="vocab_export.csv",
        caption=f"📤 词库导出完成，共 {len(records)} 个词汇。",
    )


async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/timezone — 通过 inline 按钮选择复习提醒时区"""
    telegram_id = str(update.effective_user.id)

    # get_user_settings 内部已静默处理表不存在，此处可安全调用
    settings = get_user_settings(telegram_id)
    current_tz = settings.get("timezone", "UTC")

    # 探测写权限是否正常（表不存在时此处会抛 RuntimeError）
    try:
        # 用一次无害的 upsert 验证表可用性（写入当前值，等价于 no-op）
        set_user_timezone(telegram_id, current_tz)
    except RuntimeError:
        # 表不存在，提示用户在 Supabase 执行建表 SQL
        sql = (
            "CREATE TABLE user_settings (\n"
            "  telegram_id TEXT PRIMARY KEY,\n"
            "  timezone TEXT NOT NULL DEFAULT 'UTC',\n"
            "  remind_start INT NOT NULL DEFAULT 8,\n"
            "  remind_end INT NOT NULL DEFAULT 22\n"
            ");"
        )
        await update.message.reply_text(
            "⚠️ *时区功能需要先建表*\n\n"
            "请在 Supabase SQL Editor 执行以下 SQL，然后重试 /timezone：\n\n"
            f"```sql\n{sql}\n```",
            parse_mode="Markdown",
        )
        return

    text = (
        f"🌏 *时区设置*\n\n"
        f"当前时区：`{current_tz}`\n\n"
        f"复习提醒只在本地时间 08:00–22:00 之间推送。\n"
        f"请选择你所在的时区："
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=timezone_keyboard(),
    )


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast <msg> — 仅管理员：向所有有词汇的用户广播消息"""
    telegram_id = str(update.effective_user.id)

    if not ADMIN_TELEGRAM_ID or telegram_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ 权限不足。")
        return

    if not context.args:
        await update.message.reply_text(
            "用法：`/broadcast <消息内容>`",
            parse_mode="Markdown",
        )
        return

    msg_text = " ".join(context.args).strip()
    user_ids = get_all_user_ids()

    if not user_ids:
        await update.message.reply_text("当前没有任何用户。")
        return

    success = 0
    fail = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=msg_text)
            success += 1
        except Exception as exc:
            logger.warning("广播失败 uid=%s: %s", uid, exc)
            fail += 1

    await update.message.reply_text(
        f"📢 广播完成：成功 {success} 人，失败 {fail} 人。"
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/users — 仅管理员：查看用户统计"""
    telegram_id = str(update.effective_user.id)

    if not ADMIN_TELEGRAM_ID or telegram_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ 权限不足。")
        return

    stats = get_admin_stats()
    text = (
        "👥 *用户统计*\n\n"
        f"总用户数：{stats['total_users']}\n"
        f"7日活跃：{stats['active_7d']}\n"
        f"Pro 用户：{stats['pro_users']}\n"
        f"总词汇量：{stats['total_words']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/update <词> — 编辑已保存词汇的词性/释义/例句"""
    telegram_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "用法：`/update <单词>`\n例如：`/update devastated`",
            parse_mode="Markdown",
        )
        return

    word = " ".join(context.args).strip()
    records = get_vocab_by_word(telegram_id, word)

    if not records:
        await update.message.reply_text(f"词库中找不到「{word}」，请检查拼写。")
        return

    if len(records) == 1:
        # 单义词，直接显示详情 + 编辑按钮
        r = records[0]
        record = get_vocab_detail(r["id"])
        if not record:
            await update.message.reply_text("词汇记录不存在。")
            return
        text = _format_vocab_detail(record)
        # /update 入口不在 /vocab 列表中，page 用 0 作占位
        keyboard = vocab_detail_keyboard(r["id"], 0)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        # 多义词，先显示选择列表
        lines = [f"「{records[0]['word']}」有 {len(records)} 个释义，请选择要编辑的条目："]
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for r in records:
            pos_tag = f"[{r['pos']}] " if r.get("pos") else ""
            label = f"{pos_tag}{r['definition']}"
            rows.append([
                InlineKeyboardButton(label, callback_data=f"vedit_sel:{r['id']}:0")
            ])
        keyboard = InlineKeyboardMarkup(rows)
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


def _format_vocab_detail(record: dict) -> str:
    """将完整词汇记录格式化为详情消息文本（Markdown）"""
    word = record.get("word", "")
    pos = record.get("pos", "")
    definition = record.get("definition", "")
    context_sentence = record.get("context", "")
    level = record.get("level", 0)
    review_count = record.get("review_count", 0)
    next_review = record.get("next_review", "")

    pos_tag = f"[{pos}] " if pos else ""
    lines = [f"📖 *{word}*", f"{pos_tag}{definition}"]
    if context_sentence:
        lines.append(f"📝 _{context_sentence}_")

    # 复习进度信息
    meta_parts = [f"Lv{level}", f"复习 {review_count} 次"]
    if next_review and level < 7:
        try:
            dt = datetime.fromisoformat(next_review.replace("Z", "+00:00"))
            meta_parts.append(f"下次 {dt.strftime('%Y-%m-%d')}")
        except Exception:
            pass
    lines.append("_" + " | ".join(meta_parts) + "_")

    return "\n".join(lines)


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/health — 仅管理员：检查 Bot、DB、调度器状态"""
    telegram_id = str(update.effective_user.id)
    if not ADMIN_TELEGRAM_ID or telegram_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ 权限不足。")
        return

    # Bot 状态（能执行到此处说明正常）
    bot_status = "✅ 运行中"

    # 数据库连接测试
    db_ok, db_msg = check_db_connection()
    db_status = f"✅ {db_msg}" if db_ok else f"❌ {db_msg}"

    # 调度器状态（从 bot_data 取实例）
    scheduler = context.application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        jobs = scheduler.get_jobs()
        job_lines = "\n".join(
            f"  • `{j.id}`: 下次 {j.next_run_time.strftime('%m-%d %H:%M UTC') if j.next_run_time else 'N/A'}"
            for j in jobs
        )
        sched_status = f"✅ 运行中（{len(jobs)} 个任务）\n{job_lines}"
    else:
        sched_status = "❌ 未运行"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        f"🔧 *系统状态*\n`{now_str}`\n\n"
        f"*Bot：* {bot_status}\n\n"
        f"*数据库：* {db_status}\n\n"
        f"*调度器：* {sched_status}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settings — 通知推送偏好面板（时段/开关）"""
    telegram_id = str(update.effective_user.id)
    settings = get_user_settings(telegram_id)

    tz = settings.get("timezone", "UTC")
    start_h = settings.get("remind_start", 8)
    end_h = settings.get("remind_end", 22)
    enabled = settings.get("remind_enabled", True)

    enabled_label = "✅ 开启" if enabled else "❌ 关闭"
    toggle_label = "🔕 关闭推送" if enabled else "🔔 开启推送"

    text = (
        f"🔔 *通知设置*\n\n"
        f"🌏 时区：`{tz}`（用 /timezone 修改）\n"
        f"⏰ 推送时段：{start_h:02d}:00 – {end_h:02d}:00\n"
        f"📢 自动复习推送：{enabled_label}"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=settings_panel_keyboard(toggle_label),
    )


async def _send_quiz(send_fn, question) -> None:
    """发送一道测验题（可被命令和调度器复用），根据题型组装不同文案"""
    keyboard = quiz_keyboard(
        question.options,
        question.record_id,
        quiz_type=question.quiz_type,
        practice_mode=question.practice_mode,  # 传递练习模式标识
    )

    if question.quiz_type == "meaning":
        # 选义题：Markdown 格式，展示单词 + 例句，选中文释义
        text = (
            f"🔤 *选义题*\n\n"
            f"*{question.word}*\n\n"
            f'"{question.sentence}"\n\n'
            f"请选择 *{question.word}* 在句中的意思："
        )
        await send_fn(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        # 填空题：用 HTML 格式，避免下划线被 Markdown 当成斜体符号解析
        safe_sentence = html_lib.escape(question.sentence).replace("______", "<b>______</b>")
        safe_def = html_lib.escape(question.definition)
        text = (
            f"🧠 <b>填空题</b>\n\n"
            f"请选出最适合填入 <b>______</b> 的词：\n\n"
            f"<i>{safe_sentence}</i>\n\n"
            f"💡 提示：{safe_def}"
        )
        await send_fn(text, parse_mode="HTML", reply_markup=keyboard)
