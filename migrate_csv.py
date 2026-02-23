"""
CSV → PostgreSQL 数据迁移脚本
将旧 Supabase 导出的 4 个 CSV 文件导入到新 Oracle Cloud PostgreSQL 数据库。

用法：
  # 使用环境变量 DATABASE_URL，CSV 文件放在 ./csv_data/ 目录下
  python migrate_csv.py --dir ./csv_data/

  # 手动指定各文件路径和数据库连接
  python migrate_csv.py \
    --vocab      vocab_records.csv \
    --settings   user_settings.csv \
    --subscriptions subscriptions.csv \
    --codes      activation_codes.csv \
    --db "postgresql://vocab_user:xxx@40.233.69.38:5432/vocab_master"
"""

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

# 加载 .env（如果存在）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── 数据清理工具函数 ───────────────────────────────────────────────────────────

def _parse_uuid(s: str) -> uuid.UUID | None:
    """解析 UUID 字符串，空值返回 None"""
    s = s.strip()
    if not s:
        return None
    return uuid.UUID(s)


def _parse_datetime(s: str) -> datetime | None:
    """解析 ISO8601 时间字符串，空值返回 None"""
    s = s.strip()
    if not s:
        return None
    # 兼容多种格式：
    #   'Z' 后缀 → '+00:00'
    #   空格分隔（'2026-02-15 05:40:23.076711+00'） → 'T' 分隔
    #   '+00' 非标准后缀 → '+00:00'
    s = s.replace("Z", "+00:00").replace(" ", "T")
    if s.endswith("+00") and not s.endswith("+00:00"):
        s = s + ":00"
    return datetime.fromisoformat(s)


def _parse_date(s: str):
    """解析 YYYY-MM-DD 日期字符串，空值返回 None"""
    from datetime import date
    s = s.strip()
    if not s:
        return None
    return date.fromisoformat(s)


def _parse_pg_array(s: str) -> list[str] | None:
    """
    解析 PostgreSQL 数组格式：{en,zh} → ['en', 'zh']
    空值返回 None。
    """
    s = s.strip()
    if not s or s == "{}":
        return None
    # 去掉首尾的 {}
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1]
        # 分割并去除引号（Supabase 对含空格的元素加引号）
        items = []
        for item in inner.split(","):
            item = item.strip().strip('"')
            if item:
                items.append(item)
        return items if items else None
    return None


def _parse_json_array(s: str) -> list[str] | None:
    """
    解析 JSON 数组格式：["en","zh"] → ['en', 'zh']
    空值返回 None。
    """
    s = s.strip()
    if not s or s == "[]":
        return None
    try:
        result = json.loads(s)
        return result if isinstance(result, list) and result else None
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_bool(s: str) -> bool | None:
    """
    解析布尔值：Supabase CSV 导出为 't'/'f' 或 'true'/'false'，空值返回 None
    """
    s = s.strip().lower()
    if not s:
        return None
    if s in ("t", "true", "1", "yes"):
        return True
    if s in ("f", "false", "0", "no"):
        return False
    return None


def _parse_int(s: str) -> int | None:
    """解析整数，空值返回 None"""
    s = s.strip()
    if not s:
        return None
    return int(s)


def _parse_float(s: str) -> float | None:
    """解析浮点数，空值返回 None"""
    s = s.strip()
    if not s:
        return None
    return float(s)


def _parse_str(s: str) -> str | None:
    """空字符串转 None"""
    s = s.strip()
    return s if s else None


# ── 各表 CSV → dict 转换 ──────────────────────────────────────────────────────

def _convert_vocab_row(row: dict) -> dict:
    """将 vocab_records CSV 行转为 ORM 可插入的 dict"""
    return {
        "id":              _parse_uuid(row["id"]),
        "telegram_id":     row["telegram_id"].strip(),
        "word":            row["word"].strip(),
        "pos":             _parse_str(row.get("pos", "")),
        "definition":      row["definition"].strip(),
        "context":         _parse_str(row.get("context", "")),
        "target_language": row.get("target_language", "en").strip() or "en",
        "native_language": row.get("native_language", "zh").strip() or "zh",
        "level":           _parse_int(row.get("level", "0")) or 0,
        "next_review":     _parse_datetime(row.get("next_review", "")),
        "ease_factor":     _parse_float(row.get("ease_factor", "")) or 2.5,
        "review_count":    _parse_int(row.get("review_count", "0")) or 0,
        "created_at":      _parse_datetime(row.get("created_at", "")),
        "word_level":      _parse_int(row.get("word_level", "")),
        "quiz_synonyms":   _parse_pg_array(row.get("quiz_synonyms", "")),
        "antonyms":        _parse_pg_array(row.get("antonyms", "")),
        "word_family":     _parse_pg_array(row.get("word_family", "")),
        "etymology":       _parse_str(row.get("etymology", "")),
        "collocations":    _parse_pg_array(row.get("collocations", "")),
    }


