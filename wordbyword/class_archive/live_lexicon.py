from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from wordbyword.class_archive.archive import DEFAULT_VOCABULARY_INTELLIGENCE_ROOT
from wordbyword.class_archive.usage_signals import DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.candidates import (
    LEARNING_SIGNAL_FIELD_RENAMES,
    LEGACY_VOCABULARY_SCORE_FIELDS,
    VOCABULARY_SIGNAL_FIELDS,
    normalized_learning_signal_score,
    parse_learning_signal_score,
    row_learning_signal_score,
)
from wordbyword.dictionary_build.output_files import read_jsonl, write_jsonl

DEFAULT_CANONICAL_DICTIONARY_PATH = artifact_path(Path("build-dictionary"), ArtifactId.FINAL_DICTIONARY)
DEFAULT_LIVE_GRADE_LEXICON_ROOT = DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "live-grade-lexicon"
DEFAULT_LIVE_LEXICON_PATH = DEFAULT_LIVE_GRADE_LEXICON_ROOT / "grade1-live-lexicon-canonical.jsonl"
DEFAULT_SUPPLEMENTAL_LEXICON_PATH = DEFAULT_LIVE_GRADE_LEXICON_ROOT / "grade1-live-lexicon-supplemental.jsonl"
DEFAULT_USAGE_CANDIDATES_PATH = artifact_path(DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT, ArtifactId.DICTIONARY_CANDIDATES)
DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH = DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT / "10_unmatched_usage_candidates.jsonl"
CANONICAL_SOURCE_DICTIONARY_SEED = "seeded_from_dictionary"
CANONICAL_SOURCE_LIVE_LEXICON = "existing_live_lexicon"
SUPPLEMENTAL_SOURCE_TIER = "supplemental_private"
SUPPLEMENTAL_STATUS_OBSERVED = "non_canonical_observed"
SUPPLEMENTAL_STATUS_INACTIVE = "inactive_observed"
SUPPLEMENTAL_STATUS_PROMOTED = "promoted_to_canonical"
ProgressCallback = Callable[[str, str], None]
SCORE_FIELD_RENAMES = LEARNING_SIGNAL_FIELD_RENAMES
SCORE_FIELDS = {*LEGACY_VOCABULARY_SCORE_FIELDS, *VOCABULARY_SIGNAL_FIELDS}
REMOVED_CANONICAL_FIELDS = {
    "example_generated_with_ollama",
    "meaning_generated_with_ollama",
    "example_replaced_with_cleaned_sentence",
    "class_count",
    "class_printout_count",
    "class_document_count",
    "roi_score",
}


@dataclass(frozen=True)
class LiveLexiconUpdateResult:
    canonical_dictionary_path: Path
    usage_candidates_path: Path
    usage_summary_path: Path
    live_lexicon_path: Path
    supplemental_lexicon_path: Path
    unmatched_usage_output_path: Path
    canonical_source: str
    canonical_rows: int
    live_lexicon_rows: int
    usage_candidate_rows: int
    total_class_printouts: int
    matched_count: int
    unmatched_count: int
    supplemental_rows: int
    supplemental_active_rows: int
    supplemental_added_count: int
    supplemental_updated_count: int
    supplemental_inactive_count: int
    supplemental_promoted_count: int


@dataclass(frozen=True)
class SupplementalLexiconUpdate:
    rows: list[dict[str, Any]]
    active_rows: int
    added_count: int
    updated_count: int
    inactive_count: int
    promoted_count: int


class LiveLexiconUpdateError(RuntimeError):
    """Raised when live lexicon usage updates cannot be written."""


