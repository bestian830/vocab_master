"""
AI 解析器：意图识别 + 词汇结构化解析
支持 DeepSeek / OpenAI 兼容接口
支持多语言：target_language（学习语言）+ native_language（母语/释义语言）
"""
import json
import logging
import random
import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, AI_BASE_URL, AI_MODEL

logger = logging.getLogger(__name__)

# ── 例句场景列表（随机注入 prompt，确保每次生成例句不同） ──────────────────────────
_SENTENCE_SCENARIOS = [
    "daily conversation",
    "workplace / professional setting",
    "academic or formal writing",
    "travel or outdoor activity",
    "news article or journalism",
    "social media or casual text",
    "literature or storytelling",
    "sports or competition",
    "technology or science",
    "historical or cultural context",
]

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
    # 富词汇元数据（可选，AI 顺带生成）
    word_level:   int  = 3   # 1-5 难度级别，默认 3（保守估计）
    synonyms:     list = None  # 同义词列表：[正确同义词, 错误近义1, 错误近义2, 错误近义3]（目标语言）
    antonyms:     list = None  # 反义词（目标语言，最多3个）
    word_family:  list = None  # 词族（目标语言词形变化，最多6个）
    etymology:    str  = ""    # 词根/前后缀简述（目标语言）
    collocations: list = None  # 常见固定搭配（目标语言，最多5个）

    def __post_init__(self):
        """确保 list 字段不为 None"""
        if self.synonyms is None:
            self.synonyms = []
        if self.antonyms is None:
            self.antonyms = []
        if self.word_family is None:
            self.word_family = []
        if self.collocations is None:
            self.collocations = []


@dataclass
class ParseResult:
    """解析结果，区分词汇与非词汇意图"""
    is_vocab: bool
    vocabs: list[ParsedVocab] | None = None     # 词汇意图时的词汇列表（支持多个）
    rejection_message: str | None = None        # 非词汇意图时的回复文本


# ── JSON 围栏清洗器 ────────────────────────────────────────────────────────────

def _strip_json_fence(raw: str) -> str:
    """去除 AI 返回内容中的 ```json...``` 代码块围栏，只保留纯 JSON 字符串"""
    # 匹配 ```json ... ``` 或 ``` ... ```
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return raw.strip()


