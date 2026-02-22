"""
词汇水平评估模块：onboarding 时用于判定用户词汇水平（1-5 级）。
使用 Toggle 按钮让用户选择自己认识的词，根据选择结果评分。
目前仅支持英语的 5 组锚点词测评；其他语言走自选流程。
"""

# 5 级词汇水平定义
LEVEL_NAMES: dict[int, dict] = {
    1: {"zh": "初学（初中/A2）",     "en": "Beginner (A2)"},
    2: {"zh": "基础（高中/四级/B1）", "en": "Elementary (B1)"},
    3: {"zh": "进阶（六级/雅思6/B2）","en": "Intermediate (B2)"},
    4: {"zh": "高级（托福/雅思7+/C1）","en": "Advanced (C1)"},
    5: {"zh": "专家（GRE/学术写作/C2）","en": "Expert (C2)"},
}

# 英语 5 组锚点词库，每组 8 个词
ASSESSMENT_BATCHES: dict[int, list[str]] = {
    1: [
        "decide", "suggest", "improve", "flexible",
        "attitude", "achieve", "resource", "extreme",
    ],
    2: [
        "aggressive", "inevitable", "justify", "acknowledge",
        "obstacle", "sustainable", "remarkable", "eliminate",
    ],
    3: [
        "ambiguous", "prevalent", "scrutinize", "contemplate",
        "elaborate", "nuance", "coherent", "pledge",
    ],
    4: [
        "ubiquitous", "contentious", "proliferate", "exacerbate",
        "elusive", "benevolent", "apprehensive", "tenacious",
    ],
    5: [
        "ephemeral", "abstruse", "recalcitrant", "perspicacious",
        "perfidious", "equivocate", "pellucid", "acrimonious",
    ],
}

# 每组至少认识几个词才算"通过"该组
_PASS_THRESHOLD = 5


def score_assessment(selections: dict[int, set[int]]) -> int:
    """
    根据用户在各组的选词结果计算词汇水平（1-5）。

    selections: {batch_idx: set(word_indices)}，batch_idx 为 1-5。

    评分逻辑：
    - 找到用户在该组认识 >= 5/8 个词的最高组 → proficiency_level = 该组
    - 所有组都 < 5 → Level 1
    - 所有组都 >= 5 → Level 5
    """
    best_level = 1
    for batch_idx in range(1, 6):
        selected_count = len(selections.get(batch_idx, set()))
        if selected_count >= _PASS_THRESHOLD:
            best_level = batch_idx
    return best_level


def get_level_name(level: int, lang: str = "zh") -> str:
    """返回等级的本地化名称（如 '进阶（六级/雅思6/B2）'）"""
    meta = LEVEL_NAMES.get(level, {})
    return meta.get(lang, meta.get("en", f"Level {level}"))
