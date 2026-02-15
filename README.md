# Vocab Master

一个基于 Telegram 的英语词汇记忆 Bot，利用**艾宾浩斯遗忘曲线**（SM-2 算法）自动安排复习，让单词真正记住而不是背了忘、忘了背。

> 截图占位：_（待补充）_

---

## 功能概览

### 用户命令

| 命令 | 说明 |
|------|------|
| `/start` | 欢迎消息 + 使用说明 |
| `/vocab` | 分页浏览词库（支持 Inline 翻页） |
| `/review` | 手动触发一道复习题 |
| `/plan` | 查看订阅状态与词库统计 |
| `/activate <码>` | 激活 Pro 订阅码 |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/gencode <天数> <数量>` | 批量生成激活码（最多 50 个） |
| `/extend <telegram_id> <天数>` | 直接为指定用户续期 |

### 使用流程

1. **添加词汇**
   - 发送单词：`devastated` → Bot 返回词性 + 释义 + 例句，自动入库
   - 发送句子：`I was utterly devastated by the news` → Bot 翻译整句，提取关键词，用户点选要记的词
   - 发送中文：`苟且偷生` → Bot 找到对应英文表达

2. **定时复习**
   - Bot 后台每小时检查到期词汇，自动推送复习题
   - 也可随时发送 `/review` 手动触发

3. **两种题型**
   - 🧠 **填空题**：看例句 + 释义提示，从 4 个选项中选出目标单词
   - 🔤 **选义题**：看单词 + 例句，从 4 个选项中选出正确中文释义

4. **间隔升级**
   - 答对 → 升一级，按 SM-2 时间表延长复习间隔（1/2/4/7/14/30/90/3650 天）
   - 答错 → 降回 level 0，明天重新复习

---

## 技术架构

| 层级 | 技术 |
|------|------|
| Bot 框架 | python-telegram-bot v20（async） |
| 数据库 | Supabase（PostgreSQL） |
| AI 服务 | DeepSeek API（兼容 OpenAI 接口） |
| 调度器 | APScheduler（AsyncIOScheduler） |
| 部署目标 | Railway（Webhook 模式） |
| 运行环境 | Python 3.11+ |

---

## 快速上手（本地开发）

### 前置条件

- Python 3.11+
- [Supabase](https://supabase.com) 账号（免费套餐即可）
- Telegram Bot Token（通过 [@BotFather](https://t.me/BotFather) 创建）
- DeepSeek API Key（或其他兼容 OpenAI 接口的 AI 服务）

### 步骤

```bash
# 1. 克隆项目
git clone <repo-url>
cd vocab_master

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入所有必填变量

# 4. 初始化数据库
# 在 Supabase 控制台 → SQL Editor 中执行：
# database/schema.sql

# 5. 启动 Bot
python main.py
```

---

## 环境变量说明

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | BotFather 提供的 Bot Token |
| `SUPABASE_URL` | ✅ | — | Supabase 项目 URL |
| `SUPABASE_ANON_KEY` | ✅ | — | Supabase anon/public key |
| `DEEPSEEK_API_KEY` | ✅ | — | AI 服务 API Key |
| `AI_BASE_URL` | ❌ | `https://api.deepseek.com/v1` | 兼容 OpenAI 接口的 base URL |
| `AI_MODEL` | ❌ | `deepseek-chat` | 使用的模型名称 |
| `SCHEDULER_INTERVAL_MINUTES` | ❌ | `60` | 调度器检查间隔（分钟） |
| `ADMIN_TELEGRAM_ID` | ❌ | — | 管理员 Telegram 用户 ID，留空则禁用管理员功能 |

---

## 数据库初始化

在 Supabase 控制台的 **SQL Editor** 中执行 [`database/schema.sql`](database/schema.sql)，该文件会创建所需的全部表和索引：

- `vocab_records` — 用户词汇记录（含 SM-2 复习状态）
- `subscriptions` — 订阅信息
- `activation_codes` — 激活码

---

## 生产部署（Railway）

1. 在 Railway 创建新项目，连接 GitHub 仓库
2. 在 Railway 的 **Variables** 面板配置所有环境变量
3. 将 `main.py` 中的启动方式切换为 **Webhook 模式**：
   ```python
   # 替换 application.run_polling() 为：
   application.run_webhook(
       listen="0.0.0.0",
       port=int(os.environ.get("PORT", 8443)),
       webhook_url=f"https://<your-railway-domain>/webhook",
   )
   ```
4. Railway 会自动分配域名并保持 Bot 持续运行

---

## 项目结构

```
vocab_master/
├── main.py                  # 入口：注册 handler + 启动调度器 + polling
├── config.py                # 环境变量读取
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板
├── database/
│   ├── client.py            # Supabase CRUD，单例 get_client()
│   └── schema.sql           # 建表 SQL
├── ai/
│   └── parser.py            # parse_user_input() + generate_quiz_sentence()
├── core/
│   ├── sm2.py               # SM-2 算法：next_level_and_review()
│   └── quiz.py              # 测验题生成：build_quiz()
├── bot/
│   ├── keyboards.py         # Inline 键盘：quiz_keyboard(), vocab_page_keyboard()
│   └── handlers/
│       ├── commands.py      # /start /vocab /review /plan /activate /gencode /extend
│       ├── messages.py      # 普通文本 → AI 解析 → 入库
│       └── callbacks.py     # Inline button 回调：答题 + 翻页
└── scheduler/
    └── reminder.py          # 定时推送到期复习：setup_scheduler(bot)
```

---

## License

MIT
