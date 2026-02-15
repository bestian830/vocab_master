"""
SM-2 简化版：硬编码 8 级时间表
MVP 阶段不使用动态 ease_factor，该字段保留供后续迭代使用。

级别与复习间隔对应关系（从 level 0 答对后进入下一级）：
  level 0 → 答对后 +1天   答错 → 回到 level 0
  level 1 → 答对后 +2天
  level 2 → 答对后 +4天
  level 3 → 答对后 +7天
  level 4 → 答对后 +14天
  level 5 → 答对后 +30天
  level 6 → 答对后 +90天
  level 7 → 已"掌握"，停止复习（next_review 设为 10 年后）
"""
from datetime import datetime, timedelta, timezone

# 每个级别答对后距下次复习的天数
_INTERVALS_DAYS: list[int] = [1, 2, 4, 7, 14, 30, 90, 3650]


def next_level_and_review(current_level: int, correct: bool) -> tuple[int, datetime]:
    """
    根据当前级别和作答是否正确，返回 (新级别, 下次复习时间)。

    :param current_level: 当前 level（0-7）
    :param correct: True=答对，False=答错
    :return: (new_level, next_review_utc)
    """
    if correct:
        # 答对：升一级（最高 7）
        new_level = min(current_level + 1, 7)
    else:
        # 答错：退回 level 0，明天重新复习
        new_level = 0

    interval_days = _INTERVALS_DAYS[new_level]
    next_review = datetime.now(tz=timezone.utc) + timedelta(days=interval_days)
    return new_level, next_review


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
