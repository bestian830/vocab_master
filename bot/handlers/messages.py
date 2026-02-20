"""
处理用户发送的普通文本消息（词汇查询入口）
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from ai.parser import parse_user_input, analyze_sentence
from database.client import (
    upsert_vocab, is_pro, count_vocab, get_today_add_count,
    update_vocab_fields, get_user_settings,
)
from config import FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from bot.keyboards import sentence_vocab_keyboard, vocab_confirm_keyboard
from bot.i18n import t_async

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

    # 展示处理中状态
    settings = get_user_settings(telegram_id)
    active_language = settings.get("active_language", "en")
    native_language = settings.get("native_language", "zh")
    lang = native_language

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
                    )
                except Exception as exc:
                    logger.error("数据库写入失败 (%s): %s", token, exc)
                    lines.append(await t_async("batch_save_fail", lang, word=vocab.word))
                    continue

                pos_tag = f" [{vocab.pos}]" if vocab.pos else ""
                if is_new:
                    lines.append(await t_async("batch_new", lang,
                                               word=vocab.word, pos_tag=pos_tag, definition=vocab.definition))
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
            translation, vocabs = await analyze_sentence(
                user_input,
                target_language=active_language,
                native_language=native_language,
            )
        except Exception as exc:
            logger.error("整句分析失败: %s", exc)
            msg = await t_async("parse_fail_simple", lang)
            await processing_msg.edit_text(msg)
            return

        if not vocabs:
            msg = await t_async("sentence_no_vocab", lang, translation=translation)
            await processing_msg.edit_text(msg, parse_mode="Markdown")
            return

        msg_id = processing_msg.message_id
        context.chat_data[str(msg_id)] = [
            {"word": v.word, "pos": v.pos, "definition": v.definition, "context": v.context}
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

    # 解析结果预览：暂存词汇，等用户点击 Add 才入库
    msg_id = processing_msg.message_id
    context.chat_data[f"vc_{msg_id}"] = [
        {"word": v.word, "pos": v.pos, "definition": v.definition, "context": v.context}
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
