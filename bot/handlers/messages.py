"""
处理用户发送的普通文本消息（词汇查询入口）
"""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from ai.parser import parse_user_input, analyze_sentence, evaluate_sentence
from database.client import (
    upsert_vocab, is_pro, count_vocab, get_today_add_count,
    update_vocab_fields, get_user_settings,
    update_vocab_after_review, get_recent_word_levels, set_proficiency_level,
    get_vocab_by_word,
)
from config import FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from bot.keyboards import sentence_vocab_keyboard, vocab_confirm_keyboard
from bot.i18n import t_async
from core.sm2 import next_level_and_review, format_next_review

logger = logging.getLogger(__name__)


def _is_sentence(text: str) -> bool:
    """
    判断输入是否为完整句子（需走整句分析流程）。
    规则：末尾有句末标点，或词数 >= 7。
    """
    stripped = text.rstrip()
    ends_with_punct = stripped and stripped[-1] in ".!?"
    word_count = len(text.split())
    return ends_with_punct or word_count >= 7


def _is_context_question(text: str, context) -> bool:
    """
    判断用户输入是否为对上一句话的上下文提问（"这里的X是什么意思"）。
    条件：词数不超过20，含询问意思的关键词，且 user_data 中有 last_sentence。
    """
    if not context.user_data.get("last_sentence"):
        return False
    words = text.split()
    if len(words) > 20:
        return False
    # 中文询问模式 + 英文询问模式
    pattern = re.compile(
        r'是什么意思|啥意思|是啥意思|什么意思|mean[st]?\b|meaning\s+of|'
        r'what\s+does|what\s+is.*mean|how\s+to\s+use|怎么用|啥意思',
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def _extract_context_word(text: str, last_sentence: str) -> str | None:
    """
    从上下文提问中提取目标词。
    支持多种提问格式：'这里的X是什么意思'/'what does X mean'/'引号包裹'等。
    """
    # 引号包裹的词（中英文引号）
    m = re.search(r'[「"\'"]([^「"\'"\s]{1,30})[」"\'"]', text)
    if m:
        return m.group(1).strip()

    # 中文模式："的X是什么意思" / "这里的X啥意思"
    m = re.search(
        r'(?:的|这里的|那个|该)([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff\-\s]{1,30}?)'
        r'(?:是什么意思|啥意思|是啥|什么意思|mean)',
        text,
    )
    if m:
        return m.group(1).strip()

    # 英文模式："what does X mean" / "meaning of X"
    m = re.search(
        r'(?:what\s+does\s+|meaning\s+of\s+)([\w\-\']+(?:\s+[\w\-\']+)?)',
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    # 在文本中找到出现在 last_sentence 里的英文单词/短语
    # 先找文本中的英文单词
    eng_words = re.findall(r'\b([A-Za-z][a-zA-Z\-\']{2,25}(?:\s+[a-zA-Z]{2,15})?)\b', text)
    for w in eng_words:
        w_lower = w.lower()
        if w_lower in last_sentence.lower() and len(w_lower) > 2:
            return w.strip()

    # 最后兜底：找任意英文单词（长度>2）
    m = re.search(r'\b([A-Za-z][a-zA-Z\-\']{2,25})\b', text)
    if m:
        return m.group(1).strip()

    return None


async def _handle_context_question(
    update: Update,
    context,
    user_input: str,
    telegram_id: str,
    lang: str,
    active_language: str,
    native_language: str,
) -> bool:
    """
    处理上下文提问（如"这里的trunk是什么意思"）。
    返回 True 表示已处理，False 表示无法处理（调用方继续走普通流程）。
    """
    last_sentence = context.user_data.get("last_sentence", "")

    # 提取目标词
    word = _extract_context_word(user_input, last_sentence)
    if not word:
        return False

    processing_msg = await update.message.reply_text("⏳ 分析中…")

    try:
        from ai.parser import explain_word_in_context
        result = await explain_word_in_context(
            word=word,
            context_sentence=last_sentence,
            native_language=native_language,
            target_language=active_language,
        )
    except Exception as exc:
        logger.error("上下文词义解析失败: %s", exc)
        await processing_msg.delete()
        return False

    explanation = result.get("explanation", "")
    pos = result.get("pos", "noun")
    definition = result.get("definition", "")
    actual_word = result.get("word", word)

    if not explanation:
        await processing_msg.delete()
        return False

    # 在 chat_data 中存储词汇，供用户点击"加入词库"使用
    msg_id = processing_msg.message_id
    context.chat_data[f"vc_{msg_id}"] = [{
        "word": actual_word,
        "pos": pos,
        "definition": definition,
        "context": last_sentence,
        "word_level": None,
        "quiz_synonyms": None,
        "antonyms": None,
        "word_family": None,
        "etymology": None,
        "collocations": None,
    }]
    context.chat_data[f"vc_{msg_id}_lang"] = {
        "target_language": active_language,
        "native_language": native_language,
        "telegram_id": telegram_id,
    }

    # 构建"加入词库"按钮的轻量 vocab 对象
    class _SimpleVocab:
        def __init__(self, word, pos):
            self.word = word
            self.pos = pos

    vocab_objs = [_SimpleVocab(actual_word, pos)]
    keyboard = await vocab_confirm_keyboard(vocab_objs, msg_id, lang)

    # 展示解析内容 + 加入词库按钮
    header = f"📌 *{actual_word}* 在此处的含义：\n\n"
    full_text = header + explanation
    try:
        await processing_msg.edit_text(
            full_text, parse_mode="Markdown", reply_markup=keyboard
        )
    except Exception:
        # Markdown 解析失败则降级为纯文本
        try:
            await processing_msg.edit_text(full_text, reply_markup=keyboard)
        except Exception:
            await update.message.reply_text(full_text, reply_markup=keyboard)

    return True


async def _handle_generation_answer(
    update, context, user_sentence: str, pending: dict
) -> None:
    """
    处理造句题的用户回答：调用 AI 评分，根据结果更新 SM-2，支持最多 2 次重试。
    """
    record_id    = pending["record_id"]
    word         = pending["word"]
    definition   = pending["definition"]
    target_lang  = pending["target_language"]
    native_lang  = pending["native_language"]
    practice_mode = pending["practice_mode"]
    current_ef   = pending["ease_factor"]
    current_level = pending["current_level"]
    retry_count  = pending["retry_count"]

    # 调用 AI 评分
    processing_msg = await update.message.reply_text("⏳ 评分中…")
    try:
        eval_result = await evaluate_sentence(
            word=word,
            definition=definition,
            user_sentence=user_sentence,
            target_lang=target_lang,
            native_lang=native_lang,
        )
    except Exception as exc:
        logger.error("造句评分失败: %s", exc)
        eval_result = {
            "result": "fuzzy",
            "grammar_ok": True,
            "context_ok": True,
            "feedback": "评分服务暂时不可用。",
            "improved": user_sentence,
        }

    result    = eval_result["result"]
    feedback  = eval_result.get("feedback", "")
    improved  = eval_result.get("improved", user_sentence)

    if result == "wrong" and retry_count < 2:
        # 还能重试：给提示但不公布答案
        pending["retry_count"] = retry_count + 1
        context.user_data["pending_generation"] = pending

        retry_msg = (
            f"❌ *语境不太对*\n\n"
            f"💡 提示：{feedback}\n\n"
            f"再试一次？（剩余 {2 - retry_count} 次机会）"
        )
        await processing_msg.edit_text(retry_msg, parse_mode="Markdown")
        return

    # 最终判定：按结果更新 SM-2
    context.user_data.pop("pending_generation", None)

    if result == "correct":
        sm2_result = "correct"
    elif result == "fuzzy":
        sm2_result = "fuzzy"
    else:
        sm2_result = "wrong"

    if not practice_mode:
        new_level, new_ef, next_review_dt = next_level_and_review(current_level, current_ef, sm2_result)
        next_review_iso = format_next_review(next_review_dt)
        try:
            update_vocab_after_review(record_id, new_level, next_review_iso, ease_factor=new_ef)
        except Exception as exc:
            logger.error("造句题更新失败: %s", exc)
        date_str = next_review_dt.strftime("%m/%d")
    else:
        new_level = current_level
        date_str = ""

    # 构建回复文案
    if result == "correct":
        reply = (
            f"✅ *很好！*\n\n"
            f"你的句子：{user_sentence}\n"
            f"语法 ✓，语境 ✓\n\n"
            f"💡 更地道的表达：\n_{improved}_"
        )
        if not practice_mode and date_str:
            reply += f"\n\n🗓 下次复习：{date_str}"
    elif result == "fuzzy":
        reply = (
            f"🤔 *差一点点*\n\n"
            f"你的句子：{user_sentence}\n\n"
            f"💡 {feedback}\n\n"
            f"更好的表达：\n_{improved}_"
        )
        if not practice_mode and date_str:
            reply += f"\n\n🗓 明天再复习"
    else:
        # wrong（已用完重试次数）
        reply = (
            f"❌ *这次没答对*\n\n"
            f"你的句子：{user_sentence}\n\n"
            f"💡 {feedback}\n\n"
            f"参考表达：\n_{improved}_"
        )
        if not practice_mode and date_str:
            reply += f"\n\n🗓 明天再复习"

    try:
        await processing_msg.edit_text(reply, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(reply, parse_mode="Markdown")


async def _check_dynamic_level_adjust(
    telegram_id: str, new_count: int, current_level: int, lang: str, send_fn
) -> None:
    """
    每新增第 50 个词时触发动态等级调整检查。
    基于最近 50 词的平均 word_level 与 current_level 比较，差值超过 1.5 时自动升/降级。
    """
    if new_count % 50 != 0:
        return

    from core.assessment import get_level_name
    avg_level = get_recent_word_levels(telegram_id, limit=50)
    if avg_level is None:
        return

    new_level = None
    if avg_level <= current_level - 1.5:
        new_level = max(1, current_level - 1)
        level_name = get_level_name(new_level, lang)
        notify = (
            f"📊 根据你最近添加的词汇，我已将你的词汇等级调整为 "
            f"*Level {new_level}（{level_name}）*，这样我能为你推荐更适合的词！"
        )
    elif avg_level >= current_level + 1.5:
        new_level = min(5, current_level + 1)
        level_name = get_level_name(new_level, lang)
        notify = (
            f"🚀 你最近添加的词汇水平很高！已将你的词汇等级升级为 "
            f"*Level {new_level}（{level_name}）*！"
        )
    else:
        return

    try:
        set_proficiency_level(telegram_id, new_level, mark_done=True)
        await send_fn(notify, parse_mode="Markdown")
    except Exception as exc:
        logger.error("动态等级调整失败: %s", exc)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    用户发送任意文本时触发：
    - 整句输入 → 翻译 + 可点击词汇按钮（由用户选择入库）
    - 单词/词组 → AI 解析后直接入库并回复确认
    根据用户的 active_language 和 native_language 决定解析行为。
    """
    telegram_id = str(update.effective_user.id)
    user_input = update.message.text.strip()

    if not user_input:
        return

    # ── pending_edit 状态优先处理 ──────────────────────────────────────────────
    pending = context.user_data.get("pending_edit")
    if pending:
        record_id = pending["record_id"]
        field = pending["field"]
        word = pending["word"]
        page = pending.get("page", 0)
        del context.user_data["pending_edit"]

        # 获取用户语言（pending_edit 存入时的 native_language）
        lang = context.user_data.get("native_language", "zh")

        success = update_vocab_fields(record_id, {field: user_input})
        field_key = {"pos": "edit_field_pos", "definition": "edit_field_def", "context": "edit_field_ctx"}.get(field, field)
        field_name = await t_async(field_key, lang)
        if success:
            msg = await t_async("edit_updated", lang, word=word, field=field_name)
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            msg = await t_async("edit_failed", lang)
            await update.message.reply_text(msg)
        return

    # ── pending_generation 状态处理（造句题评分） ────────────────────────────
    pending_gen = context.user_data.get("pending_generation")
    if pending_gen:
        await _handle_generation_answer(update, context, user_input, pending_gen)
        return

    # ── 复习 / 练习 session 期间阻止普通词汇输入 ────────────────────────────────
    if context.user_data.get("active_session"):
        await update.message.reply_text("请先完成当前复习，或点击结束按钮退出。")
        return

    # 读取用户语言设置
    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    lang = native_language

    # ── 上下文提问检测（pending_edit 后、250词限制前） ──────────────────────────
    if _is_context_question(user_input, context):
        handled = await _handle_context_question(
            update, context, user_input, telegram_id,
            lang, active_language, native_language,
        )
        if handled:
            return

    # ── 输入词数限制（超过 250 词直接拒绝） ────────────────────────────────────
    MAX_WORDS = 250
    if len(user_input.split()) > MAX_WORDS:
        await update.message.reply_text(
            f"⚠️ 输入超过 {MAX_WORDS} 词，请分段发送（每次不超过 {MAX_WORDS} 词）。"
        )
        return

    processing_text = await t_async("processing", lang)
    processing_msg = await update.message.reply_text(processing_text)

    # ── 批量添加流程（含逗号且非句子） ────────────────────────────────────────
    if "," in user_input and not _is_sentence(user_input):
        tokens = [t.strip() for t in user_input.split(",") if t.strip()]
        if len(tokens) >= 2:
            pro = is_pro(telegram_id)
            if not pro:
                total_count = count_vocab(telegram_id, target_language=active_language)
                today_count = get_today_add_count(telegram_id)
                max_by_total = max(0, FREE_WORD_LIMIT - total_count)
                max_by_daily = max(0, FREE_DAILY_LIMIT - today_count)
                allowed = min(max_by_total, max_by_daily)
                if allowed == 0:
                    msg = await t_async("limit_both_reached", lang,
                                        total_limit=FREE_WORD_LIMIT, daily_limit=FREE_DAILY_LIMIT)
                    await processing_msg.edit_text(msg, parse_mode="Markdown")
                    return
                tokens_to_process = tokens[:allowed]
                hit_limit = len(tokens) > allowed
            else:
                tokens_to_process = tokens
                hit_limit = False

            title = await t_async("batch_result_title", lang,
                                   done=len(tokens_to_process), total=len(tokens))
            lines = [title]
            for token in tokens_to_process:
                try:
                    result = await parse_user_input(
                        token,
                        target_language=active_language,
                        native_language=native_language,
                    )
                except Exception as exc:
                    logger.error("AI 解析失败 (%s): %s", token, exc)
                    lines.append(await t_async("batch_parse_fail", lang, token=token))
                    continue

                if not result.is_vocab or not result.vocabs:
                    lines.append(await t_async("batch_not_vocab", lang, token=token))
                    continue

                vocab = result.vocabs[0]
                try:
                    _, is_new = upsert_vocab(
                        telegram_id=telegram_id,
                        word=vocab.word,
                        pos=vocab.pos,
                        definition=vocab.definition,
                        context=vocab.context,
                        target_language=active_language,
                        native_language=native_language,
                        word_level=vocab.word_level,
                        quiz_synonyms=vocab.synonyms,
                        antonyms=vocab.antonyms,
                        word_family=vocab.word_family,
                        etymology=vocab.etymology,
                        collocations=vocab.collocations,
                    )
                except Exception as exc:
                    logger.error("数据库写入失败 (%s): %s", token, exc)
                    lines.append(await t_async("batch_save_fail", lang, word=vocab.word))
                    continue

                pos_tag = f" [{vocab.pos}]" if vocab.pos else ""
                if is_new:
                    lines.append(await t_async("batch_new", lang,
                                               word=vocab.word, pos_tag=pos_tag, definition=vocab.definition))
                    # 新词入库后检查动态等级调整
                    new_count = count_vocab(telegram_id, target_language=active_language)
                    current_proficiency = settings.get("proficiency_level", 0) or 0
                    if current_proficiency > 0:
                        await _check_dynamic_level_adjust(
                            telegram_id, new_count, current_proficiency, lang,
                            update.message.reply_text,
                        )
                else:
                    lines.append(await t_async("batch_exists", lang,
                                               word=vocab.word, pos_tag=pos_tag, definition=vocab.definition))

            if hit_limit:
                lines.append(await t_async("batch_hit_limit", lang))

            await processing_msg.edit_text("\n".join(lines), parse_mode="Markdown")
            return

    # ── 整句分析流程 ────────────────────────────────────────────────────────────
    if _is_sentence(user_input):
        if not is_pro(telegram_id):
            if count_vocab(telegram_id, target_language=active_language) >= FREE_WORD_LIMIT:
                msg = await t_async("limit_total_reached", lang, limit=FREE_WORD_LIMIT)
                await processing_msg.edit_text(msg, parse_mode="Markdown")
                return
            if get_today_add_count(telegram_id) >= FREE_DAILY_LIMIT:
                msg = await t_async("limit_daily_reached", lang, limit=FREE_DAILY_LIMIT)
                await processing_msg.edit_text(msg, parse_mode="Markdown")
                return

        try:
            user_level = settings.get("proficiency_level", 3) or 3
            translation, vocabs = await analyze_sentence(
                user_input,
                target_language=active_language,
                native_language=native_language,
                user_level=user_level,
            )
        except Exception as exc:
            logger.error("整句分析失败: %s", exc)
            msg = await t_async("parse_fail_simple", lang)
            await processing_msg.edit_text(msg)
            return

        # 后端硬过滤：去掉不高于用户水平的词（AI 不总是严格遵守 system prompt）
        if user_level and user_level > 0:
            vocabs = [v for v in vocabs if v.word_level is None or v.word_level > user_level]

        # 去重：过滤掉词库中已有相同词汇（同词 + 同词性）
        filtered_vocabs = []
        for v in vocabs:
            existing = get_vocab_by_word(telegram_id, v.word, target_language=active_language)
            if any(r.get("pos") == v.pos for r in existing):
                logger.debug("整句分析去重跳过：%s [%s]（已在词库中）", v.word, v.pos)
                continue
            filtered_vocabs.append(v)
        vocabs = filtered_vocabs

        if not vocabs:
            # 过滤后无词：静默结束，不提示用户
            await processing_msg.delete()
            return

        # 保存最近分析的句子，供上下文提问使用
        context.user_data["last_sentence"] = user_input

        msg_id = processing_msg.message_id
        context.chat_data[str(msg_id)] = [
            {
                "word": v.word, "pos": v.pos,
                "definition": v.definition, "context": v.context,
                "word_level": v.word_level,
                "quiz_synonyms": v.synonyms,
                "antonyms": v.antonyms,
                "word_family": v.word_family,
                "etymology": v.etymology,
                "collocations": v.collocations,
            }
            for v in vocabs
        ]
        context.chat_data[f"{msg_id}_lang"] = {
            "target_language": active_language,
            "native_language": native_language,
        }

        vocab_lines = "\n".join(
            f"• *{v.word}* [{v.pos}] — {v.definition}" for v in vocabs
        )
        msg = await t_async("sentence_add_prompt", lang,
                            translation=translation, vocab_lines=vocab_lines)
        keyboard = sentence_vocab_keyboard(vocabs, msg_id, set())
        await processing_msg.edit_text(msg, parse_mode="Markdown", reply_markup=keyboard)
        return

    # ── 单词/词组流程 ──────────────────────────────────────────────────────────
    try:
        result = await parse_user_input(
            user_input,
            target_language=active_language,
            native_language=native_language,
        )
    except Exception as exc:
        logger.error("AI 解析失败: %s", exc)
        msg = await t_async("parse_fail", lang)
        await processing_msg.edit_text(msg)
        return

    if not result.is_vocab:
        await processing_msg.edit_text(result.rejection_message)
        return

    # 词汇意图：在入库前检查免费限额
    if not is_pro(telegram_id):
        if count_vocab(telegram_id, target_language=active_language) >= FREE_WORD_LIMIT:
            msg = await t_async("limit_total_reached", lang, limit=FREE_WORD_LIMIT)
            await processing_msg.edit_text(msg, parse_mode="Markdown")
            return
        if get_today_add_count(telegram_id) >= FREE_DAILY_LIMIT:
            msg = await t_async("limit_daily_reached", lang, limit=FREE_DAILY_LIMIT)
            await processing_msg.edit_text(msg, parse_mode="Markdown")
            return

    # 解析结果预览：暂存词汇（含富元数据），等用户点击 Add 才入库
    msg_id = processing_msg.message_id
    context.chat_data[f"vc_{msg_id}"] = [
        {
            "word": v.word, "pos": v.pos,
            "definition": v.definition, "context": v.context,
            "word_level": v.word_level,
            "quiz_synonyms": v.synonyms,
            "antonyms": v.antonyms,
            "word_family": v.word_family,
            "etymology": v.etymology,
            "collocations": v.collocations,
        }
        for v in result.vocabs
    ]
    context.chat_data[f"vc_{msg_id}_lang"] = {
        "target_language": active_language,
        "native_language": native_language,
        "telegram_id": telegram_id,
    }

    # 构造预览文案（展示每条解析结果）
    lines = []
    for vocab in result.vocabs:
        pos_tag = f" [{vocab.pos}]" if vocab.pos else ""
        lines.append(
            f"*{vocab.word}*{pos_tag}\n"
            f"📖 {vocab.definition}\n"
            f"📝 _{vocab.context}_"
        )
    preview = "\n\n".join(lines)
    keyboard = await vocab_confirm_keyboard(result.vocabs, msg_id, lang)
    await processing_msg.edit_text(preview, parse_mode="Markdown", reply_markup=keyboard)
