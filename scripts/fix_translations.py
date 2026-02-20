"""
翻译修复脚本：读取 gen_translations.py 的输出，
修复占位符名称被 AI 翻译的问题（如 {mot} → {word}），
然后将修复后的字典打印为可直接粘贴的 Python 代码。

用法（在项目根目录）：
    python -m scripts.gen_translations 2>/dev/null | python scripts/fix_translations.py
或：
    python scripts/fix_translations.py < gen_output.txt
"""
import sys
import re

# 修复规则：错误占位符 → 正确占位符
# 按语言整理 AI 常见误译
PLACEHOLDER_FIXES = {
    # 法语
    "mot": "word",
    "définition": "definition",
    "ligne_de_contexte": "context_line",
    "niveau": "level",
    "mode": "mode",  # fr 通常保持不变
    "traduction": "translation",
    "affichage": "display",
    "nb_jours": "days",
    # 西班牙语
    "palabra": "word",
    "definición": "definition",
    "linea_de_contexto": "context_line",
    "nivel": "level",
    "modo": "mode",
    "traducción": "translation",
    "traduccion": "translation",
    "dias": "days",
    "día": "date",
    # 葡萄牙语
    "palavra": "word",
    "definição": "definition",
    "linha_de_contexto": "context_line",
    "nível": "level",
    "modo": "mode",
    "tradução": "translation",
    "traducao": "translation",
    "dias": "days",
    # 俄语（拉丁转写）
    "slovo": "word",
    "opredelenie": "definition",
    "uroven": "level",
    "rezhim": "mode",
    "perevod": "translation",
    # 意大利语
    "parola": "word",
    "definizione": "definition",
    "riga_contesto": "context_line",
    "livello": "level",
    "modalità": "mode",
    "modalita": "mode",
    "traduzione": "translation",
    "giorni": "days",
    # 日语（可能出现片假名或平假名）
    "言葉": "word",
    "定義": "definition",
    "レベル": "level",
    "モード": "mode",
    "翻訳": "translation",
    # 韓語
    "단어": "word",
    "정의": "definition",
    "수준": "level",
    "모드": "mode",
    "번역": "translation",
}


def fix_placeholder(match: re.Match) -> str:
    """修复单个占位符中的变量名"""
    inner = match.group(1)  # 花括号内的内容，如 "mot" 或 "count:>4"
    # 提取变量名（冒号之前的部分）
    if ":" in inner:
        varname, fmt_spec = inner.split(":", 1)
    else:
        varname, fmt_spec = inner, None

    # 如果变量名在修复表中，替换它
    fixed_varname = PLACEHOLDER_FIXES.get(varname, varname)

    if fmt_spec is not None:
        return "{" + fixed_varname + ":" + fmt_spec + "}"
    else:
        return "{" + fixed_varname + "}"


def fix_string(s: str) -> str:
    """修复字符串中所有错误的占位符"""
    return re.sub(r"\{([^}]+)\}", fix_placeholder, s)


def main() -> None:
    raw = sys.stdin.read()

    # 修复占位符
    fixed = re.sub(r"\{([^}]+)\}", fix_placeholder, raw)

    print(fixed)


if __name__ == "__main__":
    main()
