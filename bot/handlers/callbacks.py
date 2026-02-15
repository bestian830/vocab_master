"""
Inline button 回调处理器：测验答题 + 词库翻页
"""
import logging

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from core.sm2 import next_level_and_review, format_next_review, level_description
from database.client import (
    update_vocab_after_review, get_vocab_list, count_vocab, upsert_vocab,
    is_pro, get_today_add_count, delete_vocab_by_id, delete_vocab_by_word,
    get_vocab_detail, set_user_timezone, get_user_settings,
)
from config import FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from core.quiz import build_quiz
from bot.keyboards import quiz_keyboard, vocab_page_keyboard, sentence_vocab_keyboard, vocab_detail_keyboard, edit_field_keyboard
from bot.handlers.commands import _send_quiz, _vocab_line

import math

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    统一回调入口，根据 callback_data 前缀分发：
    - "quiz:{record_id}:{index|skip}" → 填空题答题
    - "qm:{record_id}:{index|skip}"   → 选义题答题
    - "vocab_page:{page}"             → 词库翻页
    - "sa:{msg_id}:{index}"           → 整句分析词汇入库
    """
    query = update.callback_query
    data: str = query.data
    telegram_id = str(update.effective_user.id)

    # vinfo / vedit / tz 回调单独处理（需要访问 context）
    if data.startswith("vinfo:"):
        await _handle_vocab_info(query, data)
        return
    elif data.startswith("vedit:"):
        await _handle_vocab_edit(query, telegram_id, data, context)
        return
    elif data.startswith("vedit_sel:"):
        await _handle_vocab_edit_select(query, data)
        return
    elif data.startswith("vedit_cancel:"):
        await _handle_vocab_edit_cancel(query, telegram_id, data, context)
        return
    elif data.startswith("tz:"):
        await _handle_timezone_select(query, telegram_id, data)
        return

    await query.answer()   # 必须先 answer，消除加载动画

    if data.startswith("quiz:"):
        await _handle_quiz_answer(query, telegram_id, data)
    elif data.startswith("qm:"):
        await _handle_meaning_answer(query, telegram_id, data)
    elif data.startswith("qzp:"):
        # 练习模式填空题
        await _handle_quiz_answer(query, telegram_id, data, practice_mode=True)
    elif data.startswith("qmp:"):
        # 练习模式选义题
        await _handle_meaning_answer(query, telegram_id, data, practice_mode=True)
    elif data.startswith("vocab_page:"):
        await _handle_vocab_page(query, telegram_id, data)
    elif data.startswith("sa:"):
        await _handle_sentence_add(query, telegram_id, data, context)
    elif data.startswith("vd:"):
        await _handle_vocab_delete(query, telegram_id, data)
    elif data.startswith("qend:"):
        await _handle_quiz_end(query, data)
    else:
        logger.warning("未知 callback_data: %s", data)


# ── 测验答题 ──────────────────────────────────────────────────────────────────

async def _handle_quiz_answer(
    query, telegram_id: str, data: str, practice_mode: bool = False
) -> None:
    """
    callback_data 格式: quiz:{record_id}:{option_index|skip}
    practice_mode=True 时不更新 DB，反馈不显示级别变化
    """
    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    _, record_id, choice = parts

    # 从消息中恢复选项（按钮文本）
    options = [
        btn.text
        for row in query.message.reply_markup.inline_keyboard[:2]
        for btn in row
    ]

    if choice == "skip":
        await query.edit_message_text(
            query.message.text + "\n\n⏭ 已跳过",
            parse_mode="Markdown",
        )
        await _push_next_quiz(query, telegram_id, practice_mode=practice_mode)
        return

    try:
        chosen_index = int(choice)
        chosen_word = options[chosen_index]
    except (ValueError, IndexError):
        logger.error("无法解析 choice: %s", choice)
        return

    from database.client import get_client
    db = get_client()
    rows = db.table("vocab_records").select("word,level,definition").eq("id", record_id).limit(1).execute().data
    if not rows:
        await query.edit_message_text("记录不存在，可能已被删除。")
        return

    row = rows[0]
    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]

    correct = (chosen_word.lower() == correct_word.lower())

    if practice_mode:
        # 练习模式：不更新 DB，反馈不显示级别变化
        if correct:
            feedback = (
                f"✅ *答对啦！*（练习模式）\n\n"
                f"*{correct_word}* — {definition}"
            )
        else:
            feedback = (
                f"❌ *答错了*（练习模式）\n\n"
                f"正确答案是：*{correct_word}* — {definition}"
            )
    else:
        # 正式复习：更新 SM-2 级别
        new_level, next_review_dt = next_level_and_review(current_level, correct)
        next_review_iso = format_next_review(next_review_dt)

        try:
            update_vocab_after_review(record_id, new_level, next_review_iso)
        except Exception as exc:
            logger.error("更新复习记录失败: %s", exc)

        if correct:
            feedback = (
                f"✅ *答对啦！*\n\n"
                f"*{correct_word}* — {definition}\n"
                f"🎯 当前级别：{level_description(new_level)}\n"
                f"📅 下次复习：{next_review_dt.strftime('%m/%d')}"
            )
        else:
            feedback = (
                f"❌ *答错了*\n\n"
                f"正确答案是：*{correct_word}* — {definition}\n"
                f"😔 级别重置为：{level_description(new_level)}\n"
                f"📅 明天再复习一次吧！"
            )

    try:
        await query.edit_message_text(feedback, parse_mode="Markdown")
    except BadRequest as e:
        # 用户快速双击时消息内容未变，忽略即可
        if "not modified" not in str(e).lower():
            raise
        return

    # 自动推送下一题
    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode)


# ── 选义题答题 ────────────────────────────────────────────────────────────────

async def _handle_meaning_answer(
    query, telegram_id: str, data: str, practice_mode: bool = False
) -> None:
    """
    callback_data 格式: qm:{record_id}:{option_index|skip}
    用户选择中文释义，与数据库中的 definition 比对判断正误
    practice_mode=True 时不更新 DB，反馈不显示级别变化
    """
    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    _, record_id, choice = parts

    if choice == "skip":
        # 模糊/拿不准：不更新 DB，提示后推送下一题
        await query.edit_message_text(
            query.message.text + "\n\n🤔 已标记为模糊，下次继续复习",
            parse_mode="Markdown",
        )
        await _push_next_quiz(query, telegram_id, practice_mode=practice_mode)
        return

    # 从按钮文本恢复选项列表（前两行共4个选项）
    options = [
        btn.text
        for row in query.message.reply_markup.inline_keyboard[:2]
        for btn in row
    ]

    try:
        chosen_index = int(choice)
        chosen_def = options[chosen_index]
    except (ValueError, IndexError):
        logger.error("无法解析 choice: %s", choice)
        return

    # 从数据库获取正确释义和当前级别
    from database.client import get_client
    db = get_client()
    rows = db.table("vocab_records").select("word,level,definition").eq("id", record_id).limit(1).execute().data
    if not rows:
        await query.edit_message_text("记录不存在，可能已被删除。")
        return

    row = rows[0]
    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]

    # 比对用户所选释义与正确释义
    correct = (chosen_def.strip() == definition.strip())

    if practice_mode:
        # 练习模式：不更新 DB，反馈不显示级别变化
        if correct:
            feedback = (
                f"✅ *答对啦！*（练习模式）\n\n"
                f"*{correct_word}* — {definition}"
            )
        else:
            feedback = (
                f"❌ *答错了*（练习模式）\n\n"
                f"正确释义是：*{correct_word}* — {definition}"
            )
    else:
        # 正式复习：更新 SM-2 级别
        new_level, next_review_dt = next_level_and_review(current_level, correct)
        next_review_iso = format_next_review(next_review_dt)

        try:
            update_vocab_after_review(record_id, new_level, next_review_iso)
        except Exception as exc:
            logger.error("更新复习记录失败: %s", exc)

        if correct:
            feedback = (
                f"✅ *答对啦！*\n\n"
                f"*{correct_word}* — {definition}\n"
                f"🎯 当前级别：{level_description(new_level)}\n"
                f"📅 下次复习：{next_review_dt.strftime('%m/%d')}"
            )
        else:
            feedback = (
                f"❌ *答错了*\n\n"
                f"正确释义是：*{correct_word}* — {definition}\n"
                f"😔 级别重置为：{level_description(new_level)}\n"
                f"📅 明天再复习一次吧！"
            )

    try:
        await query.edit_message_text(feedback, parse_mode="Markdown")
    except BadRequest as e:
        # 用户快速双击时消息内容未变，忽略即可
        if "not modified" not in str(e).lower():
            raise
        return

    # 自动推送下一题
    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode)


# ── 词库翻页 ──────────────────────────────────────────────────────────────────

async def _handle_vocab_page(query, telegram_id: str, data: str) -> None:
    """
    callback_data 格式: vocab_page:{page}（0-indexed）
    """
    try:
        page = int(data.split(":")[1])
    except (ValueError, IndexError):
        return

    total = count_vocab(telegram_id)
    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    records = get_vocab_list(telegram_id, page=page, page_size=PAGE_SIZE)

    # 正文只显示标题行，词汇详情通过按钮弹窗展示
    text = f"📚 *你的词库* ({page + 1}/{total_pages} 页，共 {total} 词)\n点击单词按钮查看详情"
    keyboard = vocab_page_keyboard(page, total_pages, records)
    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except BadRequest as e:
        # 快速重复点击时消息内容未变，忽略即可
        if "not modified" not in str(e).lower():
            raise


# ── 整句分析词汇入库 ──────────────────────────────────────────────────────────

async def _handle_sentence_add(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: sa:{msg_id}:{index}
    将整句分析中用户选中的词汇入库，更新按钮显示 ✅，消息保持不变。
    """
    parts = data.split(":", 2)
    if len(parts) != 3:
        return

    _, msg_id_str, index_str = parts
    try:
        msg_id = int(msg_id_str)
        index = int(index_str)
    except ValueError:
        logger.error("无法解析 sa callback: %s", data)
        return

    # 从 chat_data 中取回词汇列表
    vocabs_raw: list[dict] | None = context.chat_data.get(str(msg_id))
    if not vocabs_raw or index >= len(vocabs_raw):
        await query.answer("词汇信息已过期，请重新发送句子。", show_alert=True)
        return

    vocab = vocabs_raw[index]

    # 入库前检查免费用户限额
    if not is_pro(telegram_id):
        if count_vocab(telegram_id) >= FREE_WORD_LIMIT:
            await query.answer(
                f"词库已达 {FREE_WORD_LIMIT} 词上限，订阅 Pro 解锁无限词库。",
                show_alert=True,
            )
            return
        if get_today_add_count(telegram_id) >= FREE_DAILY_LIMIT:
            await query.answer(
                f"今日已添加 {FREE_DAILY_LIMIT} 个词，明天再来或订阅 Pro。",
                show_alert=True,
            )
            return

    # 入库
    try:
        record, is_new = upsert_vocab(
            telegram_id=telegram_id,
            word=vocab["word"],
            pos=vocab["pos"],
            definition=vocab["definition"],
            context=vocab["context"],
        )
    except Exception as exc:
        logger.error("整句词汇入库失败 (%s): %s", vocab["word"], exc)
        await query.answer("保存失败，请重试。", show_alert=True)
        return

    hint = "已加入词库！" if is_new else "已在词库中"
    await query.answer(f"✅ {vocab['word']} {hint}")

    # 记录已添加的下标（存在 chat_data 的辅助 key 中）
    added_key = f"{msg_id}_added"
    added: set[int] = context.chat_data.get(added_key, set())
    added.add(index)
    context.chat_data[added_key] = added

    # 重建 ParsedVocab-like 对象列表供 keyboard 函数使用
    class _V:
        def __init__(self, d):
            self.word = d["word"]
    vocab_objs = [_V(v) for v in vocabs_raw]

    keyboard = sentence_vocab_keyboard(vocab_objs, msg_id, added)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass  # 消息内容未变时 Telegram 会报错，忽略即可


