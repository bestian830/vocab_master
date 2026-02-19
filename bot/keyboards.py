"""
Telegram inline keyboard 布局工具
含用户可见文案的函数均为 async，接受 lang 参数，内部用 t_async() 生成按钮标签。
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot.i18n import t_async


async def quiz_keyboard(
    options: list[str],
    record_id: str,
    quiz_type: str = "fill",
    practice_mode: bool = False,
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    生成测验选项键盘：4 个选项（2行×2列）+ 跳过/模糊 + 结束按钮
    - fill 题：callback_data 前缀 "quiz:"（正式）或 "qzp:"（练习），底部按钮为"跳过"
    - meaning 题：callback_data 前缀 "qm:"（正式）或 "qmp:"（练习），底部按钮为"模糊/拿不准"
    """
    # 根据题型和练习模式选择 callback 前缀和跳过按钮文本
    if quiz_type == "meaning":
        prefix = "qmp" if practice_mode else "qm"
        skip_label = await t_async("btn_fuzzy", lang)
    else:
        prefix = "qzp" if practice_mode else "quiz"
        skip_label = await t_async("btn_skip", lang)

    end_label = await t_async("btn_end_practice" if practice_mode else "btn_end_review", lang)

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
    （无需翻译，词汇标签本身即为目标语言词汇）
    """
    rows = []
    for i, vocab in enumerate(vocabs):
        label = f"✅ {vocab.word}" if i in added_indices else vocab.word
        rows.append([InlineKeyboardButton(label, callback_data=f"sa:{msg_id}:{i}")])
    return InlineKeyboardMarkup(rows)


async def vocab_confirm_keyboard(
    vocabs: list,
    msg_id: int,
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    单词入库前确认键盘：每条词汇一行"✅ Add"按钮，底部一个"Skip ❌"按钮。
    callback_data 格式：
      vc:add:{msg_id}:{idx}   — 入库指定词汇
      vc:skip:{msg_id}        — 跳过，不入库
    """
    add_label = "✅ Add" if lang != "zh" else "✅ 添加"
    skip_label = "Skip ❌" if lang != "zh" else "跳过 ❌"

    rows = []
    for i, vocab in enumerate(vocabs):
        pos_part = f"  [{vocab.pos}]" if getattr(vocab, "pos", None) else ""
        btn_label = f"{add_label}  {vocab.word}{pos_part}"
        rows.append([InlineKeyboardButton(btn_label, callback_data=f"vc:add:{msg_id}:{i}")])
    rows.append([InlineKeyboardButton(skip_label, callback_data=f"vc:skip:{msg_id}")])
    return InlineKeyboardMarkup(rows)


async def delete_confirm_keyboard(
    records: list[dict],
    lang: str = "zh",
) -> InlineKeyboardMarkup:
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
    cancel_label = await t_async("btn_cancel", lang)
    rows = []
    if len(records) == 1:
        confirm_label = await t_async("delete_ok_one", lang)
        # 取短文案作为确认按钮（"✅ 已删除该词汇条目。" 太长，用固定简短标签）
        confirm_btn = "✅ Confirm" if lang != "zh" else "✅ 确认删除"
        rows.append([
            InlineKeyboardButton(confirm_btn, callback_data=f"vd:confirm:{records[0]['id']}"),
            InlineKeyboardButton(cancel_label, callback_data="vd:cancel"),
        ])
    else:
        # 多义词，让用户选择删哪一条
        for r in records:
            pos_tag = f"[{r['pos']}] " if r.get("pos") else ""
            label = f"{pos_tag}{r['definition']}"
            rows.append([InlineKeyboardButton(label, callback_data=f"vd:one:{r['id']}")])
        delete_all_label = "🗑️ 全部删除" if lang == "zh" else "🗑️ Delete All"
        rows.append([InlineKeyboardButton(delete_all_label, callback_data=f"vd:all:{records[0]['word']}")])
        rows.append([InlineKeyboardButton(cancel_label, callback_data="vd:cancel")])
    return InlineKeyboardMarkup(rows)


