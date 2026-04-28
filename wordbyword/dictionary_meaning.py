from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from wordbyword.example_cleanup import (
    CleanupProgressCallback,
    cleanup_batch_sizes,
    emit_cleanup_batch_progress,
)
from wordbyword.krdict import KrdictDataset, lookup_meaning
from wordbyword.llm import (
    ChatMessage,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    strip_markdown_fence,
)
from wordbyword.text import clean_example_text

DEFAULT_DICTIONARY_MEANING_BATCH_SIZE = 40
MISSING_MEANING_PLACEHOLDER = "N/A"
MAX_PROMPT_VARIANTS = 8

BATCH_DICTIONARY_MEANING_SYSTEM_PROMPT = """You write short Korean dictionary definitions for vocabulary headwords.
For each input row, write exactly one short Korean definition for the target headword.
Write the definition in the style of a Korean learner dictionary: a single noun phrase or short clause, written in Korean.
Do not translate to English.
Do not include the headword itself, example sentences, or commentary.
If multiple senses exist, choose the most common everyday sense.
Return one TSV line for every input row.
Columns: id<TAB>meaning
Rules:
- meaning must be exactly one short Korean definition
- do not add a header
- do not add commentary, numbering, markdown fences, or extra columns
- keep the ids exactly as provided"""


@dataclass(frozen=True)
class DictionaryMeaningLookupOutputs:
    rows: list[dict[str, object]]
    reviewed_count: int
    found_count: int
    missing_count: int


@dataclass(frozen=True)
class DictionaryMeaningGenerationOutputs:
    rows: list[dict[str, object]]
    attempted_count: int
    generated_count: int
    failed_count: int
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


@dataclass(frozen=True)
class BatchMeaningRow:
    row_id: str
    meaning: str


@dataclass(frozen=True)
class IndexedMeaningRow:
    row_id: str
    row: Mapping[str, object]
    prompt_packet: Mapping[str, object]


@dataclass(frozen=True)
class DictionaryMeaningBatchSnapshot:
    batch_number: int
    rows: list[dict[str, object]]
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]
    succeeded_row_ids: list[str]
    failed_row_ids: list[str]
    unresolved_failure_rows: list[dict[str, object]]


DictionaryMeaningBatchCallback = Callable[[DictionaryMeaningBatchSnapshot], None]


def build_dictionary_meaning_lookup_outputs(
    generated_entries: Sequence[Mapping[str, object]],
    dataset: KrdictDataset,
) -> DictionaryMeaningLookupOutputs:
    rows: list[dict[str, object]] = []
    found_count = 0
    missing_count = 0
    for entry in generated_entries:
        meaning = lookup_meaning(
            dataset,
            display=str(entry.get("display", "")),
            canonical_key=str(entry.get("canonical_key", "")),
        )
        if meaning:
            row_meaning = meaning
            meaning_source = "krdict"
            found_count += 1
        else:
            row_meaning = MISSING_MEANING_PLACEHOLDER
            meaning_source = "missing"
            missing_count += 1
        rows.append(
            {
                **dict(entry),
                "meaning": row_meaning,
                "meaning_source": meaning_source,
            }
        )
    return DictionaryMeaningLookupOutputs(
        rows=rows,
        reviewed_count=len(generated_entries),
        found_count=found_count,
        missing_count=missing_count,
    )


