"""
测验生成模块：从词库中选取到期词汇，生成选择题
支持两种题型：
- fill（填空题）：4个英文单词选项（2行×2列）+ "跳过"按钮
- meaning（选义题）：4个中文释义选项（2行×2列）+ "模糊/拿不准"按钮
"""
import re
import random
import logging
from dataclasses import dataclass

from ai.parser import generate_quiz_sentence, generate_example_sentence
from database.client import get_due_vocab, get_practice_vocab, get_random_words_for_distractor

logger = logging.getLogger(__name__)


@dataclass
class QuizQuestion:
    """一道测验题"""
    record_id: str          # vocab_records.id，用于答题后更新
    word: str               # 目标单词
    definition: str         # 目标单词释义
    sentence: str           # 例句（fill题含______占位符，meaning题为原始例句）
    correct_answer: str     # 正确选项文本（fill题为word，meaning题为definition）
    options: list[str]      # 4 个选项（已打乱顺序）
    correct_index: int      # 正确选项在 options 中的下标
    quiz_type: str          # "fill" 或 "meaning"
    practice_mode: bool = False  # True 时答题不触发 SM-2 更新


async def build_quiz(telegram_id: str, practice_mode: bool = False) -> QuizQuestion | None:
    """
    为指定用户生成一道复习题。
    随机在 fill（填空题）和 meaning（选义题）中选一种。
    practice_mode=True 时从全部词库取词，答题不触发 SM-2 更新。
    若无可用词汇则返回 None。
    """
    # 1. 根据模式获取词汇列表，随机选一条
    if practice_mode:
        vocab_list = get_practice_vocab(telegram_id)
    else:
        vocab_list = get_due_vocab(telegram_id)
    if not vocab_list:
        return None

    target = random.choice(vocab_list)
    record_id = target["id"]
    word = target["word"]
    definition = target["definition"]

    # 2. 获取干扰项（其他词汇记录，fill题取word，meaning题取definition）
    distractors_raw = get_random_words_for_distractor(telegram_id, record_id, count=3)
    random.shuffle(distractors_raw)

    # 3. 随机决定题型
    quiz_type = random.choice(["fill", "meaning"])

    if quiz_type == "fill":
        return await _build_fill_quiz(target, record_id, word, definition, distractors_raw, practice_mode)
    else:
        return await _build_meaning_quiz(target, record_id, word, definition, distractors_raw, practice_mode)