async def vocab_page_keyboard(
    page: int,
    total_pages: int,
    records: list[dict] | None = None,
    lang: str = "zh",
) -> InlineKeyboardMarkup | None:
    """
    分页导航键盘：上一页 / 下一页，每条词汇右侧加详情按钮。
    records: 当前页词汇列表，用于生成每条的详情按钮（可选）
    """
    rows = []

    # 每条词汇的详情按钮（每行一个），显示词 + 词性，点击展开详情消息
    if records:
        for r in records:
            pos_part = f"  [{r['pos']}]" if r.get("pos") else ""
            label = f"{r['word']}{pos_part}"
            rows.append([InlineKeyboardButton(label, callback_data=f"vinfo:{r['id']}:{page}")])

    # 分页导航按钮
    if total_pages > 1:
        prev_label = await t_async("btn_prev", lang)
        next_label = await t_async("btn_next", lang)
        nav_buttons: list[InlineKeyboardButton] = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(prev_label, callback_data=f"vocab_page:{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(next_label, callback_data=f"vocab_page:{page + 1}"))
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


def timezone_keyboard() -> InlineKeyboardMarkup:
    """
    时区选择键盘，每行2个按钮，callback_data = tz:{IANA_timezone}
    时区名称为地名（不翻译），保持原样。
    """
    rows = []
    for i in range(0, len(_TIMEZONE_OPTIONS), 2):
        row = []
        label, tz = _TIMEZONE_OPTIONS[i]
        row.append(InlineKeyboardButton(label, callback_data=f"tz:{tz}"))
        if i + 1 < len(_TIMEZONE_OPTIONS):
            label2, tz2 = _TIMEZONE_OPTIONS[i + 1]
            row.append(InlineKeyboardButton(label2, callback_data=f"tz:{tz2}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def vocab_detail_keyboard(
    record_id: str,
    page: int,
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    词汇详情页键盘：三个编辑按钮 + 返回词库按钮
    callback_data 格式：
      vedit:{record_id}:{field}:{page}  — 进入字段编辑
      vocab_page:{page}                 — 返回词库列表
    """
    pos_label = await t_async("btn_edit_pos", lang)
    def_label = await t_async("btn_edit_def", lang)
    ctx_label = await t_async("btn_edit_ctx", lang)
    back_label = await t_async("btn_back_vocab", lang)

    row1 = [
        InlineKeyboardButton(pos_label, callback_data=f"vedit:{record_id}:pos:{page}"),
        InlineKeyboardButton(def_label, callback_data=f"vedit:{record_id}:definition:{page}"),
        InlineKeyboardButton(ctx_label, callback_data=f"vedit:{record_id}:context:{page}"),
    ]
    row2 = [
        InlineKeyboardButton(back_label, callback_data=f"vocab_page:{page}"),
    ]
    return InlineKeyboardMarkup([row1, row2])


async def edit_field_keyboard(
    record_id: str,
    field: str,
    page: int,
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    编辑字段等待输入时的键盘：仅一个取消按钮
    callback_data 格式：vedit_cancel:{page}
    """
    cancel_label = await t_async("btn_cancel", lang)
    row = [
        InlineKeyboardButton(cancel_label, callback_data=f"vedit_cancel:{page}"),
    ]
    return InlineKeyboardMarkup([row])


async def settings_panel_keyboard(
    toggle_label: str,
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    通知设置主面板键盘：更改时段 + 开关推送
    toggle_label: 已翻译的开关按钮文案（由调用方用 t_async 获取）
    """
    window_label = await t_async("btn_change_window", lang)
    row1 = [InlineKeyboardButton(window_label, callback_data="settings:window")]
    row2 = [InlineKeyboardButton(toggle_label, callback_data="settings:toggle")]
    return InlineKeyboardMarkup([row1, row2])


async def remind_window_keyboard(lang: str = "zh") -> InlineKeyboardMarkup:
    """
    推送时段选择面板：5 种预设 + 返回按钮
    callback_data 格式：settings:set_win:{start}:{end}
    """
    all_day_label = await t_async("btn_all_day", lang)
    back_label = await t_async("btn_back", lang)

    # 前4个时段选项固定（不翻译，时间格式通用）
    options = [
        ("06:00–22:00", 6, 22),
        ("07:00–23:00", 7, 23),
        ("08:00–22:00", 8, 22),
        ("09:00–21:00", 9, 21),
        (all_day_label, 0, 24),
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
    rows.append([InlineKeyboardButton(back_label, callback_data="settings:back")])
    return InlineKeyboardMarkup(rows)


# ── 多语言管理键盘 ────────────────────────────────────────────────────────────

async def language_panel_keyboard(
    learning_languages: list[str],
    active_language: str,
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    多语言管理主面板键盘。
    每个已有语言一个切换按钮（当前激活显示 ✅），底部有添加新语言和设置母语按钮。
    callback_data 格式：lang:switch:{code}
    """
    from core.language import get_language_display

    add_label = await t_async("btn_add_lang", lang)
    native_label = await t_async("btn_set_native", lang)

    rows = []

    # 已有语言按钮（两两一行）
    lang_buttons = []
    for lang_code in learning_languages:
        label = get_language_display(lang_code)
        if lang_code == active_language:
            label = f"✅ {label}"
        lang_buttons.append(
            InlineKeyboardButton(label, callback_data=f"lang:switch:{lang_code}")
        )
    for i in range(0, len(lang_buttons), 2):
        rows.append(lang_buttons[i:i + 2])

    rows.append([InlineKeyboardButton(add_label, callback_data="lang:add")])
    rows.append([InlineKeyboardButton(native_label, callback_data="lang:native")])

    return InlineKeyboardMarkup(rows)


async def add_language_keyboard(
    existing_languages: list[str],
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    添加新语言面板：显示所有支持的目标语言，已有的标注 ✓。
    点击任意语言均触发 lang:add_confirm:{code}。
    """
    from core.language import SUPPORTED_TARGET_LANGUAGES

    back_label = await t_async("btn_back", lang)

    rows = []
    buttons = []
    for label, code in SUPPORTED_TARGET_LANGUAGES:
        display = f"✓ {label}" if code in existing_languages else label
        buttons.append(
            InlineKeyboardButton(display, callback_data=f"lang:add_confirm:{code}")
        )
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

    rows.append([InlineKeyboardButton(back_label, callback_data="lang:back")])
    return InlineKeyboardMarkup(rows)


async def onboarding_native_keyboard(lang: str = "zh") -> InlineKeyboardMarkup:
    """
    新用户引导：母语选择键盘，callback_data = ob_native:{code}
    与 native_language_keyboard 复用相同语言列表，但使用不同 callback 前缀。
    """
    from core.language import SUPPORTED_NATIVE_LANGUAGES

    rows = []
    buttons = []
    for label, code in SUPPORTED_NATIVE_LANGUAGES:
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"ob_native:{code}")
        )
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    return InlineKeyboardMarkup(rows)


async def onboarding_lang_keyboard(lang: str = "zh") -> InlineKeyboardMarkup:
    """
    新用户引导：学习语言选择键盘，callback_data = ob_lang:{code}
    与 add_language_keyboard 复用相同语言列表，但使用不同 callback 前缀。
    """
    from core.language import SUPPORTED_TARGET_LANGUAGES

    rows = []
    buttons = []
    for label, code in SUPPORTED_TARGET_LANGUAGES:
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"ob_lang:{code}")
        )
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    return InlineKeyboardMarkup(rows)


async def native_language_keyboard(
    current_native: str = "",
    lang: str = "zh",
) -> InlineKeyboardMarkup:
    """
    母语（释义语言）选择面板：展示所有支持的母语选项。
    current_native: 当前母语代码，用于标注 ✅。
    callback_data 格式：lang:set_native:{code}
    """
    from core.language import SUPPORTED_NATIVE_LANGUAGES

    back_label = await t_async("btn_back", lang)

    rows = []
    buttons = []
    for label, code in SUPPORTED_NATIVE_LANGUAGES:
        display = f"✅ {label}" if code == current_native else label
        buttons.append(
            InlineKeyboardButton(display, callback_data=f"lang:set_native:{code}")
        )
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

    rows.append([InlineKeyboardButton(back_label, callback_data="lang:back")])
    return InlineKeyboardMarkup(rows)
