from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from wordbyword.dictionary_build.canonical_keys import row_covered_canonical_keys

STAGE_TWELVE_STRIPPED_FIELDS = {
    "original_example",
    "cleaned_example_document",
    "cleaned_example_sentence_index",
    "cleaned_example_quality_score",
    "cleaned_example_covered_canonical_keys",
    "example_needs_rewrite",
    "example_assignment_source",
    "assigned_cleaned_sentence",
    "assigned_cleaned_sentence_document",
    "assigned_cleaned_sentence_page",
    "assigned_cleaned_sentence_sentence_index",
    "assigned_cleaned_sentence_quality_score",
    "assigned_cleaned_sentence_cleanup_action",
    "assigned_cleaned_sentence_covered_canonical_keys",
}


@dataclass(frozen=True)
class LinkedDictionaryOutputs:
    rows: list[dict[str, object]]
    linked_count: int
    unlinked_count: int


@dataclass(frozen=True)
class IndexedCleanedExampleSentence:
    index: int
    row: Mapping[str, object]
    covered_keys: set[str]
    cleaned_quality_score: float


@dataclass(frozen=True)
class IndexedDictionaryCandidate:
    index: int
    row: Mapping[str, object]
    quality_score: float


def build_linked_dictionary_candidate_outputs(
    dictionary_entries: Sequence[Mapping[str, object]],
    cleaned_example_sentences: Sequence[Mapping[str, object]],
) -> LinkedDictionaryOutputs:
    sorted_dictionary_entries = sorted(
        [
            IndexedDictionaryCandidate(
                index=index,
                row=row,
                quality_score=safe_float(row.get("quality_score")),
            )
            for index, row in enumerate(dictionary_entries)
        ],
        key=lambda item: (-item.quality_score, item.index),
    )
    remaining_sentences = sorted(
        [
            IndexedCleanedExampleSentence(
                index=index,
                row=row,
                covered_keys=set(
                    row_covered_canonical_keys(
                        row,
                        field_names=("cleaned_covered_canonical_keys",),
                    )
                ),
                cleaned_quality_score=safe_float(row.get("cleaned_quality_score")),
            )
            for index, row in enumerate(cleaned_example_sentences)
        ],
        key=lambda item: (-item.cleaned_quality_score, item.index),
    )

    rows: list[dict[str, object]] = []
    linked_count = 0

    for indexed_entry in sorted_dictionary_entries:
        entry = indexed_entry.row
        canonical_key = str(entry.get("canonical_key", ""))
        match_index = next(
            (
                index
                for index, sentence in enumerate(remaining_sentences)
                if canonical_key and canonical_key in sentence.covered_keys
            ),
            None,
        )

        original_example = str(entry.get("example", ""))

        if match_index is None:
            linked_row = build_linked_dictionary_row(entry, original_example=original_example)
        else:
            matched_sentence = remaining_sentences.pop(match_index).row
            linked_count += 1
            linked_row = build_linked_dictionary_row(
                entry,
                original_example=original_example,
                matched_sentence=matched_sentence,
            )

        rows.append(linked_row)

    return LinkedDictionaryOutputs(
        rows=rows,
        linked_count=linked_count,
        unlinked_count=len(rows) - linked_count,
    )


def build_linked_dictionary_row(
    entry: Mapping[str, object],
    *,
    original_example: str,
    matched_sentence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {}
    assigned_example = (
        str(matched_sentence.get("cleaned_sentence", ""))
        if matched_sentence is not None
        else original_example
    )
    for key, value in entry.items():
        if key in STAGE_TWELVE_STRIPPED_FIELDS:
            continue
        if key == "example":
            row[key] = assigned_example
            continue
        if key == "curation_status":
            row[key] = "rewrite_ready"
            continue
        row[key] = value

    if "curation_status" not in row:
        row["curation_status"] = "rewrite_ready"
    if "example" not in row:
        row["example"] = assigned_example
    row["example_replaced_with_cleaned_sentence"] = matched_sentence is not None
    return row


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
