from __future__ import annotations

import json
import locale
import os
from functools import lru_cache
from pathlib import Path
from typing import Final

LANGUAGE_SYSTEM: Final = "system"
LANGUAGE_EN: Final = "en"
LANGUAGE_KO: Final = "ko"
SUPPORTED_LANGUAGE_SETTINGS: Final = frozenset({LANGUAGE_SYSTEM, LANGUAGE_EN, LANGUAGE_KO})
SUPPORTED_RESOLVED_LANGUAGES: Final = frozenset({LANGUAGE_EN, LANGUAGE_KO})

I18N_ASSET_ROOT: Final = Path(__file__).with_name("assets") / "i18n"


def normalize_language(value: object) -> str:
    text = "" if value is None else str(value).strip().lower()
    return text if text in SUPPORTED_LANGUAGE_SETTINGS else LANGUAGE_SYSTEM


def resolve_language(value: object) -> str:
    selected = normalize_language(value)
    if selected in SUPPORTED_RESOLVED_LANGUAGES:
        return selected
    return _system_language()


def catalog(language: object) -> dict[str, str]:
    resolved = resolve_language(language)
    if resolved == LANGUAGE_EN:
        return dict(_catalog_for_language(LANGUAGE_EN))
    english = _catalog_for_language(LANGUAGE_EN)
    localized = _catalog_for_language(resolved)
    return {key: localized.get(key, fallback) for key, fallback in english.items()}


def text(language: object, key: str) -> str:
    return catalog(language).get(key, key)


def language_state(selected: object, display_language: object | None = None) -> dict[str, object]:
    normalized = normalize_language(selected)
    resolved = resolve_language(normalized)
    label_language = resolved if display_language is None else resolve_language(display_language)
    return {
        "selected": normalized,
        "resolved": resolved,
        "options": [
            {"value": LANGUAGE_SYSTEM, "label": text(label_language, "language.system")},
            {"value": LANGUAGE_EN, "label": text(label_language, "language.english")},
            {"value": LANGUAGE_KO, "label": text(label_language, "language.korean")},
        ],
    }


@lru_cache(maxsize=None)
def _catalog_for_language(language: str) -> dict[str, str]:
    path = I18N_ASSET_ROOT / f"{language}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"GUI i18n catalog must be an object: {path}")
    return {str(key): str(value) for key, value in payload.items()}


def _system_language() -> str:
    try:
        from PySide6.QtCore import QLocale

        if QLocale.system().language() == QLocale.Language.Korean:
            return LANGUAGE_KO
    except Exception:
        pass

    candidates = [
        os.environ.get("LANGUAGE", ""),
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
        str(locale.getlocale(locale.LC_CTYPE)[0] or ""),
    ]
    return LANGUAGE_KO if any(candidate.lower().startswith("ko") for candidate in candidates) else LANGUAGE_EN
