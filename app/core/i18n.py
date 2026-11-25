"""Arayüz metinleri: t("bölüm.anahtar", dil) → metin.

Metinler app/locales/tr.toml ve en.toml dosyalarındadır; kodda metin yazılmaz, anahtar yazılır.
Yeni metin = iki dosyaya aynı anahtarla bir satır (bir test iki dosyanın aynı anahtarlara sahip olduğunu
denetler). Metin içinde {ad} yer tutucusu olabilir: t("menu.count", lang, n=5).
Anahtar bulunamazsa anahtarın kendisi döner: ekranda görünür, gözden kaçmaz.

Not: endpoint adları ve Docs metinleri buradan gelmez; FastAPI'den ya da portal katmanından gelir.
"""
import tomllib
from pathlib import Path

LOCALES_DIR = Path(__file__).parent.parent / "locales"
LANGS = ["tr", "en"]
DEFAULT_LANG = "tr"


def load(lang: str) -> dict[str, str]:
    """TOML dosyasını düz sözlüğe çevirir: [login] title = "..." → {"login.title": "..."}."""
    with open(LOCALES_DIR / f"{lang}.toml", "rb") as file:
        return flatten(tomllib.load(file))


def flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    flat = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            flat.update(flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


TEXTS = {lang: load(lang) for lang in LANGS}


def upper(text: str, lang: str = DEFAULT_LANG) -> str:
    """Büyük harf, dile göre: Türkçede "i" → "İ", "ı" → "I" (Python'un upper()'ı "Cihazlar"ı "CIHAZLAR" yapıyordu)."""
    if lang == "tr":
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()


def t(key: str, lang: str = DEFAULT_LANG, **values) -> str:
    text = TEXTS.get(lang, TEXTS[DEFAULT_LANG]).get(key, key)
    return text.format(**values) if values else text