def update_live_lexicon(
    *,
    canonical_dictionary_path: Path = DEFAULT_CANONICAL_DICTIONARY_PATH,
    usage_candidates_path: Path = DEFAULT_USAGE_CANDIDATES_PATH,
    usage_summary_path: Path | None = None,
    live_lexicon_path: Path = DEFAULT_LIVE_LEXICON_PATH,
    supplemental_lexicon_path: Path = DEFAULT_SUPPLEMENTAL_LEXICON_PATH,
    unmatched_usage_output_path: Path = DEFAULT_UNMATCHED_USAGE_OUTPUT_PATH,
    progress: ProgressCallback | None = None,
) -> LiveLexiconUpdateResult:
    resolved_usage_summary_path = usage_summary_path or artifact_path(usage_candidates_path.parent, ArtifactId.SUMMARY)
    _report(progress, "load", f"reading usage candidates from {usage_candidates_path}")
    usage_candidates = read_jsonl_objects(usage_candidates_path, label="usage candidates")
    usage_summary = read_json_object(resolved_usage_summary_path, label="usage summary")
    total_class_printouts = parse_nonnegative_int(
        usage_summary.get("document_count"),
        field="document_count",
        canonical_key="usage summary",
    )
    canonical_source_rows, canonical_source = read_canonical_source_rows(
        canonical_dictionary_path=canonical_dictionary_path,
        live_lexicon_path=live_lexicon_path,
    )
    _report(
        progress,
        "load",
        canonical_source_detail(
            canonical_source=canonical_source,
            canonical_dictionary_path=canonical_dictionary_path,
            live_lexicon_path=live_lexicon_path,
        ),
    )
    usage_by_key = {
        canonical_key(row): row
        for row in usage_candidates
        if canonical_key(row)
    }

    _report(
        progress,
        "analyze",
        f"matching {len(usage_candidates):,} usage candidate(s) against {len(canonical_source_rows):,} canonical row(s)",
    )
    updated_rows: list[dict[str, Any]] = []
    matched_count = 0
    for row in canonical_source_rows:
        updated_row = canonical_live_row(row, preserve_curation_status=canonical_source == CANONICAL_SOURCE_LIVE_LEXICON)
        key = canonical_key(updated_row)
        usage_row = usage_by_key.get(key)
        if usage_row is None:
            updated_row["roi_score"] = learning_signal_roi_score(updated_row)
            updated_rows.append(updated_row)
            continue

        updated_row["class_count"] = parse_nonnegative_int(usage_row.get("count"), field="count", canonical_key=key)
        updated_row["class_printout_count"] = parse_nonnegative_int(
            usage_row.get("document_count"),
            field="document_count",
            canonical_key=key,
        )
        updated_row["variants"] = merge_variants(updated_row.get("variants"), usage_row.get("variants"))
        updated_row["roi_score"] = learning_signal_roi_score(usage_row)
        updated_rows.append(updated_row)
        matched_count += 1

    updated_rows = sort_canonical_live_rows(updated_rows)
    live_keys = active_canonical_keys(updated_rows)
    unmatched_usage_candidates = [
        dict(row)
        for row in usage_candidates
        if canonical_key(row) not in live_keys
    ]
    _report(
        progress,
        "maintain",
        f"maintaining supplemental rows from {len(unmatched_usage_candidates):,} unmatched usage candidate(s)",
    )
    supplemental_result = update_supplemental_rows(
        supplemental_lexicon_path=supplemental_lexicon_path,
        unmatched_usage_candidates=unmatched_usage_candidates,
        active_live_keys=live_keys,
    )

    _report(progress, "write", "writing canonical, supplemental, and unmatched usage outputs")
    write_jsonl_atomic(live_lexicon_path, updated_rows)
    write_jsonl_atomic(supplemental_lexicon_path, supplemental_result.rows)
    write_jsonl_atomic(unmatched_usage_output_path, unmatched_usage_candidates)
    _report(
        progress,
        "complete",
        f"matched {matched_count:,}; unmatched {len(unmatched_usage_candidates):,}; supplemental active {supplemental_result.active_rows:,}",
    )

    return LiveLexiconUpdateResult(
        canonical_dictionary_path=canonical_dictionary_path,
        usage_candidates_path=usage_candidates_path,
        usage_summary_path=resolved_usage_summary_path,
        live_lexicon_path=live_lexicon_path,
        supplemental_lexicon_path=supplemental_lexicon_path,
        unmatched_usage_output_path=unmatched_usage_output_path,
        canonical_source=canonical_source,
        canonical_rows=len(canonical_source_rows),
        live_lexicon_rows=len(updated_rows),
        usage_candidate_rows=len(usage_candidates),
        total_class_printouts=total_class_printouts,
        matched_count=matched_count,
        unmatched_count=len(unmatched_usage_candidates),
        supplemental_rows=len(supplemental_result.rows),
        supplemental_active_rows=supplemental_result.active_rows,
        supplemental_added_count=supplemental_result.added_count,
        supplemental_updated_count=supplemental_result.updated_count,
        supplemental_inactive_count=supplemental_result.inactive_count,
        supplemental_promoted_count=supplemental_result.promoted_count,
    )


