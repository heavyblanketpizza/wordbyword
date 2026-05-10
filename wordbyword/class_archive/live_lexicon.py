from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from wordbyword.class_archive.archive import DEFAULT_VOCABULARY_INTELLIGENCE_ROOT
from wordbyword.class_archive.usage_signals import DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT
from wordbyword.dictionary_build.output_files import read_jsonl, write_jsonl

DEFAULT_CANONICAL_DICTIONARY_PATH = Path("build-dictionary") / "15_final_dictionary.jsonl"
DEFAULT_LIVE_GRADE_LEXICON_ROOT = DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "live-grade-lexicon"
DEFAULT_LIVE_LEXICON_PATH = DEFAULT_LIVE_GRADE_LEXICON_ROOT / "grade1-live-lexicon-canonical.jsonl"
DEFAULT_USAGE_CANDIDATES_PATH = DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT / "08_dictionary_candidates.jsonl"
DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH = DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT / "10_unmatched_usage_candidates.jsonl"
REMOVED_CANONICAL_FIELDS = {
    "example_generated_with_ollama",
    "meaning_generated_with_ollama",
    "example_replaced_with_cleaned_sentence",
    "class_count",
    "class_printout_count",
    "class_document_count",
}


@dataclass(frozen=True)
class LiveLexiconUpdateResult:
    canonical_dictionary_path: Path
    usage_candidates_path: Path
    usage_summary_path: Path
    live_lexicon_path: Path
    unmatched_usage_output_path: Path
    canonical_rows: int
    live_lexicon_rows: int
    usage_candidate_rows: int
    total_class_printouts: int
    matched_count: int
    unmatched_count: int


class LiveLexiconUpdateError(RuntimeError):
    """Raised when live lexicon usage updates cannot be written."""


def update_live_lexicon(
    *,
    canonical_dictionary_path: Path = DEFAULT_CANONICAL_DICTIONARY_PATH,
    usage_candidates_path: Path = DEFAULT_USAGE_CANDIDATES_PATH,
    usage_summary_path: Path | None = None,
    live_lexicon_path: Path = DEFAULT_LIVE_LEXICON_PATH,
    unmatched_usage_output_path: Path = DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
) -> LiveLexiconUpdateResult:
    resolved_usage_summary_path = usage_summary_path or usage_candidates_path.parent / "00_summary.json"
    canonical_rows = read_jsonl_objects(canonical_dictionary_path, label="canonical dictionary")
    usage_candidates = read_jsonl_objects(usage_candidates_path, label="usage candidates")
    usage_summary = read_json_object(resolved_usage_summary_path, label="usage summary")
    total_class_printouts = parse_nonnegative_int(
        usage_summary.get("document_count"),
        field="document_count",
        canonical_key="usage summary",
    )
    usage_by_key = {
        canonical_key(row): row
        for row in usage_candidates
        if canonical_key(row)
    }
    live_keys = {canonical_key(row) for row in canonical_rows if canonical_key(row)}

    updated_rows: list[dict[str, Any]] = []
    matched_count = 0
    for row in canonical_rows:
        updated_row = canonical_live_row(row)
        key = canonical_key(updated_row)
        usage_row = usage_by_key.get(key)
        if usage_row is None:
            updated_row["roi_score"] = parse_score(
                updated_row.get("quality_score"),
                field="quality_score",
                canonical_key=key,
            )
            updated_rows.append(updated_row)
            continue

        updated_row["class_count"] = parse_nonnegative_int(usage_row.get("count"), field="count", canonical_key=key)
        updated_row["class_printout_count"] = parse_nonnegative_int(
            usage_row.get("document_count"),
            field="document_count",
            canonical_key=key,
        )
        updated_row["variants"] = merge_variants(updated_row.get("variants"), usage_row.get("variants"))
        updated_row["roi_score"] = usage_roi_score(
            canonical_quality_score=parse_score(
                updated_row.get("quality_score"),
                field="quality_score",
                canonical_key=key,
            ),
            usage_quality_score=parse_score(usage_row.get("quality_score"), field="quality_score", canonical_key=key),
            class_printout_count=updated_row["class_printout_count"],
            total_class_printouts=total_class_printouts,
        )
        updated_rows.append(updated_row)
        matched_count += 1

    unmatched_usage_candidates = [
        dict(row)
        for row in usage_candidates
        if canonical_key(row) not in live_keys
    ]

    write_jsonl_atomic(live_lexicon_path, updated_rows)
    write_jsonl_atomic(unmatched_usage_output_path, unmatched_usage_candidates)

    return LiveLexiconUpdateResult(
        canonical_dictionary_path=canonical_dictionary_path,
        usage_candidates_path=usage_candidates_path,
        usage_summary_path=resolved_usage_summary_path,
        live_lexicon_path=live_lexicon_path,
        unmatched_usage_output_path=unmatched_usage_output_path,
        canonical_rows=len(canonical_rows),
        live_lexicon_rows=len(updated_rows),
        usage_candidate_rows=len(usage_candidates),
        total_class_printouts=total_class_printouts,
        matched_count=matched_count,
        unmatched_count=len(unmatched_usage_candidates),
    )