def _fix_json_string_newlines(raw: str) -> str:
    """
    修复 AI 返回的 JSON 中字符串值内的字面换行符。
    AI 翻译长段落时会在 translation 字段值内插入真实 \\n，导致 json.loads 失败。
    通过字符级扫描器检测是否处于 JSON 字符串内，将字面换行替换为转义序列。
    """
    result = []
    in_string = False
    escape_next = False
    for ch in raw:
        if escape_next:
            result.append(ch)
            escape_next = False
        elif ch == '\\':
            result.append(ch)
            escape_next = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string and ch == '\n':
            result.append('\\n')   # 字符串内字面换行 → 转义
        elif in_string and ch == '\r':
            result.append('\\r')   # 字符串内字面回车 → 转义
        else:
            result.append(ch)
    return ''.join(result)


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
        f"【外来词 / 俚语处理规则】\n"
        f"如果输入的词看起来是音译外来词（如日语、韩语、粤语、英语等进入中文的网络用语），\n"
        f"请优先按其来源语言含义解析，而非按汉字字面意思推断。\n"
        f"例如：\"西巴\" 是韩语 시발 的音译，含义是感叹词/粗口；\"欧尼\" 是韩语 언니（姐姐）的音译。\n\n"
        f"【输出格式】\n"
        f"词汇意图时，严格输出以下 JSON（不要加 markdown 代码块）：\n"
        '{\n'
        '  "is_vocab": true,\n'
        '  "vocabs": [\n'
        '    {\n'
        f'      "word": "{target_name} 单词或词组（保留原始大小写，词组保留完整形式）",\n'
        '      "pos": "noun|verb|adj|adv|phrase 之一",\n'
        f'      "definition": "简洁 {native_name} 释义，不超过20字",\n'
        f'      "context": "一句自然的 {target_name} 例句，包含该词，难度适中",\n'
        '      "word_level": 1-5（1=初中/A2，2=高中/B1，3=六级/B2，4=托福/C1，5=GRE/C2）,\n'
        f'      "synonyms": ["最准确的{target_name}同义词", "{target_name}错误近义词1", "{target_name}错误近义词2", "{target_name}错误近义词3"],\n'
        f'      "antonyms": ["{target_name}反义词1（若有）", "{target_name}反义词2（若有）"],\n'
        f'      "word_family": ["{target_name}词形变化1", "{target_name}词形变化2", "...（最多6个）"],\n'
        f'      "collocations": ["{target_name}常见搭配1", "{target_name}常见搭配2", "...（最多5个）"],\n'
        f'      "etymology": "{target_name}描述的词根/前缀/后缀一句话说明（若无意义留空字符串）"\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        f"非词汇意图时，严格输出：\n"
        '{\n'
        '  "is_vocab": false,\n'
        f'  "rejection_message": "我只能帮你查{target_native_name}词汇哦～请发送你想学习的词汇 😊"\n'
        '}\n\n'
        f"【重要注意事项】\n"
        f"- word 字段必须是 {target_name}\n"
        f"- definition 字段必须是 {native_name}（唯一允许使用母语的字段）\n"
        f"- context 例句必须是 {target_name}，体现词义，不要太长（1-2行）\n"
        f"- synonyms 必须全部是 {target_name} 单词，严禁出现 {native_name} 词语\n"
        f"- antonyms 必须全部是 {target_name} 单词，若无反义词则返回空数组 []\n"
        f"- word_family 必须全部是 {target_name} 词形，若无则返回空数组 []\n"
        f"- collocations 必须全部是 {target_name} 搭配短语，若无则返回空数组 []\n"
        f"- etymology 必须用 {target_name} 描述，若无则返回空字符串\n"
        f"- synonyms[0] 必须是最准确的同义词，[1]-[3] 是形义相近但不同的错误选项（用于四选一测验）\n"
        f"- 若某字段信息不确定或不存在，返回空数组[]或空字符串，严禁编造\n"
        f"- 输出前请先内部核验同义词、搭配是否准确，确认后再输出\n"
        f"- 单个词时 vocabs 数组长度为 1，多个词时全部列出\n"
        f"- 严禁输出 JSON 以外的任何内容"
    )


