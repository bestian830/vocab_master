"""
数据库客户端：SQLAlchemy ORM + PostgreSQL
Web App 版本：使用 user_id (UUID) 替代 telegram_id，支持 Logto 认证
"""
import secrets
import string
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Float,
    Boolean, Date, UniqueConstraint, ForeignKey, func, distinct, text,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID, insert as pg_insert

from config import DATABASE_URL

# ── ORM 基类 ──────────────────────────────────────────────────────────────────

Base = declarative_base()

# ── ORM 模型定义 ──────────────────────────────────────────────────────────────

class User(Base):
    """用户主表：身份信息 + Logto 关联"""
    __tablename__ = "users"

    id              = Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logto_id        = Column(String(128), unique=True, nullable=False, index=True)
    email           = Column(String, nullable=True)
    phone           = Column(String, nullable=True)
    name            = Column(String, nullable=True)
    avatar          = Column(String, nullable=True)
    native_language = Column(String, nullable=False, default="zh")
    is_admin        = Column(Boolean, default=False, nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    # 关系
    settings      = relationship("UserSettings", back_populates="user", uselist=False)
    vocab_records = relationship("VocabRecord", back_populates="user")
    review_logs   = relationship("ReviewLog", back_populates="user")
    subscriptions = relationship("Subscription", back_populates="user")


class UserSettings(Base):
    """用户设置：频繁修改的偏好配置，与 users 分离"""
    __tablename__ = "user_settings"

    user_id            = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    timezone           = Column(String, default="UTC", nullable=False)
    remind_enabled     = Column(Boolean, default=True, nullable=False)
    remind_start       = Column(Integer, default=8, nullable=False)
    remind_end         = Column(Integer, default=22, nullable=False)
    active_language    = Column(String, default="en", nullable=False)
    learning_languages = Column(ARRAY(String), default=["en"], nullable=False)
    daily_goal         = Column(Integer, default=10, nullable=False)
    streak_days        = Column(Integer, default=0, nullable=False)
    last_active_date   = Column(Date, nullable=True)
    proficiency_level  = Column(Integer, default=0, nullable=False)
    assessment_done    = Column(Boolean, default=False, nullable=False)
    review_active_only = Column(Boolean, default=True, nullable=False)
    updated_at         = Column(DateTime(timezone=True), server_default=func.now())

    # 关系
    user = relationship("User", back_populates="settings")


class VocabRecord(Base):
    """词汇记录：一条 = 一个用户正在学习的一个词义"""
    __tablename__ = "vocab_records"

    id               = Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id          = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    word             = Column(String, nullable=False)
    pos              = Column(String, nullable=True)
    definition       = Column(Text, nullable=False)
    context          = Column(Text, nullable=True)
    target_language  = Column(String, nullable=False)
    cefr_level       = Column(String, nullable=True)
    level            = Column(Integer, default=0, nullable=False)
    next_review      = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ease_factor      = Column(Float, default=2.5, nullable=False)
    review_count     = Column(Integer, default=0, nullable=False)
    correct_count    = Column(Integer, default=0, nullable=False)
    incorrect_count  = Column(Integer, default=0, nullable=False)
    last_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    pronunciation    = Column(String, nullable=True)
    example_sentences = Column(ARRAY(Text), nullable=True)
    quiz_synonyms    = Column(ARRAY(String), nullable=True)
    antonyms         = Column(ARRAY(String), nullable=True)
    word_family      = Column(ARRAY(String), nullable=True)
    etymology        = Column(String, nullable=True)
    collocations     = Column(ARRAY(String), nullable=True)
    confusion_words  = Column(ARRAY(String), nullable=True)
    notes            = Column(Text, nullable=True)
    tags             = Column(ARRAY(String), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_id", "word", "definition", "target_language",
            name="unique_vocab",
        ),
    )

    # 关系（passive_deletes=True 让数据库处理 CASCADE 删除）
    user        = relationship("User", back_populates="vocab_records")
    review_logs = relationship("ReviewLog", back_populates="vocab", passive_deletes=True)


