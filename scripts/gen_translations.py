"""
一次性脚本：批量翻译 STRINGS["en"] 所有 key，输出可直接粘贴进 bot/i18n.py 的 Python dict。
运行方式（在项目根目录）：
    python -m scripts.gen_translations
或：
    PYTHONPATH=. python scripts/gen_translations.py
"""
import asyncio
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.i18n import STRINGS
from ai.parser import translate_ui_text

# 需要生成的 8 种语言
LANGUAGES = ["fr", "de", "es", "pt", "ru", "it", "ja", "ko"]


async def main() -> None:
    en = STRINGS["en"]

    for lang in LANGUAGES:
        print(f'\n    "{lang}": {{')
        for key, text in en.items():
            try:
                translated = await translate_ui_text(text, lang)
            except Exception as exc:
                # 翻译失败时 fallback 到英文
                print(f"    # WARNING: translation failed for key={key}: {exc}", file=sys.stderr)
                translated = text
            # 输出为 Python repr 以保留转义字符
            print(f'        "{key}": {repr(translated)},')
        print("    },")


if __name__ == "__main__":
    asyncio.run(main())