def _build_sentence_system_prompt(
    target_language: str = "en",
    native_language: str = "zh",
    user_level: int = 3,
) -> str:
    """
    根据学习语言、母语和用户词汇水平动态生成整句/段落分析 system prompt。
    user_level: 用户词汇水平（1-5），用于过滤太简单/太难的词汇。
    支持单句、多句、段落输入。
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
        f"你是{target_native_name}词汇学习助手，用户词汇水平为 Level {user_level}（1=初学，5=专家）。\n"
        f"用户会发送一段{target_native_name}文本（单句、多句或段落均可），你需要：\n"
        f"1. 将整段文本翻译为{native_name}（若段落较长，translation 做整体意译即可，不需逐句）\n"
        f"2. 按以下两阶段策略从文本中筛选出 1-5 个值得学习的词汇（优先推荐难度高于 Level {user_level} 的词）\n\n"
        f"【第一阶段：多词表达（优先）】\n"
        f"识别所有以下类型的多词表达：\n"
        f"- 短语动词：动词+介词/副词，改变基本含义（如 put over, call off, give up）\n"
        f"- 习语：非字面意思的固定表达（如 hit the nail on the head）\n"
        f"- 固定搭配：动词+名词、形容词+名词的特定组合（如 make a decision, strong argument）\n"
        f"对每个多词表达判断难度（1-5），只推荐难度 > {user_level} 的。\n\n"
        f"【第二阶段：单词（仅针对未被第一阶段涵盖的词）】\n"
        f"识别难度 > {user_level} 且未被第一阶段涵盖的单词。跳过高频常见词。\n\n"
        f"【多义词特别注意】\n"
        f"- 若某词在当前文本中使用的是不常见含义（如 trunk=象鼻 而非行李箱，bank=河岸 而非银行），\n"
        f"  必须将其列为待学词汇，并在 definition 中明确注明是哪种含义。\n"
        f"- 不要遗漏具体名词（如 whiskers/trunk/crane/pitch）在专业/自然语境下的罕见义。\n\n"
        f"【关键规则】\n"
        f"- 若一个词已被多词表达包含，不要再单独列出\n"
        f"- 总输出最多 5 个词条（多词表达优先于单词）\n"
        f"- 若所有词汇难度都不超过用户水平，vocabs 可以为空数组\n\n"
        f"严格输出以下 JSON（不要加 markdown 代码块）：\n"
        '{\n'
        f'  "translation": "整段文本的{native_name}翻译",\n'
        '  "vocabs": [\n'
        '    {\n'
        f'      "word": "{target_name}单词或词组（保留原始大小写，多词表达保留完整形式）",\n'
        '      "pos": "noun|verb|adj|adv|phrase 之一",\n'
        f'      "definition": "简洁{native_name}释义，不超过20字",\n'
        f'      "context": "一句自然的{target_native_name}例句，包含该词，难度适中（不要直接用用户的原句）",\n'
        '      "word_level": 1-5\n'
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
        max_tokens=1200,     # 多词汇+collocations 字段需要更多 token
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("AI 原始响应: %s", raw)

    # 去除 AI 可能输出的 ```json...``` 围栏，并修复字符串内的字面换行符
    raw = _strip_json_fence(raw)
    raw = _fix_json_string_newlines(raw)

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
        # 解析富词汇元数据（容错：字段缺失时使用默认值）
        synonyms    = item.get("synonyms") or []
        antonyms    = item.get("antonyms") or []
        word_family = item.get("word_family") or []
        etymology   = item.get("etymology") or ""
        collocations = item.get("collocations") or []
        try:
            word_level = int(item.get("word_level") or 3)
            word_level = max(1, min(5, word_level))   # 钳制在 1-5 范围
        except (TypeError, ValueError):
            word_level = 3
        vocabs.append(ParsedVocab(
            word=word,
            pos=item["pos"],
            definition=item["definition"].strip(),
            context=item["context"].strip(),
            word_level=word_level,
            synonyms=synonyms,
            antonyms=antonyms,
            word_family=word_family,
            etymology=etymology,
            collocations=collocations,
        ))

    return ParseResult(is_vocab=True, vocabs=vocabs)


async def analyze_sentence(
    sentence: str,
    target_language: str = "en",
    native_language: str = "zh",
    user_level: int = 3,
) -> tuple[str, list[ParsedVocab]]:
    """
    分析用户发送的完整文本（单句/多句/段落），返回 (翻译, 词汇候选列表)。
    供整句输入流程使用，不自动入库，由用户点击按钮选择保存。
    user_level: 用户词汇水平（1-5），用于过滤已掌握的词。
    """
    system_prompt = _build_sentence_system_prompt(target_language, native_language, user_level)
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": sentence.strip()},
        ],
        temperature=0.3,
        max_tokens=2000,    # 长段落翻译+多词汇 JSON 需要足够 token
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("analyze_sentence AI 原始响应: %s", raw)

    # 去除 AI 可能输出的 ```json...``` 围栏，并修复字符串内的字面换行符
    raw = _strip_json_fence(raw)
    raw = _fix_json_string_newlines(raw)

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
        try:
            wl = int(item.get("word_level") or 3)
            wl = max(1, min(5, wl))
        except (TypeError, ValueError):
            wl = 3
        vocabs.append(ParsedVocab(
            word=word,
            pos=item["pos"],
            definition=item["definition"].strip(),
            context=item["context"].strip(),
            word_level=wl,
        ))

    return translation, vocabs


async def generate_example_sentence(
    word: str, definition: str, target_language: str = "en"
) -> str:
    """为单词生成一条自然的目标语言例句（不含填空符，用于答错时补充语境）"""
    from core.language import LANGUAGE_META
    target_meta = LANGUAGE_META.get(target_language, {"name": target_language.upper()})
    lang_name = target_meta["name"]

    scenario = random.choice(_SENTENCE_SCENARIOS)
    prompt = (
        f'Word: "{word}" ({definition})\n'
        f"Scenario: {scenario}\n"
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
        temperature=0.9,
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

    scenario = random.choice(_SENTENCE_SCENARIOS)
    prompt = (
        f"Create a fill-in-the-blank sentence in {lang_name} for the word/phrase: "
        f"'{word}' ({definition}).\n"
        f"Scenario: {scenario}\n"
        f"Instructions:\n"
        f"1. Write a natural {lang_name} sentence that contains the exact word/phrase '{word}' "
        f"(do NOT change any word in the phrase).\n"
        f"2. Replace the entire word/phrase '{word}' with exactly six underscores: ______\n"
        f"3. Output ONLY the fill-in-the-blank sentence. No explanations, no parentheses.\n"
        f"4. If the phrase starts with a preposition or article (e.g. 'in limbo' starts with 'in'), "
        f"do NOT place that same word immediately before the ______.\n"
        f"   Bad:  'She was stuck in ______ for weeks.'\n"
        f"   Good: 'Her application sat ______ for weeks.'\n"
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
        temperature=0.9,
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


async def evaluate_sentence(
    word: str,
    definition: str,
    user_sentence: str,
    target_lang: str = "en",
    native_lang: str = "zh",
) -> dict:
    """
    评估用户造句是否正确使用了目标词汇。
    返回 dict:
      result:     "correct" | "fuzzy" | "wrong"
      grammar_ok: bool
      context_ok: bool
      feedback:   str（母语简短反馈）
      improved:   str（AI 改写的更地道版本）
    """
    from core.language import LANGUAGE_META, NATIVE_LANGUAGE_META
    target_meta = LANGUAGE_META.get(target_lang, {"name": target_lang.upper()})
    native_meta = NATIVE_LANGUAGE_META.get(native_lang, {"name": native_lang})
    target_name = target_meta["name"]
    native_name = native_meta["name"]

    system = (
        f"You are a strict but encouraging {target_name} teacher. "
        f"Evaluate whether the student used the word/phrase correctly in a sentence. "
        f"Respond ONLY with JSON (no markdown)."
    )
    prompt = (
        f'Word/phrase: "{word}" ({definition})\n'
        f'Student sentence: "{user_sentence}"\n\n'
        f"Evaluate:\n"
        f"1. grammar_ok: Is the sentence grammatically correct? (true/false)\n"
        f"2. context_ok: Is the word used in the right context/meaning? (true/false)\n"
        f"3. result: 'correct' if both grammar_ok AND context_ok; "
        f"'fuzzy' if intent is clear but minor errors; 'wrong' if fundamentally incorrect or word not used\n"
        f"4. feedback: one short sentence in {native_name} explaining the issue (or praise if correct)\n"
        f"5. improved: a better/more natural version of the sentence in {target_name}\n\n"
        f'Output JSON: {{"result":"correct|fuzzy|wrong","grammar_ok":true/false,'
        f'"context_ok":true/false,"feedback":"...","improved":"..."}}'
    )

    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=300,
    )
    raw = response.choices[0].message.content.strip()
    logger.debug("evaluate_sentence AI 原始响应: %s", raw)

    raw = _strip_json_fence(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("evaluate_sentence 返回非 JSON: %s", raw)
        # 容错：返回 fuzzy 结果
        return {
            "result": "fuzzy",
            "grammar_ok": True,
            "context_ok": True,
            "feedback": "评估服务暂时不可用，请稍后再试。",
            "improved": user_sentence,
        }

    # 标准化 result 字段
    result = data.get("result", "wrong")
    if result not in ("correct", "fuzzy", "wrong"):
        result = "wrong"
    return {
        "result": result,
        "grammar_ok": bool(data.get("grammar_ok", False)),
        "context_ok": bool(data.get("context_ok", False)),
        "feedback": data.get("feedback", ""),
        "improved": data.get("improved", user_sentence),
    }


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


# ── 讲词功能：针对词汇详情的教学解析 ──────────────────────────────────────────

async def explain_word_for_teaching(record: dict, native_language: str = "zh") -> str:
    """
    针对词汇详情页，生成全面的教学解析内容，以用户母语输出。
    record: 来自 get_vocab_detail() 的完整词汇记录（含 quiz_synonyms/antonyms/word_family/collocations/etymology）
    返回格式化的 Markdown 字符串。
    """
    from core.language import NATIVE_LANGUAGE_META, LANGUAGE_META
    native_meta = NATIVE_LANGUAGE_META.get(native_language, {"name": native_language})
    native_name = native_meta["name"]

    word = record.get("word", "")
    pos = record.get("pos", "")
    definition = record.get("definition", "")
    context_sentence = record.get("context", "")
    synonyms = record.get("quiz_synonyms") or []
    antonyms = record.get("antonyms") or []
    word_family = record.get("word_family") or []
    collocations = record.get("collocations") or []
    etymology = record.get("etymology", "") or ""
    target_language = record.get("target_language", "en")

    target_meta = LANGUAGE_META.get(target_language, {"name": target_language.upper()})
    target_name = target_meta["name"]

    # 构建传给 AI 的词汇信息摘要
    meta_parts = []
    if pos:
        meta_parts.append(f"词性: {pos}")
    if synonyms:
        meta_parts.append(f"同义词: {', '.join(synonyms[:4])}")
    if antonyms:
        meta_parts.append(f"反义词: {', '.join(antonyms)}")
    if word_family:
        meta_parts.append(f"词族: {', '.join(word_family)}")
    if collocations:
        meta_parts.append(f"固定搭配: {', '.join(collocations)}")
    if etymology:
        meta_parts.append(f"词源: {etymology}")
    if context_sentence:
        meta_parts.append(f"例句: {context_sentence}")
    meta_summary = "\n".join(meta_parts)

    system_prompt = (
        f"你是专业的{target_name}词汇讲解老师，用{native_name}为学生深度解析单词。\n"
        f"输出格式：使用 Markdown，结构清晰，内容实用，不要过长（控制在1200字以内）。"
    )

    user_prompt = (
        f"请用{native_name}为以下{target_name}词汇做深度讲解：\n\n"
        f"词汇：{word}\n"
        f"释义：{definition}\n"
        f"{meta_summary}\n\n"
        f"讲解内容请涵盖（根据词汇特点适当取舍）：\n"
        f"1. 核心词义与词性说明\n"
        f"2. 词形变化与词族（word family）\n"
        f"3. 同义词辨析——与近义词的细微差别\n"
        f"4. 固定搭配与真实语境示例\n"
        f"5. 词根词缀记忆法（若有意义）\n"
        f"6. 常见错误提示或易混淆点\n"
        f"7. 记忆口诀或联想技巧（可选）\n\n"
        f"直接输出 Markdown 格式讲解内容。"
    )

    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=1500,
    )
    return response.choices[0].message.content.strip()


# ── 上下文语境解析：多义词在特定句子中的含义 ───────────────────────────────────

async def explain_word_in_context(
    word: str,
    context_sentence: str,
    native_language: str = "zh",
    target_language: str = "en",
) -> dict:
    """
    解释单词在特定句子语境中的含义（专门处理多义词）。
    返回 {"word": str, "pos": str, "definition": str, "explanation": str}
    explanation 为母语格式化文本，供直接展示给用户。
    """
    from core.language import LANGUAGE_META, NATIVE_LANGUAGE_META
    target_meta = LANGUAGE_META.get(target_language, {"name": target_language.upper()})
    native_meta = NATIVE_LANGUAGE_META.get(native_language, {"name": native_language})
    target_name = target_meta["name"]
    native_name = native_meta["name"]

    system_prompt = (
        f"你是{target_name}语言专家，擅长分析多义词在不同语境中的含义。\n"
        f"用{native_name}回答，输出 JSON（不加 markdown 代码块）。"
    )

    user_prompt = (
        f"句子：\"{context_sentence}\"\n"
        f"问题：在这个句子中，\"{word}\" 是什么意思？\n\n"
        f"请输出 JSON：\n"
        f'{{\n'
        f'  "word": "{word}的标准形式",\n'
        f'  "pos": "词性（noun/verb/adj/adv/phrase）",\n'
        f'  "definition": "在此句中的{native_name}含义（简洁，不超过15字）",\n'
        f'  "explanation": "用{native_name}说明：(1)在这个句子里的具体含义 (2)与其他常见含义的对比 (3)为什么是这个意思"\n'
        f'}}\n\n'
        f"注意：explanation 字段使用 Markdown 格式，可分行展示。"
    )

    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )
    raw = response.choices[0].message.content.strip()
    logger.debug("explain_word_in_context AI 原始响应: %s", raw)

    raw = _strip_json_fence(raw)
    try:
        data = json.loads(raw)
        return {
            "word": data.get("word", word),
            "pos": data.get("pos", "noun"),
            "definition": data.get("definition", ""),
            "explanation": data.get("explanation", ""),
        }
    except json.JSONDecodeError:
        logger.error("explain_word_in_context 返回非 JSON: %s", raw)
        # 容错：将原始内容作为 explanation 返回
        return {
            "word": word,
            "pos": "noun",
            "definition": "",
            "explanation": raw,
        }
