"""
Telegram inline keyboard 布局工具
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def quiz_keyboard(
    options: list[str],
    record_id: str,
    quiz_type: str = "fill",
    practice_mode: bool = False,
) -> InlineKeyboardMarkup:
    """
    生成测验选项键盘：4 个选项（2行×2列）+ 1 个底部按钮
    - fill 题：callback_data 前缀 "quiz:"（正式）或 "qzp:"（练习），底部按钮为"跳过"
    - meaning 题：callback_data 前缀 "qm:"（正式）或 "qmp:"（练习），底部按钮为"模糊/拿不准"
    """
    # 根据题型和练习模式选择 callback 前缀和跳过按钮文本
    if quiz_type == "meaning":
        prefix = "qmp" if practice_mode else "qm"
        skip_label = "🤔 模糊/拿不准"
    else:
        prefix = "qzp" if practice_mode else "quiz"
        skip_label = "⏭ 跳过"

    # 第一行：选项 0, 1
    row1 = [
        InlineKeyboardButton(options[0], callback_data=f"{prefix}:{record_id}:0"),
        InlineKeyboardButton(options[1], callback_data=f"{prefix}:{record_id}:1"),
    ]
    # 第二行：选项 2, 3
    row2 = [
        InlineKeyboardButton(options[2], callback_data=f"{prefix}:{record_id}:2"),
        InlineKeyboardButton(options[3], callback_data=f"{prefix}:{record_id}:3"),
    ]
    # 第三行：跳过 / 模糊拿不准
    row3 = [
        InlineKeyboardButton(skip_label, callback_data=f"{prefix}:{record_id}:skip"),
    ]
    # 第四行：结束按钮
    end_label = "🔚 结束练习" if practice_mode else "🔚 结束复习"
    end_prefix = "qend:p" if practice_mode else "qend:r"
    row4 = [InlineKeyboardButton(end_label, callback_data=end_prefix)]
    return InlineKeyboardMarkup([row1, row2, row3, row4])


def sentence_vocab_keyboard(
    vocabs: list,
    msg_id: int,
    added_indices: set[int],
) -> InlineKeyboardMarkup:
    """
    整句分析结果的词汇选择键盘：每个词一行，点击后入库。
    added_indices: 已入库的词的下标集合，已入库的显示 ✅ 前缀。
    """
    rows = []
    for i, vocab in enumerate(vocabs):
        label = f"✅ {vocab.word}" if i in added_indices else vocab.word
        rows.append([InlineKeyboardButton(label, callback_data=f"sa:{msg_id}:{i}")])
    return InlineKeyboardMarkup(rows)


def delete_confirm_keyboard(records: list[dict]) -> InlineKeyboardMarkup:
    """
    根据词汇记录数生成删词确认键盘：
    - 1 条记录：[✅ 确认删除] [❌ 取消]
    - 2+ 条记录（一词多义）：每条一个按钮 + [🗑️ 全部删除] + [❌ 取消]
    callback_data 格式：
      单条确认：vd:confirm:{record_id}
      多条单条：vd:one:{record_id}
      全部删除：vd:all:{word}
      取消：vd:cancel
    """
    rows = []
    if len(records) == 1:
        # 只有一条，直接询问确认
        rows.append([
            InlineKeyboardButton("✅ 确认删除", callback_data=f"vd:confirm:{records[0]['id']}"),
            InlineKeyboardButton("❌ 取消", callback_data="vd:cancel"),
        ])
    else:
        # 多义词，让用户选择删哪一条
        for r in records:
            pos_tag = f"[{r['pos']}] " if r.get("pos") else ""
            label = f"{pos_tag}{r['definition']}"
            rows.append([InlineKeyboardButton(label, callback_data=f"vd:one:{r['id']}")])
        # 全部删除按钮
        rows.append([InlineKeyboardButton("🗑️ 全部删除", callback_data=f"vd:all:{records[0]['word']}")])
        rows.append([InlineKeyboardButton("❌ 取消", callback_data="vd:cancel")])
    return InlineKeyboardMarkup(rows)


def vocab_page_keyboard(
    page: int,
    total_pages: int,
    records: list[dict] | None = None,
) -> InlineKeyboardMarkup | None:
    """
    分页导航键盘：上一页 / 下一页，每条词汇右侧加详情按钮。
    records: 当前页词汇列表，用于生成每条的详情按钮（可选）
    """
    rows = []

    # 每条词汇的详情按钮（每行两个），显示词 + 词性，点击展开详情消息
    if records:
        buttons = []
        for r in records:
            pos_part = f" ({r['pos']})" if r.get("pos") else ""
            label = f"🔍 {r['word']}{pos_part}"
            # callback_data 带上页码，供详情页「返回」时用
            buttons.append(InlineKeyboardButton(label, callback_data=f"vinfo:{r['id']}:{page}"))
        # 两两一行
        for i in range(0, len(buttons), 2):
            rows.append(buttons[i:i + 2])

    # 分页导航按钮
    if total_pages > 1:
        nav_buttons: list[InlineKeyboardButton] = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀ 上一页", callback_data=f"vocab_page:{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("下一页 ▶", callback_data=f"vocab_page:{page + 1}"))
        if nav_buttons:
            rows.append(nav_buttons)

    return InlineKeyboardMarkup(rows) if rows else None


# 时区选项：(显示标签, IANA 时区值)
_TIMEZONE_OPTIONS = [
    # 美洲
    ("🇨🇦 Canada/Vancouver",          "America/Vancouver"),
    ("🇺🇸 USA/Los_Angeles",           "America/Los_Angeles"),
    ("🇺🇸 USA/Denver",                "America/Denver"),
    ("🇺🇸 USA/Chicago",               "America/Chicago"),
    ("🇺🇸 USA/New_York",              "America/New_York"),
    ("🇨🇦 Canada/Toronto",            "America/Toronto"),
    ("🇲🇽 Mexico/Mexico_City",        "America/Mexico_City"),
    ("🇧🇷 Brazil/Sao_Paulo",          "America/Sao_Paulo"),
    ("🇦🇷 Argentina/Buenos_Aires",    "America/Argentina/Buenos_Aires"),
    # 欧洲
    ("🇬🇧 UK/London",                 "Europe/London"),
    ("🇩🇪 Germany/Berlin",            "Europe/Berlin"),
    ("🇫🇷 France/Paris",              "Europe/Paris"),
    ("🇷🇺 Russia/Moscow",             "Europe/Moscow"),
    ("🇳🇱 Netherlands/Amsterdam",     "Europe/Amsterdam"),
    ("🇸🇪 Sweden/Stockholm",          "Europe/Stockholm"),
    # 非洲/中东
    ("🇹🇷 Turkey/Istanbul",           "Europe/Istanbul"),
    ("🇿🇦 SouthAfrica/Johannesburg",  "Africa/Johannesburg"),
    ("🇪🇬 Egypt/Cairo",               "Africa/Cairo"),
    # 亚洲
    ("🇦🇪 UAE/Dubai",                 "Asia/Dubai"),
    ("🇮🇳 India/Mumbai",              "Asia/Kolkata"),
    ("🇧🇩 Bangladesh/Dhaka",          "Asia/Dhaka"),
    ("🇹🇭 Thailand/Bangkok",          "Asia/Bangkok"),
    ("🇸🇬 Singapore/Singapore",       "Asia/Singapore"),
    ("🇨🇳 China/Shanghai",            "Asia/Shanghai"),
    ("🇭🇰 China/Hong_Kong",           "Asia/Hong_Kong"),
    ("🇰🇷 Korea/Seoul",               "Asia/Seoul"),
    ("🇯🇵 Japan/Tokyo",               "Asia/Tokyo"),
    # 大洋洲
    ("🇦🇺 Australia/Sydney",          "Australia/Sydney"),
    ("🇳🇿 NewZealand/Auckland",       "Pacific/Auckland"),
    # 其他
    ("🌐 UTC",                        "UTC"),
]


def vocab_detail_keyboard(record_id: str, page: int) -> InlineKeyboardMarkup:
    """
    词汇详情页键盘：三个编辑按钮 + 返回词库按钮
    callback_data 格式：
      vedit:{record_id}:{field}:{page}  — 进入字段编辑
      vocab_page:{page}                 — 返回词库列表
    """
    row1 = [
        InlineKeyboardButton("✏️ 词性", callback_data=f"vedit:{record_id}:pos:{page}"),
        InlineKeyboardButton("✏️ 释义", callback_data=f"vedit:{record_id}:definition:{page}"),
        InlineKeyboardButton("✏️ 例句", callback_data=f"vedit:{record_id}:context:{page}"),
    ]
    row2 = [
        InlineKeyboardButton("◀ 返回词库", callback_data=f"vocab_page:{page}"),
    ]
    return InlineKeyboardMarkup([row1, row2])


def edit_field_keyboard(record_id: str, field: str, page: int) -> InlineKeyboardMarkup:
    """
    编辑字段等待输入时的键盘：仅一个取消按钮
    callback_data 格式：vedit_cancel:{page}
    """
    row = [
        InlineKeyboardButton("❌ 取消", callback_data=f"vedit_cancel:{page}"),
    ]
    return InlineKeyboardMarkup([row])


def settings_panel_keyboard(toggle_label: str) -> InlineKeyboardMarkup:
    """
    通知设置主面板键盘：更改时段 + 开关推送
    toggle_label: "🔕 关闭推送" 或 "🔔 开启推送"
    """
    row1 = [InlineKeyboardButton("⏰ 更改时段", callback_data="settings:window")]
    row2 = [InlineKeyboardButton(toggle_label, callback_data="settings:toggle")]
    return InlineKeyboardMarkup([row1, row2])


def remind_window_keyboard() -> InlineKeyboardMarkup:
    """
    推送时段选择面板：5 种预设 + 返回按钮
    callback_data 格式：settings:set_win:{start}:{end}
    """
    options = [
        ("06:00–22:00", 6, 22),
        ("07:00–23:00", 7, 23),
        ("08:00–22:00", 8, 22),
        ("09:00–21:00", 9, 21),
        ("全天（00:00–24:00）", 0, 24),
    ]
    rows = []
    # 前4个两两一行
    for i in range(0, 4, 2):
        row = [
            InlineKeyboardButton(options[i][0], callback_data=f"settings:set_win:{options[i][1]}:{options[i][2]}"),
            InlineKeyboardButton(options[i+1][0], callback_data=f"settings:set_win:{options[i+1][1]}:{options[i+1][2]}"),
        ]
        rows.append(row)
    # 第5个独占一行
    rows.append([
        InlineKeyboardButton(options[4][0], callback_data=f"settings:set_win:{options[4][1]}:{options[4][2]}"),
    ])
    # 返回按钮
    rows.append([InlineKeyboardButton("← 返回", callback_data="settings:back")])
    return InlineKeyboardMarkup(rows)


def timezone_keyboard() -> InlineKeyboardMarkup:
    """
    时区选择键盘，每行2个按钮，callback_data = tz:{IANA_timezone}
    """
    rows = []
    # 每行2个，配对排列
    for i in range(0, len(_TIMEZONE_OPTIONS), 2):
        row = []
        label, tz = _TIMEZONE_OPTIONS[i]
        row.append(InlineKeyboardButton(label, callback_data=f"tz:{tz}"))
        if i + 1 < len(_TIMEZONE_OPTIONS):
            label2, tz2 = _TIMEZONE_OPTIONS[i + 1]
            row.append(InlineKeyboardButton(label2, callback_data=f"tz:{tz2}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)