def build_dictionary_meaning_generation_outputs(
    rows_with_meanings: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    on_progress: CleanupProgressCallback | None = None,
    on_batch_complete: DictionaryMeaningBatchCallback | None = None,
) -> DictionaryMeaningGenerationOutputs:
    materialized_rows = [dict(row) for row in rows_with_meanings]
    pending_indexed: list[IndexedMeaningRow] = []
    row_index_by_row_id: dict[str, int] = {}
    for index, row in enumerate(materialized_rows):
        if not needs_generated_meaning(row):
            continue
        row_id = f"r{index + 1:04d}"
        pending_indexed.append(
            IndexedMeaningRow(
                row_id=row_id,
                row=row,
                prompt_packet=build_meaning_prompt_packet(row),
            )
        )
        row_index_by_row_id[row_id] = index

    attempted_count = len(pending_indexed)
    all_attempt_rows: list[dict[str, object]] = []
    unresolved_failure_rows: dict[str, dict[str, object]] = {}

    for batch_number, batch in enumerate(
        iter_meaning_generation_batches(pending_indexed, on_progress=on_progress),
        start=1,
    ):
        batch_attempt_rows: list[dict[str, object]] = []
        batch_failure_rows: list[dict[str, object]] = []
        batch_succeeded_row_ids: list[str] = []
        batch_failed_row_ids: list[str] = []

        try:
            reviews = review_dictionary_meaning_batch_with_fallback(client, batch)
        except (LLMRequestError, LLMResponseError) as exc:
            failure_reason = str(exc)
            for indexed_row in batch:
                attempt_row = build_meaning_attempt_row(
                    indexed_row,
                    meaning="",
                    accepted=False,
                    failure_reason=failure_reason,
                )
                batch_attempt_rows.append(attempt_row)
                batch_failure_rows.append(attempt_row)
                batch_failed_row_ids.append(indexed_row.row_id)
                unresolved_failure_rows[indexed_row.row_id] = attempt_row
            all_attempt_rows.extend(batch_attempt_rows)
            _emit_meaning_batch_snapshot(
                on_batch_complete,
                batch_number=batch_number,
                rows=materialized_rows,
                batch_attempt_rows=batch_attempt_rows,
                batch_failure_rows=batch_failure_rows,
                succeeded_row_ids=batch_succeeded_row_ids,
                failed_row_ids=batch_failed_row_ids,
                unresolved_failure_rows=list(unresolved_failure_rows.values()),
            )
            continue

        for indexed_row in batch:
            review = reviews[indexed_row.row_id]
            meaning = (review.meaning or "").strip()
            if not meaning:
                attempt_row = build_meaning_attempt_row(
                    indexed_row,
                    meaning="",
                    accepted=False,
                    failure_reason="empty_meaning",
                )
                batch_attempt_rows.append(attempt_row)
                batch_failure_rows.append(attempt_row)
                batch_failed_row_ids.append(indexed_row.row_id)
                unresolved_failure_rows[indexed_row.row_id] = attempt_row
                continue

            row_index = row_index_by_row_id[indexed_row.row_id]
            materialized_rows[row_index]["meaning"] = meaning
            materialized_rows[row_index]["meaning_source"] = "ollama"
            attempt_row = build_meaning_attempt_row(
                indexed_row,
                meaning=meaning,
                accepted=True,
            )
            batch_attempt_rows.append(attempt_row)
            batch_succeeded_row_ids.append(indexed_row.row_id)
            unresolved_failure_rows.pop(indexed_row.row_id, None)

        all_attempt_rows.extend(batch_attempt_rows)
        _emit_meaning_batch_snapshot(
            on_batch_complete,
            batch_number=batch_number,
            rows=materialized_rows,
            batch_attempt_rows=batch_attempt_rows,
            batch_failure_rows=batch_failure_rows,
            succeeded_row_ids=batch_succeeded_row_ids,
            failed_row_ids=batch_failed_row_ids,
            unresolved_failure_rows=list(unresolved_failure_rows.values()),
        )

    return DictionaryMeaningGenerationOutputs(
        rows=materialized_rows,
        attempted_count=attempted_count,
        generated_count=attempted_count - len(unresolved_failure_rows),
        failed_count=len(unresolved_failure_rows),
        attempt_rows=all_attempt_rows,
        failure_rows=list(unresolved_failure_rows.values()),
    )


def needs_generated_meaning(row: Mapping[str, object]) -> bool:
    meaning = str(row.get("meaning", "")).strip()
    return not meaning or meaning == MISSING_MEANING_PLACEHOLDER


