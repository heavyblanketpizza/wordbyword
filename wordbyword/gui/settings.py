from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.llm import LLMConfigurationError, normalize_provider_name
from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL
from wordbyword.grade_profiles import (
    DEFAULT_TARGET_GRADE,
    grade_workspace,
    normalize_target_grade,
)
from wordbyword.gui.i18n import LANGUAGE_SYSTEM, normalize_language
from wordbyword.gui.themes import THEME_DEFAULT, normalize_theme

SETTINGS_SCHEMA_VERSION = 6
SETTINGS_DIRNAME = ".wordbyword"
SETTINGS_FILENAME = "gui-settings.json"
TEXT_SCALE_DEFAULT = 1.0
TEXT_SCALE_MIN = 0.9
TEXT_SCALE_MAX = 1.3
TEXT_SCALE_STEP = 0.05


def default_settings() -> dict[str, object]:
    grade_defaults = grade_dependent_default_settings(DEFAULT_TARGET_GRADE)
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "gui_language": LANGUAGE_SYSTEM,
        "gui_theme": THEME_DEFAULT,
        "gui_text_scale": TEXT_SCALE_DEFAULT,
        "target_grade": DEFAULT_TARGET_GRADE,
        "provider": "ollama",
        "model": DEFAULT_EXAMPLE_CLEANUP_MODEL,
        "base_url": "",
        "dictionary_input_dir": grade_defaults["dictionary_input_dir"],
        "dictionary_output_dir": grade_defaults["dictionary_output_dir"],
        "reference_dictionary_path": grade_defaults["reference_dictionary_path"],
        "dictionary_force": False,
        "include_numbers": False,
        "include_punctuation": False,
        "class_source_dir": grade_defaults["class_source_dir"],
        "source_output_root": grade_defaults["source_output_root"],
        "rewrite_output_root": grade_defaults["rewrite_output_root"],
        "print_output_root": grade_defaults["print_output_root"],
        "rewrite_dictionary_path": "",
        "force_class_printouts": False,
        "skip_pdf_parse": False,
        "pdf_render_dpi": 144,
        "print_font_path": "",
        "print_body_font_size": "",
        "archive_root": grade_defaults["archive_root"],
        "usage_signal_output_root": grade_defaults["usage_signal_output_root"],
        "canonical_dictionary_path": grade_defaults["canonical_dictionary_path"],
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
    workspace = grade_workspace(grade)
    return {
        "dictionary_input_dir": str(workspace.dictionary_input_dir),
        "dictionary_output_dir": str(workspace.dictionary_output_dir),
        "reference_dictionary_path": str(workspace.reference_dictionary_path),
        "class_source_dir": str(workspace.class_source_dir),
        "source_output_root": str(workspace.source_output_root),
        "rewrite_output_root": str(workspace.rewrite_output_root),
        "print_output_root": str(workspace.print_output_root),
        "archive_root": str(workspace.archive_root),
        "usage_signal_output_root": str(workspace.usage_signal_output_root),
        "canonical_dictionary_path": str(artifact_path(workspace.dictionary_output_dir, ArtifactId.FINAL_DICTIONARY)),
        "usage_candidates_path": str(workspace.usage_candidates_path),
        "live_lexicon_path": str(workspace.live_lexicon_path),
        "supplemental_lexicon_path": str(workspace.supplemental_lexicon_path),
        "unmatched_usage_output_path": str(workspace.unmatched_usage_output_path),
        "after_class_report_output_root": str(workspace.after_class_report_output_root),
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
        elif key == "gui_theme":
            result[key] = normalize_theme(value)
        elif key == "gui_text_scale":
            result[key] = normalize_text_scale(value)
        elif key == "provider":
            result[key] = normalize_provider_setting(value)
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


def normalize_text_scale(value: object) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return TEXT_SCALE_DEFAULT
    if not math.isfinite(scale):
        return TEXT_SCALE_DEFAULT
    clamped = min(TEXT_SCALE_MAX, max(TEXT_SCALE_MIN, scale))
    steps = round((clamped - TEXT_SCALE_MIN) / TEXT_SCALE_STEP)
    snapped = TEXT_SCALE_MIN + steps * TEXT_SCALE_STEP
    return round(min(TEXT_SCALE_MAX, max(TEXT_SCALE_MIN, snapped)), 2)


def normalize_provider_setting(value: object) -> str:
    try:
        return normalize_provider_name(str(value or "ollama"))
    except LLMConfigurationError:
        return "ollama"
