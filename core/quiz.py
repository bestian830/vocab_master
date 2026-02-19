"""
测验生成模块：从词库中选取到期词汇，生成选择题
支持两种题型：
- fill（填空题）：4个选项（2行×2列）+ "跳过"按钮
- meaning（选义题）：4个中文释义选项（2行×2列）+ "模糊/拿不准"按钮
支持多语言：通过 target_language 指定学习语言
"""
import re
import random
import logging
from dataclasses import dataclass, field

from ai.parser import generate_quiz_sentence, generate_example_sentence
from database.client import get_due_vocab, get_practice_vocab, get_random_words_for_distractor

logger = logging.getLogger(__name__)

# 填空题干扰词：按目标学习语言，词库不足时使用
_FALLBACK_WORDS: dict[str, list[str]] = {
    "en": ["apple", "run", "beautiful", "quickly", "jump"],
    "zh": ["走路", "食物", "快乐", "颜色", "朋友"],
    "ja": ["食べ物", "走る", "きれい", "友達", "色"],
    "de": ["Apfel", "laufen", "schön", "schnell", "Freund"],
    "fr": ["pomme", "courir", "beau", "vite", "ami"],
    "ko": ["사과", "달리다", "아름다운", "빨리", "친구"],
    "pt": ["maçã", "correr", "bonito", "rápido", "amigo"],
    "it": ["mela", "correre", "bello", "velocemente", "amico"],
    "es": ["manzana", "correr", "hermoso", "rápido", "amigo"],
    "ru": ["яблоко", "бежать", "красивый", "быстро", "друг"],
}

# 选义题干扰释义：按母语，词库不足时使用
_FALLBACK_DEFS: dict[str, list[str]] = {
    "zh": ["快速地移动", "感到非常高兴", "一种常见的食物", "表示同意或确认"],
    "en": ["move quickly", "feel very happy", "a common food item", "express agreement"],
    "ja": ["素早く動く", "とても嬉しい", "一般的な食べ物", "同意を示す"],
    "de": ["sich schnell bewegen", "sich sehr freuen", "ein häufiges Lebensmittel", "Zustimmung ausdrücken"],
    "fr": ["se déplacer rapidement", "se sentir très heureux", "un aliment courant", "exprimer un accord"],
    "ko": ["빠르게 움직이다", "매우 기쁘다", "일반적인 음식", "동의를 표하다"],
    "pt": ["mover-se rapidamente", "sentir-se muito feliz", "um alimento comum", "expressar concordância"],
    "it": ["muoversi velocemente", "sentirsi molto felice", "un alimento comune", "esprimere accordo"],
    "es": ["moverse rápidamente", "sentirse muy feliz", "un alimento común", "expresar acuerdo"],
    "ru": ["быстро двигаться", "чувствовать себя очень счастливым", "обычный продукт питания", "выражать согласие"],
}


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
    target_language: str = "en"  # 该题目对应的学习语言


async def build_quiz(
    telegram_id: str,
    practice_mode: bool = False,
    target_language: str = "en",
    native_language: str = "zh",
    force_vocab: dict | None = None,
) -> QuizQuestion | None:
    """
    为指定用户生成一道复习题。
    随机在 fill（填空题）和 meaning（选义题）中选一种。
    practice_mode=True 时从全部词库取词，答题不触发 SM-2 更新。
    target_language 指定学习语言，过滤词库。
    force_vocab: 直接指定本题目标词（跳过随机选取），用于 shuffle 队列模式。
    若无可用词汇则返回 None。
    """
    # 1. 根据模式获取词汇列表，随机选一条（同时按 native_language 过滤，避免跨母语混词）
    if force_vocab is not None:
        # 由外部（shuffle 队列）直接指定目标词，跳过随机选取
        target = force_vocab
    elif practice_mode:
        vocab_list = get_practice_vocab(
            telegram_id, target_language=target_language, native_language=native_language
        )
        if not vocab_list:
            return None
        target = random.choice(vocab_list)
    else:
        vocab_list = get_due_vocab(
            telegram_id, target_language=target_language, native_language=native_language
        )
        if not vocab_list:
            return None
        target = random.choice(vocab_list)
    record_id = target["id"]
    word = target["word"]
    definition = target["definition"]

    # 2. 获取同语言同母语干扰项，避免不同母语释义混入
    distractors_raw = get_random_words_for_distractor(
        telegram_id, record_id, count=3,
        target_language=target_language,
        native_language=native_language,
    )
    random.shuffle(distractors_raw)

    # 3. 随机决定题型
    quiz_type = random.choice(["fill", "meaning"])

    if quiz_type == "fill":
        return await _build_fill_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )
    else:
        return await _build_meaning_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )


async def _build_fill_quiz(
    target: dict,
    record_id: str,
    word: str,
    definition: str,
    distractors_raw: list[dict],
    practice_mode: bool = False,
    target_language: str = "en",
    native_language: str = "zh",
) -> QuizQuestion:
    """
    构建填空题：AI 生成目标语言例句，4个词汇选项
    """
    # 生成新例句（避免与入库时相同）
    try:
        sentence = await generate_quiz_sentence(word, definition, target_language=target_language)
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
                    logger.info("填空题答案泄露（短语），已回退到入库 context：%s", sentence)

    # 最终兜底：去掉空白后若目标词仍可见（单词或短语均适用），回退到 context 或简单句
    sentence_no_blank = re.sub(r'_+', '', sentence)
    if re.search(r'\b' + re.escape(word) + r'\b', sentence_no_blank, re.IGNORECASE):
        fallback = target.get("context", "")
        if fallback:
            replaced = re.sub(re.escape(word), "______", fallback, flags=re.IGNORECASE)
            sentence = replaced if replaced != fallback else fallback + " (______)"
        else:
            sentence = f"______ — {definition}"
        logger.info("填空题答案泄露（兜底），已回退：%s", sentence)

    # 用 fallback 词补足干扰项（词库不足时，按目标语言选词）
    fallback_words = _FALLBACK_WORDS.get(target_language, _FALLBACK_WORDS["en"])
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
        target_language=target_language,
    )


async def _build_meaning_quiz(
    target: dict,
    record_id: str,
    word: str,
    definition: str,
    distractors_raw: list[dict],
    practice_mode: bool = False,
    target_language: str = "en",
    native_language: str = "zh",
) -> QuizQuestion:
    """
    构建选义题：AI 生成新例句（不含占位符），4个母语释义选项
    """
    # 调用 AI 生成多样化例句，展示单词在真实语境中的用法
    try:
        sentence = await generate_example_sentence(word, definition, target_language=target_language)
        if not sentence:
            raise ValueError("empty response")
    except Exception as exc:
        logger.warning("选义题生成例句失败，使用入库 context: %s", exc)
        sentence = target.get("context") or f"She used the word {word} in her speech."

    # 从干扰项中取 definition 作混淆选项（按母语选 fallback，确保语言一致）
    fallback_definitions = _FALLBACK_DEFS.get(native_language, _FALLBACK_DEFS["en"])
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
        target_language=target_language,
    )
