"""
Bot UI 国际化（i18n）模块
- zh / en：使用硬编码字典（零延迟，无 API 消耗）
- 其他语言（ja/ko/fr/de/es/pt/ru/it 等）：调 AI 动态翻译英文原文，带进程内内存缓存
"""
import hashlib
import logging

logger = logging.getLogger(__name__)

# 进程内 UI 翻译缓存：(target_lang, md5(en_text)) → translated_text
_ui_cache: dict[tuple[str, str], str] = {}

# 翻译字典：按语言代码分组，key 为字符串 ID
STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        # ── 帮助文案 ─────────────────────────────────────────────────────────
        "help_text": (
            "*命令列表：*\n"
            "/vocab — 查看你的词库\n"
            "/review — 立即开始复习\n"
            "/practice — 自由练习（不计入进度）\n"
            "/language — 多语言学习管理\n"
            "/search <词> — 搜索词库\n"
            "/export — 导出词库为 CSV\n"
            "/stats — 学习统计\n"
            "/streak — 连续学习天数\n"
            "/update <词> — 编辑词汇的词性/释义/例句\n"
            "/delete <词> — 从词库删除单词\n"
            "/timezone — 设置复习提醒时区\n"
            "/settings — 通知设置（时段/开关）\n"
            "/plan — 查看订阅状态\n"
            "/activate <码> — 激活订阅\n"
            "/help — 显示此帮助信息"
        ),
        # ── 欢迎消息 ─────────────────────────────────────────────────────────
        "start_welcome": (
            "👋 欢迎使用 *Vocab Master*！\n\n"
            "我能帮你记住词汇，使用艾宾浩斯遗忘曲线自动安排复习。\n"
            "支持多种语言：英语、日语、法语等。\n\n"
            "*使用方法：*\n"
            "• 直接发送单词或词组（如 `devastated`）\n"
            "• 发送含目标词的句子（如 `I was utterly devastated`）\n"
            "• 发送中文词语（如 `苹果`），我会找到对应词汇\n\n"
        ),
        "start_tz_prompt": "🌏 先设置时区，以便正确安排复习提醒：",

        # ── 测验题目 ─────────────────────────────────────────────────────────
        "quiz_meaning_title": "🔤 *选义题*",
        "quiz_meaning_instruction": "请选择 *{word}* 在句中的意思：",
        "quiz_fill_title": "🧠 <b>填空题</b>",
        "quiz_fill_instruction": "请选出最适合填入 <b>______</b> 的词：",
        "quiz_fill_hint": "💡 提示：{definition}",

        # ── 测验反馈 ─────────────────────────────────────────────────────────
        "quiz_correct": (
            "✅ *答对啦！*\n\n"
            "*{word}* — {definition}"
            "{context_line}\n\n"
            "🎯 当前级别：{level}\n"
            "📅 下次复习：{date}"
        ),
        "quiz_correct_practice": (
            "✅ *答对啦！*\n\n"
            "*{word}* — {definition}"
            "{context_line}\n\n"
            "🎮 练习模式，不计入复习进度"
        ),
        "quiz_wrong_fill": (
            "❌ *答错了*\n\n"
            "正确答案是：*{word}* — {definition}"
            "{context_line}\n\n"
            "😔 级别降至：{level}\n"
            "📅 明天再复习一次吧！"
        ),
        "quiz_wrong_meaning": (
            "❌ *答错了*\n\n"
            "正确释义是：*{word}* — {definition}"
            "{context_line}\n\n"
            "😔 级别降至：{level}\n"
            "📅 明天再复习一次吧！"
        ),
        "quiz_wrong_fill_practice": (
            "❌ *答错了*\n\n"
            "正确答案是：*{word}* — {definition}"
            "{context_line}\n\n"
            "🎮 练习模式，不计入复习进度"
        ),
        "quiz_wrong_meaning_practice": (
            "❌ *答错了*\n\n"
            "正确释义是：*{word}* — {definition}"
            "{context_line}\n\n"
            "🎮 练习模式，不计入复习进度"
        ),
        "quiz_skip_append": "\n\n⏭ 已跳过，明天继续复习",
        "quiz_fuzzy_append": "\n\n🤔 已标记为模糊，明天继续复习",
        "quiz_done": "🎉 本轮复习完成！保持学习节奏哦～",
        "practice_done": "🎉 练习完成！",
        "quiz_end_review": "复习已结束，随时可以 /review 继续～",
        "quiz_end_practice": "练习已结束，随时可以 /review 继续～",

        # ── 级别描述（用于测验反馈）─────────────────────────────────────────
        "level_0": "初次学习",
        "level_1": "初步记忆",
        "level_2": "短期记忆",
        "level_3": "中期记忆",
        "level_4": "长期记忆",
        "level_5": "深度记忆",
        "level_6": "牢固掌握",
        "level_7": "已掌握 ✓",

        # ── 会话状态 ─────────────────────────────────────────────────────────
        "session_active": (
            "⏳ 你正在进行{mode}，先完成当前题目吧～\n"
            "（点击题目下方的「结束」按钮可提前结束）"
        ),
        "session_mode_review": "复习",
        "session_mode_practice": "练习",
        "pending_quiz_exists": (
            "⏳ 你有一道题正在等待作答，请先回答（或等待15分钟后自动关闭）。"
        ),

        # ── 词库 ─────────────────────────────────────────────────────────────
        "vocab_empty": (
            "{lang_name}词库还是空的～\n"
            "发送任意单词开始积累，或用 /language 切换语言。"
        ),
        "vocab_title": "📚 *{lang_name}词库* ({page}/{total_pages} 页，共 {total} 词)",
        "vocab_click_hint": "点击单词按钮查看详情",
        "vocab_record_not_found": "词汇记录不存在，可能已被删除。",
        "vocab_mastered": "✓ 已掌握",

        # ── 复习/练习流程 ────────────────────────────────────────────────────
        "review_no_vocab": "{lang_name}词库还是空的～\n发送任意单词开始积累吧！",
        "review_no_due_practice": (
            "⏳ {lang_name}当前无到期词汇，进入练习模式（答题不计入复习进度）…"
        ),
        "review_generating": "⏳ 正在生成{lang_name}复习题…",
        "review_error": "生成复习题时出错，请稍后重试。",
        "practice_empty": (
            "{lang_name}词库还是空的～先发送单词积累吧！\n"
            "或使用 /language 切换到其他语言。"
        ),
        "practice_start": "🎮 进入{lang_name}练习模式（答题不计入复习进度）…",
        "practice_error": "生成练习题时出错，请稍后重试。",

        # ── 统计 ─────────────────────────────────────────────────────────────
        "stats_title": "📊 *学习统计*（{lang_name}）",
        "stats_total": "📚 总词数：{count} 词",
        "stats_today_added": "➕ 今日新增：{count} 词",
        "stats_due": "⚡ 待复习：{count} 词",
        "stats_level_dist": "*级别分布：*",
        "stats_level_line": "Lv{level} {label:<4}  {count:>4}词  {bar}  {pct}%",
        "stats_lang_dist": "\n*各语言词库：*",
        "stats_lang_item": "• {display} — {count} 词",
        # 级别短标签（统计用，4字内）
        "stats_lv_labels": "入门|初级|初级+|中级|中级+|高级|精通|已掌握",

        # ── 连续学习 ─────────────────────────────────────────────────────────
        "streak_title": "🔥 *连续学习：{streak}*",
        "streak_total": "📊 累计总复习：{count} 次",
        "streak_0": "0 天（尚未开始复习）",
        "streak_1": "1 天 🌱",
        "streak_few": "{days} 天 📈",
        "streak_week": "{days} 天 🔥",
        "streak_month": "{days} 天 🏆",

        # ── 解析/处理 ────────────────────────────────────────────────────────
        "processing": "⏳ 正在解析…",
        "parse_fail": (
            "😕 解析失败，请稍后重试。\n"
            "（若持续出现，请检查 AI 服务配置）"
        ),
        "parse_fail_simple": "😕 解析失败，请稍后重试。",
        "sentence_no_vocab": (
            "📖 *翻译：*{translation}\n\n"
            "（未识别到值得记录的词汇）"
        ),
        "sentence_add_prompt": (
            "📖 *翻译：*{translation}\n\n"
            "*点击下方词汇加入词库：*\n{vocab_lines}"
        ),

        # ── 批量添加 ─────────────────────────────────────────────────────────
        "batch_result_title": "📚 *批量添加结果（{done}/{total}）：*",
        "batch_hit_limit": "\n⚠️ 已达添加上限，剩余词汇未处理。",
        "batch_parse_fail": "❌ {token} — 解析失败",
        "batch_not_vocab": "❌ {token} — 不是有效词汇",
        "batch_save_fail": "❌ {word} — 保存失败",
        "batch_new": "✅ *{word}*{pos_tag} — {definition}",
        "batch_exists": "📖 *{word}*{pos_tag} — {definition}（已在词库中）",

        # ── 限额提示 ─────────────────────────────────────────────────────────
        "limit_total_reached": (
            "📚 词库已达 {limit} 词上限。\n"
            "发送 `/activate 激活码` 订阅 Pro 解锁无限词库。"
        ),
        "limit_daily_reached": (
            "⏰ 今日已添加 {limit} 个词，明天再来吧！\n"
            "发送 `/activate 激活码` 订阅 Pro 不受限制。"
        ),
        "limit_both_reached": (
            "📚 已达添加上限（词库 {total_limit} 词 / 每日 {daily_limit} 词）。\n"
            "发送 `/activate 激活码` 订阅 Pro 解锁无限词库。"
        ),
        "limit_total_alert": "词库已达 {limit} 词上限，订阅 Pro 解锁无限词库。",
        "limit_daily_alert": "今日已添加 {limit} 个词，明天再来或订阅 Pro。",

        # ── 词汇编辑 ─────────────────────────────────────────────────────────
        "edit_field_pos": "词性",
        "edit_field_def": "释义",
        "edit_field_ctx": "例句",
        "edit_prompt": (
            "✏️ 编辑 *{word}* 的{field}\n\n"
            "当前值：_{current}_\n\n"
            "请直接发送新{field}："
        ),
        "edit_updated": "✅ *{word}* 的{field}已更新。",
        "edit_failed": "⚠️ 更新失败，记录可能已不存在。",
        "edit_unknown_field": "未知字段",
        "edit_detail_review": "复习 {count} 次",
        "edit_detail_next": "下次 {date}",

        # ── 删词 ─────────────────────────────────────────────────────────────
        "delete_cancelled": "❌ 已取消删除。",
        "delete_ok_one": "✅ 已删除该词汇条目。",
        "delete_ok_all": "✅ 已删除「{word}」的全部 {count} 个释义。",
        "delete_failed": "⚠️ 删除失败，记录可能已不存在。",
        "delete_batch_title": "🗑️ *批量删除结果：*",
        "delete_batch_ok": "✅ {word} — 已删除 {count} 条",
        "delete_batch_not_found": "❌ {word} — 词库中未找到",

        # ── 通知设置 ─────────────────────────────────────────────────────────
        "settings_title": "🔔 *通知设置*",
        "settings_tz_line": "🌏 时区：`{tz}`（用 /timezone 修改）",
        "settings_window_line": "⏰ 推送时段：{start}:00 – {end}:00",
        "settings_push_label": "📢 自动复习推送：{status}",
        "settings_push_on": "✅ 开启",
        "settings_push_off": "❌ 关闭",
        "settings_window_prompt": "⏰ 请选择推送时段：",
        "settings_toggle_off": "🔕 关闭推送",
        "settings_toggle_on": "🔔 开启推送",
        "settings_review_scope_label": "🌐 定时复习范围：{status}",
        "settings_review_scope_on": "仅当前语言",
        "settings_review_scope_off": "全部语言",
        "settings_toggle_review_scope_on": "🌐 切换为：仅当前语言",
        "settings_toggle_review_scope_off": "🌐 切换为：全部语言",

        # ── 时区 ─────────────────────────────────────────────────────────────
        "timezone_title": "🌏 *时区设置*",
        "timezone_prompt": (
            "当前时区：`{tz}`\n\n"
            "复习提醒只在本地时间 08:00–22:00 之间推送。\n"
            "请选择你所在的时区："
        ),
        "timezone_saved": (
            "🌏 *时区设置*\n\n"
            "✅ 已保存时区：`{tz}`\n\n"
            "复习提醒只在本地时间 08:00–22:00 之间推送。\n\n"
            "以后可通过 /settings 修改推送时段或关闭提醒"
        ),
        "timezone_save_fail": "保存失败，请重试。",
        "timezone_saved_toast": "✅ 时区已设置为 {tz}",

        # ── 语言管理 ─────────────────────────────────────────────────────────
        "lang_panel_title": "🌍 <b>多语言学习管理</b>",
        "lang_active_line": "📖 当前激活：{display}",
        "lang_native_line": "🔤 释义语言（母语）：{display}",
        "lang_vocab_label": "<b>你的词库：</b>",
        "lang_vocab_count": "• {display} — {count} 词",
        "lang_add_title": (
            "➕ <b>添加学习语言</b>\n\n"
            "选择你想添加的语言（✓ 表示已有词库）："
        ),
        "lang_native_title": (
            "🔤 <b>设置释义语言（母语）</b>\n\n"
            "选择你希望用哪种语言显示释义："
        ),
        "native_switch_warning": (
            "⚠️ *切换母语警告*\n\n"
            "当前母语：{current}\n"
            "词库共 {count} 个单词\n\n"
            "**切换后，这些词汇和复习进度将被永久删除，无法恢复。**\n\n"
            "确认要切换并清空词库吗？"
        ),
        "native_switch_confirm_btn": "🗑️ 确认清空并切换",
        "native_switch_done": "✅ 已切换至 {display}，旧词库已清空（删除 {count} 个单词）",

        # ── 订阅到期提醒 ─────────────────────────────────────────────────────
        "expiry_reminder": (
            "⏰ 您的 Pro 订阅将于 *{date}* 到期（3 天内）。\n"
            "发送 `/activate 激活码` 续订，继续享受无限词库。"
        ),

        # ── 按钮标签 ─────────────────────────────────────────────────────────
        "btn_skip": "⏭ 跳过",
        "btn_fuzzy": "🤔 模糊/拿不准",
        "btn_end_review": "🔚 结束复习",
        "btn_end_practice": "🔚 结束练习",
        "btn_prev": "◀ 上一页",
        "btn_next": "下一页 ▶",
        "btn_back_vocab": "◀ 返回词库",
        "btn_cancel": "❌ 取消",
        "btn_edit_pos": "✏️ 词性",
        "btn_edit_def": "✏️ 释义",
        "btn_edit_ctx": "✏️ 例句",
        "btn_add_lang": "➕ 添加新语言",
        "btn_set_native": "🔤 设置母语（释义语言）",
        "btn_back": "← 返回",
        "btn_change_window": "⏰ 更改时段",
        "btn_all_day": "全天（00:00–24:00）",

        # ── 新用户引导（Onboarding） ──────────────────────────────────────────
        "onboard_native_title": (
            "👋 欢迎使用 Vocab Master！\n"
            "请先选择你的母语（用于界面语言和词汇释义）："
        ),
        "onboard_lang_title": "很好！请选择你想学习的语言：",
        "onboard_done": (
            "✅ 设置完成！\n\n"
            "复习提醒已设为本地时间 08:00–22:00，可通过 /settings 调整。\n\n"
            "现在直接发单词或句子就能开始学习了！"
        ),
    },

    "en": {
        # ── Help ─────────────────────────────────────────────────────────────
        "help_text": (
            "*Commands:*\n"
            "/vocab — View your vocabulary list\n"
            "/review — Start a review session\n"
            "/practice — Free practice (no progress tracking)\n"
            "/language — Manage learning languages\n"
            "/search <word> — Search vocabulary\n"
            "/export — Export vocabulary as CSV\n"
            "/stats — Learning statistics\n"
            "/streak — Consecutive learning days\n"
            "/update <word> — Edit a word's POS/definition/example\n"
            "/delete <word> — Delete a word from vocabulary\n"
            "/timezone — Set review reminder timezone\n"
            "/settings — Notification settings\n"
            "/plan — View subscription status\n"
            "/activate <code> — Activate subscription\n"
            "/help — Show this help message"
        ),
        # ── Welcome ───────────────────────────────────────────────────────────
        "start_welcome": (
            "👋 Welcome to *Vocab Master*!\n\n"
            "I help you remember vocabulary using the Ebbinghaus forgetting curve "
            "to schedule reviews automatically.\n"
            "Supports multiple languages: English, Japanese, French and more.\n\n"
            "*How to use:*\n"
            "• Send a word or phrase (e.g. `devastated`)\n"
            "• Send a sentence with the target word (e.g. `I was utterly devastated`)\n"
            "• Send a word in your native language and I'll find the vocabulary\n\n"
        ),
        "start_tz_prompt": "🌏 Please set your timezone for accurate review reminders:",

        # ── Quiz questions ────────────────────────────────────────────────────
        "quiz_meaning_title": "🔤 *Meaning Quiz*",
        "quiz_meaning_instruction": "Select the meaning of *{word}* in context:",
        "quiz_fill_title": "🧠 <b>Fill-in-the-blank</b>",
        "quiz_fill_instruction": "Select the best word to fill in <b>______</b>:",
        "quiz_fill_hint": "💡 Hint: {definition}",

        # ── Quiz feedback ─────────────────────────────────────────────────────
        "quiz_correct": (
            "✅ *Correct!*\n\n"
            "*{word}* — {definition}"
            "{context_line}\n\n"
            "🎯 Level: {level}\n"
            "📅 Next review: {date}"
        ),
        "quiz_correct_practice": (
            "✅ *Correct!*\n\n"
            "*{word}* — {definition}"
            "{context_line}\n\n"
            "🎮 Practice mode — not counted toward progress"
        ),
        "quiz_wrong_fill": (
            "❌ *Wrong*\n\n"
            "Correct answer: *{word}* — {definition}"
            "{context_line}\n\n"
            "😔 Level down to: {level}\n"
            "📅 Review again tomorrow!"
        ),
        "quiz_wrong_meaning": (
            "❌ *Wrong*\n\n"
            "Correct meaning: *{word}* — {definition}"
            "{context_line}\n\n"
            "😔 Level down to: {level}\n"
            "📅 Review again tomorrow!"
        ),
        "quiz_wrong_fill_practice": (
            "❌ *Wrong*\n\n"
            "Correct answer: *{word}* — {definition}"
            "{context_line}\n\n"
            "🎮 Practice mode — not counted toward progress"
        ),
        "quiz_wrong_meaning_practice": (
            "❌ *Wrong*\n\n"
            "Correct meaning: *{word}* — {definition}"
            "{context_line}\n\n"
            "🎮 Practice mode — not counted toward progress"
        ),
        "quiz_skip_append": "\n\n⏭ Skipped — review again tomorrow",
        "quiz_fuzzy_append": "\n\n🤔 Marked as fuzzy — review again tomorrow",
        "quiz_done": "🎉 Review session complete! Keep up the momentum~",
        "practice_done": "🎉 Practice complete!",
        "quiz_end_review": "Review ended. You can /review again anytime~",
        "quiz_end_practice": "Practice ended. You can /review again anytime~",

        # ── Level descriptions ────────────────────────────────────────────────
        "level_0": "Beginner",
        "level_1": "Elementary",
        "level_2": "Short-term",
        "level_3": "Intermediate",
        "level_4": "Long-term",
        "level_5": "Advanced",
        "level_6": "Mastering",
        "level_7": "Mastered ✓",

        # ── Session state ─────────────────────────────────────────────────────
        "session_active": (
            "⏳ You're currently in a {mode} session. "
            "Please finish the current question first~\n"
            "(Click the End button below to stop early)"
        ),
        "session_mode_review": "review",
        "session_mode_practice": "practice",

        # ── Vocab ─────────────────────────────────────────────────────────────
        "vocab_empty": (
            "Your {lang_name} vocabulary is empty~\n"
            "Send any word to start building, or use /language to switch."
        ),
        "vocab_title": "📚 *{lang_name} Vocabulary* (Page {page}/{total_pages}, {total} words)",
        "vocab_click_hint": "Tap a word button to view details",
        "vocab_record_not_found": "Vocabulary record not found, it may have been deleted.",
        "vocab_mastered": "✓ Mastered",

        # ── Review/Practice ───────────────────────────────────────────────────
        "review_no_vocab": "Your {lang_name} vocabulary is empty~\nSend any word to start building!",
        "review_no_due_practice": (
            "⏳ No due words for {lang_name}. "
            "Entering practice mode (no progress tracking)…"
        ),
        "review_generating": "⏳ Generating {lang_name} review question…",
        "review_error": "Failed to generate review question. Please try again.",
        "practice_empty": (
            "Your {lang_name} vocabulary is empty~\n"
            "Send some words first!\n"
            "Or use /language to switch."
        ),
        "practice_start": "🎮 Entering {lang_name} practice mode (no progress tracking)…",
        "practice_error": "Failed to generate practice question. Please try again.",

        # ── Stats ─────────────────────────────────────────────────────────────
        "stats_title": "📊 *Learning Statistics* ({lang_name})",
        "stats_total": "📚 Total words: {count}",
        "stats_today_added": "➕ Added today: {count}",
        "stats_due": "⚡ Due for review: {count}",
        "stats_level_dist": "*Level distribution:*",
        "stats_level_line": "Lv{level} {label:<8}  {count:>4} wds  {bar}  {pct}%",
        "stats_lang_dist": "\n*Vocabulary by language:*",
        "stats_lang_item": "• {display} — {count} words",
        "stats_lv_labels": "Beginner|Elem.|Short|Inter.|Long|Adv.|Master|Done",

        # ── Streak ────────────────────────────────────────────────────────────
        "streak_title": "🔥 *Streak: {streak}*",
        "streak_total": "📊 Total reviews: {count}",
        "streak_0": "0 days (not started yet)",
        "streak_1": "1 day 🌱",
        "streak_few": "{days} days 📈",
        "streak_week": "{days} days 🔥",
        "streak_month": "{days} days 🏆",

        # ── Processing/Save ───────────────────────────────────────────────────
        "processing": "⏳ Processing…",
        "parse_fail": (
            "😕 Failed to parse. Please try again.\n"
            "(If this persists, check your AI service configuration)"
        ),
        "parse_fail_simple": "😕 Failed to parse. Please try again.",
        "sentence_no_vocab": (
            "📖 *Translation:* {translation}\n\n"
            "(No vocabulary worth saving was identified)"
        ),
        "sentence_add_prompt": (
            "📖 *Translation:* {translation}\n\n"
            "*Tap a word below to add to vocabulary:*\n{vocab_lines}"
        ),

        # ── Batch add ─────────────────────────────────────────────────────────
        "batch_result_title": "📚 *Batch add results ({done}/{total}):*",
        "batch_hit_limit": "\n⚠️ Limit reached — remaining words were not processed.",
        "batch_parse_fail": "❌ {token} — parse failed",
        "batch_not_vocab": "❌ {token} — not a valid vocabulary item",
        "batch_save_fail": "❌ {word} — save failed",
        "batch_new": "✅ *{word}*{pos_tag} — {definition}",
        "batch_exists": "📖 *{word}*{pos_tag} — {definition} (already in vocabulary)",

        # ── Limits ────────────────────────────────────────────────────────────
        "limit_total_reached": (
            "📚 Vocabulary limit reached ({limit} words).\n"
            "Send `/activate <code>` to subscribe to Pro for unlimited vocabulary."
        ),
        "limit_daily_reached": (
            "⏰ Daily limit reached ({limit} words today). Come back tomorrow!\n"
            "Send `/activate <code>` to subscribe to Pro for no limits."
        ),
        "limit_both_reached": (
            "📚 Limit reached (vocabulary: {total_limit} / daily: {daily_limit} words).\n"
            "Send `/activate <code>` to subscribe to Pro for unlimited vocabulary."
        ),
        "limit_total_alert": "Vocabulary limit reached ({limit} words). Subscribe to Pro for unlimited.",
        "limit_daily_alert": "Daily limit reached ({limit} words). Come back tomorrow or subscribe to Pro.",

        # ── Edit ──────────────────────────────────────────────────────────────
        "edit_field_pos": "POS",
        "edit_field_def": "definition",
        "edit_field_ctx": "example",
        "edit_prompt": (
            "✏️ Edit *{word}* — {field}\n\n"
            "Current: _{current}_\n\n"
            "Please send the new {field}:"
        ),
        "edit_updated": "✅ *{word}*'s {field} has been updated.",
        "edit_failed": "⚠️ Update failed — record may no longer exist.",
        "edit_unknown_field": "Unknown field",
        "edit_detail_review": "Reviewed {count}×",
        "edit_detail_next": "Next {date}",

        # ── Delete ────────────────────────────────────────────────────────────
        "delete_cancelled": "❌ Deletion cancelled.",
        "delete_ok_one": "✅ Vocabulary entry deleted.",
        "delete_ok_all": "✅ Deleted all {count} entries for \"{word}\".",
        "delete_failed": "⚠️ Deletion failed — record may no longer exist.",
        "delete_batch_title": "🗑️ *Batch delete results:*",
        "delete_batch_ok": "✅ {word} — deleted {count} entries",
        "delete_batch_not_found": "❌ {word} — not found in vocabulary",

        # ── Settings ──────────────────────────────────────────────────────────
        "settings_title": "🔔 *Notification Settings*",
        "settings_tz_line": "🌏 Timezone: `{tz}` (change with /timezone)",
        "settings_window_line": "⏰ Reminder window: {start}:00 – {end}:00",
        "settings_push_label": "📢 Auto review reminders: {status}",
        "settings_push_on": "✅ On",
        "settings_push_off": "❌ Off",
        "settings_window_prompt": "⏰ Select reminder window:",
        "settings_toggle_off": "🔕 Turn Off",
        "settings_toggle_on": "🔔 Turn On",
        "settings_review_scope_label": "🌐 Review scope: {status}",
        "settings_review_scope_on": "Active language only",
        "settings_review_scope_off": "All languages",
        "settings_toggle_review_scope_on": "🌐 Switch to: Active language only",
        "settings_toggle_review_scope_off": "🌐 Switch to: All languages",

        # ── Timezone ──────────────────────────────────────────────────────────
        "timezone_title": "🌏 *Timezone Settings*",
        "timezone_prompt": (
            "Current timezone: `{tz}`\n\n"
            "Reminders are sent between 08:00–22:00 local time.\n"
            "Please select your timezone:"
        ),
        "timezone_saved": (
            "🌏 *Timezone Settings*\n\n"
            "✅ Timezone saved: `{tz}`\n\n"
            "Reminders are sent between 08:00–22:00 local time.\n\n"
            "Use /settings to adjust the window or disable reminders"
        ),
        "timezone_save_fail": "Failed to save. Please try again.",
        "timezone_saved_toast": "✅ Timezone set to {tz}",

        # ── Language management ───────────────────────────────────────────────
        "lang_panel_title": "🌍 <b>Language Learning Manager</b>",
        "lang_active_line": "📖 Active language: {display}",
        "lang_native_line": "🔤 Definition language (native): {display}",
        "lang_vocab_label": "<b>Your vocabulary:</b>",
        "lang_vocab_count": "• {display} — {count} words",
        "lang_add_title": (
            "➕ <b>Add Learning Language</b>\n\n"
            "Select a language to add (✓ = already have vocabulary):"
        ),
        "lang_native_title": (
            "🔤 <b>Set Definition Language (Native)</b>\n\n"
            "Select the language you want definitions displayed in:"
        ),
        "native_switch_warning": (
            "⚠️ *Switch Native Language Warning*\n\n"
            "Current native language: {current}\n"
            "Vocabulary: {count} words\n\n"
            "**After switching, all these words and review progress will be permanently deleted and cannot be recovered.**\n\n"
            "Confirm switch and clear vocabulary?"
        ),
        "native_switch_confirm_btn": "🗑️ Confirm & Clear",
        "native_switch_done": "✅ Switched to {display}. Old vocabulary cleared ({count} words deleted).",

        # ── Subscription expiry ───────────────────────────────────────────────
        "expiry_reminder": (
            "⏰ Your Pro subscription expires on *{date}* (within 3 days).\n"
            "Send `/activate <code>` to renew and keep your unlimited vocabulary."
        ),

        # ── Buttons ───────────────────────────────────────────────────────────
        "btn_skip": "⏭ Skip",
        "btn_fuzzy": "🤔 Fuzzy / Not sure",
        "btn_end_review": "🔚 End Review",
        "btn_end_practice": "🔚 End Practice",
        "btn_prev": "◀ Previous",
        "btn_next": "Next ▶",
        "btn_back_vocab": "◀ Back to List",
        "btn_cancel": "❌ Cancel",
        "btn_edit_pos": "✏️ POS",
        "btn_edit_def": "✏️ Definition",
        "btn_edit_ctx": "✏️ Example",
        "btn_add_lang": "➕ Add Language",
        "btn_set_native": "🔤 Set Native Language",
        "btn_back": "← Back",
        "btn_change_window": "⏰ Change Window",
        "btn_all_day": "All day (00:00–24:00)",

        # ── Onboarding ────────────────────────────────────────────────────────
        "onboard_native_title": (
            "👋 Welcome to Vocab Master!\n"
            "Please select your native language (used for UI and definitions):"
        ),
        "onboard_lang_title": "Great! Please select the language you want to learn:",
        "onboard_done": (
            "✅ Setup complete!\n\n"
            "Review reminders are set for 08:00–22:00 local time. "
            "You can adjust this in /settings.\n\n"
            "Start learning by sending a word or sentence now!"
        ),
    },

