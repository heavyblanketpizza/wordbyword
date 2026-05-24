from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wordbyword.after_class_reports import DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT
from wordbyword.class_archive.archive import DEFAULT_CLASS_SESSION_ARCHIVE_ROOT
from wordbyword.class_archive.live_lexicon import (
    DEFAULT_CANONICAL_DICTIONARY_PATH,
    DEFAULT_LIVE_LEXICON_PATH,
    DEFAULT_SUPPLEMENTAL_LEXICON_PATH,
    DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
    DEFAULT_USAGE_CANDIDATES_PATH,
)
from wordbyword.class_archive.usage_signals import DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.class_printing.printable import DEFAULT_PRINT_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    DEFAULT_PARSED_SOURCE_ROOT,
    DEFAULT_SOURCE_MATERIAL_ROOT,
)
from wordbyword.cli.options import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_REFERENCE_DICTIONARY_PATH
from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL

SETTINGS_SCHEMA_VERSION = 1
SETTINGS_DIRNAME = ".wordbyword"
SETTINGS_FILENAME = "gui-settings.json"


def default_settings() -> dict[str, object]:
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "model": DEFAULT_EXAMPLE_CLEANUP_MODEL,
        "base_url": "",
        "dictionary_input_dir": str(DEFAULT_INPUT_DIR),
        "dictionary_output_dir": str(DEFAULT_OUTPUT_DIR),
        "reference_dictionary_path": str(DEFAULT_REFERENCE_DICTIONARY_PATH),
        "dictionary_force": False,
        "include_numbers": False,
        "include_punctuation": False,
        "class_source_dir": str(DEFAULT_SOURCE_MATERIAL_ROOT),
        "source_output_root": str(DEFAULT_PARSED_SOURCE_ROOT),
        "rewrite_output_root": str(DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT),
        "print_output_root": str(DEFAULT_PRINT_OUTPUT_ROOT),
        "force_class_printouts": False,
        "skip_pdf_parse": False,
        "pdf_render_dpi": 144,
        "print_font_path": "",
        "print_body_font_size": "",
        "archive_root": str(DEFAULT_CLASS_SESSION_ARCHIVE_ROOT),
        "usage_signal_output_root": str(DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT),
        "canonical_dictionary_path": str(DEFAULT_CANONICAL_DICTIONARY_PATH),
        "usage_candidates_path": str(DEFAULT_USAGE_CANDIDATES_PATH),
        "usage_summary_path": "",
        "live_lexicon_path": str(DEFAULT_LIVE_LEXICON_PATH),
        "supplemental_lexicon_path": str(DEFAULT_SUPPLEMENTAL_LEXICON_PATH),
        "unmatched_usage_output_path": str(DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH),
        "after_class_printout_path": "",
        "after_class_report_output_root": str(DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT),
        "after_class_report_font_path": "",
        "include_near_duplicates": False,
    }


def default_settings_path(*, home: Path | None = None) -> Path:
    root = Path.home() if home is None else home
    return root / SETTINGS_DIRNAME / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> dict[str, object]:
    resolved_path = default_settings_path() if path is None else path
    settings = default_settings()
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if not isinstance(payload, Mapping):
        return settings
    settings.update(_known_settings(payload))
    settings["schema_version"] = SETTINGS_SCHEMA_VERSION
    return settings


def save_settings(settings: Mapping[str, object], path: Path | None = None) -> Path:
    resolved_path = default_settings_path() if path is None else path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    payload = default_settings()
    payload.update(_known_settings(settings))
    payload["schema_version"] = SETTINGS_SCHEMA_VERSION
    resolved_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return resolved_path


def _known_settings(payload: Mapping[str, object]) -> dict[str, object]:
    defaults = default_settings()
    result: dict[str, object] = {}
    for key, default_value in defaults.items():
        if key not in payload or key == "schema_version":
            continue
        value = payload[key]
        if isinstance(default_value, bool):
            result[key] = bool(value)
        elif isinstance(default_value, int):
            result[key] = _int_or_default(value, default_value)
        else:
            result[key] = "" if value is None else str(value)
    return result


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
