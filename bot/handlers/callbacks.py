"""
Inline button 回调处理器：测验答题 + 词库翻页 + 多语言管理
"""
import logging
import math
import re
import time

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ai.parser import generate_example_sentence, translate_to_native, explain_word_for_teaching
from core.sm2 import next_level_and_review, format_next_review
from core.language import get_language_display, get_native_language_display
from database.client import (
    update_vocab_after_review, get_vocab_list, count_vocab, upsert_vocab,
    is_pro, get_today_add_count, delete_vocab_by_id, delete_vocab_by_word,
    get_vocab_detail, set_user_timezone, get_user_settings,
    update_remind_window, set_remind_enabled, get_vocab_count_by_language,
    set_user_languages, add_learning_language,
    count_vocab_by_native, delete_vocab_by_native_language,
    set_proficiency_level,
)
from config import FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from core.quiz import build_quiz
from bot.i18n import t_async
from bot.keyboards import (
    quiz_keyboard, vocab_page_keyboard, sentence_vocab_keyboard,
    vocab_detail_keyboard, settings_panel_keyboard,
    remind_window_keyboard, language_panel_keyboard, add_language_keyboard,
    native_language_keyboard, onboarding_lang_keyboard, timezone_keyboard,
    vocab_confirm_keyboard, native_switch_confirm_keyboard,
    assessment_keyboard, level_select_keyboard, synonym_quiz_keyboard,
    proficiency_keyboard,
)
import random as _random
from bot.handlers.commands import _send_quiz, _vocab_line

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