class ReviewLog(Base):
    """复习日志：逐次答题记录，用于 AI 深度分析"""
    __tablename__ = "review_logs"

    id               = Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id          = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    vocab_id         = Column(PgUUID(as_uuid=True), ForeignKey("vocab_records.id", ondelete="CASCADE"), nullable=False, index=True)
    quiz_type        = Column(String, nullable=False)
    is_correct       = Column(Boolean, nullable=False)
    user_answer      = Column(String, nullable=True)
    correct_answer   = Column(String, nullable=False)
    response_time_ms = Column(Integer, nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    # 关系
    user  = relationship("User", back_populates="review_logs")
    vocab = relationship("VocabRecord", back_populates="review_logs")


class Subscription(Base):
    """订阅记录：每次订阅/续费是独立记录"""
    __tablename__ = "subscriptions"

    id                = Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id           = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    plan              = Column(String, nullable=False)
    status            = Column(String, nullable=False)
    starts_at         = Column(DateTime(timezone=True), nullable=False)
    expires_at        = Column(DateTime(timezone=True), nullable=False)
    payment_provider  = Column(String, nullable=False)
    provider_order_id = Column(String, nullable=True)
    amount            = Column(Integer, nullable=True)
    currency          = Column(String, nullable=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())

    # 关系
    user = relationship("User", back_populates="subscriptions")


class ActivationCode(Base):
    """激活码：运营/送礼用，不用于正常订阅流程"""
    __tablename__ = "activation_codes"

    code          = Column(String, primary_key=True)
    plan          = Column(String, default="monthly", nullable=False)
    duration_days = Column(Integer, nullable=False)
    purpose       = Column(String, nullable=True)
    used_by       = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    used_at       = Column(DateTime(timezone=True), nullable=True)
    expires_at    = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


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
    """启动时调用，自动建表（表已存在则跳过）"""
    Base.metadata.create_all(bind=_get_engine())


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    """将 ORM 行对象转为 dict（datetime/UUID → str）"""
    d = {}
    for c in row.__table__.columns:
        v = getattr(row, c.name)
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


# ── 用户 CRUD ─────────────────────────────────────────────────────────────────

def get_or_create_user(
    logto_id: str,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
    avatar: str | None = None,
    native_language: str = "zh",
) -> tuple[dict, bool]:
    """
    根据 logto_id 查找或创建用户。
    返回 (用户 dict, is_new: bool)。
    登录时调用：首次登录自动创建用户 + 默认设置。
    """
    with _session() as session:
        row = session.query(User).filter_by(logto_id=logto_id).first()
        if row:
            # 更新可变字段（社交登录信息可能变更）
            if email is not None:
                row.email = email
            if phone is not None:
                row.phone = phone
            if name is not None:
                row.name = name
            if avatar is not None:
                row.avatar = avatar
            return _row_to_dict(row), False

        # 新用户：创建 user + 默认 settings
        user = User(
            logto_id=logto_id,
            email=email,
            phone=phone,
            name=name,
            avatar=avatar,
            native_language=native_language,
        )
        session.add(user)
        session.flush()  # 获取 user.id

        settings = UserSettings(user_id=user.id)
        session.add(settings)

        return _row_to_dict(user), True


def get_user_by_id(user_id: str) -> dict | None:
    """按内部 UUID 获取用户"""
    with _session() as session:
        row = session.query(User).filter_by(id=uuid.UUID(user_id)).first()
        return _row_to_dict(row) if row else None


def get_user_by_logto_id(logto_id: str) -> dict | None:
    """按 Logto ID 获取用户"""
    with _session() as session:
        row = session.query(User).filter_by(logto_id=logto_id).first()
        return _row_to_dict(row) if row else None


def update_user(user_id: str, **fields) -> bool:
    """更新用户字段（name/avatar/native_language/email/phone）"""
    allowed = {"name", "avatar", "native_language", "email", "phone"}
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return False
    with _session() as session:
        row = session.query(User).filter_by(id=uuid.UUID(user_id)).first()
        if row is None:
            return False
        for k, v in safe_fields.items():
            setattr(row, k, v)
        return True


# ── 词汇记录 CRUD ─────────────────────────────────────────────────────────────

def upsert_vocab(
    user_id: str,
    word: str,
    pos: str | None,
    definition: str,
    context: str | None,
    target_language: str = "en",
    cefr_level: str | None = None,
    pronunciation: str | None = None,
    example_sentences: list[str] | None = None,
    quiz_synonyms: list[str] | None = None,
    antonyms: list[str] | None = None,
    word_family: list[str] | None = None,
    etymology: str | None = None,
    collocations: list[str] | None = None,
    confusion_words: list[str] | None = None,
    tags: list[str] | None = None,
) -> tuple[dict, bool]:
    """
    插入新词汇；若 (user_id, word, definition, target_language) 已存在则跳过。
    返回 (记录 dict, is_new: bool)。
    """
    uid = uuid.UUID(user_id)
    values = dict(
        user_id=uid,
        word=word,
        pos=pos,
        definition=definition,
        context=context,
        target_language=target_language,
    )
    # 可选字段：有值时才包含
    optional = {
        "cefr_level": cefr_level,
        "pronunciation": pronunciation,
        "example_sentences": example_sentences,
        "quiz_synonyms": quiz_synonyms,
        "antonyms": antonyms,
        "word_family": word_family,
        "etymology": etymology,
        "collocations": collocations,
        "confusion_words": confusion_words,
        "tags": tags,
    }
    for k, v in optional.items():
        if v is not None:
            values[k] = v

    with _session() as session:
        stmt = pg_insert(VocabRecord).values(**values)
        stmt = stmt.on_conflict_do_nothing(constraint="unique_vocab")
        result = session.execute(stmt)
        is_new = result.rowcount == 1

        row = session.query(VocabRecord).filter_by(
            user_id=uid,
            word=word,
            definition=definition,
            target_language=target_language,
        ).first()
        return (_row_to_dict(row) if row else {}), is_new


def get_due_vocab(
    user_id: str,
    target_language: str = "en",
) -> list[dict]:
    """查询该用户指定语言所有到期（next_review <= now）的词汇"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        rows = (
            session.query(VocabRecord)
            .filter(
                VocabRecord.user_id == uid,
                VocabRecord.target_language == target_language,
                VocabRecord.next_review <= datetime.now(timezone.utc),
            )
            .order_by(VocabRecord.next_review)
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def get_all_due_users() -> list[dict]:
    """
    查询所有有到期词汇的用户，按 (user_id, target_language) 去重。
    返回 [{"user_id": ..., "target_language": ...}, ...]
    """
    with _session() as session:
        pairs = (
            session.query(VocabRecord.user_id, VocabRecord.target_language)
            .filter(VocabRecord.next_review <= datetime.now(timezone.utc))
            .distinct()
            .all()
        )
        return [{"user_id": str(uid), "target_language": lang} for uid, lang in pairs]


def get_vocab_list(
    user_id: str,
    page: int = 0,
    page_size: int = 10,
    target_language: str = "en",
) -> list[dict]:
    """分页获取用户指定语言词库，按 level ASC, next_review ASC 排序"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        rows = (
            session.query(
                VocabRecord.id,
                VocabRecord.word,
                VocabRecord.pos,
                VocabRecord.definition,
                VocabRecord.level,
                VocabRecord.review_count,
                VocabRecord.next_review,
                VocabRecord.cefr_level,
                VocabRecord.tags,
            )
            .filter(
                VocabRecord.user_id == uid,
                VocabRecord.target_language == target_language,
            )
            .order_by(VocabRecord.level.asc(), VocabRecord.next_review.asc())
            .offset(page * page_size)
            .limit(page_size)
            .all()
        )
        return [_convert_row_dict(r._asdict()) for r in rows]


