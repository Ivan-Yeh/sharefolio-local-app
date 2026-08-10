"""Display-only translations, loaded from translations.json.

Language only affects how values are rendered in the UI (labels, titles,
column headers, messages). It never affects how trades/incomes are stored
or parsed — those stay in whatever the CSV/DB already contains.

Edit translations.json directly to add or change strings; it is reloaded
automatically whenever it changes on disk.
"""

import json
import sys
from pathlib import Path

SUPPORTED_LANGUAGES = ["en", "zh_Hant"]
LANGUAGE_NAMES = {"en": "English", "zh_Hant": "繁體中文"}

if getattr(sys, "frozen", False):
    _base_dir = Path(sys._MEIPASS)
else:
    _base_dir = Path(__file__).parent

TRANSLATIONS_FILE = _base_dir / "translations.json"

_cache: dict[str, dict[str, str]] = {}
_cache_mtime: float | None = None


def _load() -> dict[str, dict[str, str]]:
    global _cache, _cache_mtime
    try:
        mtime = TRANSLATIONS_FILE.stat().st_mtime
    except OSError:
        return _cache
    if mtime != _cache_mtime:
        with TRANSLATIONS_FILE.open(encoding="utf-8") as f:
            _cache = json.load(f)
        _cache_mtime = mtime
    return _cache


def t(key: str, lang: str, **kwargs) -> str:
    translations = _load()
    text = translations.get(lang, {}).get(key)
    if text is None:
        text = translations.get("en", {}).get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text