def _extract_quiz_sentence(message_text: str, quiz_type: str, correct_word: str = "") -> str:
    """
    从题目消息纯文本中提取例句，用于答错反馈展示用户刚看过的那句话。
    使用语言无关的规则（不依赖中文字符检测）。
    """
    lines = [l.strip() for l in message_text.splitlines() if l.strip()]
    if quiz_type == "fill":
        for line in lines:
            # 跳过标题行（以特定 emoji 开头）
            if line.startswith(("🧠", "💡", "🔤")):
                continue
            # 跳过以冒号结尾的提示/指令行（各语言均适用）
            if line.endswith((":", "：")):
                continue
            if "______" in line or "___" in line:
                if correct_word:
                    filled = re.sub(r'_+', correct_word, line, count=1)
                    # 修复短语首词重复：如 "in in limbo" → "in limbo"
                    words = correct_word.split()
                    if len(words) > 1:
                        first = re.escape(words[0])
                        phrase = re.escape(correct_word)
                        filled = re.sub(
                            rf'(?<!\w){first}\s+{phrase}(?!\w)',
                            correct_word,
                            filled,
                            flags=re.IGNORECASE,
                        )
                    return filled
                return line
    else:  # meaning
        # 找被引号包裹的行（选义题的例句格式）
        for line in lines:
            if line.startswith('"') and line.endswith('"'):
                return line[1:-1]
    return ""


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    统一回调入口，根据 callback_data 前缀分发：
    - "quiz:{record_id}:{index|skip}" → 填空题答题
    - "qm:{record_id}:{index|skip}"   → 选义题答题
    - "vocab_page:{page}"             → 词库翻页
    - "sa:{msg_id}:{index}"           → 整句分析词汇入库
    - "lang:{action}:{...}"           → 多语言管理
    """
    query = update.callback_query
    data: str = query.data
    telegram_id = str(update.effective_user.id)

    # vinfo / explain / tz / settings / lang 回调单独处理（需要访问 context，内部自行 answer）
    if data.startswith("vinfo:"):
        await _handle_vocab_info(query, data, context)
        return
    elif data.startswith("explain:"):
        await _handle_explain_word(query, telegram_id, data, context)
        return
    elif data.startswith("tz:"):
        await _handle_timezone_select(query, telegram_id, data, context)
        return
    elif data.startswith("settings:"):
        await _handle_settings_callback(query, telegram_id, data, context)
        return
    elif data.startswith("lang:"):
        await _handle_language_callback(query, telegram_id, data, context)
        return
    elif data.startswith("ob_native:"):
        await _handle_onboard_native(query, telegram_id, data, context)
        return
    elif data.startswith("ob_lang:"):
        await _handle_onboard_lang(query, telegram_id, data, context)
        return
    elif data.startswith("vc:"):
        await _handle_vocab_confirm(query, telegram_id, data, context)
        return
    elif data.startswith("assess_toggle:"):
        await _handle_assess_toggle(query, telegram_id, data, context)
        return
    elif data.startswith("assess_next:"):
        await _handle_assess_next(query, telegram_id, data, context)
        return
    elif data == "assess_done":
        await _handle_assess_done(query, telegram_id, context)
        return
    elif data.startswith("level_set:"):
        await _handle_level_set(query, telegram_id, data, context)
        return

    await query.answer()

    if data.startswith("quiz:"):
        await _handle_quiz_answer(query, telegram_id, data, context=context)
    elif data.startswith("qm:"):
        await _handle_meaning_answer(query, telegram_id, data, context=context)
    elif data.startswith("qzp:"):
        await _handle_quiz_answer(query, telegram_id, data, practice_mode=True, context=context)
    elif data.startswith("qmp:"):
        await _handle_meaning_answer(query, telegram_id, data, practice_mode=True, context=context)
    elif data.startswith("qs:"):
        await _handle_synonym_answer(query, telegram_id, data, context=context)
    elif data.startswith("qsp:"):
        await _handle_synonym_answer(query, telegram_id, data, practice_mode=True, context=context)
    elif data.startswith("gen_skip:"):
        await _handle_gen_skip(query, telegram_id, data, context)
    elif data.startswith("vocab_page:"):
        await _handle_vocab_page(query, telegram_id, data, context)
    elif data.startswith("sa:"):
        await _handle_sentence_add(query, telegram_id, data, context)
    elif data.startswith("vd:"):
        await _handle_vocab_delete(query, telegram_id, data, context)
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
    从 vocab_record 读取 native_language 决定反馈语言
    """
    # 防止 Telegram 重试导致的重复处理（网络断线重传场景）
    if context is not None:
        answered_msgs: set = context.user_data.setdefault("answered_msgs", set())
        _msg_id = query.message.message_id
        if _msg_id in answered_msgs:
            await query.answer()
            return
        answered_msgs.add(_msg_id)

    # 用户已回答，清除调度器推送记录，允许下次继续推送
    try:
        from scheduler.reminder import _pending_quiz
        _pending_quiz.pop(telegram_id, None)
    except Exception:
        pass

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

    row = get_vocab_detail(record_id)
    if not row:
        await query.edit_message_text(await t_async("vocab_record_not_found", "zh"))
        return

    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]
    current_ef = row.get("ease_factor") or 2.5
    target_language = row.get("target_language", "en")
    native_language = row.get("native_language", "zh")
    lang = native_language

    if choice == "skip":
        if not practice_mode:
            new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, "fuzzy")
            next_review_iso = format_next_review(next_review_dt)
            try:
                update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
            except Exception as exc:
                logger.error("更新跳过记录失败: %s", exc)
        skip_append = await t_async("quiz_skip_append", lang)
        await query.edit_message_text(
            query.message.text + skip_append,
            parse_mode="Markdown",
        )
        await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)
        return

    try:
        chosen_index = int(choice)
        chosen_word = options[chosen_index]
    except (ValueError, IndexError):
        logger.error("无法解析 choice: %s", choice)
        return

    correct = (chosen_word.lower() == correct_word.lower())

    if practice_mode:
        if correct:
            # 答对时也展示例句翻译，辅助学习
            quiz_sentence = _extract_quiz_sentence(query.message.text, "fill", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            feedback = await t_async("quiz_correct_practice", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line)
        else:
            quiz_sentence = _extract_quiz_sentence(query.message.text, "fill", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            feedback = await t_async("quiz_wrong_fill_practice", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line)
    else:
        result = "correct" if correct else "wrong"
        new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, result)
        next_review_iso = format_next_review(next_review_dt)

        try:
            update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
        except Exception as exc:
            logger.error("更新复习记录失败: %s", exc)

        if correct:
            # 答对时也展示例句翻译，辅助学习
            quiz_sentence = _extract_quiz_sentence(query.message.text, "fill", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            level_text = await t_async(f"level_{new_level}", lang)
            feedback = await t_async("quiz_correct", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line,
                                     level=level_text, date=next_review_dt.strftime('%m/%d'))
        else:
            quiz_sentence = _extract_quiz_sentence(query.message.text, "fill", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            level_text = await t_async(f"level_{new_level}", lang)
            feedback = await t_async("quiz_wrong_fill", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line, level=level_text)

    try:
        await query.edit_message_text(feedback, parse_mode="Markdown")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise
        return

    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)


# ── 选义题答题 ────────────────────────────────────────────────────────────────

async def _handle_meaning_answer(
    query, telegram_id: str, data: str, practice_mode: bool = False, context=None
) -> None:
    """
    callback_data 格式: qm:{record_id}:{option_index|skip}
    用户选择母语释义，与数据库中的 definition 比对判断正误
    """
    # 防止 Telegram 重试导致的重复处理（网络断线重传场景）
    if context is not None:
        answered_msgs: set = context.user_data.setdefault("answered_msgs", set())
        _msg_id = query.message.message_id
        if _msg_id in answered_msgs:
            await query.answer()
            return
        answered_msgs.add(_msg_id)

    # 用户已回答，清除调度器推送记录，允许下次继续推送
    try:
        from scheduler.reminder import _pending_quiz
        _pending_quiz.pop(telegram_id, None)
    except Exception:
        pass

    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    _, record_id, choice = parts

    row = get_vocab_detail(record_id)
    if not row:
        await query.edit_message_text(await t_async("vocab_record_not_found", "zh"))
        return

    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]
    current_ef = row.get("ease_factor") or 2.5
    target_language = row.get("target_language", "en")
    native_language = row.get("native_language", "zh")
    lang = native_language

    if choice == "skip":
        if not practice_mode:
            new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, "fuzzy")
            next_review_iso = format_next_review(next_review_dt)
            try:
                update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
            except Exception as exc:
                logger.error("更新模糊记录失败: %s", exc)
        fuzzy_append = await t_async("quiz_fuzzy_append", lang)
        await query.edit_message_text(
            query.message.text + fuzzy_append,
            parse_mode="Markdown",
        )
        await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)
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

    correct = (chosen_def.strip() == definition.strip())

    if practice_mode:
        if correct:
            # 答对时也展示例句翻译，辅助学习
            quiz_sentence = _extract_quiz_sentence(query.message.text, "meaning", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            feedback = await t_async("quiz_correct_practice", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line)
        else:
            quiz_sentence = _extract_quiz_sentence(query.message.text, "meaning", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            feedback = await t_async("quiz_wrong_meaning_practice", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line)
    else:
        result = "correct" if correct else "wrong"
        new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, result)
        next_review_iso = format_next_review(next_review_dt)

        try:
            update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
        except Exception as exc:
            logger.error("更新复习记录失败: %s", exc)

        if correct:
            # 答对时也展示例句翻译，辅助学习
            quiz_sentence = _extract_quiz_sentence(query.message.text, "meaning", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            level_text = await t_async(f"level_{new_level}", lang)
            feedback = await t_async("quiz_correct", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line,
                                     level=level_text, date=next_review_dt.strftime('%m/%d'))
        else:
            quiz_sentence = _extract_quiz_sentence(query.message.text, "meaning", correct_word)
            context_text = quiz_sentence or row.get("context") or ""
            context_line = await _build_context_line(context_text, correct_word, definition, target_language, native_language)
            level_text = await t_async(f"level_{new_level}", lang)
            feedback = await t_async("quiz_wrong_meaning", lang,
                                     word=correct_word, definition=definition,
                                     context_line=context_line, level=level_text)

    try:
        await query.edit_message_text(feedback, parse_mode="Markdown")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise
        return

    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)


# ── 词库翻页 ──────────────────────────────────────────────────────────────────

async def _handle_vocab_page(query, telegram_id: str, data: str, context=None) -> None:
    """
    callback_data 格式: vocab_page:{page}（0-indexed）
    从 context.user_data 读取 vocab_language 和 native_language
    """
    try:
        page = int(data.split(":")[1])
    except (ValueError, IndexError):
        return

    active_language = "en"
    lang = "zh"
    if context is not None:
        active_language = context.user_data.get("vocab_language", "en")
        lang = context.user_data.get("native_language", "zh")

    total = count_vocab(telegram_id, target_language=active_language, native_language=lang)
    total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1
    page = max(0, min(page, total_pages - 1))

    records = get_vocab_list(
        telegram_id, page=page, page_size=PAGE_SIZE,
        target_language=active_language, native_language=lang,
    )
    lang_display = get_language_display(active_language)

    title = await t_async("vocab_title", lang, lang_name=lang_display, page=page + 1, total_pages=total_pages, total=total)
    hint = await t_async("vocab_click_hint", lang)
    text = f"{title}\n{hint}"
    keyboard = await vocab_page_keyboard(page, total_pages, records, lang=lang)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
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

    vocabs_raw: list[dict] | None = context.chat_data.get(str(msg_id))
    if not vocabs_raw or index >= len(vocabs_raw):
        await query.answer("词汇信息已过期，请重新发送句子。", show_alert=True)
        return

    vocab = vocabs_raw[index]

    lang_meta = context.chat_data.get(f"{msg_id}_lang", {})
    target_language = lang_meta.get("target_language", "en")
    native_language = lang_meta.get("native_language", "zh")

    # 入库前检查免费用户限额
    if not is_pro(telegram_id):
        if count_vocab(telegram_id, target_language=target_language) >= FREE_WORD_LIMIT:
            alert = await t_async("limit_total_alert", native_language, limit=FREE_WORD_LIMIT)
            await query.answer(alert, show_alert=True)
            return
        if get_today_add_count(telegram_id) >= FREE_DAILY_LIMIT:
            alert = await t_async("limit_daily_alert", native_language, limit=FREE_DAILY_LIMIT)
            await query.answer(alert, show_alert=True)
            return

    try:
        record, is_new = upsert_vocab(
            telegram_id=telegram_id,
            word=vocab["word"],
            pos=vocab["pos"],
            definition=vocab["definition"],
            context=vocab["context"],
            target_language=target_language,
            native_language=native_language,
            word_level=vocab.get("word_level"),
            quiz_synonyms=vocab.get("quiz_synonyms"),
            antonyms=vocab.get("antonyms"),
            word_family=vocab.get("word_family"),
            etymology=vocab.get("etymology"),
            collocations=vocab.get("collocations"),
        )
    except Exception as exc:
        logger.error("整句词汇入库失败 (%s): %s", vocab["word"], exc)
        await query.answer("保存失败，请重试。", show_alert=True)
        return

    hint = "已加入词库！" if is_new else "已在词库中"
    await query.answer(f"✅ {vocab['word']} {hint}")

    # 新词入库后，检查是否触发动态等级调整
    if is_new:
        new_count = count_vocab(telegram_id, target_language=target_language)
        settings_fresh = get_user_settings(telegram_id)
        current_proficiency = settings_fresh.get("proficiency_level", 0) or 0
        if current_proficiency > 0:
            from bot.handlers.messages import _check_dynamic_level_adjust
            await _check_dynamic_level_adjust(
                telegram_id, new_count, current_proficiency, native_language,
                query.message.reply_text,
            )

    added_key = f"{msg_id}_added"
    added: set[int] = context.chat_data.get(added_key, set())
    added.add(index)
    context.chat_data[added_key] = added

    class _V:
        def __init__(self, d):
            self.word = d["word"]
    vocab_objs = [_V(v) for v in vocabs_raw]

    keyboard = sentence_vocab_keyboard(vocab_objs, msg_id, added)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


# ── 自动推送下一题 ─────────────────────────────────────────────────────────────

async def _push_next_quiz(
    query, telegram_id: str, practice_mode: bool = False, context=None, lang: str = "zh"
) -> None:
    """
    答题或跳过后，自动推送下一道题目。
    practice 模式：从 context.user_data["practice_queue"] pop 取词（shuffle 队列），
                   队列耗尽时重新获取并 shuffle（循环不停止）。
    review 模式：沿用原逻辑（build_quiz 随机选到期词）。
    """
    target_language = "en"
    native_language = "zh"
    if context is not None:
        target_language = context.user_data.get("active_language", "en")
        native_language = context.user_data.get("native_language", "zh")

    try:
        force_vocab = None
        if practice_mode and context is not None:
            # 从 shuffle 队列 pop 下一个词；队列空则重新获取并 shuffle
            from database.client import get_practice_vocab
            queue: list = context.user_data.get("practice_queue", [])
            if not queue:
                queue = get_practice_vocab(
                    telegram_id, target_language=target_language, native_language=native_language
                )
                _random.shuffle(queue)
            if queue:
                force_vocab = queue.pop(0)
                context.user_data["practice_queue"] = queue

        next_question = await build_quiz(
            telegram_id, practice_mode=practice_mode,
            target_language=target_language, native_language=native_language,
            force_vocab=force_vocab,
        )
        if next_question:
            sent = await _send_quiz(query.message.reply_text, next_question, lang=lang, context=context)
            if sent is not None and context is not None:
                context.user_data["last_quiz_msg_id"] = sent.message_id
        else:
            if context is not None:
                context.user_data.pop("active_session", None)
            if practice_mode:
                done_msg = await t_async("practice_done", lang)
            else:
                done_msg = await t_async("quiz_done", lang)
            await query.message.reply_text(done_msg)
    except Exception as exc:
        logger.error("推送下一题失败: %s", exc)


# ── 删词回调 ──────────────────────────────────────────────────────────────────

async def _handle_vocab_delete(query, telegram_id: str, data: str, context=None) -> None:
    """
    callback_data 格式：
      vd:confirm:{record_id}  — 单条确认删除
      vd:one:{record_id}      — 多义词中删除某一条
      vd:all:{word}           — 多义词全部删除
      vd:cancel               — 取消
    """
    # 获取用户语言
    lang = "zh"
    if context is not None:
        lang = context.user_data.get("native_language", "zh")

    parts = data.split(":", 2)
    if len(parts) < 2:
        return

    action = parts[1]

    if action == "cancel":
        msg = await t_async("delete_cancelled", lang)
        await query.edit_message_text(msg)
        return

    if action in ("confirm", "one"):
        if len(parts) < 3:
            return
        record_id = parts[2]
        deleted = delete_vocab_by_id(record_id)
        if deleted:
            msg = await t_async("delete_ok_one", lang)
        else:
            msg = await t_async("delete_failed", lang)
        await query.edit_message_text(msg)

    elif action == "all":
        if len(parts) < 3:
            return
        word = parts[2]
        # 从 user_data 读取激活语言，限制跨语言删除
        active_language = None
        if context is not None:
            active_language = context.user_data.get("active_language")
        count = delete_vocab_by_word(telegram_id, word, target_language=active_language)
        if count > 0:
            msg = await t_async("delete_ok_all", lang, word=word, count=count)
        else:
            msg = await t_async("delete_failed", lang)
        await query.edit_message_text(msg)


# ── 结束测验 ───────────────────────────────────────────────────────────────────

async def _handle_quiz_end(query, data: str, context=None) -> None:
    """用户点击结束按钮，停止推题并清除活跃会话状态"""
    lang = "zh"
    if context is not None:
        lang = context.user_data.get("native_language", "zh")

    if data == "qend:p":
        msg = await t_async("quiz_end_practice", lang)
    else:
        msg = await t_async("quiz_end_review", lang)

    if context is not None:
        context.user_data.pop("active_session", None)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(msg)


# ── 词汇详情（编辑消息模式）────────────────────────────────────────────────────

async def _handle_vocab_info(query, data: str, context=None) -> None:
    """
    callback_data 格式: vinfo:{record_id}:{page}
    将消息替换为词汇详情 + 编辑按钮 + 返回按钮
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

    # 从 record 中取语言，fallback 到 user_data
    lang = record.get("native_language", "zh")
    if context is not None:
        lang = context.user_data.get("native_language", lang)

    from bot.handlers.commands import _format_vocab_detail
    text = await _format_vocab_detail(record, lang=lang)
    keyboard = await vocab_detail_keyboard(record_id, page, lang=lang)

    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ── 词汇讲词功能 ──────────────────────────────────────────────────────────────

async def _handle_explain_word(query, telegram_id: str, data: str, context=None) -> None:
    """
    callback_data 格式: explain:{record_id}:{page}
    调用 AI 对词汇进行深度讲解，展示教学内容。
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

    # 读取用户母语
    native_language = record.get("native_language", "zh")
    if context is not None:
        native_language = context.user_data.get("native_language", native_language)

    await query.answer()

    # 展示加载提示
    loading_text = "⏳ AI 讲词中，请稍候…"
    try:
        await query.edit_message_text(loading_text)
    except Exception:
        pass

    try:
        explanation = await explain_word_for_teaching(record, native_language=native_language)
    except Exception as exc:
        logger.error("AI 讲词失败 (record_id=%s): %s", record_id, exc)
        explanation = "讲词服务暂时不可用，请稍后再试。"

    # 构建返回按钮（返回词汇详情页）
    word = record.get("word", "")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    back_btn = InlineKeyboardButton("← 返回", callback_data=f"vinfo:{record_id}:{page}")
    keyboard = InlineKeyboardMarkup([[back_btn]])

    # 讲词内容可能较长（>4096 chars），超长时发新消息
    max_len = 4000
    if len(explanation) > max_len:
        explanation = explanation[:max_len] + "\n\n…（内容已截断）"

    try:
        await query.edit_message_text(explanation, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            # Markdown 解析失败（可能含特殊字符），降级为纯文本
            try:
                await query.edit_message_text(explanation, reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(explanation, reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(explanation, reply_markup=keyboard)


# ── 时区选择回调 ──────────────────────────────────────────────────────────────

async def _handle_timezone_select(query, telegram_id: str, data: str, context=None) -> None:
    """callback_data 格式: tz:{IANA_timezone}"""
    parts = data.split(":", 1)
    if len(parts) != 2:
        await query.answer("解析错误", show_alert=False)
        return

    tz_value = parts[1]
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

    try:
        set_user_timezone(telegram_id, tz_value)
    except Exception as exc:
        logger.error("保存时区失败 (%s): %s", tz_value, exc)
        fail_msg = await t_async("timezone_save_fail", lang)
        await query.answer(fail_msg, show_alert=True)
        return

    toast = await t_async("timezone_saved_toast", lang, tz=tz_value)
    await query.answer(toast, show_alert=False)
    try:
        # 若处于 onboarding 流程，时区完成后接入词汇水平评估
        if context and context.user_data.pop("onboarding_tz", False):
            settings = get_user_settings(telegram_id)
            active_lang = settings.get("active_language", "en")

            if active_lang == "en":
                # 英语：启动 5 组锚点词 toggle 评估
                from core.assessment import ASSESSMENT_BATCHES
                context.user_data["assessment"] = {
                    "batch": 1,
                    "selections": {},
                    "target_lang": active_lang,
                    "lang": lang,
                }
                words = ASSESSMENT_BATCHES[1]
                msg = (
                    "📊 *词汇水平测评* — 第 1 组 / 共 5 组\n\n"
                    "点击你 *确定认识* 的单词（可多选）："
                )
                keyboard = assessment_keyboard(1, words, set(), is_last_batch=False)
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
            else:
                # 非英语：显示自选水平键盘
                msg = (
                    "📊 *词汇水平设置*\n\n"
                    "请选择你在该语言的词汇水平："
                )
                keyboard = level_select_keyboard()
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
        else:
            saved_msg = await t_async("timezone_saved", lang, tz=tz_value)
            await query.edit_message_text(saved_msg, parse_mode="Markdown")
    except Exception:
        pass


# ── 通知设置回调 ───────────────────────────────────────────────────────────────

async def _handle_settings_callback(query, telegram_id: str, data: str, context=None) -> None:
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

    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

    await query.answer()

    if action == "window":
        prompt = await t_async("settings_window_prompt", lang)
        try:
            await query.edit_message_text(
                prompt,
                reply_markup=await remind_window_keyboard(lang=lang),
            )
        except Exception:
            pass

    elif action == "set_win":
        try:
            start_h = int(parts[2])
            end_h = int(parts[3])
        except (IndexError, ValueError):
            return
        try:
            update_remind_window(telegram_id, start_h, end_h)
        except Exception as exc:
            logger.error("更新推送时段失败: %s", exc)
        settings = get_user_settings(telegram_id)
        text, keyboard = await _build_settings_panel(settings, lang)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass

    elif action == "toggle":
        current = settings.get("remind_enabled", True)
        try:
            set_remind_enabled(telegram_id, not current)
        except Exception as exc:
            logger.error("切换推送开关失败: %s", exc)
        settings = get_user_settings(telegram_id)
        text, keyboard = await _build_settings_panel(settings, lang)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass

    elif action == "back":
        settings = get_user_settings(telegram_id)
        text, keyboard = await _build_settings_panel(settings, lang)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass


async def _build_settings_panel(settings: dict, lang: str = "zh") -> tuple[str, object]:
    """根据用户设置构造通知设置面板的文案和键盘"""
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
    keyboard = await settings_panel_keyboard(toggle_label, lang=lang)
    return text, keyboard


# ── 多语言管理回调 ─────────────────────────────────────────────────────────────

async def _handle_language_callback(
    query, telegram_id: str, data: str, context
) -> None:
    """
    处理所有 lang:* 回调。
    callback_data 格式：
      lang:switch:{code}      → 切换激活语言
      lang:add                → 显示添加语言面板
      lang:add_confirm:{code} → 添加语言并设为激活
      lang:native             → 显示母语设置面板
      lang:set_native:{code}  → 设置母语
      lang:back               → 返回主面板
    """
    parts = data.split(":", 2)
    action = parts[1] if len(parts) >= 2 else ""

    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")

    await query.answer()

    if action == "switch":
        lang_code = parts[2] if len(parts) >= 3 else "en"
        try:
            set_user_languages(telegram_id, active_language=lang_code)
        except Exception as exc:
            logger.error("切换激活语言失败: %s", exc)
        if context is not None:
            context.user_data["active_language"] = lang_code
        await _refresh_language_panel(query, telegram_id, lang)

    elif action == "add":
        existing = settings.get("learning_languages") or ["en"]
        keyboard = await add_language_keyboard(existing, lang=lang)
        title = await t_async("lang_add_title", lang)
        try:
            await query.edit_message_text(title, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass

    elif action == "add_confirm":
        lang_code = parts[2] if len(parts) >= 3 else "en"
        try:
            add_learning_language(telegram_id, lang_code)
            set_user_languages(telegram_id, active_language=lang_code)
        except Exception as exc:
            logger.error("添加语言失败: %s", exc)
        if context is not None:
            context.user_data["active_language"] = lang_code
        await _refresh_language_panel(query, telegram_id, lang)

    elif action == "native":
        current_native = settings.get("native_language", "zh")
        keyboard = await native_language_keyboard(current_native, lang=lang)
        title = await t_async("lang_native_title", lang)
        try:
            await query.edit_message_text(title, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass

    elif action == "set_native":
        lang_code = parts[2] if len(parts) >= 3 else "zh"
        current_native = settings.get("native_language", "zh")
        # 相同母语：静默刷新面板，不弹警告
        if lang_code == current_native:
            await _refresh_language_panel(query, telegram_id, lang)
            return
        # 统计当前母语词库数量，用于警告文案
        word_count = count_vocab_by_native(telegram_id, current_native)
        new_display = get_native_language_display(lang_code)
        current_display = get_native_language_display(current_native)
        # 警告消息用【当前母语】显示
        warning = await t_async(
            "native_switch_warning", lang,
            current=current_display, count=word_count,
        )
        keyboard = await native_switch_confirm_keyboard(lang_code, lang=lang)
        try:
            await query.edit_message_text(warning, parse_mode="Markdown",
                                          reply_markup=keyboard)
        except Exception:
            pass

    elif action == "do_native":
        lang_code = parts[2] if len(parts) >= 3 else "zh"
        current_native = settings.get("native_language", "zh")
        # 删除旧母语下的全部词汇
        deleted = delete_vocab_by_native_language(telegram_id, current_native)
        # 更新母语设置
        try:
            set_user_languages(telegram_id, native_language=lang_code)
        except Exception as exc:
            logger.error("切换母语失败: %s", exc)
        if context is not None:
            context.user_data["native_language"] = lang_code
        new_display = get_native_language_display(lang_code)
        # 成功消息用【新母语】语言显示
        done_msg = await t_async(
            "native_switch_done", lang_code,
            display=new_display, count=deleted,
        )
        try:
            await query.edit_message_text(done_msg, parse_mode="Markdown")
        except Exception:
            pass
        await _refresh_language_panel(query, telegram_id, lang_code)

    elif action == "back":
        await _refresh_language_panel(query, telegram_id, lang)


async def _refresh_language_panel(query, telegram_id: str, lang: str = "zh") -> None:
    """重新生成并展示多语言管理主面板（编辑当前消息）"""
    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    learning_languages = settings.get("learning_languages") or ["en"]

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

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.error("刷新语言面板失败: %s", e)


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

async def _build_context_line(
    context_text: str,
    word: str,
    definition: str,
    target_language: str,
    native_language: str,
) -> str:
    """
    构建答错时的语境行（例句 + 翻译）。
    若无例句则调 AI 生成一条。
    返回格式："\n\n📖 _例句_\n🔤 翻译" 或空串。
    """
    if context_text:
        try:
            translation = await translate_to_native(context_text, native_language=native_language)
            return f"\n\n📖 _{context_text}_\n🔤 {translation}"
        except Exception:
            return f"\n\n📖 _{context_text}_"
    else:
        try:
            generated = await generate_example_sentence(
                word, definition, target_language=target_language
            )
            if generated:
                try:
                    translation = await translate_to_native(generated, native_language=native_language)
                    return f"\n\n💡 _{generated}_\n🔤 {translation}"
                except Exception:
                    return f"\n\n💡 _{generated}_"
        except Exception:
            pass
    return ""


# ── 新用户引导回调 ─────────────────────────────────────────────────────────────

async def _handle_onboard_native(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: ob_native:{lang_code}
    用户选择母语 → 保存并显示学习语言选择键盘
    """
    code = data[len("ob_native:"):]
    try:
        set_user_languages(telegram_id, native_language=code)
    except Exception as exc:
        logger.error("引导设置母语失败: %s", exc)

    msg = await t_async("onboard_lang_title", code)
    keyboard = await onboarding_lang_keyboard(code)
    await query.answer()
    try:
        await query.edit_message_text(msg, reply_markup=keyboard)
    except Exception:
        pass


async def _handle_vocab_confirm(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式:
      vc:add:{msg_id}:{idx}  — 入库解析结果中的第 idx 条词汇
      vc:skip:{msg_id}       — 跳过，取消本次入库
    """
    parts = data.split(":", 3)
    if len(parts) < 3:
        await query.answer()
        return

    action = parts[1]

    # 已入库的占位按钮：直接静默响应
    if action == "done":
        await query.answer()
        return

    await query.answer()

    if action == "skip":
        msg_id_str = parts[2]
        # 清理暂存数据
        context.chat_data.pop(f"vc_{msg_id_str}", None)
        context.chat_data.pop(f"vc_{msg_id_str}_lang", None)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if action == "add":
        if len(parts) < 4:
            return
        msg_id_str = parts[2]
        try:
            idx = int(parts[3])
        except ValueError:
            return

        vocabs_raw: list[dict] | None = context.chat_data.get(f"vc_{msg_id_str}")
        if not vocabs_raw or idx >= len(vocabs_raw):
            await query.answer("词汇信息已过期，请重新发送。", show_alert=True)
            return

        lang_meta = context.chat_data.get(f"vc_{msg_id_str}_lang", {})
        target_language = lang_meta.get("target_language", "en")
        native_language = lang_meta.get("native_language", "zh")
        tid = lang_meta.get("telegram_id", telegram_id)
        lang = native_language

        vocab = vocabs_raw[idx]

        # 入库前检查免费用户限额
        if not is_pro(tid):
            if count_vocab(tid, target_language=target_language) >= FREE_WORD_LIMIT:
                alert = await t_async("limit_total_alert", lang, limit=FREE_WORD_LIMIT)
                await query.answer(alert, show_alert=True)
                return
            if get_today_add_count(tid) >= FREE_DAILY_LIMIT:
                alert = await t_async("limit_daily_alert", lang, limit=FREE_DAILY_LIMIT)
                await query.answer(alert, show_alert=True)
                return

        try:
            _, is_new = upsert_vocab(
                telegram_id=tid,
                word=vocab["word"],
                pos=vocab["pos"],
                definition=vocab["definition"],
                context=vocab["context"],
                target_language=target_language,
                native_language=native_language,
                word_level=vocab.get("word_level"),
                quiz_synonyms=vocab.get("quiz_synonyms"),
                antonyms=vocab.get("antonyms"),
                word_family=vocab.get("word_family"),
                etymology=vocab.get("etymology"),
                collocations=vocab.get("collocations"),
            )
        except Exception as exc:
            logger.error("vc:add 入库失败 (%s): %s", vocab["word"], exc)
            await query.answer("保存失败，请重试。", show_alert=True)
            return

        hint = "已加入词库！" if is_new else "已在词库中"
        await query.answer(f"✅ {vocab['word']} {hint}")

        # 新词入库后，检查是否触发动态等级调整
        if is_new:
            new_count = count_vocab(tid, target_language=target_language)
            settings_fresh = get_user_settings(tid)
            current_proficiency = settings_fresh.get("proficiency_level", 0) or 0
            if current_proficiency > 0:
                from bot.handlers.messages import _check_dynamic_level_adjust
                await _check_dynamic_level_adjust(
                    tid, new_count, current_proficiency, lang,
                    query.message.reply_text,
                )

        # 将已入库的按钮替换为 ✅ 已添加，不可再点
        class _V:
            def __init__(self, d):
                self.word = d["word"]
                self.pos = d.get("pos")
        vocab_objs = [_V(v) for v in vocabs_raw]

        # 标记已入库按钮：更新按钮标签为"✅ 已添加 {word}"
        added_label = "✅ 已添加" if lang == "zh" else "✅ Added"
        skip_label = "Skip ❌" if lang != "zh" else "跳过 ❌"
        add_label = "✅ Add" if lang != "zh" else "✅ 添加"

        # 追踪已入库下标
        added_key = f"vc_{msg_id_str}_added"
        added: set[int] = context.chat_data.get(added_key, set())
        added.add(idx)
        context.chat_data[added_key] = added

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for i, v in enumerate(vocab_objs):
            pos_part = f"  [{v.pos}]" if v.pos else ""
            if i in added:
                label = f"{added_label}  {v.word}{pos_part}"
                # 已入库的不再响应，callback 指向一个无害的占位
                rows.append([InlineKeyboardButton(label, callback_data=f"vc:done:{msg_id_str}:{i}")])
            else:
                label = f"{add_label}  {v.word}{pos_part}"
                rows.append([InlineKeyboardButton(label, callback_data=f"vc:add:{msg_id_str}:{i}")])
        # 只有还存在未添加的词时才显示跳过按钮
        if len(added) < len(vocab_objs):
            rows.append([InlineKeyboardButton(skip_label, callback_data=f"vc:skip:{msg_id_str}")])
        keyboard = InlineKeyboardMarkup(rows)

        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass


async def _handle_onboard_lang(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: ob_lang:{lang_code}
    用户选择学习语言 → 保存并显示时区选择键盘
    """
    code = data[len("ob_lang:"):]
    try:
        add_learning_language(telegram_id, code)
        set_user_languages(telegram_id, active_language=code)
    except Exception as exc:
        logger.error("引导设置学习语言失败: %s", exc)

    if context is not None:
        context.user_data["onboarding_tz"] = True   # 标记进入引导时区步骤

    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")
    tz_prompt = await t_async("start_tz_prompt", lang)
    await query.answer()
    try:
        await query.edit_message_text(tz_prompt, reply_markup=timezone_keyboard())
    except Exception:
        pass


# ── 词汇水平评估回调 ───────────────────────────────────────────────────────────

async def _handle_assess_toggle(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: assess_toggle:{batch_idx}:{word_idx}
    切换词汇的选中/未选状态，刷新消息中的键盘。
    """
    parts = data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    try:
        batch_idx = int(parts[1])
        word_idx = int(parts[2])
    except ValueError:
        await query.answer()
        return

    await query.answer()

    assessment = context.user_data.get("assessment", {})
    selections = assessment.get("selections", {})
    batch_selections: set = set(selections.get(batch_idx, set()))

    # toggle
    if word_idx in batch_selections:
        batch_selections.discard(word_idx)
    else:
        batch_selections.add(word_idx)
    selections[batch_idx] = batch_selections
    assessment["selections"] = selections
    context.user_data["assessment"] = assessment

    from core.assessment import ASSESSMENT_BATCHES
    words = ASSESSMENT_BATCHES.get(batch_idx, [])
    is_last = (batch_idx == 5)

    # 只更新键盘，不重传消息文本，速度快约 3-5 倍，避免快速点击时请求冲突
    keyboard = assessment_keyboard(batch_idx, words, batch_selections, is_last_batch=is_last)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


async def _handle_assess_next(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: assess_next:{batch_idx}
    从当前组前往下一组（batch_idx + 1）。
    """
    parts = data.split(":")
    if len(parts) != 2:
        await query.answer()
        return
    try:
        batch_idx = int(parts[1])
    except ValueError:
        await query.answer()
        return

    await query.answer()

    next_batch = batch_idx + 1
    if next_batch > 5:
        # 不应发生，直接触发完成
        await _handle_assess_done(query, telegram_id, context)
        return

    # 确保当前组 selections 初始化
    assessment = context.user_data.get("assessment", {})
    if "selections" not in assessment:
        assessment["selections"] = {}
    assessment["batch"] = next_batch
    context.user_data["assessment"] = assessment

    from core.assessment import ASSESSMENT_BATCHES
    words = ASSESSMENT_BATCHES.get(next_batch, [])
    is_last = (next_batch == 5)
    current_selections = set(assessment["selections"].get(next_batch, set()))

    msg = (
        f"📊 *词汇水平测评* — 第 {next_batch} 组 / 共 5 组\n\n"
        f"点击你 *确定认识* 的单词（可多选）："
    )
    keyboard = assessment_keyboard(next_batch, words, current_selections, is_last_batch=is_last)
    try:
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        pass


async def _handle_assess_done(query, telegram_id: str, context) -> None:
    """
    callback_data: assess_done
    计算评估结果，写入 DB，显示结果消息。
    """
    await query.answer()

    from core.assessment import score_assessment, get_level_name, ASSESSMENT_BATCHES
    assessment = context.user_data.pop("assessment", {})
    selections = assessment.get("selections", {})

    level = score_assessment(selections)
    lang = assessment.get("lang", "zh")

    try:
        set_proficiency_level(telegram_id, level, mark_done=True)
    except Exception as exc:
        logger.error("保存词汇水平失败: %s", exc)

    level_name = get_level_name(level, lang)

    # 构建反馈消息
    level_hints = {
        1: "之后分析句子时我会推荐初学词汇。",
        2: "之后分析句子时我会推荐高中/四级水平词汇。",
        3: "之后分析句子时我会推荐六级/雅思6水平词汇。",
        4: "之后分析句子时我会推荐托福/雅思7+水平词汇。",
        5: "之后分析句子时我会推荐GRE/学术写作水平词汇。",
    }
    hint = level_hints.get(level, "")

    msg = (
        f"🎯 *评估完成！*\n\n"
        f"你的词汇水平大约是 *Level {level}（{level_name}）*\n\n"
        f"{hint}\n\n"
        f"你也可以随时用 /level 调整词汇等级。"
    )
    try:
        await query.edit_message_text(msg, parse_mode="Markdown")
    except Exception:
        pass


# ── /level 手动设置词汇水平 ───────────────────────────────────────────────────

async def _handle_level_set(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: level_set:{level}（1-5）
    手动设置用户词汇水平，静默写入 DB 后刷新键盘。
    """
    parts = data.split(":")
    if len(parts) != 2:
        await query.answer()
        return
    try:
        level = int(parts[1])
        level = max(1, min(5, level))
    except ValueError:
        await query.answer()
        return

    try:
        set_proficiency_level(telegram_id, level, mark_done=True)
    except Exception as exc:
        logger.error("手动设置词汇水平失败: %s", exc)

    from core.assessment import get_level_name
    settings = get_user_settings(telegram_id)
    lang = settings.get("native_language", "zh")
    level_name = get_level_name(level, lang)

    await query.answer(f"✅ 词汇等级已设为 Level {level}", show_alert=False)

    # 判断是来自 onboarding 还是 /level 命令（看消息内容区分）
    msg_text = query.message.text or ""
    if "测评" in msg_text or "词汇水平设置" in msg_text and "level_set" not in msg_text:
        # onboarding 自选流程：显示完成消息
        msg = (
            f"🎯 *设置完成！*\n\n"
            f"你的词汇水平已设为 *Level {level}（{level_name}）*\n\n"
            f"你也可以随时用 /level 调整词汇等级。"
        )
        try:
            await query.edit_message_text(msg, parse_mode="Markdown")
        except Exception:
            pass
    else:
        # /level 命令：刷新键盘
        text = (
            f"📊 *词汇水平设置*\n\n"
            f"当前等级：**Level {level}** — {level_name}\n\n"
            f"选择你的词汇水平："
        )
        keyboard = proficiency_keyboard(level)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            pass


# ── 同义词辨析题回调 ──────────────────────────────────────────────────────────

async def _handle_synonym_answer(
    query, telegram_id: str, data: str, practice_mode: bool = False, context=None
) -> None:
    """
    callback_data 格式: qs:{record_id}:{choice_idx|skip}
    用户选择同义词选项，判断是否正确并更新 SM-2。
    """
    # 防止 Telegram 重试导致的重复处理（网络断线重传场景）
    if context is not None:
        answered_msgs: set = context.user_data.setdefault("answered_msgs", set())
        _msg_id = query.message.message_id
        if _msg_id in answered_msgs:
            await query.answer()
            return
        answered_msgs.add(_msg_id)

    # 用户已回答，清除调度器推送记录，允许下次继续推送
    try:
        from scheduler.reminder import _pending_quiz
        _pending_quiz.pop(telegram_id, None)
    except Exception:
        pass

    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    _, record_id, choice = parts

    row = get_vocab_detail(record_id)
    if not row:
        await query.edit_message_text(await t_async("vocab_record_not_found", "zh"))
        return

    correct_word = row["word"]
    current_level = row["level"]
    definition = row["definition"]
    current_ef = row.get("ease_factor") or 2.5
    target_language = row.get("target_language", "en")
    native_language = row.get("native_language", "zh")
    lang = native_language

    # 取正确同义词（quiz_synonyms[0]）
    quiz_synonyms = row.get("quiz_synonyms") or []
    correct_synonym = quiz_synonyms[0] if quiz_synonyms else ""

    if choice == "skip":
        if not practice_mode:
            new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, "fuzzy")
            next_review_iso = format_next_review(next_review_dt)
            try:
                update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
            except Exception as exc:
                logger.error("更新跳过记录失败: %s", exc)
        skip_append = await t_async("quiz_skip_append", lang)
        await query.edit_message_text(
            query.message.text + skip_append,
            parse_mode="Markdown",
        )
        await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)
        return

    # 从按钮文本恢复选项
    options = [
        btn.text
        for row_kb in query.message.reply_markup.inline_keyboard[:2]
        for btn in row_kb
    ]
    try:
        chosen_index = int(choice)
        chosen_word = options[chosen_index]
    except (ValueError, IndexError):
        return

    correct = correct_synonym and (chosen_word.strip() == correct_synonym.strip())

    if practice_mode:
        feedback = (
            f"✅ *正确！*\n\n*{correct_word}* — {definition}\n同义词：{correct_synonym}"
            if correct else
            f"❌ *不对*\n\n*{correct_word}* — {definition}\n正确同义词：*{correct_synonym}*"
        )
    else:
        result = "correct" if correct else "wrong"
        new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, result)
        next_review_iso = format_next_review(next_review_dt)
        try:
            update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
        except Exception as exc:
            logger.error("更新同义词题记录失败: %s", exc)

        if correct:
            level_text = await t_async(f"level_{new_level}", lang)
            feedback = (
                f"✅ *正确！*\n\n*{correct_word}* — {definition}\n"
                f"同义词：{correct_synonym}\n\n"
                f"📈 {level_text} → 🗓 {next_review_dt.strftime('%m/%d')}"
            )
        else:
            level_text = await t_async(f"level_{new_level}", lang)
            feedback = (
                f"❌ *答错了*\n\n*{correct_word}* — {definition}\n"
                f"正确同义词：*{correct_synonym}*\n\n"
                f"📉 {level_text}"
            )

    try:
        await query.edit_message_text(feedback, parse_mode="Markdown")
    except Exception:
        pass

    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)


# ── 造句题跳过回调 ─────────────────────────────────────────────────────────────

async def _handle_gen_skip(query, telegram_id: str, data: str, context) -> None:
    """
    callback_data 格式: gen_skip:{record_id}
    跳过造句题，等同于 fuzzy，清除 pending_generation 状态。
    """
    parts = data.split(":", 1)
    record_id = parts[1] if len(parts) == 2 else ""

    # 清除 pending_generation 状态
    pending = context.user_data.pop("pending_generation", None)
    lang = "zh"
    if pending:
        lang = pending.get("native_language", "zh")
        practice_mode = pending.get("practice_mode", False)
        current_ef = pending.get("ease_factor", 2.5)
        current_level = pending.get("current_level", 0)

        if not practice_mode and record_id:
            new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, "fuzzy")
            next_review_iso = format_next_review(next_review_dt)
            try:
                update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
            except Exception as exc:
                logger.error("造句跳过更新失败: %s", exc)
    else:
        practice_mode = False

    await query.answer()

    word = pending.get("word", "") if pending else ""
    skip_msg = f"⏭️ 已跳过「{word}」的造句练习" if word else "⏭️ 已跳过"
    try:
        await query.edit_message_text(skip_msg)
    except Exception:
        pass

    await _push_next_quiz(query, telegram_id, practice_mode=practice_mode, context=context, lang=lang)