"fr": {
        "help_text": "*Commandes:*\n/vocab — Consultez votre liste de vocabulaire\n/review — Commencez une session de révision\n/practice — Pratique libre (sans suivi des progrès)\n/language — Gérez les langues d'apprentissage\n/search <mot> — Recherchez un mot de vocabulaire\n/export — Exportez votre vocabulaire au format CSV\n/stats — Statistiques d'apprentissage\n/streak — Jours d'apprentissage consécutifs\n/update <mot> — Editez les informations d'un mot (POS/définition/exemple)\n/delete <mot> — Supprimez un mot de votre liste de vocabulaire\n/timezone — Définissez le fuseau horaire des rappels de révision\n/settings — Paramètres des notifications\n/plan — Consultez l'état de votre abonnement\n/activate <code> — Activez votre abonnement\n/help — Affichez ce message d'aide",
        "start_welcome": "👋 Bienvenue dans *Vocab Master* !\n\nJe vous aide à retenir des vocabulaires en utilisant la courbe d'oubli d'Ebbinghaus pour programmer automatiquement les révisions.\nPrise en charge de plusieurs langues : anglais, japonais, français et plus encore.\n\n*Comment utiliser :*\n• Envoyez un mot ou une phrase (par exemple, `devasté`)\n• Envoyez une phrase contenant le mot cible (par exemple, `Je suis complètement devasté`)\n• Envoyez un mot en langue maternelle et je trouverai le vocabulaire.",
        "start_tz_prompt": '🌏 Veuillez définir votre fuseau horaire pour des rappels de révision précis:',
        "quiz_meaning_title": '🔤 *Quiz sur le sens*',
        "quiz_meaning_instruction": 'Sélectionnez le sens de *{word}* dans le contexte :',
        "quiz_fill_title": '🧠 <b>Remplissage de lacunes</b>',
        "quiz_fill_instruction": 'Sélectionnez le meilleur mot pour remplir <b>______</b>:',
        "quiz_fill_hint": '💡 Conseil: {definition}',
        "quiz_correct": '✅ *Correct !*\n\n*{word}* — {definition}{context_line}\n\n🎯 Niveau: {level}\n📅 Prochaine révision: {date}',
        "quiz_correct_practice": '✅ *Correct !*\n\n*{word}* — {definition}{context_line}\n\n🎮 Mode pratique — ne compté pas vers la progression',
        "quiz_wrong_fill": '❌ *Mauvais*\n\nRéponse correcte: *{word}* — {definition}{context_line}\n\n😔 Niveau baissé à: {level}\n📅 Revue demain !',
        "quiz_wrong_meaning": '❌ *Mauvais*\n\nSignification correcte : *{word}* — {definition}{context_line}\n\n😔 Niveau baissé à : {level}\n📅 Revue demain !',
        "quiz_wrong_fill_practice": '❌ *Mauvais*\n\nRéponse correcte: *{word}* — {definition}{context_line}\n\n🎮 Mode pratique — ne compte pas pour la progression',
        "quiz_wrong_meaning_practice": '❌ *Mauvais*\n\nSignification correcte : *{word}* — {definition}{context_line}\n\n🎮 Mode pratique — ne pas compter pour la progression',
        "quiz_skip_append": '⏭ Passé — réviser à nouveau demain',
        "quiz_fuzzy_append": '🤔 Marqué comme flou — révérifier demain',
        "quiz_done": '🎉 Session de révision terminée ! Continue à maintenir ce rythme~',
        "practice_done": '🎉 Pratique terminée!',
        "quiz_end_review": 'Review terminé. Vous pouvez reprendre /review à tout moment~',
        "quiz_end_practice": 'La pratique est terminée. Vous pouvez réexécuter /review à tout moment~',
        "level_0": 'Débutant',
        "level_1": 'Elémentaire',
        "level_2": 'court terme',
        "level_3": 'Intermédiaire',
        "level_4": 'À long terme',
        "level_5": 'Avancé',
        "level_6": 'Maîtrisant',
        "level_7": 'Maîtrisé ✓',
        "session_active": "⏳ Vous êtes actuellement dans une session {mode}. Veuillez terminer d'abord la question en cours~\n(Cliquez sur le bouton Fin ci-dessous pour arrêter tôt)",
        "session_mode_review": '/review',
        "session_mode_practice": 'practice',
        "vocab_empty": 'Votre vocabulaire en {lang_name} est vide~\nEnvoyez任何一个词以开始构建，或使用/language切换。',
        "vocab_title": '📚 *{lang_name} Vocabulary* (Page {page}/{total_pages}, {total} mots)',
        "vocab_click_hint": 'Appuyez sur un bouton de mot pour afficher les détails',
        "vocab_record_not_found": 'Enregistrement de vocabulaire non trouvé, il peut avoir été supprimé.',
        "vocab_mastered": '✓ Maîtrisé',
        "review_no_vocab": 'Votre vocabulaire en {lang_name} est vide~\nEnvoyez任何一个词以开始构建！',
        "review_no_due_practice": '⏳ Aucunes mots à réviser pour {lang_name}. Entrez en mode pratique (sans suivi des progrès)…',
        "review_generating": '⏳ Génération de la question de révision en {lang_name}…',
        "review_error": 'Échec de la génération de la question de révision. Veuillez essayer à nouveau.',
        "practice_empty": "Votre vocabulaire en {lang_name} est vide~\nEnvoyez d'abord quelques mots !\nSinon, utilisez /language pour changer.",
        "practice_start": '🎮 Entrainement en mode {lang_name} (sans suivi des progrès)…',
        "practice_error": "Échec de la génération de la question d'entraînement. Veuillez essayer à nouveau.",
        "stats_title": "📊 *Statistiques d'apprentissage* ({lang_name})",
        "stats_total": '📚 Total mots: {count}',
        "stats_today_added": "➕ Ajouté aujourd'hui: {count}",
        "stats_due": '⚡ À réviser : {count}',
        "stats_level_dist": '*Distribution de niveau:*',
        "stats_level_line": 'Niv.{level} {label:<8}  {count:>4} mots  {bar}  {pct}%',
        "stats_lang_dist": '*Dictionnaire par langue:*',
        "stats_lang_item": '• {display} — {count} mots',
        "stats_lv_labels": 'Débutant|Elem.|Court|Inter.|Long|Avancé|Maître|Fini',
        "streak_title": '🔥 *Poursuite: {streak}*',
        "streak_total": '📊 Total reviews: {count}',
        "streak_0": '0 jours (`date`) (encore未翻译)commencer',
        "streak_1": '1 jour 🌱',
        "streak_few": '{days} jours 📈',
        "streak_week": '{days} jours 🔥',
        "streak_month": '{days} jours 🏆',
        "processing": '⏳ Traitement…',
        "parse_fail": '😕 Échec de la parsing. Veuillez essayer à nouveau.\n(Si ce problème persiste, vérifiez votre configuration du service AI)',
        "parse_fail_simple": '😕 Échec de la parsing. Veuillez essayer à nouveau.',
        "sentence_no_vocab": '📖 *Translation:* {translation}\n\n(Aucun vocabulaire worth saving a été identifié)',
        "sentence_add_prompt": "📖 *Traduction:* {translation}\n\n*Appuyez sur un mot ci-dessous pour l'ajouter au vocabulaire :*\n{vocab_lines}",
        "batch_result_title": '📚 *Batch add results ({done}/{total}):*',
        "batch_hit_limit": "⚠️ Limite atteinte — les mots restants n'ont pas été traités.",
        "batch_parse_fail": '❌ {token} — analyse faillie',
        "batch_not_vocab": "❌ {token} — n'est pas un élément de vocabulaire valide",
        "batch_save_fail": '❌ {word} — enregistrement échoué',
        "batch_new": '✅ *{word}*{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition} (déjà dans le vocabulaire)*',
        "limit_total_reached": '📚 Limite de vocabulaire atteinte ({limit} mots).\nEnvoyez `/activate <code>` pour vous abonner au Pro pour un accès illimité au vocabulaire.',
        "limit_daily_reached": "⏰ Limite journalière atteinte ({limit} mots aujourd'hui). Revenez demain !\nEnvoyez `/activate <code>` pour vous abonner au Pro sans limites.",
        "limit_both_reached": '📚 Limite atteinte (vocabulary: {total_limit} / daily: {daily_limit} mots).\nEnvoyez `/activate <code>` pour vous abonner au Pro pour une vocabulaire illimité.',
        "limit_total_alert": 'Limite de vocabulaire atteinte ({limit} mots). Abonnez-vous à Pro pour une utilisation illimitée.',
        "limit_daily_alert": 'Limite journalière atteinte ({limit} mots). Revenez demain ou souscrivez au Pro.',
        "edit_field_pos": 'POS',
        "edit_field_def": 'definition',
        "edit_field_ctx": 'exemple',
        "edit_prompt": '✏️ Éditer *{word}* — {field}\n\nActuel: _{current}_\n\nVeuillez envoyer le nouveau {field}:',
        "edit_updated": "✅ *{word}*'s {field} a été mis à jour.",
        "edit_failed": "⚠️ Mise à jour échouée — le registre n'existe peut-être plus.",
        "edit_unknown_field": 'Champ inconnu',
        "edit_detail_review": 'Revue {count}×',
        "edit_detail_next": 'Prochainement {date}',
        "delete_cancelled": '❌ Suppression annulée.',
        "delete_ok_one": '✅ Mot supprimé.',
        "delete_ok_all": '✅ Supprimé toutes {count} entrées pour "{word}".',
        "delete_failed": "⚠️ Suppression échouée — le enregistrement n'existe peut-être plus.",
        "delete_batch_title": '🗑️ *Résultats de suppression en batch:*',
        "delete_batch_ok": '✅ {word} — supprimé {count} entrées',
        "delete_batch_not_found": '❌ {word} — non trouvé dans le vocabulaire',
        "settings_title": '🔔 *Paramètres des Notifications*',
        "settings_tz_line": '🌍 Fuseau horaire: `{tz}` (changer avec /timezone)',
        "settings_window_line": '⏰ Fenêtre de rappel : {start}:00 – {end}:00',
        "settings_push_label": '📢 Alertes de révision automatique : {status}',
        "settings_push_on": '✅ On',
        "settings_push_off": '❌ Arrêt',
        "settings_window_prompt": '⏰ Sélectionnez la fenêtre de rappel:',
        "settings_toggle_off": '🔕 Désactiver',
        "settings_toggle_on": '🔔 Activez',
        "settings_review_scope_label": '🌐 Portée de révision : {status}',
        "settings_review_scope_on": 'Langue active uniquement',
        "settings_review_scope_off": 'Toutes les langues',
        "settings_toggle_review_scope_on": '🌐 Passer à : Langue active uniquement',
        "settings_toggle_review_scope_off": '🌐 Passer à : Toutes les langues',
        "timezone_title": '🌍 *Paramètres fuseau horaire*',
        "timezone_prompt": 'fuseau horaire actuel: `{tz}`\n\nLes rappels sont envoyés entre 08:00–22:00 heure locale.\nVeuillez sélectionner votre fuseau horaire:',
        "timezone_saved": '🌏 *Paramètres fuseau horaire*\n\n✅ Fuseau horaire enregistré: `{tz}`\n\nLes rappels sont envoyés entre 08:00–22:00 heure locale.\n\nUtilisez /settings pour ajuster la fenêtre ou désactiver les rappels',
        "timezone_save_fail": 'Échec de sauvegarde. Veuillez essayer à nouveau.',
        "timezone_saved_toast": '✅ Fuseau horaire défini sur {tz}',
        "lang_panel_title": '🌍 <b>Gestionnaire de Langues Apprises</b>',
        "lang_active_line": '📖 Langue active: {display}',
        "lang_native_line": '🔤 Langue des définitions (natif) : {display}',
        "lang_vocab_label": '<b>Votre vocabulaire :</b>',
        "lang_vocab_count": '• {display} — {count} mots',
        "lang_add_title": "➕ <b>Ajouter une langue d'apprentissage</b>\n\nSélectionnez une langue à ajouter (✓ = vocabulaire déjà présent)",
        "lang_native_title": '🔤 <b>Choisissez la langue des définitions (Natif)</b>\n\nSelect the language you want definitions displayed in:',
        "expiry_reminder": '⏰ Votre abonnement Pro expire le *{date}* (dans 3 jours).\nEnvoyez `/activate <code>` pour renouveler et garder votre vocabulaire illimité.',
        "btn_skip": '⏭ Passer',
        "btn_fuzzy": '🤔 Flou / Pas sûr',
        "btn_end_review": '🔚 Terminer La Revue',
        "btn_end_practice": '🔚 Terminer la pratique',
        "btn_prev": '◀ Précédent',
        "btn_next": 'Suivant ▶',
        "btn_back_vocab": '◀ Retour à la Liste',
        "btn_cancel": '❌ Annuler',
        "btn_edit_pos": '✏️ Catégorie grammaticale',
        "btn_edit_def": '✏️ Définition',
        "btn_edit_ctx": '✏️ Exemple',
        "btn_add_lang": '➕ Ajouter une langue',
        "btn_set_native": '🔤 Définir la Langue Natale',
        "btn_back": '← Retour',
        "btn_change_window": '⏰ Modifier Fenêtre',
        "btn_all_day": 'Toute la journée (00:00–24:00)',
        "onboard_native_title": "👋 Bienvenue dans Vocab Master !\nVeuillez sélectionner votre langue maternelle (utilisée pour l'interface utilisateur et les définitions) :",
        "onboard_lang_title": "D'accord ! Veuillez sélectionner la langue que vous souhaitez apprendre:",
        "onboard_done": "✅ Configuration terminée !\n\nLes rappels de révision sont fixés de 08:00 à 22:00 heure locale. Vous pouvez les ajuster dans /settings.\n\nCommencez l'apprentissage en envoyant un mot ou une phrase maintenant !",
    },

    "de": {
        "help_text": '*Commands:*\n/vocab — Deine Vokabelliste anzeigen\n/review — Starte eine Review-Sitzung\n/practice — Freie Übung (keine Fortschrittsüberwachung)\n/language — Lernsprachen verwalten\n/search <wort> — Vokabelliste durchsuchen\n/export — Vokabelliste als CSV exportieren\n/stats — Lernstatistiken\n/streak — Anzahl an aufeinanderfolgenden Lerntagen\n/update <wort> — Wort-POS/Bedeutung/Bespiel bearbeiten\n/delete <wort> — Wort aus der Vokabelliste löschen\n/timezone — Erinnerungszeitzone für Reviews festlegen\n/settings — Benachrichtigungs-Einstellungen\n/plan — Abonnementstatus anzeigen\n/activate <code> — Abonnement aktivieren\n/help — Zeige diese Hilfe-Anleitung',
        "start_welcome": '👋 Willkommen bei *Vocab Master*!\n\nIch helfe dir, Vokabeln zu behalten, indem ich die Ebbinghaus-Vergessenskurve nutze, um Reviews automatisch zu planen.\nUnterstützt mehrere Sprachen: Englisch, Japanisch, Französisch und mehr.\n\n*Wie du es nutzt:*\n• Sende eine Wort oder Phrase (z.B. `devastated`)\n• Sende eine Satz mit dem Zielwort (z.B. `Ich war vollständig entsetzt`)\n• Sende ein Wort in deiner Muttersprache und ich werde das Vokabular finden.',
        "start_tz_prompt": '🌏 Bitte stellen Sie Ihre Zeitzone ein, um genaue Review-Erinnerungen zu erhalten:',
        "quiz_meaning_title": '💡 *Bedeutungs-Quiz*',
        "quiz_meaning_instruction": 'Wählen Sie den Bedeutung von *{word}* im Kontext:',
        "quiz_fill_title": '🧠 <b>Leerstellenfüllen</b>',
        "quiz_fill_instruction": 'Wählen Sie das beste Wort, um einzufügen: <b>______</b>',
        "quiz_fill_hint": '💡 Tipp: {definition}',
        "quiz_correct": '✅ *Richtig!*\n\n*{word}* — {definition}{context_line}\n\n🎯 Level: {level}\n📅 Next review: {date}',
        "quiz_correct_practice": '✅ *Richtig!*\n\n*{word}* — {definition}{context_line}\n\n🎮 Praktiziermodus — nicht in deinem Fortschritt berücksichtigt',
        "quiz_wrong_fill": '❌ *Falsch*\n\nRichtige Antwort: *{word}* — {definition}{context_line}\n\n😔 Einen Level runter: {level}\n📅 Morgen wieder review!',
        "quiz_wrong_meaning": '❌ *Falsch*\n\nRichtiger Sinn: *{word}* — {definition}{context_line}\n\n😔 Einen Level runter: {level}\n📅 Morgen wieder-reviewen!',
        "quiz_wrong_fill_practice": '❌ *Falsch*\n\nRichtige Antwort: *{word}* — {definition}{context_line}\n\n🎮 Praktiziermodus — nicht zur Fortschrittsbilanz zähler',
        "quiz_wrong_meaning_practice": '❌ *Falsch*\n\nRichtiger Sinn: *{word}* — {definition}{context_line}\n\n🎮 Praktiziermodus — nicht in die Fortschrittsbilanz einberechnet',
        "quiz_skip_append": '⏭ Übersprungen — mache es wieder durch /review am nächsten Tag',
        "quiz_fuzzy_append": '🤔 Marked als ungenau — wieder review amTomorrow',
        "quiz_done": '🎉 Review-Sitzung abgeschlossen! Halte den Rhythmus aufrecht~',
        "practice_done": '🎉 Übung abgeschlossen!',
        "quiz_end_review": 'Review beendet. Du kannst /review jederzeit wieder ausführen~',
        "quiz_end_practice": 'Praktischung beendet. Du kannst /review jederzeit wieder ausführen~',
        "level_0": 'Anfänger',
        "level_1": 'Elementar',
        "level_2": 'Kurzfristig',
        "level_3": 'Fortgeschritten',
        "level_4": 'Langfristig',
        "level_5": 'Fortgeschritten',
        "level_6": 'Meisternd',
        "level_7": 'Meisterung ✓',
        "session_active": '⏳ Sie befinden sich derzeit in einer {mode}-Sitzung. Bitte fahren Sie das aktuelle Frage zuerst zu Ende~\n(Klicken Sie auf den Button Ende unten, um früh zu beenden)',
        "session_mode_review": 'Überprüfung',
        "session_mode_practice": 'Übung',
        "vocab_empty": 'Dein {lang_name}-Vokabular ist leer~ \nSend any word to start building, or use /language to switch.',
        "vocab_title": '📚 *{lang_name} Wörterbuch* (Seite {page}/{total_pages}, {total} Wörter)',
        "vocab_click_hint": 'Tippe auf ein Wort-Button, um Details zu sehen',
        "vocab_record_not_found": 'Vokabular-Eintrag nicht gefunden, er könnte gelöscht worden sein.',
        "vocab_mastered": '✓ Meisterhaft',
        "review_no_vocab": 'Dein {lang_name}-Vokabular ist leer~ \nSend any word to start building!',
        "review_no_due_practice": '⏳ Keine fälligen Wörter für {lang_name}. Eintritt in den Übungsmodus (keine Fortschrittsverfolgung)…',
        "review_generating": '⏳ Generiere {lang_name}-Review-Frage…',
        "review_error": 'Fehler beim Erstellen der Review-Frage. Bitte versuchen Sie es erneut.',
        "practice_empty": 'Dein {lang_name}-Vokabular ist leer~ \nSchicke zuerst einige Wörter!\nOder wechsle mit /language.',
        "practice_start": '🎮 Entering {lang_name} Übungsmodus (keine Fortschrittsverfolgung)…',
        "practice_error": 'Fehler beim Generieren eines Übungsaufgabens. Bitte versuchen Sie es erneut.',
        "stats_title": '📊 *Lernstatistiken* ({lang_name})',
        "stats_total": '📚 Gesamtzahl der Wörter: {count}',
        "stats_today_added": '➕ Hinzugefügt heute: {count}',
        "stats_due": '⚡ Due for review: {count}',
        "stats_level_dist": '*Niveauverteilung:*',
        "stats_level_line": 'Lv{level} {label:<8}  {count:>4} wds  {bar}  {pct}%',
        "stats_lang_dist": '*Vokabular nach Sprache:*',
        "stats_lang_item": '• {display} — {count} Wörter',
        "stats_lv_labels": 'Anfänger|Elem.|Kurz|Mittlerer|Lang|Fortgeschritten|Meister|Fertig',
        "streak_title": '🔥 *Streak: {streak}*',
        "streak_total": '📊 Gesamt-Anz. Reviews: {count}',
        "streak_0": '0 Tage (noch nicht gestartet)',
        "streak_1": '1 Tag 🌱',
        "streak_few": '{days} Tage 📈',
        "streak_week": '{days} Tage 🔥',
        "streak_month": '{days} Tage 🏆',
        "processing": '⏳ Verarbeitung…',
        "parse_fail": '😕 Das Parsing ist fehlgeschlagen. Bitte versuchen Sie es erneut.\n(Wenn das Problem bestehen bleibt, überprüfen Sie Ihre Konfiguration Ihres AI-Dienstes)',
        "parse_fail_simple": '😕 Das kann nicht analysiert werden. Bitte versuche es erneut.',
        "sentence_no_vocab": '📖 *Übersetzung:* {translation}\n\n(Bei den zu überprüfenden Wörtern wurde nichts Wichtiges gefunden)',
        "sentence_add_prompt": '📖 *Übersetzung:* {translation}\n\n*Tippe auf ein Wort unten, um es dem Vokabular hinzuzufügen:*\n{vocab_lines}',
        "batch_result_title": '📚 *Batch add results ({done}/{total}):*',
        "batch_hit_limit": '⚠️ Limit erreicht — die übrigen Wörter wurden nicht verarbeitet.',
        "batch_parse_fail": '❌ {token} — Parse fehlgeschlagen',
        "batch_not_vocab": '❌ {token} — kein gültiges Vokabularelement',
        "batch_save_fail": '❌ {word} — gespeichert werden gescheitert',
        "batch_new": '✅ *{word}*{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition} (bereits im Vokabular)',
        "limit_total_reached": '📚 Vokabularlimit erreicht ({limit} Wörter).\nSenden Sie `/activate <code>` um Pro zu aktivieren und ein unbeschränktes Vokabular zu erhalten.',
        "limit_daily_reached": '⏰ Täglicher Limit erreicht ({limit} Wörter heute). Kommen Sie morgen zurück!\nSenden Sie `/activate <code>` um Pro zu aktivieren und Limits zu umgehen.',
        "limit_both_reached": '📚 Limit erreicht (Vokabular: {total_limit} / täglich: {daily_limit} Wörter).\nSenden Sie `/activate <code>` um auf Pro zu subscribe und ein unbeschränktes Vokabular zu erhalten.',
        "limit_total_alert": 'Vokabulär-Limit erreicht ({limit} Wörter). Abonnieren Sie Pro für unendliches Vokabulär.',
        "limit_daily_alert": 'Täglicher Limit erreicht ({limit} Wörter). Kommen Sie morgen zurück oder teilen Sie sich mit Pro.',
        "edit_field_pos": 'POS',
        "edit_field_def": 'Definition',
        "edit_field_ctx": 'Beispiel',
        "edit_prompt": '✏️ Bearbeite *{word}* — {field}\n\nAktuell: _{current}_\n\nBitte sende den neuen {field}:',
        "edit_updated": "✅ *{word}*'s {field} wurde aktualisiert.",
        "edit_failed": '⚠️ Aktualisierung fehlgeschlagen — das Record existiert möglicherweise nicht mehr.',
        "edit_unknown_field": 'Unbekanntes Feld',
        "edit_detail_review": 'Geprüft {count}×',
        "edit_detail_next": 'Nächstes {date}',
        "delete_cancelled": '❌ Löschung abgebrochen.',
        "delete_ok_one": '✅ Vokabelliste-Eintrag gelöscht.',
        "delete_ok_all": '✅ Gelöscht alle {count} Einträge für "{word}".',
        "delete_failed": '⚠️ Löschung fehlgeschlagen — das Record existiert möglicherweise nicht mehr.',
        "delete_batch_title": '🗑️ *Mehrere Ergebnisse löschen:*',
        "delete_batch_ok": '✅ {word} — gelöschte Einträge: {count}',
        "delete_batch_not_found": '❌ {word} — nicht im Vokabular gefunden',
        "settings_title": '🔔 *Benachrichtigungs-Einstellungen*',
        "settings_tz_line": '🌍 Zeitzone: `{tz}` (ändern mit /timezone)',
        "settings_window_line": '⏰ Erinnerungsfenster: {start}:00 – {end}:00',
        "settings_push_label": '📢 Auto-Review-Erinnerungen: {status}',
        "settings_push_on": '✅ Auf',
        "settings_push_off": '❌ Abgelehnt',
        "settings_window_prompt": '⏰ Wählen Sie den Erinnerungsbereich:',
        "settings_toggle_off": '🔕 Deaktivieren',
        "settings_toggle_on": '🔔 Aktivieren',
        "settings_review_scope_label": '🌐 Wiederholungsbereich: {status}',
        "settings_review_scope_on": 'Nur aktive Sprache',
        "settings_review_scope_off": 'Alle Sprachen',
        "settings_toggle_review_scope_on": '🌐 Wechseln zu: Nur aktive Sprache',
        "settings_toggle_review_scope_off": '🌐 Wechseln zu: Alle Sprachen',
        "timezone_title": '🌏 *Zeitzone-Einstellungen*',
        "timezone_prompt": 'Aktueller Zeitzone: `{tz}`\n\nErinnerungen werden zwischen 08:00–22:00 lokal gesendet.\nBitte wählen Sie Ihre Zeitzone:',
        "timezone_saved": '🌏 *Zeitzone-Einstellungen*\n\n✅ Zeitzone gespeichert: `{tz}`\n\nErinnerungen werden zwischen 08:00–22:00 lokal gesendet.\n\nVerwende /settings, um das Fenster anzupassen oder die Erinnerungen deaktivieren zu lassen.',
        "timezone_save_fail": 'Fehler beim Speichern. Bitte versuchen Sie es erneut.',
        "timezone_saved_toast": '✅ Zeitzone auf {tz} gesetzt',
        "lang_panel_title": '🌍 <b>Sprachlern-Assistent</b>',
        "lang_active_line": '📖 Aktive Sprache: {display}',
        "lang_native_line": '💡 Definitionssprache (nativ): {display}',
        "lang_vocab_label": '<b>Ihr Vokabular:</b>',
        "lang_vocab_count": '• {display} — {count} Wörter',
        "lang_add_title": '➕ <b>Hinzufügen der Lernsprache</b>\n\nWählen Sie eine Sprache aus (✓ = Vokabular bereits vorhanden):',
        "lang_native_title": '🔤 <b>Set Definition Language (Nativ)</b>\n\nWählen Sie die Sprache aus, in der Definitionen angezeigt werden sollen:',
        "expiry_reminder": '⏰ Ihre Pro-Abonnement läuft am *{date}* ab (innerhalb von 3 Tagen).\nSenden Sie `/activate <code>` an, um es zu erneuern und Ihren unbeschränkten Vokabularzugriff zu erhalten.',
        "btn_skip": '⏭ Überspringen',
        "btn_fuzzy": '🤔 Unsicher / Nicht sicher',
        "btn_end_review": '🔚 Beende die Review-Sitzung',
        "btn_end_practice": '🔚 Ende der Übung',
        "btn_prev": '◀ Vorheriges',
        "btn_next": 'Nächste ▶',
        "btn_back_vocab": '◀ Zurück zur Liste',
        "btn_cancel": '❌ Abbrechen',
        "btn_edit_pos": '✏️ Grammatikartikel',
        "btn_edit_def": '✏️ Definition',
        "btn_edit_ctx": '✏️ Beispiel',
        "btn_add_lang": '➕ Füge Sprache hinzu',
        "btn_set_native": 'aset Nativer Sprache',
        "btn_back": '← Zurück',
        "btn_change_window": '⏰ Änderungsfenster',
        "btn_all_day": 'All Tag (00:00–24:00)',
        "onboard_native_title": '👋 Willkommen bei Vocab Master!\nBitte wählen Sie Ihre Muttersprache (wird für die Benutzeroberfläche und Definitionen verwendet):',
        "onboard_lang_title": 'Great! Bitte wähle die Sprache, die du lernen möchtest:',
        "onboard_done": '✅ Einrichtung abgeschlossen!\n\nDie Überprüfungs-Erinnerungen sind für 08:00–22:00 Uhr lokale Zeit eingestellt. Sie können dies in /settings anpassen.\n\nStarten Sie das Lernen, indem Sie jetzt ein Wort oder eine Phrase senden!',
    },

    "es": {
        "help_text": '*Comandos:*\n/vocab — Ver tu lista de vocabulario\n/review — Iniciar una sesión de revisión\n/practice — Practicar libremente (sin seguimiento de progreso)\n/language — Administrar idiomas de aprendizaje\n/search <palabra> — Buscar vocabulario\n/export — Exportar vocabulario como CSV\n/stats — Estadísticas de aprendizaje\n/streak — Días de aprendizaje consecutivos\n/update <palabra> — Editar el POS/definición/ejemplo de una palabra\n/delete <palabra> — Eliminar una palabra de la lista de vocabulario\n/timezone — Establecer la zona horaria de recordatorio de revisiones\n/settings — Configuración de notificaciones\n/plan — Ver estado de suscripción\n/activate <código> — Activar suscripción\n/help — Mostrar este mensaje de ayuda',
        "start_welcome": '👋 Bienvenido a *Vocab Master*!\n\nTe ayudo a recordar vocabulario utilizando la curva de olvido de Ebbinghaus para programar las revisiones automáticamente.\nSoporta múltiples idiomas: Inglés, Japonés, Francés y más.\n\n*Cómo usarlo:*\n• Envía una palabra o frase (e.g. `devastado`)\n• Envía una oración con la palabra objetivo (e.g. `Estaba utterly devastado`)\n• Envía una palabra en tu idioma nativo y encontraré el vocabulario.',
        "start_tz_prompt": '🌏 Por favor establece tu zona horaria para recordatorios de revisión precisos:',
        "quiz_meaning_title": '💡 *Prueba de Significado*',
        "quiz_meaning_instruction": 'Selecciona el significado de *{word}* en contexto:',
        "quiz_fill_title": '🧠 <b>Completar el espacio en blanco</b>',
        "quiz_fill_instruction": 'Selecciona la mejor palabra para completar: <b>______</b>:',
        "quiz_fill_hint": '💡 Pista: {definition}',
        "quiz_correct": '✅ *Correcto!*\n\n*{word}* — {definition}{context_line}\n\n🎯 Nivel: {level}\n📅 Próxima revisión: {date}',
        "quiz_correct_practice": '✅ *Correcto!*\n\n*{word}* — {definition}{context_line}\n\n🎮 Modo de práctica — no cuenta para el progreso',
        "quiz_wrong_fill": '❌ *Incorrecto*\n\nRespuesta correcta: *{word}* — {definition}{context_line}\n\n😔 Nivel down: {level}\n📅 Revisa de nuevo mañana!',
        "quiz_wrong_meaning": '❌ *Incorrecto*\n\nSignificado correcto: *{word}* — {definition}{context_line}\n\n😔 Nivel down a: {level}\n📅 Revisa de nuevo mañana!',
        "quiz_wrong_fill_practice": '❌ *Incorrecto*\n\nRespuesta correcta: *{word}* — {definition}{context_line}\n\n🎮 Modo práctica — no cuenta para el progreso',
        "quiz_wrong_meaning_practice": '❌ *Incorrecto*\n\nSignificado correcto: *{word}* — {definition}{context_line}\n\n🎮 Modo de práctica — no cuenta para el progreso',
        "quiz_skip_append": '⏭ Saltado — revisar de nuevo mañana',
        "quiz_fuzzy_append": '🤔 Marcado como borroso — revisar de nuevo mañana',
        "quiz_done": '🎉 Sesión de revisión completa! Mantén la momentum~',
        "practice_done": '🎉 Practicar completo!',
        "quiz_end_review": 'Revisión terminada. Puedes hacer /review nuevamente en cualquier momento~',
        "quiz_end_practice": 'Práctica terminada. Puedes volver a /review en cualquier momento~',
        "level_0": 'Iniciador',
        "level_1": 'Elementario',
        "level_2": 'Plazo corto',
        "level_3": 'Intermedio',
        "level_4": 'Plazo largo',
        "level_5": 'Avanzado',
        "level_6": 'Domina',
        "level_7": 'Maestrado ✓',
        "session_active": '⏳ Actualmente estás en una sesión de {mode}. Por favor, termina la pregunta actual primero~\n(Clica el botón End para detenerte temprano)',
        "session_mode_review": 'revisión',
        "session_mode_practice": 'practicar',
        "vocab_empty": 'Tu vocabulario en {lang_name} está vacío~\nEnvía cualquier palabra para comenzar a construir, o usa /language para cambiar.',
        "vocab_title": '📚 *{lang_name} Vocabulario* (Página {page}/{total_pages}, {total} palabras)',
        "vocab_click_hint": 'Toque un botón de palabra para ver detalles',
        "vocab_record_not_found": 'Registro de vocabulario no encontrado, puede que haya sido eliminado.',
        "vocab_mastered": '✓ Maestro',
        "review_no_vocab": 'Tu vocabulario en {lang_name} está vacío~\nEnvía cualquier palabra para empezar a construir!',
        "review_no_due_practice": '⏳ No hay palabras pendientes para {lang_name}. Entrando en modo de práctica (sin seguimiento de progreso)…',
        "review_generating": '⏳ Generando una pregunta de revisión de {lang_name}…',
        "review_error": 'Falló al generar una pregunta de revisión. Por favor inténtalo de nuevo.',
        "practice_empty": 'Tu vocabulario en {lang_name} está vacío~\nEnvía algunas palabras primero!\nO usa /language para cambiar.',
        "practice_start": '🎮 Entrando en el modo de práctica de {lang_name} (sin seguimiento de progreso)…',
        "practice_error": 'Falló al generar una pregunta de práctica. Por favor inténtalo de nuevo.',
        "stats_title": '📊 *Estadísticas de Aprendizaje* ({lang_name})',
        "stats_total": '📚 Total palabras: {count}',
        "stats_today_added": '➕ Añadidos hoy: {count}',
        "stats_due": '⚡ Por revisar: {count}',
        "stats_level_dist": '*Distribución de niveles:*',
        "stats_level_line": 'Niv{level} {label:<8}  {count:>4} wds  {bar}  {pct}%',
        "stats_lang_dist": '*Vocabulario por idioma:*',
        "stats_lang_item": '• {display} — {count} palabras',
        "stats_lv_labels": 'Iniciante|Elem.|Corto|Inter.|Largo|Avanz.|Maestro|Hecho',
        "streak_title": '🔥 *Racha: {streak}*',
        "streak_total": '📊 Total reviews: {count}',
        "streak_0": '0 días (<i>no comenzado aún</i>)',
        "streak_1": '1 día 🌱',
        "streak_few": '{días} días 📈',
        "streak_week": '{days} días 🔥',
        "streak_month": '{days} días 🏆',
        "processing": '⏳ Procesando…',
        "parse_fail": '😕 No se pudo analizar. Por favor, inténtalo de nuevo.\n( Si esto persiste, verifica la configuración de tu servicio de AI)',
        "parse_fail_simple": '😕 No se pudo analizar. Por favor, inténtalo de nuevo.',
        "sentence_no_vocab": '📖 *Traducción:* {translation}\n\n(Ninguna vocabulario digno de salvar se identificó)',
        "sentence_add_prompt": '📖 *Traducción:* {translation}\n\n*Toque una palabra abajo para agregarla al vocabulario:*\n{vocab_lines}',
        "batch_result_title": '📚 *Resultado de la adición en lote ({done}/{total}):*',
        "batch_hit_limit": '⚠️ Límite alcanzado — las palabras restantes no fueron procesadas.',
        "batch_parse_fail": '❌ {token} — parse fallido',
        "batch_not_vocab": '❌ {token} — no es un elemento de vocabulario válido',
        "batch_save_fail": '❌ {word} — guardado fallido',
        "batch_new": '✅ *{word}*{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition} (ya en el vocabulario)*',
        "limit_total_reached": '📚 Se ha alcanzado el límite de vocabulario ({limit} palabras).\nEnvía `/activate <code>` para suscribirte a Pro para obtener un vocabulario ilimitado.',
        "limit_daily_reached": '⏰ Se ha alcanzado el límite diario ({limit} palabras hoy). Vuelve mañana!\nEnvía `/activate <code>` para suscribirte a Pro y eliminar los límites.',
        "limit_both_reached": '📚 Límite alcanzado (vocábulo: {total_limit} / diario: {daily_limit} palabras).\nEnvía `/activate <code>` para suscribirte a Pro para un número ilimitado de vocábulos.',
        "limit_total_alert": 'Límite de vocabulario alcanzado ({limit} palabras). Suscríbete a Pro para tener acceso ilimitado.',
        "limit_daily_alert": 'Límite diario alcanzado ({limit} palabras). Vuelve mañana o suscríbete a Pro.',
        "edit_field_pos": 'POS',
        "edit_field_def": 'definición',
        "edit_field_ctx": 'ejemplo',
        "edit_prompt": '✏️ Edit *{word}* — {field}\n\nCurrent: _{current}_\n\nPor favor envía el nuevo {field}:',
        "edit_updated": "✅ *{word}*'s {field} ha sido actualizado.",
        "edit_failed": '⚠️ Actualización fallida — el registro puede que ya no exista.',
        "edit_unknown_field": 'Campo desconocido',
        "edit_detail_review": 'Revisado {count}×',
        "edit_detail_next": 'Siguiente {date}',
        "delete_cancelled": '❌ Eliminación cancelada.',
        "delete_ok_one": '✅ Entrada de vocabulario eliminada.',
        "delete_ok_all": '✅ Eliminadas todas {count} entradas para "{word}".',
        "delete_failed": '⚠️ Eliminación fallida — el registro puede que ya no exista.',
        "delete_batch_title": '🗑️ *Borrar en lote resultados:*',
        "delete_batch_ok": '✅ {word} — eliminadas {count} entradas',
        "delete_batch_not_found": '❌ {word} — no encontrado en vocabulario',
        "settings_title": '🔔 *Configuración de Notificaciones*',
        "settings_tz_line": '🌏 Zona horaria: `{tz}` (cambiar con /timezone)',
        "settings_window_line": '⏰ Ventana de recordatorio: {start}:00 – {end}:00',
        "settings_push_label": '📢 Recordatorios de revisión automática: {status}',
        "settings_push_on": '✅ En',
        "settings_push_off": '❌ Apagado',
        "settings_window_prompt": '⏰ Selecciona la ventana de recordatorio:',
        "settings_toggle_off": '🔕 Apagar',
        "settings_toggle_on": '🔔 Enciende',
        "settings_review_scope_label": '🌐 Alcance de revisión: {status}',
        "settings_review_scope_on": 'Solo idioma activo',
        "settings_review_scope_off": 'Todos los idiomas',
        "settings_toggle_review_scope_on": '🌐 Cambiar a: Solo idioma activo',
        "settings_toggle_review_scope_off": '🌐 Cambiar a: Todos los idiomas',
        "timezone_title": '🌏 *Configuración del Timezone*',
        "timezone_prompt": 'Zona horaria actual: `{tz}`\n\nLos recordatorios se envían entre las 08:00 y las 22:00 hora local.\nPor favor, seleccione su zona horaria:',
        "timezone_saved": '🌏 <b>Configuración del Timezone</b>\n\n✅ Zona horaria guardada: `{tz}`\n\nLos recordatorios se envían entre las 08:00 y las 22:00 hora local.\n\nUsa /settings para ajustar el intervalo o desactivar los recordatorios.',
        "timezone_save_fail": 'Falló el guardado. Por favor, inténtalo de nuevo.',
        "timezone_saved_toast": '✅ Zona horaria establecida en {tz}',
        "lang_panel_title": '🌍 <b>Gestor de Aprendizaje de Lenguajes</b>',
        "lang_active_line": '📖 Idioma activo: {display}',
        "lang_native_line": '🔤 Idioma de definición (nativo): {display}',
        "lang_vocab_label": '<b>Tu vocabulario:</b>',
        "lang_vocab_count": '• {display} — {count} palabras',
        "lang_add_title": '➕ <b>Añadir Lengua de Aprendizaje</b>\n\nSelecciona una lengua para añadir (✓ = ya tiene vocabulario):',
        "lang_native_title": '🔤 <b>Set Definición Language (Nativo)</b>\n\nSelecciona el idioma en el que deseas que se muestren las definiciones:',
        "expiry_reminder": '⏰ Tu suscripción Pro expira el *{date}* (dentro de 3 días).\nEnvía `/activate <code>` para renovar y mantener tu vocabulario ilimitado.',
        "btn_skip": '⏭ Saltar',
        "btn_fuzzy": '🤔 Confuso / No está seguro',
        "btn_end_review": '🔚 Finalizar Revisión',
        "btn_end_practice": '🔚 Finaliza Práctica',
        "btn_prev": '◀ Previo',
        "btn_next": 'Siguiente ▶',
        "btn_back_vocab": '◀ Volver a la Lista',
        "btn_cancel": '❌ Cancelar',
        "btn_edit_pos": '✏️ POS',
        "btn_edit_def": '✏️ Definición',
        "btn_edit_ctx": '✏️ Ejemplo',
        "btn_add_lang": '➕ Añadir Lenguaje',
        "btn_set_native": 'LENGUAJE NATIVO',
        "btn_back": '← Atrás',
        "btn_change_window": '⏰ Cambiar Ventana',
        "btn_all_day": 'Todo el día (00:00–24:00)',
        "onboard_native_title": '👋 Bienvenido a Vocab Master!\nPor favor selecciona tu idioma nativo (usado para la interfaz y las definiciones):',
        "onboard_lang_title": 'Genial! Por favor selecciona el idioma que quieres aprender:',
        "onboard_done": '✅ Configuración completa!\n\nLos recordatorios se establecieron para el horario local de 08:00 a 22:00. Puedes ajustar esto en /settings.\n\nComienza a aprender enviando una palabra o una oración ahora!',
    },

    "pt": {
        "help_text": '*Comandos:*\n/vocab — Visualize sua lista de vocabulário\n/review — Inicie uma sessão de revisão\n/practice — Prática livre (sem rastreamento de progresso)\n/language — Gerencie as línguas de aprendizagem\n/search <palavra> — Buscar vocabulário\n/export — Exportar vocabulário como CSV\n/stats — Estatísticas de aprendizagem\n/streak — Dias de aprendizagem consecutivos\n/update <palavra> — Edite a POS/definição/exemplo de uma palavra\n/delete <palavra> — Exclua uma palavra da lista de vocabulário\n/timezone — Defina o fuso horário das lembretes de revisão\n/settings — Configurações de notificações\n/plan — Visualize o status da assinatura\n/activate <code> — Ative a assinatura\n/help — Mostrar esta mensagem de ajuda',
        "start_welcome": '👋 Bem-vindo ao *Vocab Master*!\n\nAjudo você a lembrar de vocabulário usando a curva de esquecimento de Ebbinghaus para agendar revisões automaticamente.\nSuporta múltiplos idiomas: Inglês, Japonês, Francês e mais.\n\n*Como usar:*\n• Envie uma palavra ou frase (e.g. `devastado`)\n• Envie uma frase com a palavra-alvo (e.g. `Eu estava absolutamente devastado`)\n• Envie uma palavra em seu idioma nativo e eu encontrarei o vocabulário.',
        "start_tz_prompt": '🌏 Por favor, configure seu fuso horário para lembretes de revisão precisos:',
        "quiz_meaning_title": '🔤 *Questionamento de Significado*',
        "quiz_meaning_instruction": 'Selecione o significado de *{word}* no contexto:',
        "quiz_fill_title": '🧠 <b>Pre enchere o vácuo</b>',
        "quiz_fill_instruction": 'Selecione a melhor palavra para preencher <b>______</b>:',
        "quiz_fill_hint": '💡 Dica: {definition}',
        "quiz_correct": '✅ *Correto!*\n\n*{word}* — {definition}{context_line}\n\n🎯 Nível: {level}\n📅 Próxima revisão: {date}',
        "quiz_correct_practice": '✅ *Correto!*\n\n*{word}* — {definition}{context_line}\n\n🎮 Modo de prática — não conta para o progresso',
        "quiz_wrong_fill": '❌ *Errado*\n\nResposta correta: *{word}* — {definition}{context_line}\n\n😔 Nível reduzido para: {level}\n📅 Reveja amanhã!',
        "quiz_wrong_meaning": '❌ *Errado*\n\nSignificado correto: *{word}* — {definition}{context_line}\n\n😔 Nível reduzido para: {level}\n📅 Reveja amanhã!',
        "quiz_wrong_fill_practice": '❌ *Errado*\n\nResposta correta: *{word}* — {definition}{context_line}\n\n🎮 Modo de prática — não conta para o progresso',
        "quiz_wrong_meaning_practice": '❌ *Errado*\n\nSignificado correto: *{word}* — {definition}{context_line}\n\n🎮 Modo de prática — não conta para o progresso',
        "quiz_skip_append": '⏭ Pulado — revise novamente amanhã',
        "quiz_fuzzy_append": '🤔 Marcado como vagaroso — revise novamente amanhã',
        "quiz_done": '🎉 Sessão de revisão concluída! Manter essa dinâmica~',
        "practice_done": '🎉 Prática completa!',
        "quiz_end_review": 'A revisão acabou. Você pode fazer /review novamente quando quiser~',
        "quiz_end_practice": 'Prática concluída. Você pode fazer /review novamente a qualquer momento~',
        "level_0": 'Iniciante',
        "level_1": 'Elementar',
        "level_2": 'Prazo Curto',
        "level_3": 'Intermediário',
        "level_4": 'Prazo longo',
        "level_5": 'Avançado',
        "level_6": 'Dominando',
        "level_7": 'Mestre ✓',
        "session_active": '⏳ Você está atualmente em uma sessão de {mode}. Por favor, conclua a pergunta atual primeiro~\n(Clique no botão Fim abaixo para parar cedo)',
        "session_mode_review": 'revisão',
        "session_mode_practice": 'prática',
        "vocab_empty": 'Sua {lang_name} vocabulário está vazio~\nEnvie qualquer palavra para começar a construir, ou use /language para trocar.',
        "vocab_title": '📚 *{lang_name} Vocabulário* (Página {page}/{total_pages}, {total} palavras)',
        "vocab_click_hint": 'Toque em um botão de palavra para visualizar detalhes',
        "vocab_record_not_found": 'Registo de vocabulário não encontrado, pode ter sido deletado.',
        "vocab_mastered": '✓ Dominado',
        "review_no_vocab": 'Sua {lang_name} vocabulário está vazio~\nEnvie qualquer palavra para começar a construir!',
        "review_no_due_practice": '⏳ Não há palavras em dia para {lang_name}. Entrando no modo de prática (sem rastreamento de progresso)…',
        "review_generating": '⏳ Gerando uma pergunta de revisão em {lang_name}…',
        "review_error": 'Falhou em gerar uma pergunta de revisão. Tente novamente.',
        "practice_empty": 'Sua {lang_name} vocabulário está vazio~\nEnvie algumas palavras primeiro!\nOu use /language para trocar.',
        "practice_start": '🎮 Entrando no modo de prática de {lang_name} (sem rastreamento de progresso)…',
        "practice_error": 'Falhou em gerar uma pergunta de prática. Por favor, tente novamente.',
        "stats_title": '📊 *Estatísticas de Aprendizagem* ({lang_name})',
        "stats_total": '📚 Total palavras: {count}',
        "stats_today_added": '➕ Adicionado hoje: {count}',
        "stats_due": '⚡ Próximo para revisão: {count}',
        "stats_level_dist": '*Distribuição de níveis:*',
        "stats_level_line": 'Nível{level} {label:<8}  {count:>4} pal  {bar}  {pct}%',
        "stats_lang_dist": '*vocabulário por língua:*',
        "stats_lang_item": '• {display} — {count} palavras',
        "stats_lv_labels": 'Iniciante|Elem.|Breve|Inter.|Longo|Avançado|Mestre|Concluído',
        "streak_title": '🔥 *Streak: {streak}*',
        "streak_total": '📊 Total de revisões: {count}',
        "streak_0": '0 dias (`data`) (ainda não iniciado)',
        "streak_1": '1 dia 🌱',
        "streak_few": '{days} dias 📈',
        "streak_week": '{days} dias 🔥',
        "streak_month": '{days} dias 🏆',
        "processing": '⏳ Processando…',
        "parse_fail": '😕 Falhou no parsing. Tente novamente.\n(Caso isso persista, verifique a configuração do seu serviço de AI)',
        "parse_fail_simple": '😕 Falhou no parsing. Tente novamente.',
        "sentence_no_vocab": '📖 *Tradução:* {translation}\n\n(Nenhum vocabulário digno de ser salvo foi identificado)',
        "sentence_add_prompt": '📖 *Tradução:* {translation}\n\n*Toque em uma palavra abaixo para adicionar ao vocabulário:*\n{vocab_lines}',
        "batch_result_title": '📚 *Resultados de lote ({done}/{total}):*',
        "batch_hit_limit": '⚠️ Limite atingido — as palavras restantes não foram processadas.',
        "batch_parse_fail": '❌ {token} — parse falhou',
        "batch_not_vocab": '❌ {token} — não é um item de vocabulário válido',
        "batch_save_fail": '❌ {word} — salvamento falhou',
        "batch_new": '✅ *{word}*{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition} (já na vocabulário)*',
        "limit_total_reached": '📚 O limite de vocabulário foi alcançado ({limit} palavras).\nEnvie `/activate <code>` para se inscrever no Pro para acesso ilimitado ao vocabulário.',
        "limit_daily_reached": '⏰ Limite diário atingido ({limit} palavras hoje). Volte amanhã!\nEnvie `/activate <code>` para se inscrever no Pro e remover os limites.',
        "limit_both_reached": '📚 Limite alcançado (vocabulário: {total_limit} / diário: {daily_limit} palavras).\nEnvie `/activate <code>` para se inscrever no Pro para vocabulário ilimitado.',
        "limit_total_alert": 'Limite de vocabulário alcançado ({limit} palavras). Assine o Pro para acesso ilimitado.',
        "limit_daily_alert": 'Limite diário atingido ({limit} palavras). Volte amanhã ou assine o Pro.',
        "edit_field_pos": 'POS',
        "edit_field_def": 'definição',
        "edit_field_ctx": 'exemplo',
        "edit_prompt": '✏️ Edite *{word}* — {field}\n\nAtual: _{current}_\n\nPor favor, envie o novo {field}:',
        "edit_updated": "✅ *{word}*'s {field} has been updated.",
        "edit_failed": '⚠️ Atualização falhou — o registro pode não existir mais.',
        "edit_unknown_field": 'Campo desconhecido',
        "edit_detail_review": 'Revisado {count}×',
        "edit_detail_next": 'Próximo {date}',
        "delete_cancelled": '❌ Exclusão cancelada.',
        "delete_ok_one": '✅ Entrada de vocabulário deletada.',
        "delete_ok_all": '✅ Excluídas todas as {count} entradas para "{word}".',
        "delete_failed": '⚠️ Exclusão falhou — o registro pode não existir mais.',
        "delete_batch_title": '🗑️ *Resultado da deleção em lote:*',
        "delete_batch_ok": '✅ {word} — excluídas {count} entradas',
        "delete_batch_not_found": '❌ {word} — não encontrado no vocabulário',
        "settings_title": '🔔 *Configurações de Notificações*',
        "settings_tz_line": '🌏 Fuso Horário: `{tz}` (alterar com /timezone)',
        "settings_window_line": '⏰ Janela de lembrete: {start}:00 – {end}:00',
        "settings_push_label": '📢 Lembretes de revisão automática: {status}',
        "settings_push_on": '✅ On',
        "settings_push_off": '❌ Desativado',
        "settings_window_prompt": '⏰ Selecione a janela de lembrete:',
        "settings_toggle_off": '🔕 Desligar',
        "settings_toggle_on": '🔔 Ativar',
        "settings_review_scope_label": '🌐 Escopo de revisão: {status}',
        "settings_review_scope_on": 'Apenas idioma ativo',
        "settings_review_scope_off": 'Todos os idiomas',
        "settings_toggle_review_scope_on": '🌐 Mudar para: Apenas idioma ativo',
        "settings_toggle_review_scope_off": '🌐 Mudar para: Todos os idiomas',
        "timezone_title": '🌏 *Configurações de Fuso Horário*',
        "timezone_prompt": 'Horário atual: `{tz}`\n\nLembrames são enviados entre 08:00–22:00 horário local.\nPor favor, selecione seu horário timezone:',
        "timezone_saved": '🌏 *Configurações de Fuso Horário*\n\n✅ Fuso horário salvo: `{tz}`\n\nLembrames são enviados entre 08:00–22:00 horário local.\n\nUse /settings para ajustar a janela ou desabilitar os lembretes',
        "timezone_save_fail": 'Falhou no salvamento. Por favor, tente novamente.',
        "timezone_saved_toast": '✅ Fuso horário definido para {tz}',
        "lang_panel_title": '🌍 <b>Gestor de Aprendizagem de Línguas</b>',
        "lang_active_line": '📖 Língua ativa: {display}',
        "lang_native_line": '🔤 Definição em {display} (nativo):',
        "lang_vocab_label": '<b>Sua vocabulário:</b>',
        "lang_vocab_count": '• {display} — {count} palavras',
        "lang_add_title": '➕ <b>Adicionar Língua de Estudo</b>\n\nSelecione uma língua para adicionar (✓ = já possui vocabulário):',
        "lang_native_title": '🔤 <b>Definir Língua das Definições (Nativa)</b>\n\nSelecione a língua em que as definições serão exibidas:',
        "expiry_reminder": '⏰ Sua assinatura Pro expira em *{date}* (dentro de 3 dias).\nEnvie `/activate <code>` para renovar e manter seu vocabulário ilimitado.',
        "btn_skip": '⏭ Pular',
        "btn_fuzzy": '🤔 Fuzzy / Não tem certeza',
        "btn_end_review": '🔚 Encerrar Revisão',
        "btn_end_practice": '🔚 Encerrar Prática',
        "btn_prev": '◀ Anterior',
        "btn_next": 'Próximo ▶',
        "btn_back_vocab": '◀ Voltar para a Lista',
        "btn_cancel": '❌ Cancelar',
        "btn_edit_pos": '✏️ POS',
        "btn_edit_def": '✏️ Definição',
        "btn_edit_ctx": '✏️ Exemplo',
        "btn_add_lang": '➕ Adicionar Língua',
        "btn_set_native": '🔤 Definir Língua Nativa',
        "btn_back": '← Voltar',
        "btn_change_window": '⏰ Mudar Janela',
        "btn_all_day": 'De todo o dia (00:00–24:00)',
        "onboard_native_title": '👋 Bem-vindo ao Vocab Master!\nPor favor, selecione sua língua nativa (usada para interface e definições):',
        "onboard_lang_title": 'Ótimo! Por favor, selecione o idioma que deseja aprender:',
        "onboard_done": '✅ Configuração concluída!\n\nOs lembretes de revisão estão configurados para 08:00–22:00 horário local. Você pode ajustar isso em /settings.\n\nComece a estudar enviando uma palavra ou frase agora!',
    },

    "ru": {
        "help_text": '*Команды:*\n/vocab — Просмотр вашей списка слов\n/review — Начать сессию обзора\n/practice — Свободная практика (без отслеживания прогресса)\n/language — Управление изучаемыми языками\n/search <слово> — Поиск слова\n/export — Экспорт списка слов в CSV\n/stats — Статистика обучения\n/streak — Количество последовательных дней обучения\n/update <слово> — Изменить часть речи/определение/пример слова\n/delete <слово> — Удалить слово из списка слов\n/timezone — Установить часовой пояс напоминаний\n/settings — Настройки уведомлений\n/plan — Просмотр статуса подписки\n/activate <код> — Активировать подписку\n/help — Показать это сообщение помощи',
        "start_welcome": '👋 Добро пожаловать в *Vocab Master*!\n\nЯ помогаю вам запоминать новое слово с помощью кривой забывания Эbbinghaus, автоматически планируя повторения.\nПоддерживает несколько языков: английский, японский, французский и другие.\n\n*Как использовать:*\n• Отправьте слово или фразу (например, `devastated`)\n• Отправьте предложение с целым словом (например, `Я был полностью разбомблен`)\n• Отправьте слово на вашем родном языке, и я найду соответствующее новое слово',
        "start_tz_prompt": '🌏 Пожалуйста, установите ваш часовой пояс для точных напоминаний о проверке:',
        "quiz_meaning_title": '💡 *Тест значения*',
        "quiz_meaning_instruction": 'Выберите значение слова *{word}*, которое подходит в данном контексте:',
        "quiz_fill_title": '🧠 <b>Заполните пропуски</b>',
        "quiz_fill_instruction": 'Выберите наиболее подходящее слово для заполнения <b>______</b>:',
        "quiz_fill_hint": '💡 Подсказка: {definition}',
        "quiz_correct": '✅ *Правильно!*\n\n*{слово}* — {определение}{контекстная_строка}\n\n🎯 Уровень: {уровень}\n📅 Следующий обзор: {дата}',
        "quiz_correct_practice": '✅ *Правильно!*\n\n*{слово}* — {определение}{контекстная_строка}\n\n🎮 Режим практики — не считается в прогрессе',
        "quiz_wrong_fill": '❌ *Неверно*\n\nПравильный ответ: *{слово}* — {определение}{контекстная_строка}\n\n😔 Уровень уменьшен до: {уровень}\n📅 Проверьте снова завтра!',
        "quiz_wrong_meaning": '❌ *Неверно*\n\nПравильное значение: *{слово}* — {определение}{контекстная_строка}\n\n😔 Уровень уменьшен до: {уровень}\n📅 Проверь снова завтра!',
        "quiz_wrong_fill_practice": '❌ *Неверно*\n\nПравильный ответ: *{слово}* — {определение}{контекстная_строка}\n\n🎮 Режим практики — не считается в прогрессе',
        "quiz_wrong_meaning_practice": '❌ *Неверно*\n\nПравильное значение: *{слово}* — {определение}{контекстная_строка}\n\n🎮 Режим практики — не учитывается в прогрессе',
        "quiz_skip_append": '⏭ Пропущено — обзор завтра',
        "quiz_fuzzy_append": '🤔 Отмечено как неясное — обновите завтра',
        "quiz_done": '🎉 Сессия обзора завершена! Держись за импульс~',
        "practice_done": '🎉 Практика завершена!',
        "quiz_end_review": 'Ревью завершено. Вы можете повторно использовать команду /review в любое время~',
        "quiz_end_practice": 'Практическая сессия завершена. Вы можете повторно использовать команду /review в любое время~',
        "level_0": 'Начинающий',
        "level_1": 'Элементарный',
        "level_2": 'Краткосрочный',
        "level_3": 'Интерmediate',
        "level_4": 'Долгосрочный',
        "level_5": 'Продвинутое',
        "level_6": 'Маstерство',
        "level_7": 'Маstерил ✓',
        "session_active": '⏳ Вы находитесь в сессии в режиме {mode}. Пожалуйста, завершите текущий вопрос сначала~\n(Нажмите кнопку Конец ниже, чтобы завершить сессию досрочно)',
        "session_mode_review": '复习',
        "session_mode_practice": 'practice',
        "vocab_empty": 'Ваш словарь на {lang_name} пуст~\nОтправьте любое слово, чтобы начать формирование, или используйте /language для смены.',
        "vocab_title": '📚 *{lang_name} Вocabulario* (Страница {page}/{total_pages}, {total} слов)',
        "vocab_click_hint": 'Нажмите на кнопку слова для просмотра деталей',
        "vocab_record_not_found": 'Словарная запись не найдена, она может быть удалена.',
        "vocab_mastered": '✓ Мастер',
        "review_no_vocab": 'Ваш словарь на {lang_name} пуст~\nОтправьте любой слово, чтобы начать создание!',
        "review_no_due_practice": '⏳ Нет слов для изучения в {lang_name}. Вход в режим практики (нет отслеживания прогресса)…',
        "review_generating": '⏳ Генерирование вопроса для проверки по {lang_name}…',
        "review_error": 'Не удалось сгенерировать вопрос для проверки. Пожалуйста, попробуйте снова.',
        "practice_empty": 'Ваш словарь на {lang_name} пуст~\nСначала отправьте некоторые слова!\nИли используйте /language для смены.',
        "practice_start": '🎮 Вход в режим практики по {lang_name} (без отслеживания прогресса)…',
        "practice_error": 'Не удалось сгенерировать вопрос для тренировки. Пожалуйста, попробуйте снова.',
        "stats_title": '📊 *Статистика обучения* ({lang_name})',
        "stats_total": '📚 Общее количество слов: {count}',
        "stats_today_added": '➕ Добавлено сегодня: {count}',
        "stats_due": '⚡ Количество для проверки: {count}',
        "stats_level_dist": '*Распределение уровней:*',
        "stats_level_line": 'Lv{level} {label:<8}  {count:>4} слов  {bar}  {pct}%',
        "stats_lang_dist": '*Вocabulary по языкам:*',
        "stats_lang_item": '• {display} — {count} слова',
        "stats_lv_labels": 'Начинающий|Базовый|Короткий|Средний|Длинный|Продвинутый|Мастер|Завершено',
        "streak_title": '🔥 *Стreak: {streak}*',
        "streak_total": '📊 Общее количество обзревов: {count}',
        "streak_0": '0 дней (не начато еще)',
        "streak_1": '1 день 🌱',
        "streak_few": '{days} дней 📈',
        "streak_week": '{days} дней 🔥',
        "streak_month": '{days} дней 🏆',
        "processing": '⏳ Обработка…',
        "parse_fail": '😕 Не удалось разобрать. Пожалуйста, попробуйте снова.\n(Если это продолжается, проверьте конфигурацию вашей службы AI)',
        "parse_fail_simple": '😕 Не удалось разобрать. Пожалуйста, попробуйте снова.',
        "sentence_no_vocab": '📖 *Перевод:* {translation}\n\n(Ни одного слова worth saving не найдено)',
        "sentence_add_prompt": '📖 *Перевод:* {translation}\n\n*Нажмите на слово ниже, чтобы добавить его в словарь:*\n{vocab_lines}',
        "batch_result_title": '📚 *Добавление результатов в批次({done}/{total}):*',
        "batch_hit_limit": '⚠️ Лимит достигнут — оставшиеся слова не были обработаны.',
        "batch_parse_fail": '❌ {token} — парсинг провален',
        "batch_not_vocab": '❌ {token} — не действительный элемент словаря',
        "batch_save_fail": '❌ {слово} — сохранение не удалось',
        "batch_new": '✅ <b>{слово}*{part_of_speech}</b> — {определение}',
        "batch_exists": '📖 *{слово}*{pos_tag} — {определение} (уже в словаре)*',
        "limit_total_reached": '📚 Лимитlexical_词汇_已达 ({limit} слов).\nОтправьте `/activate <code>` для подписки на Pro для неограниченного количестваlexical_词汇_.',
        "limit_daily_reached": '⏰ Дневной лимит достигнут ({limit} слов сегодня). Приходите завтра!\nОтправьте `/activate <code>`, чтобы подключиться к Pro без лимитов.',
        "limit_both_reached": '📚 Лимит достигнут (словарный запас: {total_limit} / ежедневно: {daily_limit} слов).\nОтправьте `/activate <code>` для подписки на Pro для неограниченного словарного запаса.',
        "limit_total_alert": 'Лимит словарного запаса достигнут ({limit} слов). Подпишитесь на Pro для неограниченного количества слов.',
        "limit_daily_alert": 'Дневной лимит достигнут ({limit} слов). Вернитесь завтра или подпишитесь на Pro.',
        "edit_field_pos": 'POS',
        "edit_field_def": 'определение',
        "edit_field_ctx": 'пример',
        "edit_prompt": '✏️ Редактировать *{word}* — {field}\n\nТекущее: _{current}_\n\nПожалуйста, отправьте новый {field}:',
        "edit_updated": "✅ *{слово}*'s {поле} было обновлено.",
        "edit_failed": '⚠️ Обновление не удалось — запись может больше не существовать.',
        "edit_unknown_field": 'Неизвестное поле',
        "edit_detail_review": 'Отзывлено {count}×',
        "edit_detail_next": 'Следующий {date}',
        "delete_cancelled": '❌ Удаление отменено.',
        "delete_ok_one": '✅ Словоудалено.',
        "delete_ok_all": '✅ Удалено все {count} записи для "{word}".',
        "delete_failed": '⚠️ Удаление не удалось — запись может уже не существовать.',
        "delete_batch_title": '🗑️ *Пакетное удаление результатов:*',
        "delete_batch_ok": '✅ {слово} — удалено {количество} записей',
        "delete_batch_not_found": '❌ {слово} — не найдено в словаре',
        "settings_title": '🔔 *Уведомления*',
        "settings_tz_line": '🌏 Часовой пояс: `{tz}` (изменить с помощью /timezone)',
        "settings_window_line": '⏰ Окно напоминаний: {start}:00 – {end}:00',
        "settings_push_label": '📢 Помощь с автоматическим обзором: {status}',
        "settings_push_on": '✅ На',
        "settings_push_off": '❌ Выключено',
        "settings_window_prompt": '⏰ Выберите окно напоминания:',
        "settings_toggle_off": '🔕 Отключить',
        "settings_toggle_on": '🔔 Включить',
        "settings_review_scope_label": '🌐 Область повторения: {status}',
        "settings_review_scope_on": 'Только активный язык',
        "settings_review_scope_off": 'Все языки',
        "settings_toggle_review_scope_on": '🌐 Переключить на: Только активный язык',
        "settings_toggle_review_scope_off": '🌐 Переключить на: Все языки',
        "timezone_title": '🌏 <b>Настройки часового пояса</b>',
        "timezone_prompt": 'Текущий часовой пояс: `{tz}`\n\nНапоминания отправляются с 08:00–22:00 местного времени.\nПожалуйста, выберите ваш часовой пояс:',
        "timezone_saved": '🌏 <b>Настройки часового пояса</b>\n\n✅ Часовой пояс сохранен: `{tz}`\n\nНапоминания отправляются с 08:00–22:00 местного времени.\n\nИспользуйте /settings для корректировки интервала или отключения напоминаний',
        "timezone_save_fail": 'Не удалось сохранить. Пожалуйста, попробуйте снова.',
        "timezone_saved_toast": '✅ Временная зона установлена в {tz}',
        "lang_panel_title": '🌍 <b>Менеджер изучения языков</b>',
        "lang_active_line": '📖 Активный язык: {display}',
        "lang_native_line": '💡 Определение на родном языке: {display}',
        "lang_vocab_label": '<b>Ваш словарный запас:</b>',
        "lang_vocab_count": '• {display} — {count} слова',
        "lang_add_title": '➕ <b>Добавить Изучаемый Язык</b>\n\nВыберите язык для добавления (✓ = уже есть словарный запас):',
        "lang_native_title": '🔤 <b>Выберите язык для отображения определений (НATIVE)</b>',
        "expiry_reminder": '⏰ Ваша подписка Pro заканчивается *{date}* (за 3 дня).\nОтправьте `/activate <code>` для продления и сохранения неограниченного словарного запаса.',
        "btn_skip": '⏭ Пропустить',
        "btn_fuzzy": '🤔 Не уверен/-а / Не уверен/-а в этом',
        "btn_end_review": '🔚 Закончить Обзор',
        "btn_end_practice": '🔚 Закончить Практику',
        "btn_prev": '◀ Предыдущий',
        "btn_next": 'Далее ▶',
        "btn_back_vocab": '◀ Назад к списку',
        "btn_cancel": '❌ Отмена',
        "btn_edit_pos": '✏️ Вид слова',
        "btn_edit_def": '✏️ Определение',
        "btn_edit_ctx": '✏️ Пример',
        "btn_add_lang": '➕ Добавить язык',
        "btn_set_native": 'LENGва Нативного Языка',
        "btn_back": '← Назад',
        "btn_change_window": '⏰ Изменить Время',
        "btn_all_day": 'Всегда (00:00–24:00)',
        "onboard_native_title": '👋 Добро пожаловать в Vocab Master!\nПожалуйста, выберите ваш родной язык (используется для интерфейса и определений):',
        "onboard_lang_title": 'Отлично! Пожалуйста, выберите язык, который вы хотите изучать:',
        "onboard_done": '✅ Настройки завершены!\n\nПомощники будут отправляться с 08:00 до 22:00 местного времени. Вы можете изменить это в /settings.\n\nНачните учить, отправив слово или предложение сейчас!',
    },

    "it": {
        "help_text": '*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译成意大利语的结果：\n\n*Comandi:*\n/vocab — Visualizza la tua lista lessicale\n/review — Inizia una sessione di revisione\n/practice — Pratica libera (senza tracciamento delle进步了，以下是按照指示翻译',
        "start_welcome": '👋 Benvenuto in *Vocab Master*!\n\nTi aiuto a ricordare i vocaboli utilizzando la curva di dimenticanza di Ebbinghaus per pianificare le revisioni automaticamente.\nSupporta diversi linguaggi: Inglese, Giapponese, Francese e molto altro.\n\n*Come usare:*\n• Invia una parola o una frase (e.g. `devastato`)\n• Invia una frase con la parola obiettivo (e.g. `Ero completamente devastato`)\n• Invia una parola nel tuo idioma nativo e cercherò il vocabolario per te.',
        "start_tz_prompt": '🌏 Per favore impostare la tua zona oraria per ricevere gli avvisi di revisione accurate:',
        "quiz_meaning_title": '💡 *Conoscenza del Significato*',
        "quiz_meaning_instruction": 'Seleziona il significato di *{word}* nel contesto:',
        "quiz_fill_title": '🧠 <b>Compila-la-vuota</b>',
        "quiz_fill_instruction": 'Seleziona la migliore parola per completare <b>______</b>:',
        "quiz_fill_hint": '💡 Consiglio: {definition}',
        "quiz_correct": '✅ *Corretto!*\n\n*{word}* — {definition}{context_line}\n\n🎯 Livello: {level}\n📅 Prossima revisione: {date}',
        "quiz_correct_practice": '✅ *Corretto!*\n\n*{word}* — {definition}{context_line}\n\n🎮 Modalità esercizio — non contato nel progresso',
        "quiz_wrong_fill": '❌ *Errato*\n\nRisposta corretta: *{word}* — {definition}{context_line}\n\n😔 Livello sceso a: {level}\n📅 Ripeti domani!',
        "quiz_wrong_meaning": '❌ *Errato*\n\nSignificato corretto: *{word}* — {definition}{context_line}\n\n😔 Livello diminuito a: {level}\n📅 Riavra domani!',
        "quiz_wrong_fill_practice": '❌ *Errato*\n\nRisposta corretta: *{word}* — {definition}{context_line}\n\n🎮 Modalità pratica — non contata nel progresso',
        "quiz_wrong_meaning_practice": '❌ *Errato*\n\nSignificato corretto: *{word}* — {definition}{linea_di_contegno}\n\n🎮 Modalità pratica — non contata nel progresso',
        "quiz_skip_append": '⏭ Saltato — rivedi domani',
        "quiz_fuzzy_append": '🤔 Marked as incerto — rivista di nuovo domani',
        "quiz_done": '🎉 Sessione di revisione completata! Mantieni il momentum~',
        "practice_done": '🎉 Pratica completata!',
        "quiz_end_review": 'La revisione è terminata. Puoi eseguire di nuovo il comando /review quando lo desideri~',
        "quiz_end_practice": 'Practice ended. Puoi eseguire di nuovo /review in qualsiasi momento~',
        "level_0": 'Iniziatore',
        "level_1": 'Elementare',
        "level_2": 'Pienamente a breve-termine',
        "level_3": 'Intermedio',
        "level_4": 'Prolungato',
        "level_5": 'Avanzato',
        "level_6": 'Miglioramento',
        "level_7": 'Mastrizzato ✓',
        "session_active": '⏳ Attualmente sei in una sessione di {mode}. Completa prima la domanda corrente~\n(Clicca sul pulsante Fine sotto per interrompere in anticipo)',
        "session_mode_review": 'revisione',
        "session_mode_practice": 'practice',
        "vocab_empty": 'La tua vocabulary in {lang_name} è vuota~\nInvia qualsiasi parola per iniziare a costruirla, o usa /language per cambiarla.',
        "vocab_title": '📚 *{lang_name} Vocabolario* (Pagina {page}/{total_pages}, {total} parole)',
        "vocab_click_hint": 'Fai clic su un pulsante parola per visualizzare i dettagli',
        "vocab_record_not_found": 'Vocabolario record non trovato, potrebbe essere stato eliminato.',
        "vocab_mastered": '✓ Mastroizzato',
        "review_no_vocab": 'La tua vocabulary in {lang_name} è vuota~\nInvia qualsiasi parola per iniziare a costruirla!',
        "review_no_due_practice": '⏳ Non ci sono parole da revisionare per {lang_name}. Entrata in modalità esercizio (nessun tracking delle progressioni)…',
        "review_generating": '⏳ Generazione della domanda di revisione in {lang_name}…',
        "review_error": 'Fallito nel generare la domanda di revisione. Riprova, per favore.',
        "practice_empty": "La tua vocabulary in {lang_name} è vuota~\nInvia qualche parola per prima!\nOppure usa /language per passare a un'altra lingua.",
        "practice_start": '🎮 Entrando nel modo di esercizio di {lang_name} (nessun tracking delle progressioni)…',
        "practice_error": 'Fallito nel generare una domanda di esercizio. Riprova, per favore.',
        "stats_title": '📊 *Statistiche di apprendimento* ({lang_name})',
        "stats_total": '📚 Totali parole: {count}',
        "stats_today_added": '➕ Aggiunti oggi: {count}',
        "stats_due": '⚡ Da revisionare: {count}',
        "stats_level_dist": '*Distribuzione dei livelli:*',
        "stats_level_line": 'Lv{level} {label:<8}  {count:>4} parole  {bar}  {pct}%',
        "stats_lang_dist": '*Vocabolario per lingua:*',
        "stats_lang_item": '• {display} — {count} parole',
        "stats_lv_labels": 'Iniziativo|Elem.|Breve|Inter.|Lungo|Avanzato|Maestro|Fatto',
        "streak_title": '🔥 *Streak: {streak}*',
        "streak_total": '📊 Total reviews: {count}',
        "streak_0": '0 giorni (`code`) (ancora non iniziato)',
        "streak_1": '1 giorno 🌱',
        "streak_few": '{days} giorni 📈',
        "streak_week": '{days} giorni 🔥',
        "streak_month": '{days} giorni 🏆',
        "processing": '⏳ Elaborazione…',
        "parse_fail": '😕 Parsing fallito. Riprova, per favore.\n(Se questo problema persiste, controlla la configurazione del tuo servizio AI)',
        "parse_fail_simple": '😕 Parsing fallito. Riprova, per favore.',
        "sentence_no_vocab": '📖 *Traduzione:* {translation}\n\n(Nessuna vocale da salvare è stata identificata)',
        "sentence_add_prompt": '📖 *Traduzione:* {translation}\n\n*tocca una parola sotto per aggiungerla al vocabolario:*\n{vocab_lines}',
        "batch_result_title": '📚 *Batch add results ({done}/{total}):*',
        "batch_hit_limit": '⚠️ Limite raggiunto — le parole rimanenti non sono state processate.',
        "batch_parse_fail": '❌ {token} — parse fallito',
        "batch_not_vocab": '❌ {token} — non un elemento lessicale valido',
        "batch_save_fail": '❌ {word} — salvataggio fallito',
        "batch_new": '✅ <i>{word}</i>{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition} (già nel vocabolario)',
        "limit_total_reached": '📚 Il limite di vocabolario è stato raggiunto ({limit} parole).\nInvia `/activate <code>` per abbonarti a Pro per un accesso illimitato al vocabolario.',
        "limit_daily_reached": '⏰ Limite giornaliero raggiunto ({limit} parole oggi). Torna domani!\nInvia `/activate <code>` per abbonarti a Pro senza limiti.',
        "limit_both_reached": '📚 Limit raggiunto (vocabbolario: {total_limit} / giornaliero: {daily_limit} parole).\nInvia `/activate <code>` per abbonarti a Pro per un numero illimitato di parole.',
        "limit_total_alert": 'Limite di vocabolario raggiunto ({limit} parole). Iscriviti a Pro per avere accesso illimitato.',
        "limit_daily_alert": 'Limite giornaliero raggiunto ({limit} parole). Torna domani o iscriviti a Pro.',
        "edit_field_pos": 'POS',
        "edit_field_def": 'definizione',
        "edit_field_ctx": 'esempio',
        "edit_prompt": '✏️ Modifica *{word}* — {field}\n\nCorrente: _{current}_\n\nInvia il nuovo {field}:',
        "edit_updated": "✅ *{word}*'s {field} has been updated.",
        "edit_failed": '⚠️ Aggiornamento fallito — il record potrebbe non esistere più.',
        "edit_unknown_field": 'Campo sconosciuto',
        "edit_detail_review": 'Riveduto {count}×',
        "edit_detail_next": 'Prossima {date}',
        "delete_cancelled": '❌ Cancellazione annullata.',
        "delete_ok_one": '✅ Inserimento lessicale eliminato.',
        "delete_ok_all": '✅ Eliminato tutte {count} entrate per "{word}".',
        "delete_failed": '⚠️ Cancellazione fallita — il record potrebbe non esistere più.',
        "delete_batch_title": '🗑️ *Batch delete risultati:*',
        "delete_batch_ok": '✅ {word} — eliminati {count} elementi',
        "delete_batch_not_found": '❌ {word} — non trovata nel vocabolario',
        "settings_title": '🔔 *Impostazioni Notifiche*',
        "settings_tz_line": '🌏 Fuso orario: `{tz}` (cambia con /timezone)',
        "settings_window_line": '⏰ Finestra di ricordo: {start}:00 – {end}:00',
        "settings_push_label": '📢 Avvisi di revisione automatica: {status}',
        "settings_push_on": '✅ On;line',
        "settings_push_off": '❌ Disattivato',
        "settings_window_prompt": '⏰ Seleziona la finestra di ricordo:',
        "settings_toggle_off": '🔕 Disattiva',
        "settings_toggle_on": '🔔 Attiva',
        "settings_review_scope_label": '🌐 Ambito di revisione: {status}',
        "settings_review_scope_on": 'Solo lingua attiva',
        "settings_review_scope_off": 'Tutte le lingue',
        "settings_toggle_review_scope_on": '🌐 Passa a: Solo lingua attiva',
        "settings_toggle_review_scope_off": '🌐 Passa a: Tutte le lingue',
        "timezone_title": '🌏 *Impostazioni Fuso Orario*',
        "timezone_prompt": 'Ora corrente: `{tz}`\n\nLe avvisi vengono inviati tra le 08:00–22:00 ore locali.\nSeleziona la tua zona oraria:',
        "timezone_saved": '🌏 <b>Impostazioni Orarie</b>\n\n✅ Fuso orario salvato: `{tz}`\n\nLe avvisi vengono inviati tra le 08:00-22:00 ore locali.\n\nUsa /settings per adattare il periodo o disabilitare gli avvisi',
        "timezone_save_fail": 'Fallito il salvataggio. Riprova, per favore.',
        "timezone_saved_toast": '✅ Orario impostato su {tz}',
        "lang_panel_title": '🌍 <b>Gestore di Apprendimento delle Lingue</b>',
        "lang_active_line": '📖 Lingua attiva: {display}',
        "lang_native_line": '🔤 Lingua delle definizioni (nativa): {display}',
        "lang_vocab_label": '<b>La tua vociario:</b>',
        "lang_vocab_count": '• {display} — {count} parole',
        "lang_add_title": '➕ <b>Add Lingua di Apprendimento</b>\n\nSeleziona una lingua da aggiungere (✓ = già presente vocabolario):',
        "lang_native_title": '🔤 <b>Imposta la Lingua della Definizione (Nativa)</b>\n\nSeleziona la lingua in cui vuoi che vengano visualizzate le definizioni:',
        "expiry_reminder": '⏰ La tua sottoscrizione Pro scade il *{date}* (entro 3 giorni).\nInvia `/activate <code>` per rinnovare e mantenere il tuo vocabolario illimitato.',
        "btn_skip": '⏭ Saltare',
        "btn_fuzzy": '🤔 Incerto / Non sicuro',
        "btn_end_review": '🔚 Fine Revisione',
        "btn_end_practice": '🔚 Fine Pratica',
        "btn_prev": '◀ Previo',
        "btn_next": 'Prossimo ▶',
        "btn_back_vocab": '◀ Indietro alla Lista',
        "btn_cancel": '❌ Annulla',
        "btn_edit_pos": '✏️ POS',
        "btn_edit_def": '✏️ Definizione',
        "btn_edit_ctx": '✏️ Esempio',
        "btn_add_lang": '➕ Aggiungi Lingua',
        "btn_set_native": 'asetta la lingua nativa',
        "btn_back": '← Indietro',
        "btn_change_window": '⏰ Cambia Finestra',
        "btn_all_day": 'Tutto il giorno (00:00–24:00)',
        "onboard_native_title": "👋 Benvenuto in Vocab Master!\nPer favore seleziona la tua lingua nativa (usata per l'interfaccia utente e le definizioni):",
        "onboard_lang_title": 'Bravo! Seleziona la lingua che vuoi imparare:',
        "onboard_done": '✅ Impostazioni completate!\n\nI ricordi di revisione sono impostati dalle 08:00 alle 22:00 ore locali. Puoi modificarli in /settings.\n\nInizia a studiare inviando una parola o una frase ora!',
    },

    "ja": {
        "help_text": '*コマンド:*\n/vocab — ボキャブラリーリストを表示\n/review — レビューセッションを開始\n/practice — プラクティス（進捗追跡なし）\n/language — 学習言語を管理\n/search <単語> — ボキャブラリーを検索\n/export — ボキャブラリーをCSVとしてエクスポート\n/stats — 学習統計\n/streak — 连続学習日数\n/update <単語> — 単語の品詞/定義/例文を編集\n/delete <単語> — ボキャブラリーから単語を削除\n/timezone — レビュー通知時刻 Belt を設定\n/settings — 通知設定\n/plan — サブスクリプション状態を表示\n/activate <code> — サブスクリプションを有効化\n/help — このヘルプメッセージを表示',
        "start_welcome": '👋 *Vocab Master* へようこそ！\n\n自動的にレビューをスケジュールすることで、遗忘曲線を利用し新しい単語を覚えるのを助けることができます。\n複数の言語をサポートしています：英語、日本語、フランス語など。\n\n*使い方:*\n• 単語やフレーズを送ってください（例：`devastated`）\n• 目標単語を使用した文を送ってください（例：`I was utterly devastated`）\n• 原語の単語を送ると、その単語の単語evityを検索します。',
        "start_tz_prompt": '🌏 正確なレビューのお知らせ为了准确的复习提醒，请设置您的时区:',
        "quiz_meaning_title": '意味クイズ',
        "quiz_meaning_instruction": '*{word}* の意味を選んでください:',
        "quiz_fill_title": '🧠 <b>空白埋め</b>',
        "quiz_fill_instruction": '<b>______</b>を選んでください:',
        "quiz_fill_hint": '💡 ヒント: {definition}',
        "quiz_correct": '✅ *正解！*\n\n*{word}* — {definition}{context_line}\n\n🎯 レベル: {level}\n📅 次のレビュー: {date}',
        "quiz_correct_practice": '✅ *正解！*\n\n*{word}* — {definition}{context_line}\n\n🎮 プラクティスモード — プログレスにはカウントされません',
        "quiz_wrong_fill": '❌ *誤答*\n\n正解: *{word}* — {definition}{context_line}\n\n😔 レベルダウン: {level}\n📅 明日再挑戦！',
        "quiz_wrong_meaning": '❌ *間違いました*\n\n正しい意味: *{word}* — {definition}{context_line}\n\n😔 レベルダウンしました: {level}\n📅 明日再審査です！',
        "quiz_wrong_fill_practice": '❌ *間違いました*\n\n正解: *{word}* — {definition}{context_line}\n\n🎮 プラクティスモード — プログレスにはカウントされません',
        "quiz_wrong_meaning_practice": '❌ *誤り*\n\n正しい意味: *{word}* — {definition}{context_line}\n\n🎮 プラクティスモード — プログレスにはカウントされません',
        "quiz_skip_append": '⏭ スキップしました — 明日再度レビューします',
        "quiz_fuzzy_append": '🤔 模糊としてマークしました — 明日再度レビューしてください',
        "quiz_done": '🎉 レビューセッション終了！動力を維持し続けて~',
        "practice_done": '🎉 プラクティス終了！',
        "quiz_end_review": 'レビュー終了。いつでも再び/reviewできます~',
        "quiz_end_practice": '練習終了。いつでも再び/reviewできます~',
        "level_0": '初心者',
        "level_1": '小学レベル',
        "level_2": '短期的',
        "level_3": '中級',
        "level_4": '長期的',
        "level_5": '高度な',
        "level_6": 'マスタリング',
        "level_7": 'マスターしました ✓',
        "session_active": '⏳ 現在、{mode} セッション中です。まずは現在の問題を終了してください~\n(下のEndボタンをクリックして早期終了も可能です)',
        "session_mode_review": 'レビュー',
        "session_mode_practice": '練習',
        "vocab_empty": 'あなたの{lang_name}の単語リストは空です~\n任意の単語を送信して作成を開始したり、/language を使用して切り替えてください。',
        "vocab_title": '📚 *{lang_name}の単語帳* (ページ {page}/{total_pages}, {total}語)',
        "vocab_click_hint": '単語ボタンをタップして詳細を表示してください',
        "vocab_record_not_found": '単語記録が見つかりません、削除された可能性があります。',
        "vocab_mastered": '✓ マスターしました',
        "review_no_vocab": 'あなたの{lang_name}の単語帳は空です~\nどんな単語でも送って始めてみましょう！',
        "review_no_due_practice": '⏳ {lang_name}にdueな単語はありません。練習モードに入ります（進捗を追跡しません）…',
        "review_generating": '⏳ {lang_name} のレビュー問題を作成中…',
        "review_error": 'レビュー問題の生成に失敗しました。再度試してみてください。',
        "practice_empty": 'あなたの{lang_name}の単語リストは空です~\nまずいくつかの単語を送ってください!\nまたは/languageで切り替えてください。',
        "practice_start": '🎮 {lang_name} プラクティスモードに入ります（進捗追跡なし）…',
        "practice_error": '練習問題の生成に失敗しました。再度試してみてください。',
        "stats_title": '📊 *学習統計* ({lang_name})',
        "stats_total": '📚 総合単語数: {count}',
        "stats_today_added": '➕ 今日追加：{count}',
        "stats_due": '⚡ 認識が必要な項目:{count}',
        "stats_level_dist": '*レベル分布:*',
        "stats_level_line": 'Lv{level} {label:<8}  {count:>4} 単語  {bar}  {pct}%',
        "stats_lang_dist": '*言語別単語リスト:*',
        "stats_lang_item": '• {display} — {count} 単語',
        "stats_lv_labels": '初心者|元素|短|中|長|上級|マスター|完了',
        "streak_title": '🔥 *Streak: {streak}*',
        "streak_total": '📊 総合的にレビュー: {count}',
        "streak_0": '0 日 (まだ開始していません)',
        "streak_1": '1 日 🌱',
        "streak_few": '{days} 日 📈',
        "streak_week": '{days} 日 🔥',
        "streak_month": '{days} 日 🏆',
        "processing": '⏳ 处理中…',
        "parse_fail": '😕 解析に失敗しました。再度試してみてください。\n(継続する場合は、AIサービスの設定を確認してください)',
        "parse_fail_simple": '😕 解析に失敗しました。再度試してみてください。',
        "sentence_no_vocab": '📖 *翻訳:* {translation}\n\n保存に値する単語は見つかりませんでした',
        "sentence_add_prompt": '📖 *翻訳:* {translation}\n\n*以下の単語をタップして語彙に追加:*\n{vocab_lines}',
        "batch_result_title": '📚 *バッチ追加結果 ({done}/{total}):*',
        "batch_hit_limit": '⚠️ 制限に達しました — 余下的单词未处理。',
        "batch_parse_fail": '❌ {token} — 解析に失敗しました',
        "batch_not_vocab": '❌ {token} — 有効な単語項目ではありません',
        "batch_save_fail": '❌ {word} — 保存に失敗しました',
        "batch_new": '✅ *{word}*{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition}（既に Vocabulary に登録されています）',
        "limit_total_reached": '📚 派遣語彙数制限に達しました ({limit}語)。\n `/activate <code>` を送信してプロプランにサブスクライブし、無制限の語彙を使用できます。',
        "limit_daily_reached": '⏰ 今日の制限に達しました ({limit} 単語今日). 明日戻って来てください!\nプロにサブスクリプションして制限なしにするために `/activate <code>` を送信してください。',
        "limit_both_reached": '📚 リミットに達しました（単語：{total_limit} / 日付：{daily_limit} 単語）。\nプロにサブスクライブして無制限の単語を送信するには `/activate <code>` を送信してください。',
        "limit_total_alert": '語彙数制限に達しました（{limit}語）。プロプランにご登録ください。',
        "limit_daily_alert": 'Daily limit reached ({limit} 単語). 明日戻ってcoming back or subscribe to Pro.',
        "edit_field_pos": '品詞',
        "edit_field_def": '定義',
        "edit_field_ctx": 'example',
        "edit_prompt": '✏️ *{word}* を編集 — {field}\n\n現在: _{current}_\n\n新しい {field} を送ってください:',
        "edit_updated": "✅ *{word}*'s {field} が更新されました。",
        "edit_failed": '⚠️ 更新に失敗 — レコードは存在しない可能性があります。',
        "edit_unknown_field": '不明なフィールド',
        "edit_detail_review": 'レビューしました×{count}回',
        "edit_detail_next": '次の {date}',
        "delete_cancelled": '❌ 削除がキャンセルされました。',
        "delete_ok_one": '✅ 単語-entryが削除されました。',
        "delete_ok_all": '✅ {word} に関するすべての{count}項目を削除しました。',
        "delete_failed": '⚠️ 削除に失敗しました — 記録は可能であればまだ存在しているかもしれません。',
        "delete_batch_title": '🗑️ *一括削除結果:*',
        "delete_batch_ok": '✅ {word} — {count} 項目を削除しました',
        "delete_batch_not_found": '❌ {word} — 辞書に見つかりません',
        "settings_title": '🔔 *通知設定*',
        "settings_tz_line": '🌏 タイムゾーン: `{tz}` (/timezoneで変更)',
        "settings_window_line": '⏰ リマインダー窓: {start}:00 – {end}:00',
        "settings_push_label": '📢 自動レビューのリマインダー: {status}',
        "settings_push_on": '✅ オン',
        "settings_push_off": '❌ オフ',
        "settings_window_prompt": '⏰ リマインダー時間帯を選択:',
        "settings_toggle_off": '🔕 選択をオフにします',
        "settings_toggle_on": '🔔 オンにします',
        "settings_review_scope_label": '🌐 復習範囲：{status}',
        "settings_review_scope_on": 'アクティブ言語のみ',
        "settings_review_scope_off": 'すべての言語',
        "settings_toggle_review_scope_on": '🌐 切替：アクティブ言語のみ',
        "settings_toggle_review_scope_off": '🌐 切替：すべての言語',
        "timezone_title": '🌏 *時-zone 設定*',
        "timezone_prompt": '現在の時 zone: `{tz}`\n\nリマインダーは地元時間の08:00–22:00に送信されます。\nご時 zoneを選択してください:',
        "timezone_saved": '🌏 <b>タイムゾーン設定</b>\n\n✅ タイムゾーン保存: `{tz}`\n\nリマインダーはローカル時間の08:00–22:00に送信されます。\n\n/<settings> を使用して窓を調整またはリマインダーを無効にすることができます',
        "timezone_save_fail": '保存に失敗しました。もう一度試してみてください。',
        "timezone_saved_toast": '✅ タイムゾーンを{tz}に設定しました',
        "lang_panel_title": '🌍 <b>言語学習マネージャー</b>',
        "lang_active_line": '📖 活動言語: {display}',
        "lang_native_line": '🔤 定義言語（母語）：{display}',
        "lang_vocab_label": '<b>あなたの単語リスト：</b>',
        "lang_vocab_count": '• {display} — {count} 単語',
        "lang_add_title": '➕ <b>学習言語を追加</b>\n\n追加する言語を選択 (✓ =既に単語があります):',
        "lang_native_title": '🔤 <b>定義言語を設定（母国語）</b>\n\n表示する定義の言語を選択してください:',
        "expiry_reminder": '⏰ Pro 会員期間は *{date}* までです。（3日以内）\n `/activate <code>` を送信して更新し、無制限の単語を保持してください。',
        "btn_skip": 'スキップ',
        "btn_fuzzy": '🤔 模糊 / 証拠不足',
        "btn_end_review": '🔚 レビュー終了',
        "btn_end_practice": '🔚 プラクティス終了',
        "btn_prev": '前のページ◀',
        "btn_next": '次 ▶',
        "btn_back_vocab": '◀ リストに戻る',
        "btn_cancel": '❌ キャンセル',
        "btn_edit_pos": '✏️ 品詞',
        "btn_edit_def": '✏️ 定義',
        "btn_edit_ctx": '✏️ 例文',
        "btn_add_lang": '➕ 言語追加',
        "btn_set_native": 'ometown言語を設定',
        "btn_back": '戻る←',
        "btn_change_window": '⏰ 窓を変更',
        "btn_all_day": '全日 (00:00–24:00)',
        "onboard_native_title": '👋 Vocab Masterにようこそ！\nUIや定義語を表示するための母国語を選択してください:',
        "onboard_lang_title": 'great! 请選択したい学習する言語を選んでください:',
        "onboard_done": '✅ セットアップ完了！\n\nリマインダーはローカル時間の08:00–22:00に設定されています。これを /settings で調整できます。\n\n今から単語や文を学習始めてみましょう！',
    },

    "ko": {
        "help_text": '*명령어:*\n/vocab — 단어 목록 확인\n/review — 리뷰 세션 시작\n/practice — 프리 연습 (진행 추적 없음)\n/language — 학습 언어 관리\n/search <단어> — 단어 검색\n/export — 단어 목록 CSV로 xuất khẩu\n/stats — 학습 통계\n/streak — 연속 학습 일수\n/update <단어> — 단어의 용법/정의/예문 수정\n/delete <단어> — 단어 삭제\n/timezone — 리뷰 알림 시간대 설정\n/settings — 알림 설정\n/plan — 구독 상태 확인\n/activate <code> — 구독 활성화\n/help — 이 도움말 메시지 표시',
        "start_welcome": '👋 *Vocab Master*에 오신 것을 환영합니다!\n\n이 프로그램은 에bbinghaus 잊히는 곡선을 사용하여 리뷰를 자동으로 스케줄링하여 단어를 기억하는 데 도움을 줍니다.\n다양한 언어를 지원합니다: 영어, 일본어, 프랑스어 등 더 많은 언어도 지원합니다.\n\n*사용 방법:*\n• 단어 또는 문구를 보내세요 (예: `devastated`)\n• 목표 단어를 포함한 문장을 보내세요 (예: `나는 완전히 devastared`)\n• 본인의 모국어 단어를 보내면 해당 단어의 단어를 찾아드립니다.',
        "start_tz_prompt": '🌏 정확한 리뷰 알림을 위해 타임존을 설정해 주세요:',
        "quiz_meaning_title": '뜻 퀴즈',
        "quiz_meaning_instruction": '{*word*의 의미를 선택하세요:}',
        "quiz_fill_title": '🧠 <b>빈칸 채우기</b>',
        "quiz_fill_instruction": '&lt;b&gt;______&lt;/b&gt를 선택하세요:',
        "quiz_fill_hint": '💡ヒント: {definition}',
        "quiz_correct": '✅ *정답!*  \n\n*{word}* — {definition}{context_line}\n\n🎯 레벨: {level}\n📅 다음 리뷰: {date}',
        "quiz_correct_practice": '✅ *정답!*  \n\n*{word}* — {definition}{context_line}\n\n🎮 연습 모드 — 진척미터에 반영되지 않음',
        "quiz_wrong_fill": '❌ *오답*\n\n정답: *{word}* — {definition}{context_line}\n\n😔 레벨 내려가기: {level}\n📅 내일 다시 검토!',
        "quiz_wrong_meaning": '❌ *오답*\n\n정확한 의미: *{word}* — {definition}{context_line}\n\n😔 레벨 다운: {level}\n📅 내일 다시 검토!*',
        "quiz_wrong_fill_practice": '❌ *오답*\n\n정답: *{word}* — {definition}{context_line}\n\n🎮 연습 모드 — 진척미터에 반영되지 않음',
        "quiz_wrong_meaning_practice": '❌ *오답*\n\n정확한 의미: *{word}* — {definition}{context_line}\n\n🎮 연습 모드 — 진척미터에 반영되지 않음',
        "quiz_skip_append": '⏭Tomorrow 다시 리뷰합니다 —',
        "quiz_fuzzy_append": '🤔 퍼지게 표시됨 — 내일 다시 검토',
        "quiz_done": '🎉 리뷰 세션이 완료되었습니다! Minute more, minute better~',
        "practice_done": '🎉 연습 완료!',
        "quiz_end_review": '리뷰가 종료되었습니다. 다시 /review할 수 있습니다~',
        "quiz_end_practice": '연습이 끝났습니다. 다시 언제든지 /review할 수 있어요~',
        "level_0": '초보자',
        "level_1": '초급',
        "level_2": '단기',
        "level_3": '중급',
        "level_4": '장기적인',
        "level_5": '고급',
        "level_6": '마스터링',
        "level_7": '마스터 ✓',
        "session_active": '⏳ 현재 {mode} 세션에 있습니다. 먼저 현재 문제를 마치세요~\n(아래 End 버튼을 클릭하면 일찍 중단할 수 있습니다)',
        "session_mode_review": '리뷰',
        "session_mode_practice": '실습',
        "vocab_empty": '您的 {lang_name} 卡片集是空的~\n보내시는 단어로 시작하거나 /language를 사용하여 변경하실 수 있습니다.',
        "vocab_title": '📚 *{lang_name} 단어장* (페이지 {page}/{total_pages}, {total} 단어)',
        "vocab_click_hint": '단어 버튼을 탭하여 자세한 내용을 확인하세요',
        "vocab_record_not_found": '단어장 기록을 찾을 수 없습니다. 삭제되었을 수 있습니다.',
        "vocab_mastered": '✓ 마스터했습니다',
        "review_no_vocab": '您的 {lang_name} 单词表是空的~\n发送任何单词开始构建吧！',
        "review_no_due_practice": '⏳ {lang_name}에due한 단어가 없습니다. 연습 모드로 진입합니다(진행 상황을 추적하지 않음)…',
        "review_generating": '⏳ {lang_name} 복습 문제를 생성 중…',
        "review_error": '리뷰 질문을 생성하지 못했습니다. 다시 시도해주세요.',
        "practice_empty": '您的 {lang_name} 单词表是空的~\n先发送一些单词吧！\n或者使用 /language 切换。',
        "practice_start": '🎮 {lang_name} 연습 모드로 진입 중…(진행 상황 추적X)…',
        "practice_error": '생성 중 오류가 발생했습니다. 다시 시도해주세요.',
        "stats_title": '📊 *학습 통계*({lang_name})',
        "stats_total": '📚 총 단어 수: {count}',
        "stats_today_added": '➕ 오늘 추가: {count}',
        "stats_due": '⚡ 리뷰_due_for_: `{count}`',
        "stats_level_dist": '*레벨 분포:*',
        "stats_level_line": 'Lv{level} {label:<8}  {count:>4} 단어  {bar}  {pct}%',
        "stats_lang_dist": '*언어별 단어장:*',
        "stats_lang_item": '• {display} — {count} 단어',
        "stats_lv_labels": '초보|원소|짧음|중급|긴|고급|마스터|완료',
        "streak_title": '🔥 *연속일수: {streak}*',
        "streak_total": '📊 총 리뷰 횟수: {count}',
        "streak_0": '0일 (시작되지 않음)',
        "streak_1": '1일 🌱',
        "streak_few": '{days} 일 📈',
        "streak_week": '{days} 일 🔥',
        "streak_month": '{days} 일 🏆',
        "processing": '⏳ 처리 중…',
        "parse_fail": '😕 파싱에 실패했습니다. 다시 시도해주세요.\n(만약 계속된다면, AI 서비스 구성情况进行翻译：\nuser\n😕 Failed to parse. Please try again.\n(If this persists, check your AI service configuration)',
        "parse_fail_simple": '😕 파싱에 실패했습니다. 다시 시도해주세요.',
        "sentence_no_vocab": '📖 *번역:* {translation}\n\n(저장할 만한 단어가 발견되지 않았습니다)',
        "sentence_add_prompt": '📖 *번역:* {translation}\n\n*아래 단어를 탭하여 단어장에 추가하세요:*\n{vocab_lines}',
        "batch_result_title": '📚 *배치 추가 결과 ({done}/{total}):*',
        "batch_hit_limit": '⚠️ 제한 도달 — 남은 단어는 처리되지 않았습니다.',
        "batch_parse_fail": '❌ {token} — 파싱 실패',
        "batch_not_vocab": '❌ {token} — 유효한 단어 항목이 아닙니다',
        "batch_save_fail": '❌ {word} — 저장 실패',
        "batch_new": '✅ *{word}*{pos_tag} — {definition}',
        "batch_exists": '📖 *{word}*{pos_tag} — {definition} (이미 단어장에 있습니다.)',
        "limit_total_reached": '📚 단어 제한에 도달했습니다 ({limit} 단어).\n`/activate <code>`를 보내 Pro로 구독하여 무제한 단어를 사용하세요.',
        "limit_daily_reached": '⏰ 하루 제한 도달 ({limit} 단어 오늘). 내일 다시来看看吧！（/practice）发送 `/activate <code>` 以订阅 Pro 并解除限制。',
        "limit_both_reached": '📚 제한 도달 (단어 수: {total_limit} / 일일: {daily_limit} 단어).\n`/activate <code>`를 보내 Pro로 구독하여 무제한 단어를 사용하세요.',
        "limit_total_alert": '용어 수 제한 도달 ({limit} 단어). 프로로 구독하여 무제한으로 사용하세요.',
        "limit_daily_alert": '일일 제한 도달 ({limit} 단어). 내일 다시 방문하거나 Pro로 구독하세요.',
        "edit_field_pos": '품사',
        "edit_field_def": '정의',
        "edit_field_ctx": '예시',
        "edit_prompt": '✏️ *{word}* 수정 — {field}\n\n현재: _{current}_\n\n새로운 {field}를 보내주세요:',
        "edit_updated": "✅ *{word}*'s {field}가 업데이트되었습니다.",
        "edit_failed": '⚠️ 업데이트 실패 — 기록이 더 이상 존재하지 않을 수 있습니다.',
        "edit_unknown_field": '알 수 없는 필드',
        "edit_detail_review": '검토한 횟수: {count}×\n\nPlease note that in Korean, the number comes before the word "회수" (times), so the translation is adjusted to fit Korean language structure.',
        "edit_detail_next": '다음 {date}',
        "delete_cancelled": '❌ 삭제 취소되었습니다.',
        "delete_ok_one": '✅ 단어-entry 삭제되었습니다.',
        "delete_ok_all": '✅ "{word}"에 대한 모든 {count} 개 항목이 삭제되었습니다.',
        "delete_failed": '⚠️ 삭제 실패 — 기록이 더 이상 존재하지复工复课通知：所有同学请注意，从明天开始我们将恢复正常课程安排。请大家调整状态，做好上课准备。如有疑问，请随时联系班主任。🚀📚',
        "delete_batch_title": '🗑️ *배치 삭제 결과:*',
        "delete_batch_ok": '✅ {word} — 삭제된 항목 수: {count}',
        "delete_batch_not_found": '❌ {word} — 단어장에 없습니다.',
        "settings_title": '🔔 *알림 설정*',
        "settings_tz_line": '🌏 타임존: `{tz}` (변경하기 위해 /timezone 사용)',
        "settings_window_line": '⏰ 상기 알림 창: {start}:00 – {end}:00',
        "settings_push_label": '📢 자동 리뷰 상신 알림: {status}',
        "settings_push_on": '✅ 온',
        "settings_push_off": '❌ 오프',
        "settings_window_prompt": '⏰ 알림 창 선택:',
        "settings_toggle_off": '🔕 알림 끄기',
        "settings_toggle_on": '🔔 켜기',
        "settings_review_scope_label": '🌐 복습 범위: {status}',
        "settings_review_scope_on": '활성 언어만',
        "settings_review_scope_off": '모든 언어',
        "settings_toggle_review_scope_on": '🌐 전환: 활성 언어만',
        "settings_toggle_review_scope_off": '🌐 전환: 모든 언어',
        "timezone_title": '🌏 <b>시간대 설정</b>',
        "timezone_prompt": '현재 타임존: `{tz}`\n\n리마인더는 지역 시간으로 08:00–22:00 사이에 발송됩니다.\n타임존을 선택해주세요:',
        "timezone_saved": '🌏 <b>타임존 설정</b>\n\n✅ 타임존 저장됨: `{tz}`\n\n리마인더는 로컬 시간으로 08:00–22:00 사이에 발송됩니다.\n\n/<settings>를 사용하여 창을 조정하거나 리마인더를 비활성화할 수 있습니다.',
        "timezone_save_fail": '저장에 실패했습니다. 다시 시도해주세요.',
        "timezone_saved_toast": '✅ 타임존 설정을 {tz}로 변경했습니다.',
        "lang_panel_title": '🌍 <b>언어 학습 관리자</b>',
        "lang_active_line": '📖 활성 언어: {display}',
        "lang_native_line": '🔤 정의 언어 (모국어): {display}',
        "lang_vocab_label": '<b>학습 단어：</b>',
        "lang_vocab_count": '• {display} — {count} 단어',
        "lang_add_title": '➕ <b>학습 언어 추가</b>\n\n추가할 언어를 선택하세요 (✓ = 이미 단어장이 있습니다):',
        "lang_native_title": '🔤 <b>정의 언어 설정 (국문)</b>\n\n표현된 정의를 어떤 언어로 보고자 하는지 선택하세요:',
        "expiry_reminder": '⏰ 프로 구독이 *{date}*까지 만료됩니다.(3일 이내).\n `/activate <code>`를 보내서 무제한 어휘를 계속 사용하세요.',
        "btn_skip": '⏭ 건너뛰기',
        "btn_fuzzy": '🤔 흐려 / 확실하지 않음',
        "btn_end_review": '🔚 리뷰 종료',
        "btn_end_practice": '🔚 연습 끝',
        "btn_prev": '◀ 이전',
        "btn_next": '다음 ▶',
        "btn_back_vocab": '◀ 목록으로 돌아가기',
        "btn_cancel": '❌ 취소',
        "btn_edit_pos": '✏️ 명사',
        "btn_edit_def": '✏️ 정의',
        "btn_edit_ctx": '✏️ 예시',
        "btn_add_lang": '➕ 언어 추가',
        "btn_set_native": '国籍 언어 설정 🌐',
        "btn_back": '← 뒤로',
        "btn_change_window": '⏰ 창 변경',
        "btn_all_day": '하루 종일 (00:00–24:00)',
        "onboard_native_title": '👋 Vocab Master에 오신 것을 환영합니다!\n원어민 언어를 선택해 주세요 (UI와 정의에 사용됩니다):',
        "onboard_lang_title": '좋습니다! 학습하고자 하는 언어를 선택해 주세요:',
        "onboard_done": '✅ 설정 완료!\n\n리뷰 알림은 현지 시간 08:00–22:00에 설정되었습니다. 이 설정을 변경하려면 /settings를 사용하세요.\n\n지금 단어나 문장을 보내서 학습을 시작하세요!',
    },
}


