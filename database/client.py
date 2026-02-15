"""
Supabase 客户端单例，供各模块复用
"""
import secrets
import string
from datetime import datetime, timezone, timedelta

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_ANON_KEY, ADMIN_TELEGRAM_ID

# 模块级单例
_client: Client | None = None


def get_client() -> Client:
    """返回全局 Supabase 客户端"""
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client


# ── 词汇记录 CRUD ────────────────────────────────────────────────────────────

def upsert_vocab(
    telegram_id: str,
    word: str,
    pos: str | None,
    definition: str,
    context: str | None,
) -> tuple[dict, bool]:
    """
    插入新词汇；若 (telegram_id, word, definition) 已存在则跳过（ON CONFLICT DO NOTHING）。
    返回 (记录 dict, is_new: bool)。
    冲突跳过时 result.data 为空列表，据此判断是否真正新增。
    """
    db = get_client()
    result = (
        db.table("vocab_records")
        .upsert(
            {
                "telegram_id": telegram_id,
                "word": word,
                "pos": pos,
                "definition": definition,
                "context": context,
            },
            on_conflict="telegram_id,word,definition",
            # 已存在时不覆盖任何字段（不重置复习进度）
            ignore_duplicates=True,
        )
        .execute()
    )
    # 新增时 result.data 非空；冲突跳过时为空列表
    is_new = bool(result.data)

    # 取回实际记录（upsert ignore 时 data 为空列表）
    rows = (
        db.table("vocab_records")
        .select("*")
        .eq("telegram_id", telegram_id)
        .eq("word", word)
        .eq("definition", definition)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else {}, is_new


def get_due_vocab(telegram_id: str) -> list[dict]:
    """查询该用户所有到期（next_review <= now）的词汇"""
    db = get_client()
    return (
        db.table("vocab_records")
        .select("*")
        .eq("telegram_id", telegram_id)
        .lte("next_review", "now()")
        .order("next_review")
        .execute()
        .data
    )


def get_all_due_users() -> list[dict]:
    """
    查询所有有到期词汇的用户，用于调度器批量推送。
    返回 [{"telegram_id": ..., "count": ...}, ...]
    """
    db = get_client()
    # Supabase 不直接支持 GROUP BY，用 RPC 或简化查询
    rows = (
        db.table("vocab_records")
        .select("telegram_id")
        .lte("next_review", "now()")
        .execute()
        .data
    )
    # 在 Python 侧去重
    seen: set[str] = set()
    result: list[dict] = []
    for row in rows:
        tid = row["telegram_id"]
        if tid not in seen:
            seen.add(tid)
            result.append({"telegram_id": tid})
    return result


def get_vocab_list(telegram_id: str, page: int = 0, page_size: int = 10) -> list[dict]:
    """分页获取用户词库，按 level ASC, next_review ASC 排序（最需复习的排前面）"""
    db = get_client()
    offset = page * page_size
    return (
        db.table("vocab_records")
        .select("id,word,pos,definition,level,review_count,next_review")
        .eq("telegram_id", telegram_id)
        .order("level", desc=False)
        .order("next_review", desc=False)
        .range(offset, offset + page_size - 1)
        .execute()
        .data
    )


def count_vocab(telegram_id: str) -> int:
    """返回用户词库总数"""
    db = get_client()
    result = (
        db.table("vocab_records")
        .select("id", count="exact")
        .eq("telegram_id", telegram_id)
        .execute()
    )
    return result.count or 0


def update_vocab_after_review(
    record_id: str,
    level: int,
    next_review_iso: str,
    ease_factor: float | None = None,
    telegram_id: str | None = None,
) -> None:
    """复习后更新 level、next_review、ease_factor，并将 review_count +1；同步更新连续学习天数"""
    db = get_client()
    # 先取当前 review_count 和 telegram_id（若未传入则从记录中读取）
    row = (
        db.table("vocab_records")
        .select("review_count,telegram_id")
        .eq("id", record_id)
        .single()
        .execute()
        .data
    )
    current_count = row["review_count"] if row else 0
    update_dict: dict = {
        "level": level,
        "next_review": next_review_iso,
        "review_count": current_count + 1,
    }
    # 若提供了新 ease_factor，一并更新
    if ease_factor is not None:
        update_dict["ease_factor"] = ease_factor
    db.table("vocab_records").update(update_dict).eq("id", record_id).execute()

    # 更新连续学习天数
    tid = telegram_id or (row.get("telegram_id") if row else None)
    if tid:
        update_streak(tid)


# ── 订阅系统 ─────────────────────────────────────────────────────────────────

def get_subscription(telegram_id: str) -> datetime | None:
    """返回订阅到期时间（aware datetime），无订阅或表不存在则返回 None"""
    db = get_client()
    try:
        rows = (
            db.table("subscriptions")
            .select("expires_at")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        # 表不存在（PGRST205）或其他错误时静默降级，视为无订阅
        return None
    if not rows:
        return None
    expires_str = rows[0]["expires_at"]
    # Supabase 返回 ISO 8601 字符串，解析为 aware datetime
    dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
    return dt


def is_pro(telegram_id: str) -> bool:
    """判断用户是否处于有效订阅状态（expires_at > 当前 UTC 时间）；管理员默认返回 True"""
    # 管理员账号默认享有 Pro 权限，不受订阅限制
    if ADMIN_TELEGRAM_ID and telegram_id == ADMIN_TELEGRAM_ID:
        return True
    expires = get_subscription(telegram_id)
    if expires is None:
        return False
    return expires > datetime.now(timezone.utc)


def get_today_add_count(telegram_id: str) -> int:
    """统计该用户今天（UTC 日期）新增的词汇条数"""
    db = get_client()
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    result = (
        db.table("vocab_records")
        .select("id", count="exact")
        .eq("telegram_id", telegram_id)
        .gte("created_at", today_start)
        .execute()
    )
    return result.count or 0


def activate_code(telegram_id: str, code: str) -> tuple[bool, str]:
    """
    核销激活码，写入或延长用户订阅。
    返回 (success: bool, message: str)。
    表不存在时返回友好错误信息而不是抛出异常。
    """
    db = get_client()

    # 查激活码（捕获表不存在的情况）
    try:
        rows = (
            db.table("activation_codes")
            .select("*")
            .eq("code", code)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        return False, "激活码功能未初始化，请联系管理员。"
    if not rows:
        return False, "激活码不存在，请检查是否输入正确。"

    row = rows[0]
    if row.get("used_by"):
        return False, "该激活码已被使用，无法重复激活。"

    duration_days: int = row["duration_days"]
    now = datetime.now(timezone.utc)

    # 若已有订阅且未过期则续期，否则从现在起算
    existing = get_subscription(telegram_id)
    base = existing if (existing and existing > now) else now
    new_expires = base + timedelta(days=duration_days)
    new_expires_iso = new_expires.isoformat()

    # 写入/更新 subscriptions
    db.table("subscriptions").upsert(
        {"telegram_id": telegram_id, "expires_at": new_expires_iso},
        on_conflict="telegram_id",
    ).execute()

    # 核销激活码
    db.table("activation_codes").update(
        {"used_by": telegram_id, "used_at": now.isoformat()}
    ).eq("code", code).execute()

    return True, new_expires.strftime("%Y-%m-%d")


def extend_subscription(telegram_id: str, days: int) -> str:
    """
    直接为指定用户延长订阅 days 天。
    若已有未过期订阅则在其基础上续期，否则从当前时间起算。
    返回新的到期日字符串（YYYY-MM-DD）。
    """
    db = get_client()
    now = datetime.now(timezone.utc)

    # 若已有有效订阅则续期，否则从现在起算
    existing = get_subscription(telegram_id)
    base = existing if (existing and existing > now) else now
    new_expires = base + timedelta(days=days)

    db.table("subscriptions").upsert(
        {"telegram_id": telegram_id, "expires_at": new_expires.isoformat()},
        on_conflict="telegram_id",
    ).execute()

    return new_expires.strftime("%Y-%m-%d")


def generate_codes(duration_days: int, count: int) -> list[str]:
    """
    生成 count 个随机激活码（8位大写字母+数字），写入 DB 并返回码列表。
    表不存在时抛出 RuntimeError，由上层 handler 提示用户建表。
    """
    alphabet = string.ascii_uppercase + string.digits
    db = get_client()
    codes: list[str] = []
    for _ in range(count):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        try:
            db.table("activation_codes").insert(
                {"code": code, "duration_days": duration_days}
            ).execute()
        except Exception as exc:
            raise RuntimeError("activation_codes 表不存在，请先在 Supabase 建表") from exc
        codes.append(code)
    return codes


def delete_vocab_by_id(record_id: str) -> bool:
    """按主键删除单条词汇记录，返回是否删除成功"""
    db = get_client()
    result = (
        db.table("vocab_records")
        .delete()
        .eq("id", record_id)
        .execute()
    )
    return bool(result.data)


def delete_vocab_by_word(telegram_id: str, word: str) -> int:
    """按用户 ID + 单词（大小写不敏感）删除全部匹配记录，返回删除条数"""
    db = get_client()
    result = (
        db.table("vocab_records")
        .delete()
        .eq("telegram_id", telegram_id)
        .ilike("word", word)
        .execute()
    )
    return len(result.data) if result.data else 0


def get_level_distribution(telegram_id: str) -> dict[int, int]:
    """返回各级别词汇数量 {level: count}，缺少的级别不在结果中"""
    db = get_client()
    rows = (
        db.table("vocab_records")
        .select("level")
        .eq("telegram_id", telegram_id)
        .execute()
        .data
    )
    dist: dict[int, int] = {}
    for row in rows:
        lvl = row["level"]
        dist[lvl] = dist.get(lvl, 0) + 1
    return dist


def get_vocab_by_word(telegram_id: str, word: str) -> list[dict]:
    """按用户 ID + 单词（大小写不敏感）查询所有匹配记录，含例句和下次复习时间"""
    db = get_client()
    return (
        db.table("vocab_records")
        .select("id,word,pos,definition,level,context,next_review")
        .eq("telegram_id", telegram_id)
        .ilike("word", word)
        .execute()
        .data
    )


def get_practice_vocab(telegram_id: str, limit: int = 100) -> list[dict]:
    """获取词库中随机词汇，不限 next_review，供练习模式使用"""
    return (
        get_client()
        .table("vocab_records")
        .select("*")
        .eq("telegram_id", telegram_id)
        .limit(limit)
        .execute()
        .data
    )


def get_random_words_for_distractor(
    telegram_id: str, exclude_id: str, count: int = 3
) -> list[dict]:
    """随机取 count 条不同的词作为干扰项"""
    db = get_client()
    return (
        db.table("vocab_records")
        .select("id,word,definition")
        .eq("telegram_id", telegram_id)
        .neq("id", exclude_id)
        .limit(count * 3)   # 多取一些，Python 侧随机截取
        .execute()
        .data
    )


def get_all_vocab(telegram_id: str) -> list[dict]:
    """获取用户全部词汇（不分页），用于导出"""
    db = get_client()
    return (
        db.table("vocab_records")
        .select("word,pos,definition,context,level,next_review,created_at")
        .eq("telegram_id", telegram_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )


def get_vocab_detail(record_id: str) -> dict | None:
    """按主键取单条完整词汇记录，供详情弹窗使用"""
    db = get_client()
    rows = (
        db.table("vocab_records")
        .select("*")
        .eq("id", record_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


# ── 用户设置 ──────────────────────────────────────────────────────────────────

def get_user_settings(telegram_id: str) -> dict:
    """
    获取用户设置，不存在或表不存在时返回默认值（静默处理）。
    返回字段: timezone, remind_start, remind_end, remind_enabled
    """
    db = get_client()
    try:
        rows = (
            db.table("user_settings")
            .select("timezone,remind_start,remind_end,remind_enabled")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        # 表不存在（PGRST205）或其他错误时静默降级
        return {"timezone": "UTC", "remind_start": 8, "remind_end": 22, "remind_enabled": True}
    if rows:
        # remind_enabled 字段可能尚未存在（旧数据库），降级为 True
        row = rows[0]
        row.setdefault("remind_enabled", True)
        return row
    return {"timezone": "UTC", "remind_start": 8, "remind_end": 22, "remind_enabled": True}


def has_user_settings(telegram_id: str) -> bool:
    """判断用户是否已有 user_settings 记录（用于新用户检测）"""
    db = get_client()
    try:
        rows = (
            db.table("user_settings")
            .select("telegram_id")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
            .data
        )
        return bool(rows)
    except Exception:
        return False


def update_remind_window(telegram_id: str, start_h: int, end_h: int) -> None:
    """upsert 用户推送时段（remind_start/remind_end）"""
    db = get_client()
    try:
        db.table("user_settings").upsert(
            {
                "telegram_id": telegram_id,
                "remind_start": start_h,
                "remind_end": end_h,
            },
            on_conflict="telegram_id",
        ).execute()
    except Exception as exc:
        raise RuntimeError("user_settings 表不存在") from exc


def set_remind_enabled(telegram_id: str, enabled: bool) -> None:
    """upsert remind_enabled 字段，控制是否接收自动推送"""
    db = get_client()
    try:
        db.table("user_settings").upsert(
            {
                "telegram_id": telegram_id,
                "remind_enabled": enabled,
            },
            on_conflict="telegram_id",
        ).execute()
    except Exception as exc:
        raise RuntimeError("user_settings 表不存在") from exc


def update_streak(telegram_id: str) -> None:
    """
    更新用户连续学习天数（streak_days）和最后活跃日期（last_active_date）。
    - 今天已更新过 → 不变
    - 昨天是 last_active_date → streak_days += 1
    - 超过昨天（断掉）→ streak_days = 1
    表不存在时静默忽略。
    """
    db = get_client()
    today = datetime.now(timezone.utc).date()
    try:
        rows = (
            db.table("user_settings")
            .select("streak_days,last_active_date")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        return

    if rows:
        row = rows[0]
        last_date_str = row.get("last_active_date")
        streak = row.get("streak_days") or 0

        if last_date_str:
            from datetime import date
            last_date = date.fromisoformat(last_date_str)
            if last_date == today:
                # 今天已记录，不需要更新
                return
            elif (today - last_date).days == 1:
                # 昨天有记录，连续 +1
                streak += 1
            else:
                # 断掉，重新开始
                streak = 1
        else:
            streak = 1

        try:
            db.table("user_settings").update(
                {"streak_days": streak, "last_active_date": today.isoformat()}
            ).eq("telegram_id", telegram_id).execute()
        except Exception:
            return
    else:
        # 用户设置记录不存在，创建新行
        try:
            db.table("user_settings").upsert(
                {
                    "telegram_id": telegram_id,
                    "streak_days": 1,
                    "last_active_date": today.isoformat(),
                },
                on_conflict="telegram_id",
            ).execute()
        except Exception:
            return


def get_streak(telegram_id: str) -> tuple[int, str | None]:
    """
    获取用户连续学习天数和最后活跃日期。
    返回 (streak_days, last_active_date_str)，表不存在或无记录时返回 (0, None)。
    """
    db = get_client()
    try:
        rows = (
            db.table("user_settings")
            .select("streak_days,last_active_date")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
            .data
        )
    except Exception:
        return 0, None
    if not rows:
        return 0, None
    row = rows[0]
    return row.get("streak_days") or 0, row.get("last_active_date")


def get_expiring_subscriptions(within_days: int = 3) -> list[dict]:
    """返回将在 within_days 天内到期的订阅用户列表"""
    db = get_client()
    now = datetime.now(timezone.utc)
    upper = now + timedelta(days=within_days)
    rows = (
        db.table("subscriptions")
        .select("telegram_id, expires_at")
        .gt("expires_at", now.isoformat())
        .lte("expires_at", upper.isoformat())
        .execute()
    ).data or []
    return rows


def check_db_connection() -> tuple[bool, str]:
    """测试数据库连接，返回 (ok, message)"""
    try:
        db = get_client()
        db.table("vocab_records").select("id").limit(1).execute()
        return True, "连接正常"
    except Exception as exc:
        return False, str(exc)[:80]


def get_all_user_ids() -> list[str]:
    """返回所有有词汇记录的去重用户 telegram_id 列表"""
    db = get_client()
    rows = (
        db.table("vocab_records")
        .select("telegram_id")
        .execute()
        .data
    )
    seen: set[str] = set()
    result: list[str] = []
    for row in rows:
        tid = row["telegram_id"]
        if tid not in seen:
            seen.add(tid)
            result.append(tid)
    return result


def get_admin_stats() -> dict:
    """返回管理员统计数据：total_users / active_7d / pro_users / total_words"""
    db = get_client()

    # 全部词汇记录，用于统计用户数和总词汇量
    all_rows = (
        db.table("vocab_records")
        .select("telegram_id")
        .execute()
        .data
    ) or []

    # 去重用户集合
    all_users: set[str] = {r["telegram_id"] for r in all_rows}
    total_users = len(all_users)
    total_words = len(all_rows)

    # 7 日内有新增词汇的用户（近似活跃）
    from datetime import timedelta
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    active_rows = (
        db.table("vocab_records")
        .select("telegram_id")
        .gte("created_at", seven_days_ago)
        .execute()
        .data
    ) or []
    active_7d = len({r["telegram_id"] for r in active_rows})

    # Pro 用户数（subscriptions 表，expires_at > now）
    try:
        sub_rows = (
            db.table("subscriptions")
            .select("telegram_id")
            .gt("expires_at", datetime.now(timezone.utc).isoformat())
            .execute()
            .data
        ) or []
        pro_users = len(sub_rows)
    except Exception:
        pro_users = 0

    return {
        "total_users": total_users,
        "active_7d": active_7d,
        "pro_users": pro_users,
        "total_words": total_words,
    }


def update_vocab_fields(record_id: str, fields: dict) -> bool:
    """更新词汇的可编辑字段（pos/definition/context），返回是否成功"""
    # 只允许编辑这三个字段
    allowed = {"pos", "definition", "context"}
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return False
    db = get_client()
    result = (
        db.table("vocab_records")
        .update(safe_fields)
        .eq("id", record_id)
        .execute()
    )
    return bool(result.data)


def set_user_timezone(telegram_id: str, timezone_str: str) -> None:
    """设置用户时区（upsert）。表不存在时抛出 RuntimeError 告知上层。"""
    db = get_client()
    try:
        db.table("user_settings").upsert(
            {"telegram_id": telegram_id, "timezone": timezone_str},
            on_conflict="telegram_id",
        ).execute()
    except Exception as exc:
        raise RuntimeError("user_settings 表不存在") from exc