# ── 自动推送下一题 ─────────────────────────────────────────────────────────────

async def _push_next_quiz(query, telegram_id: str, practice_mode: bool = False) -> None:
    """答题或跳过后，自动推送下一道题目；若无题可推则发送完成提示"""
    try:
        next_question = await build_quiz(telegram_id, practice_mode=practice_mode)
        if next_question:
            await _send_quiz(query.message.reply_text, next_question)
        else:
            if practice_mode:
                await query.message.reply_text("🎉 练习完成！")
            else:
                await query.message.reply_text("🎉 本轮复习完成！保持学习节奏哦～")
    except Exception as exc:
        logger.error("推送下一题失败: %s", exc)


# ── 删词回调 ──────────────────────────────────────────────────────────────────

async def _handle_vocab_delete(query, telegram_id: str, data: str) -> None:
    """
    callback_data 格式：
      vd:confirm:{record_id}  — 单条确认删除
      vd:one:{record_id}      — 多义词中删除某一条
      vd:all:{word}           — 多义词全部删除
      vd:cancel               — 取消
    """
    parts = data.split(":", 2)
    if len(parts) < 2:
        return

    action = parts[1]

    if action == "cancel":
        await query.edit_message_text("❌ 已取消删除。")
        return

    if action in ("confirm", "one"):
        if len(parts) < 3:
            return
        record_id = parts[2]
        deleted = delete_vocab_by_id(record_id)
        if deleted:
            await query.edit_message_text("✅ 已删除该词汇条目。")
        else:
            await query.edit_message_text("⚠️ 删除失败，记录可能已不存在。")

    elif action == "all":
        if len(parts) < 3:
            return
        word = parts[2]
        count = delete_vocab_by_word(telegram_id, word)
        if count > 0:
            await query.edit_message_text(f"✅ 已删除「{word}」的全部 {count} 个释义。")
        else:
            await query.edit_message_text("⚠️ 删除失败，记录可能已不存在。")