def t(key: str, lang: str = "zh", **kwargs) -> str:
    """
    同步翻译：仅 zh/en 查字典，其他语言返回英文（适合非 async 场景）。
    支持 kwargs 格式化占位符。
    """
    base = lang if lang in STRINGS else "en"
    text = STRINGS.get(base, {}).get(key) or STRINGS.get("en", {}).get(key) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


async def t_async(key: str, lang: str = "zh", **kwargs) -> str:
    """
    异步翻译：
    - zh/en 直接查字典（零延迟）
    - 其他语言：先翻译英文模板（保留 {placeholder}），再替换 kwargs
      （避免 AI 误译 {display}、{count} 等占位符）
    """
    if lang in STRINGS:
        return t(key, lang, **kwargs)
    # 先翻译模板字符串，不替换 kwargs，让 AI 保留 {display} 等占位符
    en_template = STRINGS.get("en", {}).get(key) or key
    translated = await _translate_ui(en_template, lang)
    if kwargs:
        try:
            return translated.format(**kwargs)
        except (KeyError, ValueError):
            return translated
    return translated


async def _translate_ui(text: str, target_lang: str) -> str:
    """将英文 UI 文案翻译为目标语言，带进程内内存缓存"""
    cache_key = (target_lang, hashlib.md5(text.encode()).hexdigest())
    if cache_key in _ui_cache:
        return _ui_cache[cache_key]

    from ai.parser import translate_ui_text
    try:
        translated = await translate_ui_text(text, target_lang)
        _ui_cache[cache_key] = translated
        return translated
    except Exception as exc:
        logger.warning("UI 翻译失败 (lang=%s): %s，fallback 英文", target_lang, exc)
        return text  # 降级到英文


def level_description(level: int, lang: str = "zh") -> str:
    """
    同步版级别描述（zh/en，供非 async 场景使用）。
    async 场景请改用 await t_async(f"level_{level}", lang)。
    """
    return t(f"level_{level}", lang)


def get_stats_level_labels(lang: str = "zh") -> list[str]:
    """返回统计页的级别短标签列表（8个元素，对应 level 0-7），仅 zh/en 同步版"""
    raw = t("stats_lv_labels", lang)
    labels = raw.split("|")
    while len(labels) < 8:
        labels.append(f"Lv{len(labels)}")
    return labels[:8]
