"""
处理用户发送的普通文本消息（词汇查询入口）
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from ai.parser import parse_user_input, analyze_sentence
from database.client import upsert_vocab, is_pro, count_vocab, get_today_add_count, update_vocab_fields
from config import FREE_WORD_LIMIT, FREE_DAILY_LIMIT
from bot.keyboards import sentence_vocab_keyboard

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
    """
    telegram_id = str(update.effective_user.id)
    user_input = update.message.text.strip()

    if not user_input:
        return

    # ── pending_edit 状态优先处理 ──────────────────────────────────────────────
    # 若用户正在等待编辑某个词汇字段，则将输入视为新字段值
    pending = context.user_data.get("pending_edit")
    if pending:
        record_id = pending["record_id"]
        field = pending["field"]
        word = pending["word"]
        page = pending.get("page", 0)
        # 清除状态，不论成功与否
        del context.user_data["pending_edit"]

        success = update_vocab_fields(record_id, {field: user_input})
        field_name = {"pos": "词性", "definition": "释义", "context": "例句"}.get(field, field)
        if success:
            await update.message.reply_text(
                f"✅ *{word}* 的{field_name}已更新。",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("⚠️ 更新失败，记录可能已不存在。")
        return

    # 展示处理中状态
    processing_msg = await update.message.reply_text("⏳ 正在解析…")

    # ── 批量添加流程（含逗号且非句子） ────────────────────────────────────────
    if "," in user_input and not _is_sentence(user_input):
        tokens = [t.strip() for t in user_input.split(",") if t.strip()]
        if len(tokens) >= 2:
            # 计算免费用户剩余可添加数量
            pro = is_pro(telegram_id)
            if not pro:
                total_count = count_vocab(telegram_id)
                today_count = get_today_add_count(telegram_id)
                max_by_total = max(0, FREE_WORD_LIMIT - total_count)
                max_by_daily = max(0, FREE_DAILY_LIMIT - today_count)
                # 最多可处理的 token 数（免费用户取两者最小值）
                allowed = min(max_by_total, max_by_daily)
                if allowed == 0:
                    await processing_msg.edit_text(
                        f"📚 已达添加上限（词库 {FREE_WORD_LIMIT} 词 / 每日 {FREE_DAILY_LIMIT} 词）。\n"
                        f"发送 `/activate 激活码` 订阅 Pro 解锁无限词库。",
                        parse_mode="Markdown",
                    )
                    return
                tokens_to_process = tokens[:allowed]
                hit_limit = len(tokens) > allowed
            else:
                tokens_to_process = tokens
                hit_limit = False

            lines = [f"📚 *批量添加结果（{len(tokens_to_process)}/{len(tokens)}）：*"]
            for token in tokens_to_process:
                try:
                    result = await parse_user_input(token)
                except Exception as exc:
                    logger.error("AI 解析失败 (%s): %s", token, exc)
                    lines.append(f"❌ {token} — 解析失败")
                    continue

                if not result.is_vocab or not result.vocabs:
                    lines.append(f"❌ {token} — 不是有效词汇")
                    continue

                # 取第一个词汇结果入库
                vocab = result.vocabs[0]
                try:
                    _, is_new = upsert_vocab(
                        telegram_id=telegram_id,
                        word=vocab.word,
                        pos=vocab.pos,
                        definition=vocab.definition,
                        context=vocab.context,
                    )
                except Exception as exc:
                    logger.error("数据库写入失败 (%s): %s", token, exc)
                    lines.append(f"❌ {vocab.word} — 保存失败")
                    continue

                pos_tag = f" [{vocab.pos}]" if vocab.pos else ""
                if is_new:
                    lines.append(f"✅ *{vocab.word}*{pos_tag} — {vocab.definition}")
                else:
                    lines.append(f"📖 *{vocab.word}*{pos_tag} — {vocab.definition}（已在词库中）")

            if hit_limit:
                lines.append(f"\n⚠️ 已达添加上限，剩余词汇未处理。")

            await processing_msg.edit_text("\n".join(lines), parse_mode="Markdown")
            return

    # ── 整句分析流程 ────────────────────────────────────────────────────────────
    if _is_sentence(user_input):
        # 整句流程：提前检查免费限额（至少还能添加 1 个词才展示按钮）
        if not is_pro(telegram_id):
            if count_vocab(telegram_id) >= FREE_WORD_LIMIT:
                await processing_msg.edit_text(
                    f"📚 词库已达 {FREE_WORD_LIMIT} 词上限。\n"
                    f"发送 `/activate 激活码` 订阅 Pro 解锁无限词库。",
                    parse_mode="Markdown",
                )
                return
            if get_today_add_count(telegram_id) >= FREE_DAILY_LIMIT:
                await processing_msg.edit_text(
                    f"⏰ 今日已添加 {FREE_DAILY_LIMIT} 个词，明天再来吧！\n"
                    f"发送 `/activate 激活码` 订阅 Pro 不受限制。",
                    parse_mode="Markdown",
                )
                return

        try:
            translation, vocabs = await analyze_sentence(user_input)
        except Exception as exc:
            logger.error("整句分析失败: %s", exc)
            await processing_msg.edit_text("😕 解析失败，请稍后重试。")
            return

        if not vocabs:
            await processing_msg.edit_text(
                f"📖 *翻译：*{translation}\n\n（未识别到值得记录的词汇）",
                parse_mode="Markdown",
            )
            return

        # 将词汇列表存入 chat_data，以消息 ID 为键，供回调取用
        msg_id = processing_msg.message_id
        context.chat_data[str(msg_id)] = [
            {"word": v.word, "pos": v.pos, "definition": v.definition, "context": v.context}
            for v in vocabs
        ]

        vocab_lines = "\n".join(
            f"• *{v.word}* [{v.pos}] — {v.definition}" for v in vocabs
        )
        text = (
            f"📖 *翻译：*{translation}\n\n"
            f"*点击下方词汇加入词库：*\n{vocab_lines}"
        )
        keyboard = sentence_vocab_keyboard(vocabs, msg_id, set())
        await processing_msg.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return

    # ── 单词/词组流程（原有逻辑）──────────────────────────────────────────────
    try:
        result = await parse_user_input(user_input)
    except Exception as exc:
        logger.error("AI 解析失败: %s", exc)
        await processing_msg.edit_text(
            "😕 解析失败，请稍后重试。\n（若持续出现，请检查 AI 服务配置）"
        )
        return

    if not result.is_vocab:
        await processing_msg.edit_text(result.rejection_message)
        return

    # 词汇意图：在入库前检查免费限额
    if not is_pro(telegram_id):
        if count_vocab(telegram_id) >= FREE_WORD_LIMIT:
            await processing_msg.edit_text(
                f"📚 词库已达 {FREE_WORD_LIMIT} 词上限。\n"
                f"发送 `/activate 激活码` 订阅 Pro 解锁无限词库。",
                parse_mode="Markdown",
            )
            return
        if get_today_add_count(telegram_id) >= FREE_DAILY_LIMIT:
            await processing_msg.edit_text(
                f"⏰ 今日已添加 {FREE_DAILY_LIMIT} 个词，明天再来吧！\n"
                f"发送 `/activate 激活码` 订阅 Pro 不受限制。",
                parse_mode="Markdown",
            )
            return

    # 逐一入库，收集结果
    lines = []
    for vocab in result.vocabs:
        try:
            record, is_new = upsert_vocab(
                telegram_id=telegram_id,
                word=vocab.word,
                pos=vocab.pos,
                definition=vocab.definition,
                context=vocab.context,
            )
        except Exception as exc:
            logger.error("数据库写入失败 (%s): %s", vocab.word, exc)
            lines.append(f"❌ *{vocab.word}* 保存失败")
            continue
        pos_tag = f" [{vocab.pos}]" if vocab.pos else ""

        if is_new:
            lines.append(
                f"✅ *{vocab.word}* {pos_tag}\n"
                f"📖 {vocab.definition}\n"
                f"📝 _{vocab.context}_"
            )
        else:
            lines.append(f"📖 *{vocab.word}* {pos_tag} — {vocab.definition}（已在词库中）")

    reply = "\n\n".join(lines)
    await processing_msg.edit_text(reply, parse_mode="Markdown")
