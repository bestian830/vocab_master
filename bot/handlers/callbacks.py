"""
Inline button 回调处理器：测验答题 + 词库翻页
"""
import logging
import re

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ai.parser import generate_example_sentence, translate_to_chinese
from core.sm2 import next_level_and_review, format_next_review, level_description
from database.client import (
    update_vocab_after_review, get_vocab_list, count_vocab, upsert_vocab,
    is_pro, get_today_add_count, delete_vocab_by_id, delete_vocab_by_word,
    get_vocab_detail, set_user_timezone, get_user_settings,
    update_remind_window, set_remind_enabled,
)
from config import FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from core.quiz import build_quiz
from bot.keyboards import quiz_keyboard, vocab_page_keyboard, sentence_vocab_keyboard, vocab_detail_keyboard, edit_field_keyboard, settings_panel_keyboard, remind_window_keyboard
from bot.handlers.commands import _send_quiz, _vocab_line

import math

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


def _extract_quiz_sentence(message_text: str, quiz_type: str, correct_word: str = "") -> str:
    """从题目消息纯文本中提取例句，用于答错反馈展示用户刚看过的那句话"""
    lines = [l.strip() for l in message_text.splitlines() if l.strip()]
    if quiz_type == "fill":
        # 找含占位符的行，跳过含中文字符的提示行（如"请选出最适合填入 ______ 的词："）
        for line in lines:
            if any('\u4e00' <= c <= '\u9fff' for c in line):
                continue  # 跳过含中文的行
            if "______" in line or "___" in line:
                if correct_word:
                    return re.sub(r'_+', correct_word, line, count=1)
                return line
    else:  # meaning
        # 找被引号包裹的行（选义题的例句格式）
        for line in lines:
            if line.startswith('"') and line.endswith('"'):
                return line[1:-1]  # 去掉引号
    return ""


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
    elif data.startswith("settings:"):
        await _handle_settings_callback(query, telegram_id, data)
        return

    await query.answer()   # 必须先 answer，消除加载动画

    if data.startswith("quiz:"):
        await _handle_quiz_answer(query, telegram_id, data, context=context)
    elif data.startswith("qm:"):
        await _handle_meaning_answer(query, telegram_id, data, context=context)
    elif data.startswith("qzp:"):
        # 练习模式填空题
        await _handle_quiz_answer(query, telegram_id, data, practice_mode=True, context=context)
    elif data.startswith("qmp:"):
        # 练习模式选义题
        await _handle_meaning_answer(query, telegram_id, data, practice_mode=True, context=context)
    elif data.startswith("vocab_page:"):
        await _handle_vocab_page(query, telegram_id, data)
    elif data.startswith("sa:"):
        await _handle_sentence_add(query, telegram_id, data, context)
    elif data.startswith("vd:"):
        await _handle_vocab_delete(query, telegram_id, data)
    elif data.startswith("qend:"):
        await _handle_quiz_end(query, data, context)
    else:
        logger.warning("未知 callback_data: %s", data)


# ── 测验答题 ──────────────────────────────────────────────────────────────────

