from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from wordbyword.canonical_keys import row_covered_canonical_keys, split_joined_keys
from wordbyword.example_cleanup import (
    CleanupProgressCallback,
    cleanup_batch_sizes,
    emit_cleanup_batch_progress,
)
from wordbyword.llm import ChatMessage, LLMRequestError, LLMResponseError, SupportsCompletion, strip_markdown_fence
from wordbyword.text import clean_example_text

DEFAULT_DICTIONARY_CLEANUP_BATCH_SIZE = 40
MAX_SUPPORT_SENTENCES = 3
MAX_PROMPT_VARIANTS = 8
ACTION_ALIASES = {
    "keep": "keep",
    "reject": "reject",
    "drop": "reject",
    "remove": "reject",
}
REASON_ALIASES = {
    "name": "proper_name",
    "person_name": "proper_name",
    "character_name": "proper_name",
    "proper_name": "proper_name",
    "metadata_name": "metadata_name",
    "source_specific_entity": "source_specific_entity",
    "entity": "source_specific_entity",
    "ocr_fragment": "ocr_fragment",
    "fragment": "ocr_fragment",
    "template_debris": "template_debris",
    "form_debris": "template_debris",
    "low_dictionary_value": "low_dictionary_value",
}

BATCH_CLEAN_DICTIONARY_CANDIDATE_SYSTEM_PROMPT = """You curate headword candidates for a Korean learner dictionary.
For each input row, decide whether the candidate should stay as a reusable dictionary headword.
Prefer keep.
Reject person names, character names, author names, illustrator names, translator names, and source-specific named entities such as "드소토" or "김철수".
Keep ordinary Korean vocabulary that a learner would reasonably study, even when one attached example sentence is noisy, fragmentary, or taken from a textbook prompt.
Do not reject a good word only because the source example is bad.
Reject OCR fragments, split morphemes, template/date/form debris, and obviously broken tokens.
Return TSV lines only for rows that should be rejected.
Columns: id<TAB>action<TAB>reason
Rules:
- keep: omit the row entirely
- reject: action must be reject
- reason: prefer one of proper_name, metadata_name, source_specific_entity, ocr_fragment, template_debris, low_dictionary_value
- if every row is keep, return exactly EMPTY
- do not add a header
- do not add commentary, numbering, markdown fences, or extra columns
- keep the ids exactly as provided"""


@dataclass(frozen=True)
class CleanedDictionaryOutputs:
    rows: list[dict[str, object]]
    reviewed_count: int
    rejected_count: int
    action_counts: dict[str, int]
    rejection_reason_counts: dict[str, int]


@dataclass(frozen=True)
class BatchDictionaryReviewRow:
    row_id: str
    action: str
    reason: str = ""


@dataclass(frozen=True)
class IndexedDictionaryCandidate:
    row_id: str
    candidate: Mapping[str, object]
    prompt_packet: Mapping[str, object]


DictionaryBatchRowsCallback = Callable[[list[dict[str, object]]], None]


def dictionary_cleanup_batch_sizes(total_rows: int) -> list[int]:
    return cleanup_batch_sizes(total_rows, batch_size=DEFAULT_DICTIONARY_CLEANUP_BATCH_SIZE)


def build_cleaned_dictionary_candidate_outputs(
    dictionary_entries: Sequence[Mapping[str, object]],
    candidate_terms: Sequence[Mapping[str, object]],
    support_sentence_rows: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    on_progress: CleanupProgressCallback | None = None,
    on_batch_rows: DictionaryBatchRowsCallback | None = None,
) -> CleanedDictionaryOutputs:
    term_by_key = {
        str(row["canonical_key"]): row
        for row in candidate_terms
        if row.get("canonical_key")
    }
    support_sentences_by_key = build_support_sentence_map(support_sentence_rows)
    indexed_rows = [
        IndexedDictionaryCandidate(
            row_id=f"r{index:04d}",
            candidate=row,
            prompt_packet=build_dictionary_prompt_packet(
                row,
                term_by_key.get(str(row.get("canonical_key", ""))),
                support_sentences_by_key.get(str(row.get("canonical_key", "")), ()),
            ),
        )
        for index, row in enumerate(dictionary_entries, start=1)
    ]

    rows: list[dict[str, object]] = []
    rejected_count = 0
    action_counts = {
        "keep": 0,
        "reject": 0,
    }
    rejection_reason_counts: Counter[str] = Counter()

    for batch in iter_dictionary_cleanup_batches(indexed_rows, on_progress=on_progress):
        reviews = review_dictionary_cleanup_batch_with_fallback(client, batch)
        batch_rows, batch_rejected_count = build_cleaned_dictionary_batch_rows(
            batch,
            reviews,
            action_counts,
            rejection_reason_counts,
        )
        rejected_count += batch_rejected_count
        rows.extend(batch_rows)
        if on_batch_rows is not None and batch_rows:
            on_batch_rows(batch_rows)

    return CleanedDictionaryOutputs(
        rows=rows,
        reviewed_count=len(dictionary_entries),
        rejected_count=rejected_count,
        action_counts=action_counts,
        rejection_reason_counts=dict(rejection_reason_counts),
    )


