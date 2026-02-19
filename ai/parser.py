"""
AI 解析器：意图识别 + 词汇结构化解析
支持 DeepSeek / OpenAI 兼容接口
支持多语言：target_language（学习语言）+ native_language（母语/释义语言）
"""
import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, AI_BASE_URL, AI_MODEL

logger = logging.getLogger(__name__)

# ── 初始化 OpenAI 兼容客户端 ─────────────────────────────────────────────────

_ai_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=AI_BASE_URL,
)

# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class ParsedVocab:
    """AI 解析后的词汇结构"""
    word: str           # 目标单词/词组（target_language）
    pos: str            # 词性: noun / verb / adj / adv / phrase
    definition: str     # native_language 释义（简洁）
    context: str        # 一句包含该词的 target_language 例句


@dataclass
class ParseResult:
    """解析结果，区分词汇与非词汇意图"""
    is_vocab: bool
    vocabs: list[ParsedVocab] | None = None     # 词汇意图时的词汇列表（支持多个）
    rejection_message: str | None = None        # 非词汇意图时的回复文本


# ── System Prompt 构建器 ───────────────────────────────────────────────────────

def _build_system_prompt(target_language: str = "en", native_language: str = "zh") -> str:
    """
    根据目标学习语言和母语动态生成词汇解析 system prompt。
    target_language: 用户正在学习的语言代码（如 "en", "ja", "fr"）
    native_language: 用户母语/释义语言代码（如 "zh", "en"）
    """
    from core.language import LANGUAGE_META, NATIVE_LANGUAGE_META
    target_meta = LANGUAGE_META.get(
        target_language,
        {"name": target_language.upper(), "native_name": target_language}
    )
    native_meta = NATIVE_LANGUAGE_META.get(
        native_language,
        {"name": native_language}
    )
    target_name = target_meta["name"]            # 英文名，如 "English"
    target_native_name = target_meta["native_name"]  # 本地名，如 "英语"
    native_name = native_meta["name"]             # 如 "Chinese (中文)"

    return (
        f"你是一个专门用于词汇学习的助手，只做词汇解析，拒绝任何其他请求。\n"
        f"当前学习配置：用户正在学习【{target_name}（{target_native_name}）】，释义语言为【{native_name}】。\n\n"
        f"你的任务：\n"
        f"1. 判断用户输入是否为【{target_native_name}词汇学习意图】\n"
        f"2. 若是词汇意图：解析所有词汇并以数组形式返回结构化 JSON\n"
        f"3. 若不是词汇意图：返回拒绝消息\n\n"
        f"【词汇意图判定规则】\n"
        f"- {target_name} 单词或词组 → 直接解析，整体作为一个条目\n"
        f"- 用分号或逗号列举多个词 → 每个词分别解析，全部输出，不要遗漏\n"
        f"- 多词固定表达/习语 → 整体作为一个条目，pos = phrase\n"
        f"- 含目标词的完整句子 → 提取句中最值得学习的 1-3 个词汇条目\n"
        f"  优先选：短语动词、习语、专业词汇、不常见单词\n"
        f"- 其他语言词语（如用户母语词汇）→ 找到 {target_name} 对应词并解析\n"
        f"- 其他（如问候、要求写代码、聊天等）→ 非词汇意图\n\n"
        f"【输出格式】\n"
        f"词汇意图时，严格输出以下 JSON（不要加 markdown 代码块）：\n"
        '{\n'
        '  "is_vocab": true,\n'
        '  "vocabs": [\n'
        '    {\n'
        f'      "word": "{target_name} 单词或词组（保留原始大小写，词组保留完整形式）",\n'
        '      "pos": "noun|verb|adj|adv|phrase 之一",\n'
        f'      "definition": "简洁 {native_name} 释义，不超过20字",\n'
        f'      "context": "一句自然的 {target_name} 例句，包含该词，难度适中"\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        f"非词汇意图时，严格输出：\n"
        '{\n'
        '  "is_vocab": false,\n'
        f'  "rejection_message": "我只能帮你查{target_native_name}词汇哦～请发送你想学习的词汇 😊"\n'
        '}\n\n'
        f"【注意】\n"
        f"- word 字段必须是 {target_name}\n"
        f"- definition 字段必须是 {native_name}\n"
        f"- context 例句必须是 {target_name}，体现词义，不要太长（1-2行）\n"
        f"- 单个词时 vocabs 数组长度为 1，多个词时全部列出\n"
        f"- 严禁输出 JSON 以外的任何内容"
    )