async def _build_fill_quiz(
    target: dict,
    record_id: str,
    word: str,
    definition: str,
    distractors_raw: list[dict],
    practice_mode: bool = False,
) -> QuizQuestion:
    """
    构建填空题：AI生成例句，4个英文单词选项
    """
    # 生成新例句（避免与入库时相同）
    try:
        sentence = await generate_quiz_sentence(word, definition)
    except Exception as exc:
        logger.warning("生成例句失败，使用入库时的 context: %s", exc)
        sentence = target.get("context") or f"The word is: ______."

    # 若 AI 返回多行，只取含占位符的那行（避免完整句暴露答案）
    lines = [l.strip() for l in sentence.splitlines() if l.strip()]
    placeholder_lines = [l for l in lines if "___" in l or "______" in l]
    if placeholder_lines:
        sentence = placeholder_lines[0]
    elif lines:
        sentence = lines[0]

    # 句子级拆分：AI 可能在同一行返回"原词句. 占位符句"，只保留含占位符的部分
    if ". " in sentence:
        parts = [p.strip() for p in sentence.split(". ") if p.strip()]
        placeholder_parts = [p for p in parts if "___" in p]
        if placeholder_parts:
            sentence = placeholder_parts[0]
            # 补回句尾句号（若原句以句号结尾）
            if not sentence.endswith((".", "!", "?")):
                sentence += "."

    # 确保例句含占位符，并将 ___ 统一替换为 ______ （6条下划线）
    if "______" not in sentence and "___" not in sentence:
        # 大小写不敏感替换目标单词（AI 生成句子可能首字母大写）
        replaced = re.sub(re.escape(word), "______", sentence, flags=re.IGNORECASE)
        sentence = replaced if replaced != sentence else sentence + " (______)"
    else:
        sentence = sentence.replace("___", "______")
        # 替换句子中剩余的目标单词（所有出现都变成占位符，防止答案暴露）
        sentence = re.sub(re.escape(word), "______", sentence, flags=re.IGNORECASE)

    # 泄露检测：对多词短语，若短语中的实义词仍出现在空白之外，回退到入库时的 context
    _STOPWORDS = {
        "the", "a", "an", "of", "in", "at", "to", "for", "on", "with", "by",
        "is", "are", "was", "were", "be", "been", "and", "or", "but", "it", "its",
    }
    phrase_words = [
        w.lower() for w in re.findall(r'\w+', word)
        if w.lower() not in _STOPWORDS
    ]
    if len(phrase_words) > 1:
        # 去掉占位符后检查是否残留实义词
        sentence_no_blank = re.sub(r'_+', '', sentence)
        leaked = any(
            re.search(r'\b' + re.escape(w) + r'\b', sentence_no_blank, re.IGNORECASE)
            for w in phrase_words
        )
        if leaked:
            fallback = target.get("context", "")
            if fallback:
                replaced = re.sub(re.escape(word), "______", fallback, flags=re.IGNORECASE)
                if replaced != fallback:  # 成功替换，使用 context 回退句
                    sentence = replaced
                    logger.info("填空题答案泄露，已回退到入库 context：%s", sentence)

    # 用 fallback 词补足干扰项（词库不足时）
    fallback_words = ["apple", "run", "beautiful", "quickly", "jump"]
    distractor_words: list[str] = [d["word"] for d in distractors_raw[:3]]
    while len(distractor_words) < 3:
        fb = random.choice(fallback_words)
        if fb != word and fb not in distractor_words:
            distractor_words.append(fb)

    options = [word] + distractor_words[:3]
    random.shuffle(options)
    correct_index = options.index(word)

    # 若占位符在句首，选项首字母大写（视觉匹配句子首位置）
    if sentence.startswith("______"):
        options = [opt[0].upper() + opt[1:] if opt else opt for opt in options]

    return QuizQuestion(
        record_id=record_id,
        word=word,
        definition=definition,
        sentence=sentence,
        correct_answer=word,
        options=options,
        correct_index=correct_index,
        quiz_type="fill",
        practice_mode=practice_mode,
    )


async def _build_meaning_quiz(
    target: dict,
    record_id: str,
    word: str,
    definition: str,
    distractors_raw: list[dict],
    practice_mode: bool = False,
) -> QuizQuestion:
    """
    构建选义题：AI 生成新例句（不含占位符），4个中文释义选项
    """
    # 调用 AI 生成多样化例句，展示单词在真实语境中的用法
    try:
        sentence = await generate_example_sentence(word, definition)
        if not sentence:
            raise ValueError("empty response")
    except Exception as exc:
        logger.warning("选义题生成例句失败，使用入库 context: %s", exc)
        sentence = target.get("context") or f"She used the word {word} in her speech."

    # 从干扰项中取 definition 作混淆选项
    fallback_definitions = [
        "快速地移动",
        "感到非常高兴",
        "一种常见的食物",
        "表示同意或确认",
    ]
    distractor_defs: list[str] = [
        d["definition"] for d in distractors_raw[:3]
        if d.get("definition") and d["definition"] != definition
    ]
    # 补足到3条干扰项
    fb_idx = 0
    while len(distractor_defs) < 3 and fb_idx < len(fallback_definitions):
        fb = fallback_definitions[fb_idx]
        if fb != definition and fb not in distractor_defs:
            distractor_defs.append(fb)
        fb_idx += 1

    options = [definition] + distractor_defs[:3]
    random.shuffle(options)
    correct_index = options.index(definition)

    return QuizQuestion(
        record_id=record_id,
        word=word,
        definition=definition,
        sentence=sentence,
        correct_answer=definition,
        options=options,
        correct_index=correct_index,
        quiz_type="meaning",
        practice_mode=practice_mode,
    )
