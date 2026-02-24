"""
数据库客户端：SQLAlchemy ORM + 本地 PostgreSQL
对外函数签名与原 Supabase 版本完全兼容
"""
import secrets
import string
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Float,
    Boolean, Date, UniqueConstraint, func, distinct, text,
)
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID, insert as pg_insert

from config import DATABASE_URL, ADMIN_TELEGRAM_ID

# ── ORM 基类 ──────────────────────────────────────────────────────────────────

Base = declarative_base()

# ── ORM 模型定义（取代 schema.sql）───────────────────────────────────────────

class VocabRecord(Base):
    __tablename__ = "vocab_records"

    id              = Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id     = Column(String, nullable=False, index=True)
    word            = Column(String, nullable=False)
    pos             = Column(String)
    definition      = Column(Text, nullable=False)
    context         = Column(Text)
    target_language = Column(String, default="en", nullable=False)
    native_language = Column(String, default="zh", nullable=False)
    level           = Column(Integer, default=0, nullable=False)
    next_review     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ease_factor     = Column(Float, default=2.5, nullable=False)
    review_count    = Column(Integer, default=0, nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    # 富词汇元数据（AI 在解析时顺带生成，按需展示）
    word_level    = Column(Integer, nullable=True)           # 1-5 难度级别
    quiz_synonyms = Column(ARRAY(String), nullable=True)    # 同义词列表（index 0=正确同义词，1-3=错误近义词）
    antonyms      = Column(ARRAY(String), nullable=True)    # 反义词
    word_family   = Column(ARRAY(String), nullable=True)    # 词族（如 decide→decisive,decision）
    etymology     = Column(String, nullable=True)            # 词根/前后缀简述
    collocations  = Column(ARRAY(String), nullable=True)    # 常见固定搭配（目标语言）

    __table_args__ = (
        UniqueConstraint(
            "telegram_id", "word", "definition", "target_language",
            name="unique_vocab",
        ),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    telegram_id = Column(String, primary_key=True)
    expires_at  = Column(DateTime(timezone=True), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    code          = Column(String, primary_key=True)
    duration_days = Column(Integer, nullable=False)
    used_by       = Column(String)
    used_at       = Column(DateTime(timezone=True))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base):
    __tablename__ = "user_settings"

    telegram_id        = Column(String, primary_key=True)
    timezone           = Column(String, default="UTC", nullable=False)
    remind_start       = Column(Integer, default=8, nullable=False)
    remind_end         = Column(Integer, default=22, nullable=False)
    remind_enabled     = Column(Boolean, default=True, nullable=False)
    streak_days        = Column(Integer, default=0, nullable=False)
    last_active_date   = Column(Date)
    native_language    = Column(String, default="zh", nullable=False)
    active_language    = Column(String, default="en", nullable=False)
    learning_languages = Column(ARRAY(String), default=["en"], nullable=False)
    updated_at         = Column(DateTime(timezone=True), server_default=func.now())
    # 定时复习范围：True=仅推送当前激活语言，False=推送所有语言
    review_active_only = Column(Boolean, default=True, nullable=False)
    # 词汇水平评估相关
    proficiency_level  = Column(Integer, default=0, nullable=False)   # 0=未评估, 1-5 词汇水平
    assessment_done    = Column(Boolean, default=False, nullable=False)  # 是否完成了 onboarding 评估


# ── Engine 单例 + Session 工厂 ────────────────────────────────────────────────

_engine = None
_SessionFactory = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def _get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=_get_engine(), autocommit=False, autoflush=False
        )
    return _SessionFactory


@contextmanager
def _session():
    """返回 Session context manager，自动 commit / rollback / close"""
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """启动时调用，自动建表（表已存在则跳过）+ 兜底 ALTER TABLE 补充新列"""
    Base.metadata.create_all(bind=_get_engine())
    # 对已存在的表执行 ALTER TABLE，补充新列（IF NOT EXISTS 保证幂等性）
    with _session() as session:
        session.execute(text("""
            ALTER TABLE vocab_records
              ADD COLUMN IF NOT EXISTS word_level    INTEGER,
              ADD COLUMN IF NOT EXISTS quiz_synonyms TEXT[],
              ADD COLUMN IF NOT EXISTS antonyms      TEXT[],
              ADD COLUMN IF NOT EXISTS word_family   TEXT[],
              ADD COLUMN IF NOT EXISTS etymology     TEXT,
              ADD COLUMN IF NOT EXISTS collocations  TEXT[]
        """))
        session.execute(text("""
            ALTER TABLE user_settings
              ADD COLUMN IF NOT EXISTS proficiency_level    INTEGER NOT NULL DEFAULT 0,
              ADD COLUMN IF NOT EXISTS assessment_done      BOOLEAN NOT NULL DEFAULT FALSE,
              ADD COLUMN IF NOT EXISTS review_active_only   BOOLEAN NOT NULL DEFAULT TRUE
        """))
        # 迁移 id 列从 INTEGER 到 UUID（幂等：已为 UUID 则跳过）
        session.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'vocab_records'
                      AND column_name = 'id'
                      AND data_type = 'integer'
                ) THEN
                    ALTER TABLE vocab_records ALTER COLUMN id DROP DEFAULT;
                    ALTER TABLE vocab_records ALTER COLUMN id SET DATA TYPE UUID USING gen_random_uuid();
                    ALTER TABLE vocab_records ALTER COLUMN id SET DEFAULT gen_random_uuid();
                    DROP SEQUENCE IF EXISTS vocab_records_id_seq;
                END IF;
            END
            $$;
        """))


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    """将 ORM 行对象转为 dict，兼容原 Supabase 返回格式（datetime/UUID → str）"""
    d = {}
    for c in row.__table__.columns:
        v = getattr(row, c.name)
        # datetime 必须在 date 之前判断（datetime 是 date 的子类）
        if isinstance(v, datetime):
            d[c.name] = v.isoformat()
        elif isinstance(v, date):
            d[c.name] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            d[c.name] = str(v)
        else:
            d[c.name] = v
    return d


def _convert_row_dict(d: dict) -> dict:
    """将 _asdict() 结果中的 datetime/date/UUID 值统一转为字符串"""
    result = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, date):
            result[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            result[k] = str(v)
        else:
            result[k] = v
    return result


# ── 词汇记录 CRUD ─────────────────────────────────────────────────────────────

def upsert_vocab(
    telegram_id: str,
    word: str,
    pos: str | None,
    definition: str,
    context: str | None,
    target_language: str = "en",
    native_language: str = "zh",
    word_level: int | None = None,
    quiz_synonyms: list[str] | None = None,
    antonyms: list[str] | None = None,
    word_family: list[str] | None = None,
    etymology: str | None = None,
    collocations: list[str] | None = None,
) -> tuple[dict, bool]:
    """
    插入新词汇；若 (telegram_id, word, definition, target_language) 已存在则跳过。
    返回 (记录 dict, is_new: bool)。
    支持富词汇元数据字段（word_level, quiz_synonyms, antonyms, word_family, etymology, collocations）。
    """
    # 构建插入值 dict，只在有值时才包含新字段
    values = dict(
        telegram_id=telegram_id,
        word=word,
        pos=pos,
        definition=definition,
        context=context,
        target_language=target_language,
        native_language=native_language,
    )
    if word_level is not None:
        values["word_level"] = word_level
    if quiz_synonyms is not None:
        values["quiz_synonyms"] = quiz_synonyms
    if antonyms is not None:
        values["antonyms"] = antonyms
    if word_family is not None:
        values["word_family"] = word_family
    if etymology is not None:
        values["etymology"] = etymology
    if collocations is not None:
        values["collocations"] = collocations

    with _session() as session:
        stmt = pg_insert(VocabRecord).values(**values)
        stmt = stmt.on_conflict_do_nothing(constraint="unique_vocab")
        result = session.execute(stmt)
        # rowcount=1 表示成功插入新行，=0 表示冲突跳过
        is_new = result.rowcount == 1

        # 取回实际记录（含自增 id 等字段）
        row = session.query(VocabRecord).filter_by(
            telegram_id=telegram_id,
            word=word,
            definition=definition,
            target_language=target_language,
        ).first()
        return (_row_to_dict(row) if row else {}), is_new


def get_due_vocab(
    telegram_id: str,
    target_language: str = "en",
    native_language: str | None = None,
) -> list[dict]:
    """查询该用户指定语言所有到期（next_review <= now）的词汇"""
    with _session() as session:
        query = session.query(VocabRecord).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.target_language == target_language,
            VocabRecord.next_review <= datetime.now(timezone.utc),
        )
        if native_language:
            query = query.filter(VocabRecord.native_language == native_language)
        rows = query.order_by(VocabRecord.next_review).all()
        return [_row_to_dict(r) for r in rows]


def get_all_due_users() -> list[dict]:
    """
    查询所有有到期词汇的用户，按 (telegram_id, target_language) 去重。
    返回 [{"telegram_id": ..., "target_language": ...}, ...]
    """
    with _session() as session:
        pairs = (
            session.query(VocabRecord.telegram_id, VocabRecord.target_language)
            .filter(VocabRecord.next_review <= datetime.now(timezone.utc))
            .distinct()
            .all()
        )
        return [{"telegram_id": tid, "target_language": lang} for tid, lang in pairs]


def get_vocab_list(
    telegram_id: str,
    page: int = 0,
    page_size: int = 10,
    target_language: str = "en",
    native_language: str | None = None,
) -> list[dict]:
    """分页获取用户指定语言词库，按 level ASC, next_review ASC 排序"""
    with _session() as session:
        query = session.query(
            VocabRecord.id,
            VocabRecord.word,
            VocabRecord.pos,
            VocabRecord.definition,
            VocabRecord.level,
            VocabRecord.review_count,
            VocabRecord.next_review,
        ).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.target_language == target_language,
        )
        if native_language:
            query = query.filter(VocabRecord.native_language == native_language)
        rows = (
            query
            .order_by(VocabRecord.level.asc(), VocabRecord.next_review.asc())
            .offset(page * page_size)
            .limit(page_size)
            .all()
        )
        # 命名元组 → dict，并将 datetime 转为 isoformat 字符串
        return [_convert_row_dict(r._asdict()) for r in rows]


def count_vocab(
    telegram_id: str,
    target_language: str = "en",
    native_language: str | None = None,
) -> int:
    """返回用户指定语言词库总数"""
    with _session() as session:
        query = session.query(func.count(VocabRecord.id)).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.target_language == target_language,
        )
        if native_language:
            query = query.filter(VocabRecord.native_language == native_language)
        return query.scalar() or 0


def update_vocab_after_review(
    record_id: str,
    level: int,
    next_review_iso: str,
    ease_factor: float | None = None,
    telegram_id: str | None = None,
) -> None:
    """复习后更新 level、next_review、ease_factor，并将 review_count +1；同步更新连续学习天数"""
    next_review_dt = datetime.fromisoformat(next_review_iso.replace("Z", "+00:00"))
    tid = telegram_id

    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        if row:
            row.level        = level
            row.next_review  = next_review_dt
            row.review_count = row.review_count + 1
            if ease_factor is not None:
                row.ease_factor = ease_factor
            tid = tid or row.telegram_id

    # 更新连续学习天数（使用独立 session）
    if tid:
        update_streak(tid)


# ── 订阅系统 ──────────────────────────────────────────────────────────────────

def get_subscription(telegram_id: str) -> datetime | None:
    """返回订阅到期时间（aware datetime），无订阅则返回 None"""
    try:
        with _session() as session:
            row = session.query(Subscription).filter_by(
                telegram_id=telegram_id
            ).first()
            if row is None:
                return None
            # DateTime(timezone=True) + psycopg2 返回 tz-aware datetime
            return row.expires_at
    except Exception:
        return None


def is_pro(telegram_id: str) -> bool:
    """判断用户是否处于有效订阅状态；管理员默认返回 True"""
    if ADMIN_TELEGRAM_ID and telegram_id == ADMIN_TELEGRAM_ID:
        return True
    expires = get_subscription(telegram_id)
    if expires is None:
        return False
    return expires > datetime.now(timezone.utc)


def get_today_add_count(
    telegram_id: str, target_language: str | None = None
) -> int:
    """统计该用户今天（UTC 日期）新增的词汇条数，可按语言过滤"""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with _session() as session:
        query = session.query(func.count(VocabRecord.id)).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.created_at >= today_start,
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        return query.scalar() or 0


def activate_code(telegram_id: str, code: str) -> tuple[bool, str]:
    """
    核销激活码，写入或延长用户订阅。
    返回 (success: bool, message: str)。
    """
    try:
        with _session() as session:
            code_row = session.query(ActivationCode).filter_by(code=code).first()
            if code_row is None:
                return False, "激活码不存在，请检查是否输入正确。"
            if code_row.used_by:
                return False, "该激活码已被使用，无法重复激活。"

            duration_days = code_row.duration_days
            now = datetime.now(timezone.utc)

            # 若已有有效订阅则续期，否则从现在起算
            sub_row = session.query(Subscription).filter_by(
                telegram_id=telegram_id
            ).first()
            existing = sub_row.expires_at if sub_row else None
            base = existing if (existing and existing > now) else now
            new_expires = base + timedelta(days=duration_days)

            # 写入 / 更新订阅
            if sub_row:
                sub_row.expires_at = new_expires
            else:
                session.add(Subscription(telegram_id=telegram_id, expires_at=new_expires))

            # 核销激活码
            code_row.used_by = telegram_id
            code_row.used_at = now

            return True, new_expires.strftime("%Y-%m-%d")
    except Exception:
        return False, "激活码功能未初始化，请联系管理员。"


def extend_subscription(telegram_id: str, days: int) -> str:
    """
    直接为指定用户延长订阅 days 天。
    返回新的到期日字符串（YYYY-MM-DD）。
    """
    with _session() as session:
        now = datetime.now(timezone.utc)
        sub_row = session.query(Subscription).filter_by(
            telegram_id=telegram_id
        ).first()
        existing = sub_row.expires_at if sub_row else None
        base = existing if (existing and existing > now) else now
        new_expires = base + timedelta(days=days)

        if sub_row:
            sub_row.expires_at = new_expires
        else:
            session.add(Subscription(telegram_id=telegram_id, expires_at=new_expires))

        return new_expires.strftime("%Y-%m-%d")


def generate_codes(duration_days: int, count: int) -> list[str]:
    """
    生成 count 个随机激活码（8位大写字母+数字），写入 DB 并返回码列表。
    """
    alphabet = string.ascii_uppercase + string.digits
    codes: list[str] = []
    try:
        with _session() as session:
            for _ in range(count):
                code = "".join(secrets.choice(alphabet) for _ in range(8))
                session.add(ActivationCode(code=code, duration_days=duration_days))
                codes.append(code)
    except Exception as exc:
        raise RuntimeError("activation_codes 表不存在，请先建表") from exc
    return codes


def delete_vocab_by_id(record_id: str) -> bool:
    """按主键删除单条词汇记录，返回是否删除成功"""
    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        if row is None:
            return False
        session.delete(row)
        return True


def delete_vocab_by_word(
    telegram_id: str, word: str, target_language: str | None = None
) -> int:
    """按用户 ID + 单词（大小写不敏感）删除全部匹配记录，返回删除条数。"""
    with _session() as session:
        query = session.query(VocabRecord).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.word.ilike(word),
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.all()
        for row in rows:
            session.delete(row)
        return len(rows)


def count_vocab_by_native(telegram_id: str, native_language: str) -> int:
    """返回指定用户特定母语下的全部词汇总数（跨所有 target_language）"""
    with _session() as session:
        return session.query(func.count(VocabRecord.id)).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.native_language == native_language,
        ).scalar() or 0


def delete_vocab_by_native_language(telegram_id: str, native_language: str) -> int:
    """删除指定用户特定母语下的全部词汇（跨所有 target_language），返回删除条数"""
    with _session() as session:
        rows = session.query(VocabRecord).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.native_language == native_language,
        ).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def get_level_distribution(
    telegram_id: str, target_language: str | None = None
) -> dict[int, int]:
    """返回各级别词汇数量 {level: count}，可按语言过滤"""
    with _session() as session:
        query = session.query(
            VocabRecord.level, func.count(VocabRecord.id)
        ).filter(
            VocabRecord.telegram_id == telegram_id
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.group_by(VocabRecord.level).all()
        return {lvl: cnt for lvl, cnt in rows}


def get_vocab_by_word(
    telegram_id: str, word: str, target_language: str | None = None
) -> list[dict]:
    """按用户 ID + 单词（大小写不敏感）查询所有匹配记录。"""
    with _session() as session:
        query = session.query(
            VocabRecord.id,
            VocabRecord.word,
            VocabRecord.pos,
            VocabRecord.definition,
            VocabRecord.level,
            VocabRecord.context,
            VocabRecord.next_review,
        ).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.word.ilike(word),
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.all()
        return [_convert_row_dict(r._asdict()) for r in rows]


def get_practice_vocab(
    telegram_id: str,
    limit: int = 100,
    target_language: str = "en",
    native_language: str | None = None,
) -> list[dict]:
    """获取词库中随机词汇，不限 next_review，供练习模式使用"""
    with _session() as session:
        query = session.query(VocabRecord).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.target_language == target_language,
        )
        if native_language:
            query = query.filter(VocabRecord.native_language == native_language)
        rows = query.limit(limit).all()
        return [_row_to_dict(r) for r in rows]


def get_random_words_for_distractor(
    telegram_id: str,
    exclude_id: str,
    count: int = 3,
    target_language: str = "en",
    native_language: str | None = None,
) -> list[dict]:
    """随机取 count 条同语言的不同词作为干扰项"""
    with _session() as session:
        query = session.query(
            VocabRecord.id, VocabRecord.word, VocabRecord.definition
        ).filter(
            VocabRecord.telegram_id == telegram_id,
            VocabRecord.target_language == target_language,
            VocabRecord.id != uuid.UUID(exclude_id),
        )
        if native_language:
            query = query.filter(VocabRecord.native_language == native_language)
        rows = query.order_by(func.random()).limit(count * 3).all()
        return [_convert_row_dict(r._asdict()) for r in rows]


def get_all_vocab(telegram_id: str) -> list[dict]:
    """获取用户全部词汇（不分页），用于导出"""
    with _session() as session:
        rows = session.query(
            VocabRecord.word,
            VocabRecord.pos,
            VocabRecord.definition,
            VocabRecord.context,
            VocabRecord.level,
            VocabRecord.next_review,
            VocabRecord.created_at,
            VocabRecord.target_language,
        ).filter(
            VocabRecord.telegram_id == telegram_id
        ).order_by(VocabRecord.created_at.desc()).all()
        return [_convert_row_dict(r._asdict()) for r in rows]


def get_vocab_detail(record_id: str) -> dict | None:
    """按主键取单条完整词汇记录"""
    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        return _row_to_dict(row) if row else None


def get_vocab_count_by_language(telegram_id: str) -> dict[str, int]:
    """返回各语言词汇数量 {lang_code: count}"""
    with _session() as session:
        rows = session.query(
            VocabRecord.target_language, func.count(VocabRecord.id)
        ).filter(
            VocabRecord.telegram_id == telegram_id
        ).group_by(VocabRecord.target_language).all()
        return {lang: cnt for lang, cnt in rows}


# ── 用户设置 ──────────────────────────────────────────────────────────────────

def _default_settings() -> dict:
    """返回用户设置的默认值"""
    return {
        "timezone": "UTC",
        "remind_start": 8,
        "remind_end": 22,
        "remind_enabled": True,
        "native_language": "zh",
        "active_language": "en",
        "learning_languages": ["en"],
        "review_active_only": True,
        "proficiency_level": 0,
        "assessment_done": False,
    }


def get_user_settings(telegram_id: str) -> dict:
    """
    获取用户设置，不存在时返回默认值。
    返回字段: timezone, remind_start, remind_end, remind_enabled,
              native_language, active_language, learning_languages
    """
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(
                telegram_id=telegram_id
            ).first()
            if row is None:
                return _default_settings()
            result = {
                "timezone":           row.timezone,
                "remind_start":       row.remind_start,
                "remind_end":         row.remind_end,
                "remind_enabled":     row.remind_enabled,
                "native_language":    row.native_language,
                "active_language":    row.active_language,
                "learning_languages": row.learning_languages or ["en"],
                "review_active_only": getattr(row, "review_active_only", True),
                "proficiency_level":  getattr(row, "proficiency_level", 0) or 0,
                "assessment_done":    getattr(row, "assessment_done", False) or False,
            }
            return result
    except Exception:
        return _default_settings()


def has_user_settings(telegram_id: str) -> bool:
    """判断用户是否已有 user_settings 记录（用于新用户检测）"""
    try:
        with _session() as session:
            row = session.query(UserSettings.telegram_id).filter_by(
                telegram_id=telegram_id
            ).first()
            return row is not None
    except Exception:
        return False


def set_user_languages(
    telegram_id: str,
    native_language: str | None = None,
    active_language: str | None = None,
) -> None:
    """upsert 用户语言设置（native_language 和/或 active_language）"""
    with _session() as session:
        row = session.query(UserSettings).filter_by(
            telegram_id=telegram_id
        ).first()
        if row:
            if native_language is not None:
                row.native_language = native_language
            if active_language is not None:
                row.active_language = active_language
        else:
            kwargs = {"telegram_id": telegram_id}
            if native_language is not None:
                kwargs["native_language"] = native_language
            if active_language is not None:
                kwargs["active_language"] = active_language
            session.add(UserSettings(**kwargs))


def add_learning_language(telegram_id: str, lang: str) -> None:
    """追加新语言到 learning_languages 数组（已存在则跳过）"""
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(
                telegram_id=telegram_id
            ).first()
            if row:
                current = row.learning_languages or ["en"]
                if not isinstance(current, list):
                    current = ["en"]
                if lang not in current:
                    # 赋值新列表，触发 SQLAlchemy 脏检测
                    row.learning_languages = current + [lang]
            else:
                langs = ["en"] if lang == "en" else ["en", lang]
                session.add(UserSettings(
                    telegram_id=telegram_id,
                    learning_languages=langs,
                ))
    except Exception:
        return


def update_remind_window(telegram_id: str, start_h: int, end_h: int) -> None:
    """upsert 用户推送时段（remind_start/remind_end）"""
    with _session() as session:
        row = session.query(UserSettings).filter_by(
            telegram_id=telegram_id
        ).first()
        if row:
            row.remind_start = start_h
            row.remind_end   = end_h
        else:
            session.add(UserSettings(
                telegram_id=telegram_id,
                remind_start=start_h,
                remind_end=end_h,
            ))


def set_remind_enabled(telegram_id: str, enabled: bool) -> None:
    """upsert remind_enabled 字段，控制是否接收自动推送"""
    with _session() as session:
        row = session.query(UserSettings).filter_by(
            telegram_id=telegram_id
        ).first()
        if row:
            row.remind_enabled = enabled
        else:
            session.add(UserSettings(
                telegram_id=telegram_id,
                remind_enabled=enabled,
            ))


def set_review_active_only(telegram_id: str, enabled: bool) -> None:
    """upsert review_active_only 字段，控制定时复习是仅推送当前语言还是全部语言"""
    with _session() as session:
        row = session.query(UserSettings).filter_by(
            telegram_id=telegram_id
        ).first()
        if row:
            row.review_active_only = enabled
        else:
            session.add(UserSettings(
                telegram_id=telegram_id,
                review_active_only=enabled,
            ))


def update_streak(telegram_id: str) -> None:
    """
    更新用户连续学习天数（streak_days）和最后活跃日期（last_active_date）。
    - 今天已更新过 → 不变
    - 昨天是 last_active_date → streak_days += 1
    - 超过昨天（断掉）→ streak_days = 1
    """
    today = datetime.now(timezone.utc).date()
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(
                telegram_id=telegram_id
            ).first()
            if row:
                last_date = row.last_active_date  # date 对象或 None
                streak = row.streak_days or 0

                if last_date:
                    if last_date == today:
                        return  # 今天已记录
                    elif (today - last_date).days == 1:
                        streak += 1
                    else:
                        streak = 1
                else:
                    streak = 1

                row.streak_days      = streak
                row.last_active_date = today
            else:
                # 用户设置记录不存在，创建新行
                session.add(UserSettings(
                    telegram_id=telegram_id,
                    streak_days=1,
                    last_active_date=today,
                ))
    except Exception:
        return


def get_streak(telegram_id: str) -> tuple[int, str | None]:
    """
    获取用户连续学习天数和最后活跃日期。
    返回 (streak_days, last_active_date_str)，无记录时返回 (0, None)。
    """
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(
                telegram_id=telegram_id
            ).first()
            if not row:
                return 0, None
            last = row.last_active_date
            return (row.streak_days or 0), (last.isoformat() if last else None)
    except Exception:
        return 0, None


def get_expiring_subscriptions(within_days: int = 3) -> list[dict]:
    """返回将在 within_days 天内到期的订阅用户列表"""
    with _session() as session:
        now   = datetime.now(timezone.utc)
        upper = now + timedelta(days=within_days)
        rows  = session.query(Subscription).filter(
            Subscription.expires_at > now,
            Subscription.expires_at <= upper,
        ).all()
        return [
            {"telegram_id": r.telegram_id, "expires_at": r.expires_at.isoformat()}
            for r in rows
        ]


def check_db_connection() -> tuple[bool, str]:
    """测试数据库连接，返回 (ok, message)"""
    try:
        from sqlalchemy import text
        with _session() as session:
            session.execute(text("SELECT 1"))
        return True, "连接正常"
    except Exception as exc:
        return False, str(exc)[:80]


def get_all_user_ids() -> list[str]:
    """返回所有有词汇记录的去重用户 telegram_id 列表"""
    with _session() as session:
        rows = (
            session.query(VocabRecord.telegram_id)
            .distinct()
            .all()
        )
        return [r.telegram_id for r in rows]


def get_admin_stats() -> dict:
    """返回管理员统计数据：total_users / active_7d / pro_users / total_words"""
    with _session() as session:
        total_users = session.query(
            func.count(distinct(VocabRecord.telegram_id))
        ).scalar() or 0

        total_words = session.query(
            func.count(VocabRecord.id)
        ).scalar() or 0

        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        active_7d = session.query(
            func.count(distinct(VocabRecord.telegram_id))
        ).filter(VocabRecord.created_at >= seven_days_ago).scalar() or 0

        now = datetime.now(timezone.utc)
        try:
            pro_users = session.query(
                func.count(Subscription.telegram_id)
            ).filter(Subscription.expires_at > now).scalar() or 0
        except Exception:
            pro_users = 0

        return {
            "total_users": total_users,
            "active_7d":   active_7d,
            "pro_users":   pro_users,
            "total_words": total_words,
        }


def update_vocab_fields(record_id: str, fields: dict) -> bool:
    """更新词汇的可编辑字段（pos/definition/context），返回是否成功"""
    allowed = {"pos", "definition", "context"}
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return False
    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        if row is None:
            return False
        for k, v in safe_fields.items():
            setattr(row, k, v)
        return True


def set_user_timezone(telegram_id: str, timezone_str: str) -> None:
    """设置用户时区（upsert）"""
    with _session() as session:
        row = session.query(UserSettings).filter_by(
            telegram_id=telegram_id
        ).first()
        if row:
            row.timezone = timezone_str
        else:
            session.add(UserSettings(
                telegram_id=telegram_id,
                timezone=timezone_str,
            ))


def set_proficiency_level(
    telegram_id: str, level: int, mark_done: bool = True
) -> None:
    """设置用户词汇水平（1-5），并可选标记评估完成"""
    with _session() as session:
        row = session.query(UserSettings).filter_by(
            telegram_id=telegram_id
        ).first()
        if row:
            row.proficiency_level = level
            if mark_done:
                row.assessment_done = True
        else:
            session.add(UserSettings(
                telegram_id=telegram_id,
                proficiency_level=level,
                assessment_done=mark_done,
            ))


def get_recent_word_levels(telegram_id: str, limit: int = 50) -> float | None:
    """
    返回用户最近 limit 条词汇的平均 word_level（只计有 word_level 的记录）。
    若无有效数据则返回 None。
    """
    with _session() as session:
        rows = (
            session.query(VocabRecord.word_level)
            .filter(
                VocabRecord.telegram_id == telegram_id,
                VocabRecord.word_level.isnot(None),
            )
            .order_by(VocabRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        levels = [r.word_level for r in rows if r.word_level is not None]
        return sum(levels) / len(levels) if levels else None