def _build_sentence_system_prompt(
    target_language: str = "en", native_language: str = "zh"
) -> str:
    """
    根据学习语言和母语动态生成整句分析 system prompt。
    """
    from core.language import LANGUAGE_META, NATIVE_LANGUAGE_META
    target_meta = LANGUAGE_META.get(
        target_language,
        {"name": target_language.upper(), "native_name": target_language}
    )
    native_meta = NATIVE_LANGUAGE_META.get(
        native_language,
        {"name": native_language}
    )
    target_name = target_meta["name"]             # 英文名，如 "English"
    target_native_name = target_meta["native_name"]  # 本地名，如 "英语"
    native_name = native_meta["name"]             # 如 "Chinese (中文)"

    return (
        f"你是{target_native_name}词汇学习助手。"
        f"用户会发送一个{target_native_name}句子，你需要：\n"
        f"1. 将句子翻译为{native_name}\n"
        f"2. 从句子中筛选出 1-4 个值得学习的{target_native_name}词汇\n"
        f"   优先选：短语动词、习语、专业词汇、不常见单词；跳过高频常见词\n\n"
        f"严格输出以下 JSON（不要加 markdown 代码块）：\n"
        '{\n'
        f'  "translation": "整句的{native_name}翻译",\n'
        '  "vocabs": [\n'
        '    {\n'
        f'      "word": "{target_name}单词或词组（保留原始大小写）",\n'
        '      "pos": "noun|verb|adj|adv|phrase 之一",\n'
        f'      "definition": "简洁{native_name}释义，不超过20字",\n'
        f'      "context": "一句自然的{target_native_name}例句，包含该词，难度适中（不要直接用用户的原句）"\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        "严禁输出 JSON 以外的任何内容。"
    )


# ── 核心解析函数 ──────────────────────────────────────────────────────────────

async def parse_user_input(
    user_input: str,
    target_language: str = "en",
    native_language: str = "zh",
) -> ParseResult:
    """
    调用 AI 解析用户输入，返回 ParseResult。
    支持多词汇（分号/逗号分隔）和完整词组。
    若 AI 调用失败，抛出异常由上层处理。
    """
    system_prompt = _build_system_prompt(target_language, native_language)
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input.strip()},
        ],
        temperature=0.3,
        max_tokens=800,     # 多词汇时需要更多 token
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("AI 原始响应: %s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("AI 返回非 JSON 内容: %s", raw)
        raise ValueError(f"AI 返回格式错误: {raw[:100]}")

    if not data.get("is_vocab"):
        return ParseResult(
            is_vocab=False,
            rejection_message=data.get(
                "rejection_message",
                "我只能帮你查词汇哦～请发送你想学习的单词或词组 😊",
            ),
        )

    # 解析 vocabs 数组
    raw_vocabs = data.get("vocabs")
    if not raw_vocabs or not isinstance(raw_vocabs, list):
        raise ValueError("AI 响应缺少 vocabs 数组")

    vocabs = []
    for i, item in enumerate(raw_vocabs):
        for field in ("word", "pos", "definition", "context"):
            if not item.get(field):
                raise ValueError(f"vocabs[{i}] 缺少字段: {field}")
        # 英语统一小写，其他语言保留原始大小写
        word_raw = item["word"].strip()
        word = word_raw.lower() if target_language == "en" else word_raw
        vocabs.append(ParsedVocab(
            word=word,
            pos=item["pos"],
            definition=item["definition"].strip(),
            context=item["context"].strip(),
        ))

    return ParseResult(is_vocab=True, vocabs=vocabs)


async def analyze_sentence(
    sentence: str,
    target_language: str = "en",
    native_language: str = "zh",
) -> tuple[str, list[ParsedVocab]]:
    """
    分析用户发送的完整句子，返回 (翻译, 词汇候选列表)。
    供整句输入流程使用，不自动入库，由用户点击按钮选择保存。
    """
    system_prompt = _build_sentence_system_prompt(target_language, native_language)
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": sentence.strip()},
        ],
        temperature=0.3,
        max_tokens=600,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("analyze_sentence AI 原始响应: %s", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("analyze_sentence 返回非 JSON: %s", raw)
        raise ValueError(f"AI 返回格式错误: {raw[:100]}")

    translation = data.get("translation", "")
    raw_vocabs = data.get("vocabs") or []

    vocabs = []
    for item in raw_vocabs:
        if not all(item.get(f) for f in ("word", "pos", "definition", "context")):
            continue
        word_raw = item["word"].strip()
        word = word_raw.lower() if target_language == "en" else word_raw
        vocabs.append(ParsedVocab(
            word=word,
            pos=item["pos"],
            definition=item["definition"].strip(),
            context=item["context"].strip(),
        ))

    return translation, vocabs


async def generate_example_sentence(
    word: str, definition: str, target_language: str = "en"
) -> str:
    """为单词生成一条自然的目标语言例句（不含填空符，用于答错时补充语境）"""
    from core.language import LANGUAGE_META
    target_meta = LANGUAGE_META.get(target_language, {"name": target_language.upper()})
    lang_name = target_meta["name"]

    prompt = (
        f'Word: "{word}" ({definition})\n'
        f"Write one natural {lang_name} example sentence using this word. "
        f"Output only the sentence."
    )
    resp = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a {lang_name} vocabulary teaching assistant. "
                    f"Output only the example sentence, nothing else."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=100,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


async def generate_quiz_sentence(
    word: str, definition: str, target_language: str = "en"
) -> str:
    """
    为复习测验生成一个新的填空例句（不同于入库时的 context）。
    返回含 ___ 占位符的目标语言句子。
    """
    from core.language import LANGUAGE_META
    target_meta = LANGUAGE_META.get(target_language, {"name": target_language.upper()})
    lang_name = target_meta["name"]

    prompt = (
        f"Create a fill-in-the-blank sentence in {lang_name} for the word/phrase: "
        f"'{word}' ({definition}).\n"
        f"Instructions:\n"
        f"1. Write a natural {lang_name} sentence that contains the exact word/phrase '{word}' "
        f"(do NOT change any word in the phrase).\n"
        f"2. Replace the entire word/phrase '{word}' with exactly six underscores: ______\n"
        f"3. Output ONLY the fill-in-the-blank sentence. No explanations, no parentheses.\n"
        f"Example: For 'give up' → output: Don't ______ just because it's hard.\n"
        f"Important: The word/phrase '{word}' must NOT appear anywhere in the output."
    )
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a {lang_name} vocabulary teaching assistant. "
                    f"Output only the fill-in-the-blank sentence."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=100,
    )
    result = response.choices[0].message.content.strip()

    # 若 AI 没有正确生成占位符，手动将原词替换为 ______
    if "___" not in result:
        import re as _re
        result = _re.sub(_re.escape(word), "______", result, count=1, flags=_re.IGNORECASE)
        # 最终兜底：若仍无占位符则返回通用句
        if "___" not in result:
            result = f"She demonstrated ______ in her daily work."

    return result


