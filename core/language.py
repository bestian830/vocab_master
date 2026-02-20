"""
多语言学习元数据常量，供 AI prompt 参数化和 UI 显示使用
"""

# 目标学习语言元数据（语言代码 → 元数据）
LANGUAGE_META = {
    "en": {"name": "English",     "native_name": "英语",      "flag": "🇬🇧"},
    "ja": {"name": "Japanese",    "native_name": "日语",      "flag": "🇯🇵"},
    "fr": {"name": "French",      "native_name": "法语",      "flag": "🇫🇷"},
    "de": {"name": "German",      "native_name": "德语",      "flag": "🇩🇪"},
    "es": {"name": "Spanish",     "native_name": "西班牙语",  "flag": "🇪🇸"},
    "ko": {"name": "Korean",      "native_name": "韩语",      "flag": "🇰🇷"},
    "zh": {"name": "Chinese",     "native_name": "中文",      "flag": "🇨🇳"},
    "pt": {"name": "Portuguese",  "native_name": "葡萄牙语",  "flag": "🇵🇹"},
    "it": {"name": "Italian",     "native_name": "意大利语",  "flag": "🇮🇹"},
    "ru": {"name": "Russian",     "native_name": "俄语",      "flag": "🇷🇺"},
}

# 母语元数据（释义语言选项）
NATIVE_LANGUAGE_META = {
    "zh": {"name": "Chinese (中文)",          "flag": "🇨🇳", "label": "🇨🇳 中文"},
    "en": {"name": "English",                 "flag": "🇬🇧", "label": "🇬🇧 English"},
    "ja": {"name": "Japanese (日本語)",       "flag": "🇯🇵", "label": "🇯🇵 日本語"},
    "ko": {"name": "Korean (한국어)",         "flag": "🇰🇷", "label": "🇰🇷 한국어"},
    "fr": {"name": "French (Français)",       "flag": "🇫🇷", "label": "🇫🇷 Français"},
    "de": {"name": "German (Deutsch)",        "flag": "🇩🇪", "label": "🇩🇪 Deutsch"},
    "es": {"name": "Spanish (Español)",       "flag": "🇪🇸", "label": "🇪🇸 Español"},
    "pt": {"name": "Portuguese (Português)",  "flag": "🇵🇹", "label": "🇵🇹 Português"},
    "ru": {"name": "Russian (Русский)",       "flag": "🇷🇺", "label": "🇷🇺 Русский"},
    "it": {"name": "Italian (Italiano)",      "flag": "🇮🇹", "label": "🇮🇹 Italiano"},
}

# UI 显示用：支持的目标学习语言列表（显示名, 语言代码）
SUPPORTED_TARGET_LANGUAGES = [
    ("🇬🇧 英语 (English)",         "en"),
    ("🇯🇵 日语 (Japanese)",        "ja"),
    ("🇫🇷 法语 (French)",          "fr"),
    ("🇩🇪 德语 (German)",          "de"),
    ("🇪🇸 西班牙语 (Spanish)",     "es"),
    ("🇰🇷 韩语 (Korean)",          "ko"),
    ("🇨🇳 中文 (Chinese)",         "zh"),
    ("🇵🇹 葡萄牙语 (Portuguese)",  "pt"),
    ("🇮🇹 意大利语 (Italian)",     "it"),
    ("🇷🇺 俄语 (Russian)",         "ru"),
]

# UI 显示用：支持的母语列表（显示名, 语言代码）
SUPPORTED_NATIVE_LANGUAGES = [
    ("🇨🇳 中文",      "zh"),
    ("🇬🇧 English",   "en"),
    ("🇯🇵 日本語",    "ja"),
    ("🇰🇷 한국어",    "ko"),
    ("🇫🇷 Français",  "fr"),
    ("🇩🇪 Deutsch",   "de"),
    ("🇪🇸 Español",   "es"),
    ("🇵🇹 Português", "pt"),
    ("🇷🇺 Русский",   "ru"),
    ("🇮🇹 Italiano",  "it"),
]


def get_language_display(lang_code: str) -> str:
    """返回语言代码对应的 emoji + 本地化名称，如 '🇬🇧 英语'"""
    meta = LANGUAGE_META.get(lang_code)
    if meta:
        return f"{meta['flag']} {meta['native_name']}"
    return lang_code


def get_native_language_display(lang_code: str) -> str:
    """返回母语代码对应的显示标签，如 '🇨🇳 中文'"""
    meta = NATIVE_LANGUAGE_META.get(lang_code)
    if meta:
        return meta["label"]
    return lang_code
