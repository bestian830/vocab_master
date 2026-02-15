-- Vocab Master 数据库 Schema
-- 在 Supabase SQL Editor 中执行此文件

CREATE TABLE IF NOT EXISTS vocab_records (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_id  text        NOT NULL,
  word         text        NOT NULL,
  pos          text,                          -- 词性: noun / verb / adj / adv / phrase
  definition   text        NOT NULL,          -- 中文释义
  context      text,                          -- AI 生成的例句
  level        int         NOT NULL DEFAULT 0, -- 复习级别 0-7
  next_review  timestamptz NOT NULL DEFAULT now(), -- 下次复习时间
  ease_factor  float       NOT NULL DEFAULT 2.5,   -- 保留字段，供后续 SM-2 迭代使用
  review_count int         NOT NULL DEFAULT 0,     -- 已复习次数
  created_at   timestamptz NOT NULL DEFAULT now(),

  -- 同一用户的同一单词+释义只入库一次（允许同词多义）
  UNIQUE(telegram_id, word, definition)
);

-- 查询效率索引
CREATE INDEX IF NOT EXISTS idx_vocab_telegram_id    ON vocab_records(telegram_id);
CREATE INDEX IF NOT EXISTS idx_vocab_next_review    ON vocab_records(next_review);
CREATE INDEX IF NOT EXISTS idx_vocab_user_due
  ON vocab_records(telegram_id, next_review);

-- ── 订阅系统 ──────────────────────────────────────────────────────────────────

-- 用户订阅状态（Pro 到期后自动降回免费）
CREATE TABLE IF NOT EXISTS subscriptions (
  telegram_id  text        PRIMARY KEY,
  expires_at   timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- 激活码池（线下收款后由管理员生成）
CREATE TABLE IF NOT EXISTS activation_codes (
  code          text        PRIMARY KEY,
  duration_days int         NOT NULL,   -- 激活后有效天数（30 / 365 等）
  used_by       text,                   -- 使用者 telegram_id
  used_at       timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ── 用户设置 ──────────────────────────────────────────────────────────────────

-- 存储每个用户的时区和复习提醒时间窗口
CREATE TABLE IF NOT EXISTS user_settings (
  telegram_id       text  PRIMARY KEY,
  timezone          text  NOT NULL DEFAULT 'UTC',     -- IANA 时区名，如 Asia/Shanghai
  remind_start      int     NOT NULL DEFAULT 8,         -- 推送开始小时（本地时间，0-23）
  remind_end        int     NOT NULL DEFAULT 22,        -- 推送结束小时（本地时间，0-23）
  remind_enabled    boolean NOT NULL DEFAULT TRUE,      -- 是否开启自动复习推送
  streak_days       int   NOT NULL DEFAULT 0,           -- 连续学习天数
  last_active_date  date,                               -- 最后一次有复习操作的日期（UTC）
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- 若表已存在，按需追加 streak 相关字段
ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS streak_days      INT  NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_active_date DATE;

-- 新增 remind_enabled 字段（已有库执行此语句）
ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS remind_enabled BOOLEAN NOT NULL DEFAULT TRUE;