def read_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LiveLexiconUpdateError(f"Cannot read {label}: {path}")
    try:
        rows = read_jsonl(path)
    except json.JSONDecodeError as error:
        raise LiveLexiconUpdateError(f"Invalid JSON in {label}: {path}: {error.msg}") from error
    objects: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise LiveLexiconUpdateError(f"Line {index} of {path} is not a JSON object.")
        objects.append(dict(row))
    return objects


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise LiveLexiconUpdateError(f"Cannot read {label}: {path}")
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LiveLexiconUpdateError(f"Invalid JSON in {label}: {path}: {error.msg}") from error
    if not isinstance(row, Mapping):
        raise LiveLexiconUpdateError(f"{path} is not a JSON object.")
    return dict(row)


def canonical_live_row(row: Mapping[str, Any]) -> dict[str, Any]:
    key = canonical_key(row)
    updated: dict[str, Any] = {}
    for field, value in row.items():
        if field in REMOVED_CANONICAL_FIELDS:
            continue
        if field == "count":
            updated["canonical_count"] = parse_nonnegative_int(value, field="count", canonical_key=key)
            continue
        if field == "document_count":
            updated["canonical_document_count"] = parse_nonnegative_int(
                value,
                field="document_count",
                canonical_key=key,
            )
            continue
        updated[field] = value

    if "canonical_count" not in updated:
        raise LiveLexiconUpdateError(f"count for {key} must be present in the canonical dictionary.")
    if "canonical_document_count" not in updated:
        raise LiveLexiconUpdateError(f"document_count for {key} must be present in the canonical dictionary.")
    updated["class_count"] = 0
    updated["class_printout_count"] = 0
    updated["roi_score"] = parse_score(updated.get("quality_score"), field="quality_score", canonical_key=key)
    return updated


def merge_variants(canonical_variants: Any, usage_variants: Any) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for variant in [*parse_variants(canonical_variants), *parse_variants(usage_variants)]:
        if variant in seen:
            continue
        seen.add(variant)
        merged.append(variant)
    return merged


def parse_variants(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = value.split("|")
    elif isinstance(value, (list, tuple)):
        raw_values = value
    else:
        raw_values = [value]
    return [str(variant).strip() for variant in raw_values if str(variant).strip()]


def usage_roi_score(
    *,
    canonical_quality_score: float,
    usage_quality_score: float,
    class_printout_count: int,
    total_class_printouts: int,
) -> float:
    if total_class_printouts <= 0:
        class_printout_signal = 0.0
    else:
        class_printout_signal = min(class_printout_count / total_class_printouts, 1.0)
    return round(
        (0.7 * canonical_quality_score) + (0.2 * usage_quality_score) + (0.1 * class_printout_signal),
        3,
    )


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    write_jsonl(temporary_path, rows)
    temporary_path.replace(path)


def canonical_key(row: Mapping[str, Any]) -> str:
    return str(row.get("canonical_key") or "").strip()


def parse_nonnegative_int(value: Any, *, field: str, canonical_key: str) -> int:
    if isinstance(value, bool):
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a non-negative integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a non-negative integer.") from error
    if parsed < 0:
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a non-negative integer.")
    return parsed


def parse_score(value: Any, *, field: str, canonical_key: str) -> float:
    if isinstance(value, bool):
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a number from 0.0 through 1.0.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a number from 0.0 through 1.0.") from error
    if not 0.0 <= parsed <= 1.0:
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a number from 0.0 through 1.0.")
    return round(parsed, 3)