def count_vocab(
    user_id: str,
    target_language: str = "en",
) -> int:
    """返回用户指定语言词库总数"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        return (
            session.query(func.count(VocabRecord.id))
            .filter(
                VocabRecord.user_id == uid,
                VocabRecord.target_language == target_language,
            )
            .scalar() or 0
        )


def update_vocab_after_review(
    record_id: str,
    level: int,
    next_review_iso: str,
    ease_factor: float | None = None,
    is_correct: bool | None = None,
    user_id: str | None = None,
) -> None:
    """
    复习后更新 level、next_review、ease_factor，
    递增 review_count + correct/incorrect_count，更新 last_reviewed_at，
    同步更新连续学习天数。
    """
    next_review_dt = datetime.fromisoformat(next_review_iso.replace("Z", "+00:00"))
    uid = user_id

    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        if row:
            row.level = level
            row.next_review = next_review_dt
            row.review_count = row.review_count + 1
            row.last_reviewed_at = datetime.now(timezone.utc)
            if ease_factor is not None:
                row.ease_factor = ease_factor
            if is_correct is True:
                row.correct_count = row.correct_count + 1
            elif is_correct is False:
                row.incorrect_count = row.incorrect_count + 1
            uid = uid or str(row.user_id)

    # 更新连续学习天数（使用独立 session）
    if uid:
        update_streak(uid)


def get_vocab_detail(record_id: str) -> dict | None:
    """按主键取单条完整词汇记录"""
    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        return _row_to_dict(row) if row else None


def delete_vocab_by_id(record_id: str) -> bool:
    """按主键删除单条词汇记录"""
    with _session() as session:
        row = session.query(VocabRecord).filter_by(id=uuid.UUID(record_id)).first()
        if row is None:
            return False
        session.delete(row)
        return True


def delete_vocab_by_word(
    user_id: str, word: str, target_language: str | None = None
) -> int:
    """按用户 + 单词（大小写不敏感）删除全部匹配记录，返回删除条数"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        query = session.query(VocabRecord).filter(
            VocabRecord.user_id == uid,
            VocabRecord.word.ilike(word),
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.all()
        for row in rows:
            session.delete(row)
        return len(rows)