def _convert_settings_row(row: dict) -> dict:
    """将 user_settings CSV 行转为 ORM 可插入的 dict"""
    return {
        "telegram_id":        row["telegram_id"].strip(),
        "timezone":           _parse_str(row.get("timezone", "")) or "UTC",
        "remind_start":       _parse_int(row.get("remind_start", "")) if row.get("remind_start", "").strip() else 8,
        "remind_end":         _parse_int(row.get("remind_end", "")) if row.get("remind_end", "").strip() else 22,
        "remind_enabled":     _parse_bool(row.get("remind_enabled", "t")),
        "streak_days":        _parse_int(row.get("streak_days", "")) or 0,
        "last_active_date":   _parse_date(row.get("last_active_date", "")),
        "native_language":    _parse_str(row.get("native_language", "")) or "zh",
        "active_language":    _parse_str(row.get("active_language", "")) or "en",
        "learning_languages": _parse_json_array(row.get("learning_languages", "")) or ["en"],
        "updated_at":         _parse_datetime(row.get("updated_at", "")),
        "proficiency_level":  _parse_int(row.get("proficiency_level", "")) or 0,
        "assessment_done":    _parse_bool(row.get("assessment_done", "f")) or False,
    }


def _convert_subscription_row(row: dict) -> dict:
    """将 subscriptions CSV 行转为 ORM 可插入的 dict"""
    return {
        "telegram_id": row["telegram_id"].strip(),
        "expires_at":  _parse_datetime(row.get("expires_at", "")),
        "created_at":  _parse_datetime(row.get("created_at", "")),
    }


def _convert_code_row(row: dict) -> dict:
    """将 activation_codes CSV 行转为 ORM 可插入的 dict"""
    return {
        "code":          row["code"].strip(),
        "duration_days": _parse_int(row.get("duration_days", "")),
        "used_by":       _parse_str(row.get("used_by", "")),
        "used_at":       _parse_datetime(row.get("used_at", "")),
        "created_at":    _parse_datetime(row.get("created_at", "")),
    }


# ── 主迁移逻辑 ────────────────────────────────────────────────────────────────

def migrate(
    db_url: str,
    vocab_path: Path | None,
    settings_path: Path | None,
    subscriptions_path: Path | None,
    codes_path: Path | None,
) -> None:
    """连接数据库，建表，然后按顺序导入各 CSV"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from database.client import (
        Base, VocabRecord, UserSettings, Subscription, ActivationCode
    )
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    print(f"[1/5] 连接数据库：{db_url[:40]}...")
    engine = create_engine(db_url, pool_pre_ping=True)

    print("[2/5] 初始化数据库（建表）...")
    # 直接用 engine 建表，避免 init_db() 内部使用不同 URL 的 engine
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE vocab_records
              ADD COLUMN IF NOT EXISTS word_level    INTEGER,
              ADD COLUMN IF NOT EXISTS quiz_synonyms TEXT[],
              ADD COLUMN IF NOT EXISTS antonyms      TEXT[],
              ADD COLUMN IF NOT EXISTS word_family   TEXT[],
              ADD COLUMN IF NOT EXISTS etymology     TEXT,
              ADD COLUMN IF NOT EXISTS collocations  TEXT[]
        """))
        conn.execute(text("""
            ALTER TABLE user_settings
              ADD COLUMN IF NOT EXISTS proficiency_level INTEGER NOT NULL DEFAULT 0,
              ADD COLUMN IF NOT EXISTS assessment_done   BOOLEAN NOT NULL DEFAULT FALSE
        """))
        conn.commit()
    print("      建表完成。")

    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # ── user_settings ──────────────────────────────────────────────────────────
    if settings_path and settings_path.exists():
        print(f"[3/5] 导入 user_settings：{settings_path}")
        _import_csv(
            engine, Session, pg_insert, UserSettings,
            settings_path, _convert_settings_row,
            conflict_columns=["telegram_id"],
        )
    else:
        print("[3/5] 跳过 user_settings（文件不存在）")

    # ── vocab_records ──────────────────────────────────────────────────────────
    if vocab_path and vocab_path.exists():
        print(f"[4/5] 导入 vocab_records：{vocab_path}")
        _import_csv(
            engine, Session, pg_insert, VocabRecord,
            vocab_path, _convert_vocab_row,
            conflict_columns=["id"],
        )
    else:
        print("[4/5] 跳过 vocab_records（文件不存在）")

    # ── activation_codes ───────────────────────────────────────────────────────
    if codes_path and codes_path.exists():
        print(f"[4b] 导入 activation_codes：{codes_path}")
        _import_csv(
            engine, Session, pg_insert, ActivationCode,
            codes_path, _convert_code_row,
            conflict_columns=["code"],
        )
    else:
        print("[4b] 跳过 activation_codes（文件不存在）")

    # ── subscriptions ──────────────────────────────────────────────────────────
    if subscriptions_path and subscriptions_path.exists():
        print(f"[5/5] 导入 subscriptions：{subscriptions_path}")
        _import_csv(
            engine, Session, pg_insert, Subscription,
            subscriptions_path, _convert_subscription_row,
            conflict_columns=["telegram_id"],
        )
    else:
        print("[5/5] 跳过 subscriptions（文件不存在）")

    print("\n迁移完成！")


