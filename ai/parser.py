"""
AI 解析器：意图识别 + 词汇结构化解析
支持 DeepSeek / OpenAI 兼容接口
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
    word: str           # 目标单词/词组（英文）
    pos: str            # 词性: noun / verb / adj / adv / phrase
    definition: str     # 中文释义（简洁）
    context: str        # 一句包含该词的英文例句


@dataclass
class ParseResult:
    """解析结果，区分词汇与非词汇意图"""
    is_vocab: bool
    vocabs: list[ParsedVocab] | None = None     # 词汇意图时的词汇列表（支持多个）
    rejection_message: str | None = None        # 非词汇意图时的回复文本


# ── System Prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一个专门用于词汇学习的助手，只做词汇解析，拒绝任何其他请求。

你的任务：
1. 判断用户输入是否为【词汇学习意图】（包括：英文单词、英文词组、含目标词的英文句子、中文词语）
2. 若是词汇意图：解析所有词汇并以数组形式返回结构化 JSON
3. 若不是词汇意图：返回拒绝消息

【词汇意图判定规则】
- 英文单词 → 直接解析，整体作为一个条目
- 用分号或逗号列举多个词 → 每个词分别解析，全部输出，不要遗漏
- 英文词组/固定表达/习语（通常 2-5 词，有固定含义，如 curb your enthusiasm、make ends meet、break a leg）
  → 整体作为一个条目，pos = phrase；若附有中文提问词（啥意思/什么意思/怎么理解等），忽略中文部分，只解析英文词组
- 英文完整句子（含主谓结构，或较长）→ 提取句中最值得学习的 1-3 个词汇条目
  优先选：短语动词、习语、专业词组、不常见单词
  例：Gotta put up a security camera.啥意思 → put up [verb] + security camera [noun]
- 中文词语（如"苹果"）→ 找到最常见的英文对应词（apple）并解析
- 其他（如问候、要求写代码、聊天等）→ 非词汇意图

【输出格式】
词汇意图时，严格输出以下 JSON（不要加 markdown 代码块）：
{
  "is_vocab": true,
  "vocabs": [
    {
      "word": "英文单词或词组（小写，词组保留完整形式）",
      "pos": "noun|verb|adj|adv|phrase 之一",
      "definition": "简洁中文释义，不超过20字",
      "context": "一句自然的英文例句，包含该词，难度适中"
    }
  ]
}

非词汇意图时，严格输出：
{
  "is_vocab": false,
  "rejection_message": "我只能帮你查单词哦～请发送你想学习的单词或词组 😊"
}

【注意】
- word 字段永远是英文，词组保留完整形式
- definition 字段永远是中文
- context 例句要体现词义，不要太长（1-2行）
- 单个词时 vocabs 数组长度为 1，多个词时全部列出
- 严禁输出 JSON 以外的任何内容"""


# ── 核心解析函数 ──────────────────────────────────────────────────────────────

async def parse_user_input(user_input: str) -> ParseResult:
    """
    调用 AI 解析用户输入，返回 ParseResult。
    支持多词汇（分号/逗号分隔）和完整词组。
    若 AI 调用失败，抛出异常由上层处理。
    """
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
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
                "我只能帮你查单词哦～请发送你想学习的单词或词组 😊",
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
        vocabs.append(ParsedVocab(
            word=item["word"].lower().strip(),
            pos=item["pos"],
            definition=item["definition"].strip(),
            context=item["context"].strip(),
        ))

    return ParseResult(is_vocab=True, vocabs=vocabs)


_SENTENCE_SYSTEM_PROMPT = """你是英语词汇学习助手。用户会发送一个英文句子，你需要：
1. 翻译整句话为中文
2. 从句子中筛选出 1-4 个值得学习的词汇（优先选：短语动词、习语、专业词汇、不常见单词；跳过常见词如 the/is/at/of/in/a/and 等）

严格输出以下 JSON（不要加 markdown 代码块）：
{
  "translation": "整句的中文翻译",
  "vocabs": [
    {
      "word": "英文单词或词组（小写，词组保留完整形式）",
      "pos": "noun|verb|adj|adv|phrase 之一",
      "definition": "简洁中文释义，不超过20字",
      "context": "一句自然的英文例句，包含该词，难度适中（不要直接用用户的原句）"
    }
  ]
}

严禁输出 JSON 以外的任何内容。"""


async def analyze_sentence(sentence: str) -> tuple[str, list[ParsedVocab]]:
    """
    分析用户发送的完整句子，返回 (翻译, 词汇候选列表)。
    供整句输入流程使用，不自动入库，由用户点击按钮选择保存。
    """
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": _SENTENCE_SYSTEM_PROMPT},
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
        vocabs.append(ParsedVocab(
            word=item["word"].lower().strip(),
            pos=item["pos"],
            definition=item["definition"].strip(),
            context=item["context"].strip(),
        ))

    return translation, vocabs


async def generate_example_sentence(word: str, definition: str) -> str:
    """为单词生成一条自然的英文例句（不含填空符，用于答错无例句时补充语境）"""
    prompt = (
        f'Word: "{word}" ({definition})\n'
        f"Write one natural English example sentence using this word. Output only the sentence."
    )
    resp = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": "你是英语词汇教学助手，只输出例句，不输出其他内容。"},
            {"role": "user", "content": prompt},
        ],
        max_tokens=80,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


async def generate_quiz_sentence(word: str, definition: str) -> str:
    """
    为复习测验生成一个新的填空例句（不同于入库时的 context）。
    返回含 ___ 占位符的英文句子。
    使用全英文 prompt + few-shot 示例，防止 AI 把"步骤/填空"写进句子。
    """
    prompt = (
        f"Create a fill-in-the-blank sentence for the word/phrase: '{word}' ({definition}).\n"
        f"Instructions:\n"
        f"1. Write a natural English sentence that contains the exact phrase '{word}' "
        f"(do NOT change any word in the phrase, including pronouns or articles).\n"
        f"2. Replace the entire phrase '{word}' with exactly six underscores: ______\n"
        f"3. Output ONLY the fill-in-the-blank sentence. No explanations, no parentheses.\n"
        f"Example: For 'give up' → output: Don't ______ just because it's hard.\n"
        f"Example: For 'your enthusiasm' → output: Her teacher praised ______ during the presentation.\n"
        f"Important: The word/phrase '{word}' must NOT appear anywhere in the output."
    )
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": "You are an English vocabulary teaching assistant. Output only the fill-in-the-blank sentence."},
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


async def translate_to_chinese(sentence: str) -> str:
    """将英文例句翻译为中文，用于答错反馈"""
    response = await _ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": "你是翻译助手，只输出中文翻译，不加任何解释。"},
            {"role": "user", "content": f"请将以下英文翻译成中文：\n{sentence}"},
        ],
        temperature=0.3,
        max_tokens=100,
    )
    return response.choices[0].message.content.strip()
