"""
Telegram Bot 命令处理器：/start, /vocab, /review, /search, /export, /timezone, /language
"""
import io
import csv
import math
import logging
import html as html_lib

from telegram import Update
from telegram.ext import ContextTypes

from datetime import datetime, timezone

import random

from database.client import (
    get_vocab_list, count_vocab, get_due_vocab,
    is_pro, get_subscription, get_today_add_count,
    activate_code, generate_codes, extend_subscription,
    get_level_distribution, get_vocab_by_word,
    get_all_vocab, get_user_settings, set_user_timezone,
    get_streak, check_db_connection,
    get_all_user_ids, get_admin_stats, get_vocab_detail,
    has_user_settings, get_vocab_count_by_language,
    get_practice_vocab,
)
from config import ADMIN_TELEGRAM_ID, FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from core.quiz import build_quiz
from core.language import get_language_display, get_native_language_display
from bot.i18n import t, t_async, get_stats_level_labels
from bot.keyboards import (
    quiz_keyboard, vocab_page_keyboard, delete_confirm_keyboard,
    timezone_keyboard, vocab_detail_keyboard, settings_panel_keyboard,
    language_panel_keyboard, onboarding_native_keyboard,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


def _review_countdown(next_review_str: str | None, level: int) -> str:
    """根据 next_review 时间和级别返回倒计时文本，level 7 表示已掌握不显示"""
    if level >= 7:
        return ""
    if not next_review_str:
        return ""
    try:
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
    if level >= 7:
        meta = "_✓ 已掌握_"
    elif countdown:
        meta = f"_Lv{level} ·{countdown}_"
    else:
        meta = f"_Lv{level}_"
    return f"• *{r['word']}* {pos_tag}— {r['definition']}   {meta}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — 欢迎消息 + 使用说明；新用户自动引导时区设置"""
    telegram_id = str(update.effective_user.id)
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

    welcome = await t_async("start_welcome", lang)
    help_text = await t_async("help_text", lang)
    text = welcome + help_text
    await update.message.reply_text(text, parse_mode="Markdown")

    # 新用户：启动引导流程（选母语 → 选学习语言 → 选时区 → 完成）
    if not has_user_settings(telegram_id):
        msg = await t_async("onboard_native_title", "zh")   # 默认 zh，用户尚未设置
        keyboard = await onboarding_native_keyboard("zh")
        await update.message.reply_text(msg, reply_markup=keyboard)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — 显示命令列表"""
    telegram_id = str(update.effective_user.id)
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

    help_text = await t_async("help_text", lang)
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/streak — 查看连续学习天数"""
    telegram_id = str(update.effective_user.id)
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

    streak_days, last_date = get_streak(telegram_id)

    total_reviewed = 0
    try:
        from database.client import get_client
        db = get_client()
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

    # 连续天数描述（按天数区间选 key）
    if streak_days == 0:
        streak_text = await t_async("streak_0", lang)
    elif streak_days == 1:
        streak_text = await t_async("streak_1", lang)
    elif streak_days < 7:
        streak_text = await t_async("streak_few", lang, days=streak_days)
    elif streak_days < 30:
        streak_text = await t_async("streak_week", lang, days=streak_days)
    else:
        streak_text = await t_async("streak_month", lang, days=streak_days)

    title = await t_async("streak_title", lang, streak=streak_text)
    total_line = await t_async("streak_total", lang, count=total_reviewed)
    text = f"{title}\n\n{total_line}"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/language — 多语言学习管理面板"""
    telegram_id = str(update.effective_user.id)
    settings = get_user_settings(telegram_id)

    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    learning_languages = settings.get("learning_languages") or ["en"]
    lang = native_language

    # 各语言词汇数量
    lang_counts = get_vocab_count_by_language(telegram_id)

    active_display = get_language_display(active_language)
    native_display = get_native_language_display(native_language)

    panel_title = await t_async("lang_panel_title", lang)
    active_line = await t_async("lang_active_line", lang, display=active_display)
    native_line = await t_async("lang_native_line", lang, display=native_display)
    vocab_label = await t_async("lang_vocab_label", lang)

    lines = [f"{panel_title}\n", active_line, f"{native_line}\n", vocab_label]
    for lc in learning_languages:
        count = lang_counts.get(lc, 0)
        display = get_language_display(lc)
        item = await t_async("lang_vocab_count", lang, display=display, count=count)
        lines.append(item)

    text = "\n".join(lines)
    keyboard = await language_panel_keyboard(learning_languages, active_language, lang=lang)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def cmd_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/practice — 直接进入练习模式，不受 next_review 限制"""
    telegram_id = str(update.effective_user.id)

    # 防止多开：有活跃会话时拦截
    active = context.user_data.get("active_session")
    if active:
        settings = get_user_settings(telegram_id)
        lang = settings.get("native_language", "zh")
        mode_key = "session_mode_review" if active == "review" else "session_mode_practice"
        mode_label = await t_async(mode_key, lang)
        msg = await t_async("session_active", lang, mode=mode_label)
        await update.message.reply_text(msg)
        return

    # 读取语言设置并存入 user_data
    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    context.user_data["active_language"] = active_language
    context.user_data["native_language"] = native_language
    lang = native_language

    # 检查当前激活语言是否有词汇
    total = count_vocab(telegram_id, target_language=active_language)
    if total == 0:
        lang_display = get_language_display(active_language)
        msg = await t_async("practice_empty", lang, lang_name=lang_display)
        await update.message.reply_text(msg)
        return

    lang_display = get_language_display(active_language)
    msg = await t_async("practice_start", lang, lang_name=lang_display)
    await update.message.reply_text(msg)
    context.user_data["active_session"] = "practice"

    # 构建 shuffle 队列：取全部词汇后随机打乱，逐题 pop 出题
    all_vocab = get_practice_vocab(
        telegram_id, target_language=active_language, native_language=native_language
    )
    if not all_vocab:
        context.user_data.pop("active_session", None)
        err = await t_async("practice_error", lang)
        await update.message.reply_text(err)
        return
    random.shuffle(all_vocab)
    context.user_data["practice_queue"] = all_vocab

    # 取队首出第一题
    first_vocab = context.user_data["practice_queue"].pop(0)
    question = await build_quiz(
        telegram_id, practice_mode=True,
        target_language=active_language, native_language=native_language,
        force_vocab=first_vocab,
    )
    if not question:
        context.user_data.pop("active_session", None)
        err = await t_async("practice_error", lang)
        await update.message.reply_text(err)
        return
    await _send_quiz(update.message.reply_text, question, lang=lang)


async def cmd_vocab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vocab — 分页展示用户当前激活语言的词库"""
    telegram_id = str(update.effective_user.id)

    # 解析页码参数（/vocab 2 → 第2页）
    page = 0
    if context.args:
        try:
            page = max(0, int(context.args[0]) - 1)
        except ValueError:
            pass

    # 读取当前激活语言和母语
    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    lang = native_language
    # 存入 user_data 供翻页回调使用
    context.user_data["vocab_language"] = active_language
    context.user_data["native_language"] = native_language

    total = count_vocab(telegram_id, target_language=active_language, native_language=native_language)
    if total == 0:
        lang_display = get_language_display(active_language)
        msg = await t_async("vocab_empty", lang, lang_name=lang_display)
        await update.message.reply_text(msg)
        return

    total_pages = math.ceil(total / PAGE_SIZE)
    page = min(page, total_pages - 1)

    records = get_vocab_list(
        telegram_id, page=page, page_size=PAGE_SIZE,
        target_language=active_language, native_language=native_language,
    )
    lang_display = get_language_display(active_language)

    title = await t_async("vocab_title", lang, lang_name=lang_display, page=page + 1, total_pages=total_pages, total=total)
    hint = await t_async("vocab_click_hint", lang)
    text = f"{title}\n{hint}"
    keyboard = await vocab_page_keyboard(page, total_pages, records, lang=lang)

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/review — 手动触发一道复习题"""
    telegram_id = str(update.effective_user.id)

    # 防止多开：有活跃会话时拦截
    active = context.user_data.get("active_session")
    if active:
        settings = get_user_settings(telegram_id)
        lang = settings.get("native_language", "zh")
        mode_key = "session_mode_review" if active == "review" else "session_mode_practice"
        mode_label = await t_async(mode_key, lang)
        msg = await t_async("session_active", lang, mode=mode_label)
        await update.message.reply_text(msg)
        return

    # 读取语言设置并存入 user_data（供后续 callback 使用）
    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    context.user_data["active_language"] = active_language
    context.user_data["native_language"] = native_language
    lang = native_language

    lang_display = get_language_display(active_language)

    # 检查是否有到期词汇（仅当前激活语言 × 母语组合）
    due_list = get_due_vocab(telegram_id, target_language=active_language, native_language=native_language)
    if not due_list:
        total = count_vocab(telegram_id, target_language=active_language, native_language=native_language)
        if total == 0:
            msg = await t_async("review_no_vocab", lang, lang_name=lang_display)
            await update.message.reply_text(msg)
            return
        # 有词汇但无到期 → 自动进入练习模式
        msg = await t_async("review_no_due_practice", lang, lang_name=lang_display)
        await update.message.reply_text(msg)
        context.user_data["active_session"] = "practice"
        question = await build_quiz(
            telegram_id, practice_mode=True,
            target_language=active_language, native_language=native_language
        )
        if not question:
            context.user_data.pop("active_session", None)
            err = await t_async("review_error", lang)
            await update.message.reply_text(err)
            return
        await _send_quiz(update.message.reply_text, question, lang=lang)
        return

    # 生成正式复习题
    gen_msg = await t_async("review_generating", lang, lang_name=lang_display)
    await update.message.reply_text(gen_msg)
    context.user_data["active_session"] = "review"
    question = await build_quiz(
        telegram_id, target_language=active_language, native_language=native_language
    )

    if not question:
        context.user_data.pop("active_session", None)
        err = await t_async("review_error", lang)
        await update.message.reply_text(err)
        return

    await _send_quiz(update.message.reply_text, question, lang=lang)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — 个人学习统计（含多语言分组）"""
    telegram_id = str(update.effective_user.id)

    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    lang = native_language

    total = count_vocab(telegram_id, target_language=active_language)
    today_added = get_today_add_count(telegram_id)
    due_count = len(get_due_vocab(telegram_id, target_language=active_language))
    dist = get_level_distribution(telegram_id)
    lang_counts = get_vocab_count_by_language(telegram_id)

    lang_display = get_language_display(active_language)

    # 级别短标签（zh/en 同步获取，其他语言 fallback 到英文短标签）
    level_labels_raw = get_stats_level_labels(lang if lang in ("zh", "en") else "en")

    lines = [
        await t_async("stats_title", lang, lang_name=lang_display) + "\n",
        await t_async("stats_total", lang, count=total),
        await t_async("stats_today_added", lang, count=today_added),
        await t_async("stats_due", lang, count=due_count) + "\n",
        await t_async("stats_level_dist", lang),
    ]

    for lvl, label in enumerate(level_labels_raw):
        count = dist.get(lvl, 0)
        if count == 0:
            continue
        filled = max(1, round(count / total * 8)) if total > 0 else 0
        ratio_bar = "▓" * filled + "░" * (8 - filled)
        pct = round(count / total * 100) if total > 0 else 0
        # 直接用 t() 获取格式字符串，数字不翻译
        line = t("stats_level_line", lang if lang in ("zh", "en") else "en",
                 level=lvl, label=label, count=count, bar=ratio_bar, pct=pct)
        lines.append(line)

    # 若有多语言词库，显示语言分布
    if len(lang_counts) > 1:
        lines.append(await t_async("stats_lang_dist", lang))
        for lc, cnt in lang_counts.items():
            display = get_language_display(lc)
            lines.append(await t_async("stats_lang_item", lang, display=display, count=cnt))

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete <word> — 从当前激活语言词库中删除指定单词；含逗号时批量删除"""
    from database.client import delete_vocab_by_word as _delete_by_word

    telegram_id = str(update.effective_user.id)
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")
    active_language = settings.get("active_language", "en")

    if not context.args:
        await update.message.reply_text(
            "用法：`/delete <单词>`\n例如：`/delete devastated`\n"
            "批量删除：`/delete organ, flock`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args).strip()

    # 先查整体短语是否存在（限制在当前激活语言）
    word = raw
    records = get_vocab_by_word(telegram_id, word, target_language=active_language)

    # 整体短语不存在 + 多个 token → 批量删除（同样限制语言）
    if not records and len(context.args) > 1:
        if "," in raw:
            tokens = [t.strip() for t in raw.split(",") if t.strip()]
        else:
            tokens = context.args
        batch_title = await t_async("delete_batch_title", lang)
        lines = [batch_title]
        for token in tokens:
            count = _delete_by_word(telegram_id, token, target_language=active_language)
            if count > 0:
                lines.append(await t_async("delete_batch_ok", lang, word=token, count=count))
            else:
                lines.append(await t_async("delete_batch_not_found", lang, word=token))
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

    keyboard = await delete_confirm_keyboard(records, lang=lang)
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
    lang_counts = get_vocab_count_by_language(telegram_id)
    total = sum(lang_counts.values())
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
        f"总词数：{total} 词（全语言）\n"
        f"今日新增：{today_count} 词\n"
        f"{limit_line}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_gencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gencode <天数> <数量> — 仅管理员：生成激活码"""
    telegram_id = str(update.effective_user.id)

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

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["word", "pos", "definition", "context", "level", "next_review", "created_at", "target_language"])
    for r in records:
        writer.writerow([
            r.get("word", ""),
            r.get("pos", ""),
            r.get("definition", ""),
            r.get("context", ""),
            r.get("level", 0),
            r.get("next_review", ""),
            r.get("created_at", ""),
            r.get("target_language", "en"),
        ])

    buf.seek(0)
    file_bytes = buf.getvalue().encode("utf-8-sig")

    await update.message.reply_document(
        document=io.BytesIO(file_bytes),
        filename="vocab_export.csv",
        caption=f"📤 词库导出完成，共 {len(records)} 个词汇。",
    )


async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/timezone — 通过 inline 按钮选择复习提醒时区"""
    telegram_id = str(update.effective_user.id)

    settings = get_user_settings(telegram_id)
    current_tz = settings.get("timezone", "UTC")
    lang = settings.get("native_language", "zh")

    try:
        set_user_timezone(telegram_id, current_tz)
    except RuntimeError:
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

    title = await t_async("timezone_title", lang)
    prompt = await t_async("timezone_prompt", lang, tz=current_tz)
    text = f"{title}\n\n{prompt}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=timezone_keyboard())


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

    await update.message.reply_text(f"📢 广播完成：成功 {success} 人，失败 {fail} 人。")


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
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

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
        r = records[0]
        record = get_vocab_detail(r["id"])
        if not record:
            await update.message.reply_text("词汇记录不存在。")
            return
        text = await _format_vocab_detail(record, lang=lang)
        keyboard = await vocab_detail_keyboard(r["id"], 0, lang=lang)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
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


async def _format_vocab_detail(record: dict, lang: str = "zh") -> str:
    """将完整词汇记录格式化为详情消息文本（Markdown），支持多语言"""
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

    # 复习进度信息（使用 i18n）
    review_label = await t_async("edit_detail_review", lang, count=review_count)
    meta_parts = [f"Lv{level}", review_label]
    if next_review and level < 7:
        try:
            dt = datetime.fromisoformat(next_review.replace("Z", "+00:00"))
            next_label = await t_async("edit_detail_next", lang, date=dt.strftime('%Y-%m-%d'))
            meta_parts.append(next_label)
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

    bot_status = "✅ 运行中"

    db_ok, db_msg = check_db_connection()
    db_status = f"✅ {db_msg}" if db_ok else f"❌ {db_msg}"

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
    lang = settings.get("native_language", "zh")

    tz = settings.get("timezone", "UTC")
    start_h = settings.get("remind_start", 8)
    end_h = settings.get("remind_end", 22)
    enabled = settings.get("remind_enabled", True)

    status = await t_async("settings_push_on" if enabled else "settings_push_off", lang)
    toggle_label = await t_async("settings_toggle_off" if enabled else "settings_toggle_on", lang)

    title = await t_async("settings_title", lang)
    tz_line = await t_async("settings_tz_line", lang, tz=tz)
    window_line = await t_async("settings_window_line", lang, start=f"{start_h:02d}", end=f"{end_h:02d}")
    push_line = await t_async("settings_push_label", lang, status=status)

    text = f"{title}\n\n{tz_line}\n{window_line}\n{push_line}"
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=await settings_panel_keyboard(toggle_label, lang=lang),
    )


async def _send_quiz(send_fn, question, lang: str = "zh") -> None:
    """发送一道测验题（可被命令和调度器复用），根据题型组装不同文案，支持多语言"""
    keyboard = await quiz_keyboard(
        question.options,
        question.record_id,
        quiz_type=question.quiz_type,
        practice_mode=question.practice_mode,
        lang=lang,
    )

    if question.quiz_type == "meaning":
        title = await t_async("quiz_meaning_title", lang)
        instruction = await t_async("quiz_meaning_instruction", lang, word=question.word)
        text = (
            f"{title}\n\n"
            f"*{question.word}*\n\n"
            f'"{question.sentence}"\n\n'
            f"{instruction}"
        )
        await send_fn(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        # 填空题：用 HTML 格式，避免下划线被 Markdown 当成斜体符号解析
        safe_sentence = html_lib.escape(question.sentence).replace("______", "<b>______</b>")
        safe_def = html_lib.escape(question.definition)
        title = await t_async("quiz_fill_title", lang)
        instruction = await t_async("quiz_fill_instruction", lang)
        hint = await t_async("quiz_fill_hint", lang, definition=safe_def)
        text = (
            f"{title}\n\n"
            f"{instruction}\n\n"
            f"<i>{safe_sentence}</i>\n\n"
            f"{hint}"
        )
        await send_fn(text, parse_mode="HTML", reply_markup=keyboard)