def iter_meaning_generation_batches(
    indexed_rows: Sequence[IndexedMeaningRow],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> Iterator[Sequence[IndexedMeaningRow]]:
    sizes = cleanup_batch_sizes(
        len(indexed_rows),
        batch_size=DEFAULT_DICTIONARY_MEANING_BATCH_SIZE,
    )
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


def review_dictionary_meaning_batch_with_fallback(
    client: SupportsCompletion,
    batch: Sequence[IndexedMeaningRow],
) -> dict[str, BatchMeaningRow]:
    try:
        return review_dictionary_meaning_batch(client, batch)
    except (LLMRequestError, LLMResponseError):
        if len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        first = review_dictionary_meaning_batch_with_fallback(client, batch[:midpoint])
        second = review_dictionary_meaning_batch_with_fallback(client, batch[midpoint:])
        return {**first, **second}


def review_dictionary_meaning_batch(
    client: SupportsCompletion,
    batch_rows: Sequence[IndexedMeaningRow],
) -> dict[str, BatchMeaningRow]:
    payload_lines = [
        "Candidates:",
        *[
            json.dumps({"id": row.row_id, **row.prompt_packet}, ensure_ascii=False, sort_keys=True)
            for row in batch_rows
        ],
    ]
    max_output_tokens = max(512, len(batch_rows) * 64)
    result = client.complete(
        [
            ChatMessage(role="system", content=BATCH_DICTIONARY_MEANING_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(payload_lines)),
        ],
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )
    parsed_rows = parse_dictionary_meaning_response(result.content)
    reviews: dict[str, BatchMeaningRow | None] = {row.row_id: None for row in batch_rows}
    seen_ids: set[str] = set()
    for row in parsed_rows:
        if row.row_id not in reviews:
            raise LLMResponseError(
                f"Dictionary meaning response included an unknown row id '{row.row_id}'."
            )
        if row.row_id in seen_ids:
            raise LLMResponseError(
                f"Dictionary meaning response repeated row id '{row.row_id}'."
            )
        seen_ids.add(row.row_id)
        reviews[row.row_id] = row
    missing_ids = [row_id for row_id, review in reviews.items() if review is None]
    if missing_ids:
        raise LLMResponseError(
            "Dictionary meaning response omitted row ids: " + ", ".join(missing_ids)
        )
    return {row_id: review for row_id, review in reviews.items() if review is not None}


def parse_dictionary_meaning_response(text: str) -> list[BatchMeaningRow]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned:
        raise LLMResponseError("Dictionary meaning response was empty.")
    rows: list[BatchMeaningRow] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            raise LLMResponseError(f"Invalid dictionary meaning line: {raw_line!r}")
        row_id = parts[0].strip()
        meaning = clean_example_text(parts[1]).strip()
        if not meaning:
            raise LLMResponseError(f"Missing meaning for row '{row_id}'.")
        rows.append(BatchMeaningRow(row_id=row_id, meaning=meaning))
    if not rows:
        raise LLMResponseError("Dictionary meaning response did not include any rows.")
    return rows


def build_meaning_prompt_packet(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "headword": str(row.get("display", "")) or str(row.get("canonical_key", "")),
        "canonical_key": str(row.get("canonical_key", "")),
        "pos_group": str(row.get("pos_group", "")) or "unknown",
        "variants": _prompt_variants(row.get("variants")),
        "example": clean_example_text(str(row.get("example", ""))),
    }


def build_meaning_attempt_row(
    indexed_row: IndexedMeaningRow,
    *,
    meaning: str,
    accepted: bool,
    failure_reason: str = "",
) -> dict[str, object]:
    row = indexed_row.row
    return {
        "row_id": indexed_row.row_id,
        "canonical_key": str(row.get("canonical_key", "")),
        "display": str(row.get("display", "")),
        "meaning": meaning,
        "accepted": accepted,
        "failure_reason": failure_reason,
    }


def _emit_meaning_batch_snapshot(
    on_batch_complete: DictionaryMeaningBatchCallback | None,
    *,
    batch_number: int,
    rows: list[dict[str, object]],
    batch_attempt_rows: list[dict[str, object]],
    batch_failure_rows: list[dict[str, object]],
    succeeded_row_ids: list[str],
    failed_row_ids: list[str],
    unresolved_failure_rows: list[dict[str, object]],
) -> None:
    if on_batch_complete is None:
        return
    on_batch_complete(
        DictionaryMeaningBatchSnapshot(
            batch_number=batch_number,
            rows=[dict(row) for row in rows],
            attempt_rows=batch_attempt_rows,
            failure_rows=batch_failure_rows,
            succeeded_row_ids=succeeded_row_ids,
            failed_row_ids=failed_row_ids,
            unresolved_failure_rows=unresolved_failure_rows,
        )
    )


def _prompt_variants(value: object) -> list[str]:
    if isinstance(value, list):
        cleaned = [clean_example_text(str(item)).strip() for item in value]
        return [item for item in cleaned if item][:MAX_PROMPT_VARIANTS]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part][:MAX_PROMPT_VARIANTS]
    return []