def _import_csv(
    engine,
    Session,
    pg_insert,
    model_cls,
    csv_path: Path,
    converter,
    conflict_columns: list[str],
    batch_size: int = 200,
) -> None:
    """读取 CSV，批量 upsert（ON CONFLICT DO NOTHING）"""
    rows_read = 0
    rows_inserted = 0
    rows_skipped = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        batch = []

        for raw_row in reader:
            rows_read += 1
            try:
                converted = converter(raw_row)
                # 过滤掉值为 None 的可选字段，但必填字段保留
                batch.append(converted)
            except Exception as e:
                print(f"      [WARN] 跳过第 {rows_read} 行（解析失败）: {e}")
                rows_skipped += 1
                continue

            if len(batch) >= batch_size:
                ins, skp = _execute_batch(engine, pg_insert, model_cls, batch, conflict_columns)
                rows_inserted += ins
                rows_skipped += skp
                batch = []

        # 处理最后一批
        if batch:
            ins, skp = _execute_batch(engine, pg_insert, model_cls, batch, conflict_columns)
            rows_inserted += ins
            rows_skipped += skp

    print(f"      读取 {rows_read} 行，插入 {rows_inserted} 条，冲突跳过 {rows_skipped} 条")


def _execute_batch(
    engine,
    pg_insert,
    model_cls,
    batch: list[dict],
    conflict_columns: list[str],
) -> tuple[int, int]:
    """执行一批 INSERT ... ON CONFLICT DO NOTHING，返回 (inserted, skipped)"""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    inserted = 0
    skipped = 0
    try:
        stmt = pg_insert(model_cls).values(batch)
        stmt = stmt.on_conflict_do_nothing()
        result = session.execute(stmt)
        inserted = result.rowcount if result.rowcount >= 0 else len(batch)
        skipped = len(batch) - inserted
        session.commit()
    except Exception as e:
        session.rollback()
        # 逐行重试以定位问题行
        for row in batch:
            s2 = Session()
            try:
                st2 = pg_insert(model_cls).values([row])
                st2 = st2.on_conflict_do_nothing()
                r2 = s2.execute(st2)
                inserted += r2.rowcount if r2.rowcount >= 0 else 1
                s2.commit()
            except Exception as e2:
                s2.rollback()
                print(f"      [WARN] 插入失败：{e2} | 数据：{row}")
                skipped += 1
            finally:
                s2.close()
    finally:
        session.close()
    return inserted, skipped


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="将 Supabase 导出的 CSV 迁移到 PostgreSQL 数据库"
    )
    parser.add_argument(
        "--dir",
        help="CSV 文件目录（自动查找 vocab_records.csv / user_settings.csv / "
             "subscriptions.csv / activation_codes.csv）",
    )
    parser.add_argument("--vocab",         help="vocab_records CSV 文件路径")
    parser.add_argument("--settings",      help="user_settings CSV 文件路径")
    parser.add_argument("--subscriptions", help="subscriptions CSV 文件路径")
    parser.add_argument("--codes",         help="activation_codes CSV 文件路径")
    parser.add_argument(
        "--db",
        help="数据库连接字符串（默认读取环境变量 DATABASE_URL）",
    )
    args = parser.parse_args()

    # 确定数据库 URL
    db_url = args.db or os.environ.get("DATABASE_URL")
    if not db_url:
        print("错误：请通过 --db 参数或 DATABASE_URL 环境变量指定数据库连接字符串")
        sys.exit(1)

    # 解析 CSV 路径
    def resolve(explicit: str | None, default_name: str) -> Path | None:
        if explicit:
            return Path(explicit)
        if args.dir:
            p = Path(args.dir) / default_name
            return p if p.exists() else None
        return None

    vocab_path         = resolve(args.vocab,         "vocab_records.csv")
    settings_path      = resolve(args.settings,      "user_settings.csv")
    subscriptions_path = resolve(args.subscriptions, "subscriptions.csv")
    codes_path         = resolve(args.codes,         "activation_codes.csv")

    if not any([vocab_path, settings_path, subscriptions_path, codes_path]):
        print("警告：未找到任何 CSV 文件，请检查 --dir 目录或手动指定文件路径")
        sys.exit(1)

    migrate(db_url, vocab_path, settings_path, subscriptions_path, codes_path)


if __name__ == "__main__":
    main()