def update_vocab_fields(record_id: str, fields: dict) -> bool:
    """更新词汇的可编辑字段，返回是否成功"""
    allowed = {"pos", "definition", "context", "notes", "tags", "cefr_level"}
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


def get_vocab_by_word(
    user_id: str, word: str, target_language: str | None = None
) -> list[dict]:
    """按用户 + 单词（大小写不敏感）查询所有匹配记录"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        query = session.query(
            VocabRecord.id,
            VocabRecord.word,
            VocabRecord.pos,
            VocabRecord.definition,
            VocabRecord.level,
            VocabRecord.context,
            VocabRecord.next_review,
            VocabRecord.cefr_level,
        ).filter(
            VocabRecord.user_id == uid,
            VocabRecord.word.ilike(word),
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.all()
        return [_convert_row_dict(r._asdict()) for r in rows]


def get_practice_vocab(
    user_id: str,
    limit: int = 100,
    target_language: str = "en",
) -> list[dict]:
    """获取词库中随机词汇，不限 next_review，供练习模式使用"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        rows = (
            session.query(VocabRecord)
            .filter(
                VocabRecord.user_id == uid,
                VocabRecord.target_language == target_language,
            )
            .order_by(func.random())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def get_random_words_for_distractor(
    user_id: str,
    exclude_id: str,
    count: int = 3,
    target_language: str = "en",
) -> list[dict]:
    """随机取 count 条同语言的不同词作为干扰项"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        rows = (
            session.query(VocabRecord.id, VocabRecord.word, VocabRecord.definition)
            .filter(
                VocabRecord.user_id == uid,
                VocabRecord.target_language == target_language,
                VocabRecord.id != uuid.UUID(exclude_id),
            )
            .order_by(func.random())
            .limit(count * 3)
            .all()
        )
        return [_convert_row_dict(r._asdict()) for r in rows]


def get_all_vocab(user_id: str, target_language: str | None = None) -> list[dict]:
    """获取用户全部词汇（不分页），用于导出"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        query = session.query(
            VocabRecord.word,
            VocabRecord.pos,
            VocabRecord.definition,
            VocabRecord.context,
            VocabRecord.level,
            VocabRecord.next_review,
            VocabRecord.created_at,
            VocabRecord.target_language,
            VocabRecord.cefr_level,
            VocabRecord.tags,
        ).filter(VocabRecord.user_id == uid)
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.order_by(VocabRecord.created_at.desc()).all()
        return [_convert_row_dict(r._asdict()) for r in rows]


def get_vocab_count_by_language(user_id: str) -> dict[str, int]:
    """返回各语言词汇数量 {lang_code: count}"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        rows = (
            session.query(VocabRecord.target_language, func.count(VocabRecord.id))
            .filter(VocabRecord.user_id == uid)
            .group_by(VocabRecord.target_language)
            .all()
        )
        return {lang: cnt for lang, cnt in rows}


def get_level_distribution(
    user_id: str, target_language: str | None = None
) -> dict[int, int]:
    """返回各级别词汇数量 {level: count}"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        query = session.query(
            VocabRecord.level, func.count(VocabRecord.id)
        ).filter(VocabRecord.user_id == uid)
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        rows = query.group_by(VocabRecord.level).all()
        return {lvl: cnt for lvl, cnt in rows}


