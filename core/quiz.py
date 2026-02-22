"""
测验生成模块：从词库中选取到期词汇，生成选择题
支持题型：
- fill（填空题）：4个选项（2行×2列）+ "跳过"按钮
- meaning（选义题）：4个母语释义选项（2行×2列）+ "模糊/拿不准"按钮
- synonym（同义词辨析题）：4个近义词选项
- reverse（反向选词题）：给出母语释义，选出正确目标语言单词
- generation（造句题）：用户造句，AI 评分
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
    sentence: str           # 例句（fill题含______占位符，meaning/reverse题为原始例句，generation题为参考例句）
    correct_answer: str     # 正确选项文本（fill/reverse题为word，meaning题为definition，synonym题为同义词，generation题为空）
    options: list[str]      # 4 个选项（fill/meaning/synonym/reverse题），generation题为空列表
    correct_index: int      # 正确选项在 options 中的下标（generation题为 -1）
    quiz_type: str          # "fill" | "meaning" | "synonym" | "reverse" | "generation"
    practice_mode: bool = False  # True 时答题不触发 SM-2 更新
    target_language: str = "en"  # 该题目对应的学习语言
    ease_factor: float = 2.5     # 当前 ease_factor（generation 造句题评分使用）
    native_language: str = "zh"  # 母语（generation 题反馈语言使用）
    level: int = 0               # 当前 SM-2 level（generation 题使用）


async def build_quiz(
    telegram_id: str,
    practice_mode: bool = False,
    target_language: str = "en",
    native_language: str = "zh",
    force_vocab: dict | None = None,
    allow_generation: bool = True,
) -> QuizQuestion | None:
    """
    为指定用户生成一道复习题。
    按 SM-2 level 决定题型，包含 reverse 反向选词题。
    practice_mode=True 时从全部词库取词，答题不触发 SM-2 更新。
    target_language 指定学习语言，过滤词库。
    force_vocab: 直接指定本题目标词（跳过随机选取），用于 shuffle 队列模式。
    allow_generation: 为 False 时（如调度器推送）跳过 generation 题，fallback 到 fill 题。
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
    sm2_level = target.get("level", 0)

    # 2. 根据 SM-2 level 决定题型（含 reverse 反向选词题）
    #    Level 0-1: fill / meaning / reverse 随机（三选一，均衡训练）
    #    Level 2-3: fill / reverse（强化拼写+产出）
    #    Level 4-5: fill / synonym / reverse（加入近义词辨析）
    #    Level 6-7: generation（造句 + AI 评分）
    if sm2_level <= 1:
        quiz_type = random.choice(["fill", "meaning", "reverse"])
    elif sm2_level <= 3:
        quiz_type = random.choice(["fill", "reverse"])
    elif sm2_level <= 5:
        quiz_type = random.choice(["fill", "synonym", "reverse"])
    else:
        quiz_type = "generation"

    # 调度器场景无法设置 pending_generation，generation 题 fallback 到 fill
    if not allow_generation and quiz_type == "generation":
        quiz_type = "fill"

    # 3. 获取同语言同母语干扰项（fill/meaning/synonym/reverse 题需要）
    distractors_raw = get_random_words_for_distractor(
        telegram_id, record_id, count=3,
        target_language=target_language,
        native_language=native_language,
    )
    random.shuffle(distractors_raw)

    if quiz_type == "fill":
        return await _build_fill_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )
    elif quiz_type == "meaning":
        return await _build_meaning_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )
    elif quiz_type == "synonym":
        result = await _build_synonym_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )
        # synonym 题若无同义词缓存，fallback 到 meaning 题
        if result is not None:
            return result
        return await _build_meaning_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )
    elif quiz_type == "reverse":
        return _build_reverse_quiz(
            target, record_id, word, definition,
            distractors_raw, practice_mode, target_language, native_language
        )
    else:  # generation
        return _build_generation_quiz(
            target, record_id, word, definition,
            practice_mode, target_language, native_language
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
    构建填空题：AI 生成目标语言例句，4个词汇选项。
    优先使用 quiz_synonyms[1:] 作为语义相近的干扰项（比随机词更难区分）。
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

    # 优先使用 quiz_synonyms[1:] 作为语义相近干扰项（更难区分，测验质量更高）
    quiz_synonyms = target.get("quiz_synonyms") or []
    near_synonyms = [s for s in quiz_synonyms[1:4] if s and s.lower() != word.lower()]

    distractor_words: list[str] = near_synonyms[:3]

    # 不足时补充随机词库词
    fallback_words = _FALLBACK_WORDS.get(target_language, _FALLBACK_WORDS["en"])
    for d in distractors_raw:
        if len(distractor_words) >= 3:
            break
        w = d.get("word", "")
        if w and w.lower() != word.lower() and w not in distractor_words:
            distractor_words.append(w)

    # 最终用 fallback 词补足
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
        ease_factor=target.get("ease_factor", 2.5),
        native_language=native_language,
        level=target.get("level", 0),
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
    构建选义题：AI 生成新例句（不含占位符），4个母语释义选项。
    优先尝试从 quiz_synonyms[1:] 的词库记录中获取 definition 作干扰释义。
    """
    # 调用 AI 生成多样化例句，展示单词在真实语境中的用法
    try:
        sentence = await generate_example_sentence(word, definition, target_language=target_language)
        if not sentence:
            raise ValueError("empty response")
    except Exception as exc:
        logger.warning("选义题生成例句失败，使用入库 context: %s", exc)
        sentence = target.get("context") or f"She used the word {word} in her speech."

    # 尝试从 quiz_synonyms[1:] 的词库记录中获取 definition 作干扰释义
    distractor_defs: list[str] = []
    quiz_synonyms = target.get("quiz_synonyms") or []
    near_synonyms = [s for s in quiz_synonyms[1:4] if s]
    if near_synonyms:
        from database.client import get_vocab_by_word
        telegram_id = target.get("telegram_id", "")
        for syn in near_synonyms:
            if len(distractor_defs) >= 3:
                break
            try:
                syn_records = get_vocab_by_word(telegram_id, syn, target_language=target_language)
                if syn_records:
                    d = syn_records[0].get("definition", "")
                    if d and d != definition and d not in distractor_defs:
                        distractor_defs.append(d)
            except Exception:
                pass

    # 从随机词库补足干扰释义（按母语选 fallback，确保语言一致）
    for d in distractors_raw[:3]:
        if len(distractor_defs) >= 3:
            break
        df = d.get("definition", "")
        if df and df != definition and df not in distractor_defs:
            distractor_defs.append(df)

    # 最终用 fallback 释义补足到3条
    fallback_definitions = _FALLBACK_DEFS.get(native_language, _FALLBACK_DEFS["en"])
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
        ease_factor=target.get("ease_factor", 2.5),
        native_language=native_language,
        level=target.get("level", 0),
    )


async def _build_synonym_quiz(
    target: dict,
    record_id: str,
    word: str,
    definition: str,
    distractors_raw: list[dict],
    practice_mode: bool = False,
    target_language: str = "en",
    native_language: str = "zh",
) -> QuizQuestion | None:
    """
    构建同义词辨析题：目标词 + 4 选项（quiz_synonyms 缓存数据）。
    若 quiz_synonyms 为空则返回 None，由调用方 fallback 到 meaning 题。
    """
    quiz_synonyms = target.get("quiz_synonyms") or []

    # 需要至少 1 个正确同义词 + 3 个干扰项（可从 quiz_synonyms 或随机词库补足）
    if not quiz_synonyms or not quiz_synonyms[0]:
        return None

    correct_synonym = quiz_synonyms[0]
    # 使用缓存的错误近义词（index 1-3），不足时从词库随机词补足
    wrong_options = [s for s in quiz_synonyms[1:4] if s]
    while len(wrong_options) < 3:
        fallback = random.choice(distractors_raw) if distractors_raw else None
        if fallback and fallback.get("word") and fallback["word"] not in wrong_options:
            wrong_options.append(fallback["word"])
        elif len(wrong_options) < 3:
            # 最终兜底：用随机常见词
            wrong_options.append(random.choice(["common", "normal", "typical", "usual"]))

    options = [correct_synonym] + wrong_options[:3]
    random.shuffle(options)
    correct_index = options.index(correct_synonym)

    # 用词条的 context 例句，或直接展示词 + 释义
    context_sentence = target.get("context") or f"She demonstrated {word} in her work."

    return QuizQuestion(
        record_id=record_id,
        word=word,
        definition=definition,
        sentence=context_sentence,
        correct_answer=correct_synonym,
        options=options,
        correct_index=correct_index,
        quiz_type="synonym",
        practice_mode=practice_mode,
        target_language=target_language,
        ease_factor=target.get("ease_factor", 2.5),
        native_language=native_language,
        level=target.get("level", 0),
    )


def _build_reverse_quiz(
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
    构建反向选词题：给出母语释义，选出正确的目标语言单词（测产出能力）。
    干扰项优先用 quiz_synonyms[1:] 的近义词，使题目更难。
    使用 quiz: callback 前缀，与 fill 题判对逻辑相同。
    """
    # 优先用 quiz_synonyms[1:] 的近义词作干扰项（语义相近，更难区分）
    quiz_synonyms = target.get("quiz_synonyms") or []
    near_synonyms = [s for s in quiz_synonyms[1:4] if s and s.lower() != word.lower()]
    distractor_words: list[str] = near_synonyms[:3]

    # 不足时从词库随机词补足
    fallback_words = _FALLBACK_WORDS.get(target_language, _FALLBACK_WORDS["en"])
    for d in distractors_raw:
        if len(distractor_words) >= 3:
            break
        w = d.get("word", "")
        if w and w.lower() != word.lower() and w not in distractor_words:
            distractor_words.append(w)

    # 最终 fallback 补足
    while len(distractor_words) < 3:
        fb = random.choice(fallback_words)
        if fb != word and fb not in distractor_words:
            distractor_words.append(fb)

    options = [word] + distractor_words[:3]
    random.shuffle(options)
    correct_index = options.index(word)

    # 以存储的 context 例句作为辅助语境提示
    context_sentence = target.get("context") or ""

    return QuizQuestion(
        record_id=record_id,
        word=word,
        definition=definition,
        sentence=context_sentence,
        correct_answer=word,
        options=options,
        correct_index=correct_index,
        quiz_type="reverse",
        practice_mode=practice_mode,
        target_language=target_language,
        ease_factor=target.get("ease_factor", 2.5),
        native_language=native_language,
        level=target.get("level", 0),
    )


def _build_generation_quiz(
    target: dict,
    record_id: str,
    word: str,
    definition: str,
    practice_mode: bool = False,
    target_language: str = "en",
    native_language: str = "zh",
) -> QuizQuestion:
    """
    构建造句题：用户需在聊天框输入包含目标词的句子，由 AI 评分。
    不需要 AI 异步调用，直接返回（句子评分在用户提交后异步执行）。
    """
    # 用入库时的例句作为参考提示
    context_sentence = target.get("context") or f"Use '{word}' in a sentence."

    return QuizQuestion(
        record_id=record_id,
        word=word,
        definition=definition,
        sentence=context_sentence,       # 展示给用户的参考例句
        correct_answer="",               # 无固定正确答案
        options=[],                      # 无选项
        correct_index=-1,
        quiz_type="generation",
        practice_mode=practice_mode,
        target_language=target_language,
        ease_factor=target.get("ease_factor", 2.5),
        native_language=native_language,
        level=target.get("level", 0),
    )
