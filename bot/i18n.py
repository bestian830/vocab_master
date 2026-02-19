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
            "*{word}* — {definition}\n"
            "🎯 当前级别：{level}\n"
            "📅 下次复习：{date}"
        ),
        "quiz_correct_practice": (
            "✅ *答对啦！*\n\n"
            "*{word}* — {definition}\n"
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
        "stats_today_added": "➕ 今日新增：{count} 词（全语言）",
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
        "lang_panel_title": "🌍 *多语言学习管理*",
        "lang_active_line": "📖 当前激活：{display}",
        "lang_native_line": "🔤 释义语言（母语）：{display}",
        "lang_vocab_label": "*你的词库：*",
        "lang_vocab_count": "• {display} — {count} 词",
        "lang_add_title": (
            "➕ *添加学习语言*\n\n"
            "选择你想添加的语言（✓ 表示已有词库）："
        ),
        "lang_native_title": (
            "🔤 *设置释义语言（母语）*\n\n"
            "选择你希望用哪种语言显示释义："
        ),

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
            "*{word}* — {definition}\n"
            "🎯 Level: {level}\n"
            "📅 Next review: {date}"
        ),
        "quiz_correct_practice": (
            "✅ *Correct!*\n\n"
            "*{word}* — {definition}\n"
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
        "stats_today_added": "➕ Added today: {count} (all languages)",
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
        "lang_panel_title": "🌍 *Language Learning Manager*",
        "lang_active_line": "📖 Active language: {display}",
        "lang_native_line": "🔤 Definition language (native): {display}",
        "lang_vocab_label": "*Your vocabulary:*",
        "lang_vocab_count": "• {display} — {count} words",
        "lang_add_title": (
            "➕ *Add Learning Language*\n\n"
            "Select a language to add (✓ = already have vocabulary):"
        ),
        "lang_native_title": (
            "🔤 *Set Definition Language (Native)*\n\n"
            "Select the language you want definitions displayed in:"
        ),

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
    - 其他语言：先拼出英文原文，再调 AI 翻译，带内存缓存
    """
    if lang in STRINGS:
        return t(key, lang, **kwargs)
    # 先组装英文完整文案（含 kwargs）
    en_text = t(key, "en", **kwargs)
    return await _translate_ui(en_text, lang)


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
