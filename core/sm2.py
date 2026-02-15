"""
SM-2 升级版：3种答题结果 + 动态 ease_factor 调整间隔

答题结果：
  correct — 答对：升1级，EF 小增，间隔 = base * ef/2.5
  fuzzy   — 模糊/跳过：级别不变，EF 小降，明天再复习
  wrong   — 答错：降1级（不归零），EF 较大降，明天再复习

EF 范围 1.3–3.0，初始 2.5
间隔基准表 [1,2,4,7,14,30,90,3650] 天（索引 = 级别）
"""
from datetime import datetime, timedelta, timezone

# 每个级别答对后的基准间隔天数（索引 = 级别 0-7）
_INTERVALS_DAYS: list[int] = [1, 2, 4, 7, 14, 30, 90, 3650]

# EF 初始值与边界
_EF_DEFAULT = 2.5
_EF_MIN = 1.3
_EF_MAX = 3.0


def next_level_and_review(
    current_level: int,
    current_ef: float,
    result: str,  # "correct" | "fuzzy" | "wrong"
) -> tuple[int, float, datetime]:
    """
    根据当前级别、ease_factor 和答题结果，返回 (新级别, 新EF, 下次复习时间)。

    :param current_level: 当前 level（0-7）
    :param current_ef:    当前 ease_factor（1.3-3.0）
    :param result:        答题结果，"correct"/"fuzzy"/"wrong"
    :return: (new_level, new_ef, next_review_utc)
    """
    # 级别更新
    if result == "correct":
        new_level = min(current_level + 1, 7)
    elif result == "wrong":
        # 降1级，最低回 0
        new_level = max(0, current_level - 1)
    else:
        # fuzzy：级别不变
        new_level = current_level

    # EF 更新
    if result == "correct":
        new_ef = min(_EF_MAX, current_ef + 0.1)
    elif result == "fuzzy":
        new_ef = max(_EF_MIN, current_ef - 0.15)
    else:  # wrong
        new_ef = max(_EF_MIN, current_ef - 0.3)

    # 间隔计算
    if result == "correct":
        # 答对：基准间隔 × (ef / 2.5)，EF 越高间隔越长
        base_interval = _INTERVALS_DAYS[new_level]
        interval = max(1, round(base_interval * new_ef / _EF_DEFAULT))
    else:
        # 模糊/答错：固定明天复习
        interval = 1

    next_review = datetime.now(tz=timezone.utc) + timedelta(days=interval)
    return new_level, new_ef, next_review


def format_next_review(dt: datetime) -> str:
    """将 datetime 格式化为 ISO 8601 字符串（Supabase timestamptz 格式）"""
    return dt.isoformat()


def level_progress_bar(level: int) -> str:
    """返回级别对应的进度条（8格），level 0 → 1格填充，level 7 → 8格填充"""
    filled = level + 1  # 0-indexed → 1-8 格填充
    return "█" * filled + "░" * (8 - filled)


def level_description(level: int) -> str:
    """返回级别的人性化描述，用于消息展示"""
    descriptions = {
        0: "初次学习",
        1: "初步记忆",
        2: "短期记忆",
        3: "中期记忆",
        4: "长期记忆",
        5: "深度记忆",
        6: "牢固掌握",
        7: "已掌握 ✓",
    }
    return descriptions.get(level, f"级别 {level}")
