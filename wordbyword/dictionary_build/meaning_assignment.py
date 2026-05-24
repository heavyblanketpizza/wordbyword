from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from wordbyword.dictionary_build.candidates import migrated_vocabulary_score_row


@dataclass(frozen=True)
class MeaningAssignmentOutputs:
    rows: list[dict[str, object]]
    matched_count: int
    unmatched_count: int


MeaningAssignmentRowCallback = Callable[[dict[str, object]], None]


def build_dictionary_candidates_with_meanings(
    dictionary_entries: Sequence[Mapping[str, object]],
    reference_entries: Sequence[Mapping[str, object]],
    *,
    on_row: MeaningAssignmentRowCallback | None = None,
) -> MeaningAssignmentOutputs:
    meaning_by_key = reference_meaning_by_key(reference_entries)
    rows: list[dict[str, object]] = []
    matched_count = 0

    for entry in dictionary_entries:
        canonical_key = str(entry.get("canonical_key", ""))
        has_reference_match = canonical_key in meaning_by_key
        meaning = meaning_by_key.get(canonical_key, "")
        row = {
            **migrated_vocabulary_score_row(entry),
            "curation_status": "confirmed",
            "meaning": meaning,
        }
        rows.append(row)
        if has_reference_match:
            matched_count += 1
        if on_row is not None:
            on_row(row)

    return MeaningAssignmentOutputs(
        rows=rows,
        matched_count=matched_count,
        unmatched_count=len(rows) - matched_count,
    )


def reference_meaning_by_key(
    reference_entries: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    meanings: dict[str, str] = {}
    for entry in reference_entries:
        canonical_key = str(entry.get("canonical_key", "")).strip()
        if not canonical_key or canonical_key in meanings:
            continue
        meanings[canonical_key] = str(entry.get("meaning", "") or "")
    return meanings