# ── 结束测验 ───────────────────────────────────────────────────────────────────

async def _handle_quiz_end(query, data: str) -> None:
    """用户点击结束按钮，停止推题"""
    if data == "qend:p":
        msg = "练习已结束，随时可以 /review 继续～"
    else:
        msg = "复习已结束，随时可以 /review 继续～"
    await query.edit_message_reply_markup(reply_markup=None)   # 移除键盘
    await query.message.reply_text(msg)


# ── 词汇详情（编辑消息模式）────────────────────────────────────────────────────

async def _handle_vocab_info(query, data: str) -> None:
    """
    callback_data 格式: vinfo:{record_id}:{page}
    将消息替换为词汇详情 + 编辑按钮 + 返回按钮（取代旧的 show_alert 弹窗）
    """
    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.answer("解析错误", show_alert=False)
        return

    record_id = parts[1]
    try:
        page = int(parts[2])
    except ValueError:
        page = 0

    record = get_vocab_detail(record_id)
    if not record:
        await query.answer("词汇记录不存在，可能已被删除。", show_alert=True)
        return

    from bot.handlers.commands import _format_vocab_detail
    text = _format_vocab_detail(record)
    keyboard = vocab_detail_keyboard(record_id, page)

    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ── 词汇编辑：进入字段编辑 ────────────────────────────────────────────────────

