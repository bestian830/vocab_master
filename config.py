"""
全局配置，从环境变量读取
Web App 版本：FastAPI + Logto 认证
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 数据库（PostgreSQL via SQLAlchemy）
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/vocab_master"
)

# AI 服务 (兼容 OpenAI 接口规范)
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1")
AI_MODEL: str = os.getenv("AI_MODEL", "deepseek-chat")

# Logto 认证
LOGTO_ENDPOINT: str = os.getenv("LOGTO_ENDPOINT", "")
LOGTO_APP_ID: str = os.getenv("LOGTO_APP_ID", "")
LOGTO_APP_SECRET: str = os.getenv("LOGTO_APP_SECRET", "")

# FastAPI 服务
APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
APP_ENV: str = os.getenv("APP_ENV", "development")
CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

# 订阅系统
FREE_WORD_LIMIT: int = int(os.getenv("FREE_WORD_LIMIT", "300"))
FREE_DAILY_LIMIT: int = int(os.getenv("FREE_DAILY_LIMIT", "10"))

# Stripe 支付（海外）
STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# 调度器
SCHEDULER_INTERVAL_MINUTES: int = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "60"))