def review_dictionary_cleanup_batches(
    client: SupportsCompletion,
    indexed_rows: Sequence[IndexedDictionaryCandidate],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> dict[str, BatchDictionaryReviewRow]:
    reviews: dict[str, BatchDictionaryReviewRow] = {}
    for batch in iter_dictionary_cleanup_batches(indexed_rows, on_progress=on_progress):
        reviews.update(review_dictionary_cleanup_batch_with_fallback(client, batch))
    return reviews


def iter_dictionary_cleanup_batches(
    indexed_rows: Sequence[IndexedDictionaryCandidate],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> Iterator[Sequence[IndexedDictionaryCandidate]]:
    sizes = dictionary_cleanup_batch_sizes(len(indexed_rows))
    total_batches = len(sizes)
    total_rows = len(indexed_rows)
    completed_rows = 0
    offset = 0
    for index, size in enumerate(sizes):
        emit_cleanup_batch_progress(
            on_progress,
            phase="start",
            batch_index=index + 1,
            total_batches=total_batches,
            completed_batches=index,
            completed_rows=completed_rows,
            total_rows=total_rows,
            current_batch_size=size,
        )
        batch = indexed_rows[offset : offset + size]
        yield batch
        offset += size
        completed_rows += size
        emit_cleanup_batch_progress(
            on_progress,
            phase="complete",
            batch_index=index + 1,
            total_batches=total_batches,
            completed_batches=index + 1,
            completed_rows=completed_rows,
            total_rows=total_rows,
            current_batch_size=size,
        )


def build_cleaned_dictionary_batch_rows(
    batch: Sequence[IndexedDictionaryCandidate],
    reviews: Mapping[str, BatchDictionaryReviewRow],
    action_counts: dict[str, int],
    rejection_reason_counts: Counter[str],
) -> tuple[list[dict[str, object]], int]:
    batch_rows: list[dict[str, object]] = []
    rejected_count = 0
    for row in batch:
        review = reviews[row.row_id]
        if review.action == "reject":
            rejected_count += 1
            action_counts["reject"] += 1
            rejection_reason_counts[review.reason or "unspecified"] += 1
            continue
        action_counts["keep"] += 1
        batch_rows.append(dict(row.candidate))
    return batch_rows, rejected_count


def review_dictionary_cleanup_batch_with_fallback(
    client: SupportsCompletion,
    batch: Sequence[IndexedDictionaryCandidate],
) -> dict[str, BatchDictionaryReviewRow]:
    try:
        return review_dictionary_cleanup_batch(client, batch)
    except (LLMRequestError, LLMResponseError):
        if len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        first = review_dictionary_cleanup_batch_with_fallback(client, batch[:midpoint])
        second = review_dictionary_cleanup_batch_with_fallback(client, batch[midpoint:])
        return {**first, **second}


def review_dictionary_cleanup_batch(
    client: SupportsCompletion,
    batch_rows: Sequence[IndexedDictionaryCandidate],
) -> dict[str, BatchDictionaryReviewRow]:
    payload_lines = [
        "Candidates:",
        *[
            json.dumps({"id": row.row_id, **row.prompt_packet}, ensure_ascii=False, sort_keys=True)
            for row in batch_rows
        ],
    ]
    max_output_tokens = max(512, len(batch_rows) * 24)
    result = client.complete(
        [
            ChatMessage(role="system", content=BATCH_CLEAN_DICTIONARY_CANDIDATE_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(payload_lines)),
        ],
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )
    parsed_rows = parse_dictionary_cleanup_response(result.content)
    expected_ids = [row.row_id for row in batch_rows]
    reviews = {
        row_id: BatchDictionaryReviewRow(row_id=row_id, action="keep")
        for row_id in expected_ids
    }
    seen_ids: set[str] = set()
    for row in parsed_rows:
        if row.row_id not in reviews:
            raise LLMResponseError(f"Dictionary cleanup response included an unknown row id '{row.row_id}'.")
        if row.row_id in seen_ids:
            raise LLMResponseError(f"Dictionary cleanup response repeated row id '{row.row_id}'.")
        seen_ids.add(row.row_id)
        reviews[row.row_id] = row
    return reviews


def parse_dictionary_cleanup_response(text: str) -> list[BatchDictionaryReviewRow]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned or cleaned.upper() == "EMPTY":
        return []

    rows: list[BatchDictionaryReviewRow] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            raise LLMResponseError(f"Invalid dictionary cleanup line: {raw_line!r}")
        row_id = parts[0].strip()
        action = normalize_action(parts[1])
        reason = normalize_reason(parts[2]) if len(parts) >= 3 else ""
        if action == "reject" and not reason:
            raise LLMResponseError(f"Missing reject reason for dictionary cleanup row '{row_id}'.")
        rows.append(
            BatchDictionaryReviewRow(
                row_id=row_id,
                action=action,
                reason=reason,
            )
        )
    return rows


def normalize_action(value: Any) -> str:
    if not isinstance(value, str):
        raise LLMResponseError("Dictionary cleanup response did not include an action.")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    action = ACTION_ALIASES.get(normalized)
    if action is None:
        supported = ", ".join(sorted(set(ACTION_ALIASES.values())))
        raise LLMResponseError(f"Unsupported dictionary cleanup action '{value}'. Expected one of: {supported}.")
    return action


def normalize_reason(value: Any) -> str:
    if not value:
        return ""
    normalized = clean_example_text(str(value)).strip().lower().replace("-", "_").replace(" ", "_")
    return REASON_ALIASES.get(normalized, normalized)


def build_dictionary_prompt_packet(
    dictionary_entry: Mapping[str, object],
    candidate_term: Mapping[str, object] | None,
    support_sentences: Sequence[str],
) -> dict[str, object]:
    return {
        "canonical_key": str(dictionary_entry.get("canonical_key", "")),
        "display": str(dictionary_entry.get("display", "")),
        "pos_group": str(candidate_term.get("pos_group", "unknown")) if candidate_term is not None else "unknown",
        "count": int(dictionary_entry.get("count", 0) or 0),
        "document_count": int(dictionary_entry.get("document_count", 0) or 0),
        "quality_score": float(dictionary_entry.get("quality_score", 0.0) or 0.0),
        "roi_score": float(dictionary_entry.get("roi_score", 0.0) or 0.0),
        "variants": prompt_variants(dictionary_entry.get("variants")),
        "source_example": clean_example_text(str(dictionary_entry.get("example", ""))),
        "support_sentences": list(support_sentences[:MAX_SUPPORT_SENTENCES]),
    }


def build_support_sentence_map(rows: Sequence[Mapping[str, object]]) -> dict[str, list[str]]:
    sentences_by_key: dict[str, list[str]] = {}
    for row in rows:
        sentence = support_sentence_text(row)
        if not sentence:
            continue
        for canonical_key in support_sentence_keys(row):
            bucket = sentences_by_key.setdefault(canonical_key, [])
            if sentence in bucket or len(bucket) >= MAX_SUPPORT_SENTENCES:
                continue
            bucket.append(sentence)
    return sentences_by_key


def support_sentence_text(row: Mapping[str, object]) -> str:
    for field_name in ("cleaned_sentence", "sentence", "source_sentence"):
        value = clean_example_text(str(row.get(field_name, ""))).strip()
        if value:
            return value
    return ""


def support_sentence_keys(row: Mapping[str, object]) -> list[str]:
    return row_covered_canonical_keys(row)


def prompt_variants(value: object) -> list[str]:
    if isinstance(value, list):
        return [cleaned for cleaned in (clean_example_text(str(item)).strip() for item in value) if cleaned][:MAX_PROMPT_VARIANTS]
    return split_joined_keys(value)[:MAX_PROMPT_VARIANTS]