def read_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LiveLexiconUpdateError(f"Cannot read {label}: {path}")
    try:
        rows = read_jsonl(path)
    except ValueError as error:
        raise LiveLexiconUpdateError(f"Invalid JSON in {label}: {path}: {error}") from error
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


def read_canonical_source_rows(
    *,
    canonical_dictionary_path: Path,
    live_lexicon_path: Path,
) -> tuple[list[dict[str, Any]], str]:
    if live_lexicon_path.is_file():
        return read_jsonl_objects(live_lexicon_path, label="canonical live lexicon"), CANONICAL_SOURCE_LIVE_LEXICON
    return read_jsonl_objects(canonical_dictionary_path, label="canonical dictionary"), CANONICAL_SOURCE_DICTIONARY_SEED


def canonical_source_detail(
    *,
    canonical_source: str,
    canonical_dictionary_path: Path,
    live_lexicon_path: Path,
) -> str:
    if canonical_source == CANONICAL_SOURCE_LIVE_LEXICON:
        return f"using existing canonical live lexicon from {live_lexicon_path}"
    return f"seeding canonical live lexicon from {canonical_dictionary_path}"


def _report(progress: ProgressCallback | None, phase: str, detail: str) -> None:
    if progress is not None:
        progress(phase, detail)


def canonical_live_row(row: Mapping[str, Any], *, preserve_curation_status: bool = False) -> dict[str, Any]:
    key = canonical_key(row)
    updated: dict[str, Any] = {}
    raw_count: Any = None
    raw_document_count: Any = None
    raw_variants: Any = None
    for field, value in row.items():
        if field in REMOVED_CANONICAL_FIELDS:
            continue
        if field in SCORE_FIELDS:
            continue
        if field == "count":
            raw_count = value
            continue
        if field == "document_count":
            raw_document_count = value
            continue
        if field == "curation_status" and not preserve_curation_status:
            continue
        if field == "variants":
            raw_variants = value
            continue
        updated[field] = value

    if "canonical_count" not in updated and raw_count is not None:
        updated["canonical_count"] = raw_count
    if "canonical_document_count" not in updated and raw_document_count is not None:
        updated["canonical_document_count"] = raw_document_count
    if "canonical_count" not in updated:
        raise LiveLexiconUpdateError(f"canonical_count/count for {key} must be present in the canonical source.")
    if "canonical_document_count" not in updated:
        raise LiveLexiconUpdateError(
            f"canonical_document_count/document_count for {key} must be present in the canonical source."
        )
    updated["canonical_count"] = parse_nonnegative_int(
        updated["canonical_count"],
        field="canonical_count",
        canonical_key=key,
    )
    updated["canonical_document_count"] = parse_nonnegative_int(
        updated["canonical_document_count"],
        field="canonical_document_count",
        canonical_key=key,
    )
    if not preserve_curation_status or not updated.get("curation_status"):
        updated["curation_status"] = "canonical"
    updated["variants"] = parse_variants(raw_variants)
    apply_learning_signal_fields(updated, row)
    updated["class_count"] = 0
    updated["class_printout_count"] = 0
    updated["roi_score"] = learning_signal_roi_score(updated)
    return updated


def active_canonical_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {
        key
        for row in rows
        if (key := canonical_key(row)) and row.get("active") is not False
    }


def apply_learning_signal_fields(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for legacy_field, renamed_field in SCORE_FIELD_RENAMES.items():
        value = source.get(renamed_field, source.get(legacy_field))
        if value is not None:
            target[renamed_field] = parse_learning_signal_score(value)
    target["learning_signal_score"] = row_learning_signal_score(source)
    frequency_rate = source.get("frequency_rate_per_10k")
    if frequency_rate is not None:
        target["frequency_rate_per_10k"] = parse_nonnegative_float(
            frequency_rate,
            field="frequency_rate_per_10k",
            canonical_key=canonical_key(source),
        )


def learning_signal_roi_score(row: Mapping[str, Any]) -> float:
    return normalized_learning_signal_score(row)


def sort_canonical_live_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=live_row_sort_key)


def sort_supplemental_live_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("active") is True else 1,
            *live_row_sort_key(row),
        ),
    )


