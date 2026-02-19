"""
Vocab Master Bot 主入口
启动方式: python main.py
本地开发使用 polling 模式；生产环境切换为 webhook（见注释）
"""
import logging

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN, PORT, WEBHOOK_URL
from bot.handlers.commands import (
    cmd_start, cmd_help, cmd_vocab, cmd_review, cmd_practice, cmd_streak,
    cmd_activate, cmd_plan, cmd_gencode, cmd_extend, cmd_stats,
    cmd_delete, cmd_search, cmd_export, cmd_timezone, cmd_health,
    cmd_broadcast, cmd_users, cmd_update, cmd_settings, cmd_language,
)
from bot.handlers.messages import handle_text_message
from bot.handlers.callbacks import handle_callback
from scheduler.reminder import setup_scheduler

# ── 日志配置 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    """Bot 启动后注册 Telegram 命令菜单（输入 / 时自动提示）"""
    await app.bot.set_my_commands([
        BotCommand("vocab",    "查看词库"),
        BotCommand("review",   "开始复习"),
        BotCommand("practice", "自由练习"),
        BotCommand("language", "多语言学习管理"),
        BotCommand("search",   "搜索词库"),
        BotCommand("update",   "编辑词汇内容"),
        BotCommand("delete",   "删除单词"),
        BotCommand("stats",    "学习统计"),
        BotCommand("streak",   "连续学习天数"),
        BotCommand("export",   "导出词库"),
        BotCommand("timezone", "设置时区"),
        BotCommand("settings", "通知设置"),
        BotCommand("plan",     "订阅状态"),
        BotCommand("activate", "激活订阅"),
        BotCommand("help",     "使用帮助"),
    ])
    logger.info("Telegram 命令菜单已注册")


def main() -> None:
    # 构建 Application，post_init 在 bot 准备好后自动调用
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

    # ── 注册命令处理器 ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("vocab",    cmd_vocab))
    app.add_handler(CommandHandler("review",   cmd_review))
    app.add_handler(CommandHandler("practice", cmd_practice))
    app.add_handler(CommandHandler("streak",   cmd_streak))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("plan",     cmd_plan))
    app.add_handler(CommandHandler("gencode",  cmd_gencode))
    app.add_handler(CommandHandler("extend",   cmd_extend))
    app.add_handler(CommandHandler("stats",    cmd_stats))
    app.add_handler(CommandHandler("delete",   cmd_delete))
    app.add_handler(CommandHandler("search",   cmd_search))
    app.add_handler(CommandHandler("export",   cmd_export))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CommandHandler("health",    cmd_health))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("users",     cmd_users))
    app.add_handler(CommandHandler("update",    cmd_update))
    app.add_handler(CommandHandler("settings",  cmd_settings))
    app.add_handler(CommandHandler("language",  cmd_language))

    # ── 注册普通文本消息处理器（排除命令）────────────────────────────────────
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )

    # ── 注册 Inline Button 回调处理器 ─────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── 启动调度器 ────────────────────────────────────────────────────────────
    bot = app.bot
    scheduler = setup_scheduler(bot)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler   # 供 /health 访问
    logger.info("APScheduler 已启动")

    # ── 启动 Bot（自动判断模式） ───────────────────────────────────────────────
    # 本地开发不设 WEBHOOK_URL → polling；Koyeb 上设了 WEBHOOK_URL → webhook
    if WEBHOOK_URL:
        logger.info("Bot 启动中（Webhook 模式）…")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}",
        )
    else:
        logger.info("Bot 启动中（Polling 模式）…")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
