from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wordbyword.class_archive.archive import DEFAULT_CLASS_SESSION_ARCHIVE_ROOT
from wordbyword.class_archive.live_lexicon import DEFAULT_CANONICAL_DICTIONARY_PATH
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    DEFAULT_PARSED_SOURCE_ROOT,
    DEFAULT_SOURCE_MATERIAL_ROOT,
)
from wordbyword.cli.options import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_REFERENCE_DICTIONARY_PATH
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL
from wordbyword.grade_profiles import (
    DEFAULT_TARGET_GRADE,
    default_after_class_report_output_root,
    default_live_lexicon_path,
    default_print_output_root,
    default_supplemental_lexicon_path,
    default_unmatched_usage_output_path,
    default_usage_signal_output_root,
    default_vocabulary_guided_rewrite_output_root,
    normalize_target_grade,
)
from wordbyword.gui.i18n import LANGUAGE_SYSTEM, normalize_language

SETTINGS_SCHEMA_VERSION = 2
SETTINGS_DIRNAME = ".wordbyword"
SETTINGS_FILENAME = "gui-settings.json"


def default_settings() -> dict[str, object]:
    grade_defaults = grade_dependent_default_settings(DEFAULT_TARGET_GRADE)
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "gui_language": LANGUAGE_SYSTEM,
        "target_grade": DEFAULT_TARGET_GRADE,
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
        "rewrite_output_root": grade_defaults["rewrite_output_root"],
        "print_output_root": grade_defaults["print_output_root"],
        "rewrite_dictionary_path": "",
        "force_class_printouts": False,
        "skip_pdf_parse": False,
        "pdf_render_dpi": 144,
        "print_font_path": "",
        "print_body_font_size": "",
        "archive_root": str(DEFAULT_CLASS_SESSION_ARCHIVE_ROOT),
        "usage_signal_output_root": grade_defaults["usage_signal_output_root"],
        "canonical_dictionary_path": str(DEFAULT_CANONICAL_DICTIONARY_PATH),
        "usage_candidates_path": grade_defaults["usage_candidates_path"],
        "usage_summary_path": "",
        "live_lexicon_path": grade_defaults["live_lexicon_path"],
        "supplemental_lexicon_path": grade_defaults["supplemental_lexicon_path"],
        "unmatched_usage_output_path": grade_defaults["unmatched_usage_output_path"],
        "after_class_printout_path": "",
        "after_class_report_output_root": grade_defaults["after_class_report_output_root"],
        "after_class_report_font_path": "",
        "include_near_duplicates": False,
    }


def grade_dependent_default_settings(target_grade: object) -> dict[str, str]:
    grade = normalize_target_grade(target_grade)
    explicit_target = grade != DEFAULT_TARGET_GRADE
    usage_root = default_usage_signal_output_root(grade, explicit_target=explicit_target)
    return {
        "rewrite_output_root": str(default_vocabulary_guided_rewrite_output_root(grade)),
        "print_output_root": str(default_print_output_root(grade)),
        "usage_signal_output_root": str(usage_root),
        "usage_candidates_path": str(artifact_path(usage_root, ArtifactId.DICTIONARY_CANDIDATES)),
        "live_lexicon_path": str(default_live_lexicon_path(grade)),
        "supplemental_lexicon_path": str(default_supplemental_lexicon_path(grade)),
        "unmatched_usage_output_path": str(default_unmatched_usage_output_path(grade, explicit_target=explicit_target)),
        "after_class_report_output_root": str(default_after_class_report_output_root(grade)),
    }


def settings_with_target_grade_defaults(
    settings: Mapping[str, object],
    *,
    old_target_grade: object | None = None,
) -> dict[str, object]:
    current = default_settings()
    current.update(_known_settings(settings))
    old_grade = normalize_target_grade(old_target_grade or current.get("target_grade") or DEFAULT_TARGET_GRADE)
    new_grade = normalize_target_grade(current.get("target_grade") or DEFAULT_TARGET_GRADE)
    old_defaults = grade_dependent_default_settings(old_grade)
    new_defaults = grade_dependent_default_settings(new_grade)
    for key, new_value in new_defaults.items():
        value = str(current.get(key) or "")
        if not value or value == old_defaults[key]:
            current[key] = new_value
    current["target_grade"] = new_grade
    current["schema_version"] = SETTINGS_SCHEMA_VERSION
    return current


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
    return settings_with_target_grade_defaults(settings, old_target_grade=DEFAULT_TARGET_GRADE)


def save_settings(settings: Mapping[str, object], path: Path | None = None) -> Path:
    resolved_path = default_settings_path() if path is None else path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    payload = settings_with_target_grade_defaults(settings, old_target_grade=DEFAULT_TARGET_GRADE)
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
        if key == "gui_language":
            result[key] = normalize_language(value)
        elif isinstance(default_value, bool):
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