def live_row_sort_key(row: Mapping[str, Any]) -> tuple[float, int, int, str]:
    return (
        -parse_score(row.get("roi_score"), field="roi_score", canonical_key=canonical_key(row)),
        -parse_nonnegative_int(row.get("class_printout_count", 0), field="class_printout_count", canonical_key=canonical_key(row)),
        -parse_nonnegative_int(row.get("class_count", 0), field="class_count", canonical_key=canonical_key(row)),
        canonical_key(row),
    )


def update_supplemental_rows(
    *,
    supplemental_lexicon_path: Path,
    unmatched_usage_candidates: list[dict[str, Any]],
    active_live_keys: set[str],
) -> SupplementalLexiconUpdate:
    existing_rows = (
        read_jsonl_objects(supplemental_lexicon_path, label="supplemental live lexicon")
        if supplemental_lexicon_path.is_file()
        else []
    )
    unmatched_by_key = {
        canonical_key(row): row
        for row in unmatched_usage_candidates
        if canonical_key(row)
    }

    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    added_count = 0
    updated_count = 0
    inactive_count = 0
    promoted_count = 0
    for row in existing_rows:
        key = canonical_key(row)
        if key:
            seen_keys.add(key)
        if key and key in active_live_keys:
            rows.append(supplemental_status_row(row, canonical_status=SUPPLEMENTAL_STATUS_PROMOTED))
            promoted_count += 1
            continue
        if key and key in unmatched_by_key:
            rows.append(supplemental_live_row(unmatched_by_key[key], existing_row=row))
            updated_count += 1
            continue
        rows.append(supplemental_status_row(row, canonical_status=SUPPLEMENTAL_STATUS_INACTIVE))
        inactive_count += 1

    for row in unmatched_usage_candidates:
        key = canonical_key(row)
        if not key or key in seen_keys:
            continue
        rows.append(supplemental_live_row(row))
        seen_keys.add(key)
        added_count += 1

    sorted_rows = sort_supplemental_live_rows(rows)
    return SupplementalLexiconUpdate(
        rows=sorted_rows,
        active_rows=sum(1 for row in sorted_rows if row.get("active") is True),
        added_count=added_count,
        updated_count=updated_count,
        inactive_count=inactive_count,
        promoted_count=promoted_count,
    )


def supplemental_live_row(
    usage_row: Mapping[str, Any],
    *,
    existing_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    key = canonical_key(usage_row)
    updated = dict(existing_row or {})
    for field, value in usage_row.items():
        if field in {
            "count",
            "document_count",
            "class_count",
            "class_printout_count",
            "class_document_count",
            "source_tier",
            "canonical_status",
            "active",
            "roi_score",
        }:
            continue
        if field in SCORE_FIELDS:
            continue
        if existing_row is not None and field == "curation_status" and field in updated:
            continue
        updated[field] = value
    updated.pop("count", None)
    updated.pop("document_count", None)
    updated.pop("class_document_count", None)
    updated["variants"] = merge_variants(
        existing_row.get("variants") if existing_row is not None else None,
        usage_row.get("variants"),
    )
    apply_learning_signal_fields(updated, usage_row)
    updated["class_count"] = parse_nonnegative_int(usage_row.get("count"), field="count", canonical_key=key)
    updated["class_printout_count"] = parse_nonnegative_int(
        usage_row.get("document_count"),
        field="document_count",
        canonical_key=key,
    )
    updated["roi_score"] = learning_signal_roi_score(updated)
    updated["source_tier"] = SUPPLEMENTAL_SOURCE_TIER
    updated["canonical_status"] = SUPPLEMENTAL_STATUS_OBSERVED
    updated["active"] = True
    return updated


def supplemental_status_row(row: Mapping[str, Any], *, canonical_status: str) -> dict[str, Any]:
    updated = dict(row)
    updated.pop("count", None)
    updated.pop("document_count", None)
    updated.pop("class_document_count", None)
    updated["class_count"] = 0
    updated["class_printout_count"] = 0
    apply_learning_signal_fields(updated, row)
    updated["roi_score"] = learning_signal_roi_score(updated)
    updated["source_tier"] = SUPPLEMENTAL_SOURCE_TIER
    updated["canonical_status"] = canonical_status
    updated["active"] = False
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


def parse_nonnegative_float(value: Any, *, field: str, canonical_key: str) -> float:
    if isinstance(value, bool):
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a non-negative number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a non-negative number.") from error
    if parsed < 0:
        raise LiveLexiconUpdateError(f"{field} for {canonical_key} must be a non-negative number.")
    return round(parsed, 3)


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