async def _handle_vocab_edit(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: vedit:{record_id}:{field}:{page}
    field = pos / definition / context
    将消息替换为「请发送新 xxx：」提示，并在 user_data 中记录 pending_edit
    """
    parts = data.split(":", 3)
    if len(parts) != 4:
        await query.answer("解析错误", show_alert=False)
        return

    _, record_id, field, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    # 字段名映射
    field_name = {"pos": "词性", "definition": "释义", "context": "例句"}.get(field)
    if not field_name:
        await query.answer("未知字段", show_alert=False)
        return

    record = get_vocab_detail(record_id)
    if not record:
        await query.answer("词汇记录不存在，可能已被删除。", show_alert=True)
        return

    word = record.get("word", "")
    current_val = record.get(field, "") or "（无）"

    # 将 pending_edit 写入 user_data
    context.user_data["pending_edit"] = {
        "record_id": record_id,
        "field": field,
        "word": word,
        "page": page,
    }

    text = (
        f"✏️ 编辑 *{word}* 的{field_name}\n\n"
        f"当前值：_{current_val}_\n\n"
        f"请直接发送新{field_name}："
    )
    keyboard = edit_field_keyboard(record_id, field, page)

    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ── 词汇编辑：多义词选择 ──────────────────────────────────────────────────────

async def _handle_vocab_edit_select(query, data: str) -> None:
    """
    callback_data 格式: vedit_sel:{record_id}:{page}
    用户从多义词列表中选择了一条，展示该条的详情 + 编辑按钮
    """
    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.answer("解析错误", show_alert=False)
        return

    _, record_id, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        page = 0

    record = get_vocab_detail(record_id)
    if not record:
        await query.answer("词汇记录不存在，可能已被删除。", show_alert=True)
        return

    from bot.handlers.commands import _format_vocab_detail
    text = _format_vocab_detail(record)
    keyboard = vocab_detail_keyboard(record_id, page)

    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ── 词汇编辑：取消 ────────────────────────────────────────────────────────────

async def _handle_vocab_edit_cancel(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: vedit_cancel:{page}
    清除 pending_edit，返回词库列表（原页码）
    """
    # 清除编辑状态
    context.user_data.pop("pending_edit", None)

    parts = data.split(":", 1)
    try:
        page = int(parts[1])
    except (IndexError, ValueError):
        page = 0

    # 重新加载词库列表
    total = count_vocab(telegram_id)
    import math as _math
    total_pages = _math.ceil(total / PAGE_SIZE) if total > 0 else 1
    page = max(0, min(page, total_pages - 1))
    records = get_vocab_list(telegram_id, page=page, page_size=PAGE_SIZE)

    text = f"📚 *你的词库* ({page + 1}/{total_pages} 页，共 {total} 词)\n点击单词按钮查看详情"
    keyboard = vocab_page_keyboard(page, total_pages, records)

    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ── 时区选择回调 ──────────────────────────────────────────────────────────────

async def _handle_timezone_select(query, telegram_id: str, data: str) -> None:
    """
    callback_data 格式: tz:{IANA_timezone}
    保存用户时区设置，并更新消息文本反馈
    """
    parts = data.split(":", 1)
    if len(parts) != 2:
        await query.answer("解析错误", show_alert=False)
        return

    tz_value = parts[1]

    try:
        set_user_timezone(telegram_id, tz_value)
    except Exception as exc:
        logger.error("保存时区失败 (%s): %s", tz_value, exc)
        await query.answer("保存失败，请重试。", show_alert=True)
        return

    await query.answer(f"✅ 时区已设置为 {tz_value}", show_alert=False)
    # 更新消息，移除键盘
    try:
        await query.edit_message_text(
            f"🌏 *时区设置*\n\n"
            f"✅ 已保存时区：`{tz_value}`\n\n"
            f"复习提醒只在本地时间 08:00–22:00 之间推送。",
            parse_mode="Markdown",
        )
    except Exception:
        pass