def get_today_add_count(
    user_id: str, target_language: str | None = None
) -> int:
    """统计该用户今天（UTC 日期）新增的词汇条数"""
    uid = uuid.UUID(user_id)
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with _session() as session:
        query = session.query(func.count(VocabRecord.id)).filter(
            VocabRecord.user_id == uid,
            VocabRecord.created_at >= today_start,
        )
        if target_language is not None:
            query = query.filter(VocabRecord.target_language == target_language)
        return query.scalar() or 0


# ── 复习日志 CRUD ─────────────────────────────────────────────────────────────

def create_review_log(
    user_id: str,
    vocab_id: str,
    quiz_type: str,
    is_correct: bool,
    correct_answer: str,
    user_answer: str | None = None,
    response_time_ms: int | None = None,
) -> dict:
    """记录一次答题日志"""
    with _session() as session:
        log = ReviewLog(
            user_id=uuid.UUID(user_id),
            vocab_id=uuid.UUID(vocab_id),
            quiz_type=quiz_type,
            is_correct=is_correct,
            user_answer=user_answer,
            correct_answer=correct_answer,
            response_time_ms=response_time_ms,
        )
        session.add(log)
        session.flush()
        return _row_to_dict(log)


def get_review_logs(
    user_id: str,
    vocab_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """获取用户复习日志，可按词汇过滤"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        query = session.query(ReviewLog).filter(ReviewLog.user_id == uid)
        if vocab_id is not None:
            query = query.filter(ReviewLog.vocab_id == uuid.UUID(vocab_id))
        rows = query.order_by(ReviewLog.created_at.desc()).limit(limit).all()
        return [_row_to_dict(r) for r in rows]


def get_review_stats(user_id: str, days: int = 7) -> dict:
    """获取用户近 N 天复习统计：总题数、正确数、各题型正确率"""
    uid = uuid.UUID(user_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with _session() as session:
        logs = (
            session.query(ReviewLog)
            .filter(ReviewLog.user_id == uid, ReviewLog.created_at >= since)
            .all()
        )
        total = len(logs)
        correct = sum(1 for l in logs if l.is_correct)
        # 各题型统计
        by_type: dict[str, dict] = {}
        for log in logs:
            if log.quiz_type not in by_type:
                by_type[log.quiz_type] = {"total": 0, "correct": 0}
            by_type[log.quiz_type]["total"] += 1
            if log.is_correct:
                by_type[log.quiz_type]["correct"] += 1

        return {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "by_type": by_type,
        }


# ── 订阅系统 ──────────────────────────────────────────────────────────────────

def is_pro(user_id: str) -> bool:
    """判断用户是否处于有效订阅状态；管理员默认返回 True"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        # 管理员永远 Pro
        user = session.query(User).filter_by(id=uid).first()
        if user and user.is_admin:
            return True
        # 查是否有 active 且未过期的订阅
        now = datetime.now(timezone.utc)
        active_sub = (
            session.query(Subscription)
            .filter(
                Subscription.user_id == uid,
                Subscription.status == "active",
                Subscription.expires_at > now,
            )
            .first()
        )
        return active_sub is not None


def get_active_subscription(user_id: str) -> dict | None:
    """获取用户当前有效订阅，无则返回 None"""
    uid = uuid.UUID(user_id)
    now = datetime.now(timezone.utc)
    with _session() as session:
        row = (
            session.query(Subscription)
            .filter(
                Subscription.user_id == uid,
                Subscription.status == "active",
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .first()
        )
        return _row_to_dict(row) if row else None


def create_subscription(
    user_id: str,
    plan: str,
    payment_provider: str,
    starts_at: datetime | None = None,
    duration_days: int = 30,
    provider_order_id: str | None = None,
    amount: int | None = None,
    currency: str | None = None,
) -> dict:
    """
    创建订阅记录。
    如已有活跃订阅，新订阅的 starts_at 从旧订阅 expires_at 开始（无缝续期）。
    """
    uid = uuid.UUID(user_id)
    now = datetime.now(timezone.utc)

    with _session() as session:
        # 无缝续期：若已有活跃订阅，从其到期时间起算
        if starts_at is None:
            active = (
                session.query(Subscription)
                .filter(
                    Subscription.user_id == uid,
                    Subscription.status == "active",
                    Subscription.expires_at > now,
                )
                .order_by(Subscription.expires_at.desc())
                .first()
            )
            starts_at = active.expires_at if active else now

        expires_at = starts_at + timedelta(days=duration_days)

        sub = Subscription(
            user_id=uid,
            plan=plan,
            status="active",
            starts_at=starts_at,
            expires_at=expires_at,
            payment_provider=payment_provider,
            provider_order_id=provider_order_id,
            amount=amount,
            currency=currency,
        )
        session.add(sub)
        session.flush()
        return _row_to_dict(sub)


def cancel_subscription(subscription_id: str) -> bool:
    """取消订阅（标记为 cancelled）"""
    with _session() as session:
        row = session.query(Subscription).filter_by(
            id=uuid.UUID(subscription_id)
        ).first()
        if row is None:
            return False
        row.status = "cancelled"
        return True


def activate_code(user_id: str, code: str) -> tuple[bool, str]:
    """
    核销激活码，创建订阅记录。
    返回 (success: bool, message: str)。
    """
    try:
        uid = uuid.UUID(user_id)
        now = datetime.now(timezone.utc)

        with _session() as session:
            code_row = session.query(ActivationCode).filter_by(code=code).first()
            if code_row is None:
                return False, "激活码不存在，请检查是否输入正确。"
            if code_row.used_by:
                return False, "该激活码已被使用，无法重复激活。"
            if code_row.expires_at and code_row.expires_at < now:
                return False, "该激活码已过期。"

            # 计算订阅起止时间（无缝续期）
            active = (
                session.query(Subscription)
                .filter(
                    Subscription.user_id == uid,
                    Subscription.status == "active",
                    Subscription.expires_at > now,
                )
                .order_by(Subscription.expires_at.desc())
                .first()
            )
            starts = active.expires_at if active else now
            expires = starts + timedelta(days=code_row.duration_days)

            # 创建订阅
            sub = Subscription(
                user_id=uid,
                plan=code_row.plan,
                status="active",
                starts_at=starts,
                expires_at=expires,
                payment_provider="activation_code",
                provider_order_id=code,
            )
            session.add(sub)

            # 核销激活码
            code_row.used_by = uid
            code_row.used_at = now

            return True, expires.strftime("%Y-%m-%d")
    except Exception:
        return False, "激活码功能异常，请联系管理员。"


def generate_codes(
    duration_days: int,
    count: int,
    plan: str = "monthly",
    purpose: str | None = None,
    expires_at: datetime | None = None,
) -> list[str]:
    """生成 count 个随机激活码（8位大写字母+数字），写入 DB 并返回码列表"""
    alphabet = string.ascii_uppercase + string.digits
    codes: list[str] = []
    with _session() as session:
        for _ in range(count):
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            session.add(ActivationCode(
                code=code,
                plan=plan,
                duration_days=duration_days,
                purpose=purpose,
                expires_at=expires_at,
            ))
            codes.append(code)
    return codes


def get_expiring_subscriptions(within_days: int = 3) -> list[dict]:
    """返回将在 within_days 天内到期的活跃订阅列表"""
    with _session() as session:
        now = datetime.now(timezone.utc)
        upper = now + timedelta(days=within_days)
        rows = (
            session.query(Subscription)
            .filter(
                Subscription.status == "active",
                Subscription.expires_at > now,
                Subscription.expires_at <= upper,
            )
            .all()
        )
        return [_row_to_dict(r) for r in rows]


# ── 用户设置 ──────────────────────────────────────────────────────────────────

def get_user_settings(user_id: str) -> dict:
    """获取用户设置，不存在时返回默认值"""
    uid = uuid.UUID(user_id)
    defaults = {
        "timezone": "UTC",
        "remind_start": 8,
        "remind_end": 22,
        "remind_enabled": True,
        "active_language": "en",
        "learning_languages": ["en"],
        "daily_goal": 10,
        "streak_days": 0,
        "last_active_date": None,
        "review_active_only": True,
        "proficiency_level": 0,
        "assessment_done": False,
    }
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(user_id=uid).first()
            if row is None:
                return defaults
            return {
                "timezone":           row.timezone,
                "remind_start":       row.remind_start,
                "remind_end":         row.remind_end,
                "remind_enabled":     row.remind_enabled,
                "active_language":    row.active_language,
                "learning_languages": row.learning_languages or ["en"],
                "daily_goal":         row.daily_goal,
                "streak_days":        row.streak_days or 0,
                "last_active_date":   row.last_active_date.isoformat() if row.last_active_date else None,
                "review_active_only": row.review_active_only,
                "proficiency_level":  row.proficiency_level or 0,
                "assessment_done":    row.assessment_done or False,
            }
    except Exception:
        return defaults


def update_user_settings(user_id: str, **fields) -> bool:
    """通用设置更新函数，支持任意设置字段"""
    allowed = {
        "timezone", "remind_enabled", "remind_start", "remind_end",
        "active_language", "learning_languages", "daily_goal",
        "review_active_only", "proficiency_level", "assessment_done",
    }
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return False
    uid = uuid.UUID(user_id)
    with _session() as session:
        row = session.query(UserSettings).filter_by(user_id=uid).first()
        if row is None:
            # 自动创建设置记录
            row = UserSettings(user_id=uid, **safe_fields)
            session.add(row)
        else:
            for k, v in safe_fields.items():
                setattr(row, k, v)
        return True


def add_learning_language(user_id: str, lang: str) -> None:
    """追加新语言到 learning_languages 数组（已存在则跳过）"""
    uid = uuid.UUID(user_id)
    with _session() as session:
        row = session.query(UserSettings).filter_by(user_id=uid).first()
        if row:
            current = row.learning_languages or ["en"]
            if lang not in current:
                row.learning_languages = current + [lang]
        else:
            langs = ["en"] if lang == "en" else ["en", lang]
            session.add(UserSettings(user_id=uid, learning_languages=langs))


def update_streak(user_id: str) -> None:
    """
    更新用户连续学习天数（streak_days）和最后活跃日期（last_active_date）。
    - 今天已更新过 → 不变
    - 昨天是 last_active_date → streak_days += 1
    - 超过昨天（断掉）→ streak_days = 1
    """
    today = datetime.now(timezone.utc).date()
    uid = uuid.UUID(user_id)
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(user_id=uid).first()
            if row:
                last_date = row.last_active_date
                streak = row.streak_days or 0

                if last_date:
                    if last_date == today:
                        return
                    elif (today - last_date).days == 1:
                        streak += 1
                    else:
                        streak = 1
                else:
                    streak = 1

                row.streak_days = streak
                row.last_active_date = today
            else:
                session.add(UserSettings(
                    user_id=uid,
                    streak_days=1,
                    last_active_date=today,
                ))
    except Exception:
        return


def get_streak(user_id: str) -> tuple[int, str | None]:
    """获取用户连续学习天数和最后活跃日期"""
    uid = uuid.UUID(user_id)
    try:
        with _session() as session:
            row = session.query(UserSettings).filter_by(user_id=uid).first()
            if not row:
                return 0, None
            last = row.last_active_date
            return (row.streak_days or 0), (last.isoformat() if last else None)
    except Exception:
        return 0, None


# ── 管理员 ────────────────────────────────────────────────────────────────────

def get_admin_stats() -> dict:
    """返回管理员统计数据"""
    with _session() as session:
        total_users = session.query(func.count(User.id)).scalar() or 0
        total_words = session.query(func.count(VocabRecord.id)).scalar() or 0

        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        active_7d = (
            session.query(func.count(distinct(VocabRecord.user_id)))
            .filter(VocabRecord.created_at >= seven_days_ago)
            .scalar() or 0
        )

        now = datetime.now(timezone.utc)
        pro_users = (
            session.query(func.count(distinct(Subscription.user_id)))
            .filter(
                Subscription.status == "active",
                Subscription.expires_at > now,
            )
            .scalar() or 0
        )

        return {
            "total_users": total_users,
            "active_7d": active_7d,
            "pro_users": pro_users,
            "total_words": total_words,
        }


def get_all_user_ids() -> list[str]:
    """返回所有用户 ID 列表"""
    with _session() as session:
        rows = session.query(User.id).all()
        return [str(r.id) for r in rows]


def check_db_connection() -> tuple[bool, str]:
    """测试数据库连接"""
    try:
        with _session() as session:
            session.execute(text("SELECT 1"))
        return True, "连接正常"
    except Exception as exc:
        return False, str(exc)[:80]
