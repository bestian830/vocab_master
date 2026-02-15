"""
全局配置，从环境变量读取
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# Supabase
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY: str = os.environ["SUPABASE_ANON_KEY"]

# AI 服务 (兼容 OpenAI 接口规范)
DEEPSEEK_API_KEY: str = os.environ["DEEPSEEK_API_KEY"]
AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1")
AI_MODEL: str = os.getenv("AI_MODEL", "deepseek-chat")

# 调度器
SCHEDULER_INTERVAL_MINUTES: int = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "60"))

# 订阅系统
ADMIN_TELEGRAM_ID: str = os.getenv("ADMIN_TELEGRAM_ID", "")
FREE_WORD_LIMIT: int = 300
FREE_DAILY_LIMIT: int = 10