async def _handle_quiz_answer(
    query, telegram_id: str, data: str, practice_mode: bool = False, context=None
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

    from database.client import get_client
    db = get_client()
    rows = db.table("vocab_records").select("word,level,definition,context,ease_factor").eq("id", record_id).limit(1).execute().data
    if not rows:
        await query.edit_message_text("记录不存在，可能已被删除。")
        return

    row = rows[0]
    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]
    current_ef = row.get("ease_factor") or 2.5

    if choice == "skip":
        if not practice_mode:
            # 跳过视为模糊：更新 DB，明天再复习
            new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, "fuzzy")
            next_review_iso = format_next_review(next_review_dt)
            try:
                update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
            except Exception as exc:
                logger.error("更新跳过记录失败: %s", exc)
        await query.edit_message_text(
            query.message.text + "\n\n⏭ 已跳过，明天继续复习",
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

    correct = (chosen_word.lower() == correct_word.lower())

    if practice_mode:
        # 练习模式：不更新 DB，反馈不显示级别变化
        if correct:
            feedback = (
                f"✅ *答对啦！*\n\n"
                f"*{correct_word}* — {definition}\n"
                f"🎮 练习模式，不计入复习进度"
            )
        else:
            # 优先使用题目中的例句（用户刚看过），fallback 到入库 context
            quiz_sentence = _extract_quiz_sentence(query.message.text, "fill", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            if context_text:
                try:
                    translation = await translate_to_chinese(context_text)
                    context_line = f"\n\n📖 _{context_text}_\n🔤 {translation}"
                except Exception:
                    context_line = f"\n\n📖 _{context_text}_"
            else:
                # 无例句时，调用 AI 生成一条例句补充语境
                try:
                    generated = await generate_example_sentence(correct_word, definition)
                    if generated:
                        try:
                            translation = await translate_to_chinese(generated)
                            context_line = f"\n\n💡 _{generated}_\n🔤 {translation}"
                        except Exception:
                            context_line = f"\n\n💡 _{generated}_"
                    else:
                        context_line = ""
                except Exception:
                    context_line = ""
            feedback = (
                f"❌ *答错了*\n\n"
                f"正确答案是：*{correct_word}* — {definition}"
                f"{context_line}\n\n"
                f"🎮 练习模式，不计入复习进度"
            )
    else:
        # 正式复习：使用新 3-outcome SM-2
        result = "correct" if correct else "wrong"
        new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, result)
        next_review_iso = format_next_review(next_review_dt)

        try:
            update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
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
            # 优先使用题目中的例句（用户刚看过），fallback 到入库 context
            quiz_sentence = _extract_quiz_sentence(query.message.text, "fill", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            if context_text:
                try:
                    translation = await translate_to_chinese(context_text)
                    context_line = f"\n\n📖 _{context_text}_\n🔤 {translation}"
                except Exception:
                    context_line = f"\n\n📖 _{context_text}_"
            else:
                # 无例句时，调用 AI 生成一条例句补充语境
                try:
                    generated = await generate_example_sentence(correct_word, definition)
                    if generated:
                        try:
                            translation = await translate_to_chinese(generated)
                            context_line = f"\n\n💡 _{generated}_\n🔤 {translation}"
                        except Exception:
                            context_line = f"\n\n💡 _{generated}_"
                    else:
                        context_line = ""
                except Exception:
                    context_line = ""
            feedback = (
                f"❌ *答错了*\n\n"
                f"正确答案是：*{correct_word}* — {definition}"
                f"{context_line}\n\n"
                f"😔 级别降至：{level_description(new_level)}\n"
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
    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context)


# ── 选义题答题 ────────────────────────────────────────────────────────────────

async def _handle_meaning_answer(
    query, telegram_id: str, data: str, practice_mode: bool = False, context=None
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

    # 从数据库获取正确释义和当前级别（含 ease_factor）
    from database.client import get_client
    db = get_client()
    rows = db.table("vocab_records").select("word,level,definition,context,ease_factor").eq("id", record_id).limit(1).execute().data
    if not rows:
        await query.edit_message_text("记录不存在，可能已被删除。")
        return

    row = rows[0]
    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]
    current_ef = row.get("ease_factor") or 2.5

    if choice == "skip":
        if not practice_mode:
            # 模糊/拿不准视为 fuzzy：更新 DB，明天再复习
            new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, "fuzzy")
            next_review_iso = format_next_review(next_review_dt)
            try:
                update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
            except Exception as exc:
                logger.error("更新模糊记录失败: %s", exc)
        await query.edit_message_text(
            query.message.text + "\n\n🤔 已标记为模糊，明天继续复习",
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

    # 比对用户所选释义与正确释义
    correct = (chosen_def.strip() == definition.strip())

    if practice_mode:
        # 练习模式：不更新 DB，反馈不显示级别变化
        if correct:
            feedback = (
                f"✅ *答对啦！*\n\n"
                f"*{correct_word}* — {definition}\n"
                f"🎮 练习模式，不计入复习进度"
            )
        else:
            # 优先使用题目中的例句（用户刚看过），fallback 到入库 context
            quiz_sentence = _extract_quiz_sentence(query.message.text, "meaning", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            if context_text:
                try:
                    translation = await translate_to_chinese(context_text)
                    context_line = f"\n\n📖 _{context_text}_\n🔤 {translation}"
                except Exception:
                    context_line = f"\n\n📖 _{context_text}_"
            else:
                # 无例句时，调用 AI 生成一条例句补充语境
                try:
                    generated = await generate_example_sentence(correct_word, definition)
                    if generated:
                        try:
                            translation = await translate_to_chinese(generated)
                            context_line = f"\n\n💡 _{generated}_\n🔤 {translation}"
                        except Exception:
                            context_line = f"\n\n💡 _{generated}_"
                    else:
                        context_line = ""
                except Exception:
                    context_line = ""
            feedback = (
                f"❌ *答错了*\n\n"
                f"正确释义是：*{correct_word}* — {definition}"
                f"{context_line}\n\n"
                f"🎮 练习模式，不计入复习进度"
            )
    else:
        # 正式复习：使用新 3-outcome SM-2
        result = "correct" if correct else "wrong"
        new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, result)
        next_review_iso = format_next_review(next_review_dt)

        try:
            update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
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
            # 优先使用题目中的例句（用户刚看过），fallback 到入库 context
            quiz_sentence = _extract_quiz_sentence(query.message.text, "meaning", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            if context_text:
                try:
                    translation = await translate_to_chinese(context_text)
                    context_line = f"\n\n📖 _{context_text}_\n🔤 {translation}"
                except Exception:
                    context_line = f"\n\n📖 _{context_text}_"
            else:
                # 无例句时，调用 AI 生成一条例句补充语境
                try:
                    generated = await generate_example_sentence(correct_word, definition)
                    if generated:
                        try:
                            translation = await translate_to_chinese(generated)
                            context_line = f"\n\n💡 _{generated}_\n🔤 {translation}"
                        except Exception:
                            context_line = f"\n\n💡 _{generated}_"
                    else:
                        context_line = ""
                except Exception:
                    context_line = ""
            feedback = (
                f"❌ *答错了*\n\n"
                f"正确释义是：*{correct_word}* — {definition}"
                f"{context_line}\n\n"
                f"😔 级别降至：{level_description(new_level)}\n"
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
    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context)


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

async def _push_next_quiz(query, telegram_id: str, practice_mode: bool = False, context=None) -> None:
    """答题或跳过后，自动推送下一道题目；若无题可推则发送完成提示并清除会话状态"""
    try:
        next_question = await build_quiz(telegram_id, practice_mode=practice_mode)
        if next_question:
            await _send_quiz(query.message.reply_text, next_question)
        else:
            # 无题可推，清除活跃会话状态
            if context is not None:
                context.user_data.pop("active_session", None)
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

async def _handle_quiz_end(query, data: str, context=None) -> None:
    """用户点击结束按钮，停止推题并清除活跃会话状态"""
    if data == "qend:p":
        msg = "练习已结束，随时可以 /review 继续～"
    else:
        msg = "复习已结束，随时可以 /review 继续～"
    # 清除会话状态，允许下次正常开始
    if context is not None:
        context.user_data.pop("active_session", None)
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
            f"复习提醒只在本地时间 08:00–22:00 之间推送。\n\n"
            f"以后可通过 /settings 修改推送时段或关闭提醒",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ── 通知设置回调 ───────────────────────────────────────────────────────────────

async def _handle_settings_callback(query, telegram_id: str, data: str) -> None:
    """
    处理 /settings 面板的所有 inline 回调。
    callback_data 格式：
      settings:window          → 显示推送时段选择面板
      settings:set_win:{s}:{e} → 写入时段，返回面板
      settings:toggle          → 切换 remind_enabled，刷新面板
      settings:back            → 返回主面板
    """
    parts = data.split(":", 3)
    action = parts[1] if len(parts) >= 2 else ""

    await query.answer()

    if action == "window":
        # 显示时段选择面板
        try:
            await query.edit_message_text(
                "⏰ 请选择推送时段：",
                reply_markup=remind_window_keyboard(),
            )
        except Exception:
            pass

    elif action == "set_win":
        # settings:set_win:{start}:{end}
        try:
            start_h = int(parts[2])
            end_h = int(parts[3])
        except (IndexError, ValueError):
            return
        try:
            update_remind_window(telegram_id, start_h, end_h)
        except Exception as exc:
            logger.error("更新推送时段失败: %s", exc)
        # 刷新主面板
        settings = get_user_settings(telegram_id)
        text, keyboard = _build_settings_panel(settings)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass

    elif action == "toggle":
        # 切换 remind_enabled
        settings = get_user_settings(telegram_id)
        current = settings.get("remind_enabled", True)
        try:
            set_remind_enabled(telegram_id, not current)
        except Exception as exc:
            logger.error("切换推送开关失败: %s", exc)
        # 刷新主面板（重新读取）
        settings = get_user_settings(telegram_id)
        text, keyboard = _build_settings_panel(settings)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass

    elif action == "back":
        # 返回主面板
        settings = get_user_settings(telegram_id)
        text, keyboard = _build_settings_panel(settings)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass


def _build_settings_panel(settings: dict) -> tuple[str, object]:
    """根据用户设置构造通知设置面板的文案和键盘"""
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
    keyboard = settings_panel_keyboard(toggle_label)
    return text, keyboard