async def translate_to_native(sentence: str, native_language: str = "zh") -> str:
    """将目标语言句子翻译为用户母语（支持多语言）"""
    from core.language import NATIVE_LANGUAGE_META
    native_meta = NATIVE_LANGUAGE_META.get(native_language, {"name": native_language})
    native_name = native_meta["name"]

    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": f"你是翻译助手，只输出{native_name}翻译，不加任何解释。",
            },
            {
                "role": "user",
                "content": f"请将以下内容翻译成{native_name}：\n{sentence}",
            },
        ],
        temperature=0.3,
        max_tokens=150,
    )
    return response.choices[0].message.content.strip()


async def translate_to_chinese(sentence: str) -> str:
    """向后兼容别名：将句子翻译为中文"""
    return await translate_to_native(sentence, "zh")


async def translate_ui_text(text: str, target_lang: str) -> str:
    """
    将 Bot UI 文案翻译为目标语言，供 i18n.t_async() 调用。
    规则：保留 Markdown/HTML 格式符、bot 命令、emoji、模板占位符。
    """
    from core.language import NATIVE_LANGUAGE_META
    meta = NATIVE_LANGUAGE_META.get(target_lang, {"name": target_lang})
    lang_name = meta["name"]

    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Translate the following bot UI text to {lang_name}. "
                    "Strict rules: "
                    "1. Keep ALL formatting markers exactly as-is: *text*, _text_, <b>text</b>, <i>text</i>. "
                    "2. Keep bot commands unchanged: /review /vocab /practice /activate /settings "
                    "/timezone /language /help /stats /streak /search /export /update /delete /plan. "
                    "3. Keep emoji unchanged. "
                    "4. Keep template placeholders unchanged: {word} {date} {level} {definition} "
                    "{lang_name} {count} {tz} {display} {translation} etc. "
                    "5. Keep backtick spans unchanged: `code`. "
                    "6. Output ONLY the translated text, no explanations."
                ),
            },
            {"role": "user", "content": text},
        ],
        temperature=0.1,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()
