from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from wordbyword.dictionary_build.candidates import migrated_vocabulary_score_row
from wordbyword.dictionary_build.example_cleanup import (
    CleanupProgressCallback,
    cleanup_batch_sizes,
    emit_cleanup_batch_progress,
)
from wordbyword.dictionary_build.llm import (
    ChatMessage,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    normalize_text_result,
    strip_markdown_fence,
)
from wordbyword.dictionary_build.text import clean_example_text

DEFAULT_GENERATED_MEANING_BATCH_SIZE = 40

BATCH_GENERATE_MEANING_SYSTEM_PROMPT = """You write concise Korean dictionary meanings for learner vocabulary.
For each input row, write exactly one meaning for the target canonical_key.
Use the example sentence to infer the intended sense.
Prefer a compact Korean explanation suitable for a learner dictionary.
Do not write another example sentence.
Do not mention that you used the example sentence.
Return one TSV line for every input row.
Columns: id<TAB>meaning
Rules:
- meaning must be Korean
- meaning must be concise, natural, and definition-like
- do not add a header
- do not add commentary, numbering, markdown fences, or extra columns
- keep the ids exactly as provided"""


@dataclass(frozen=True)
class GeneratedMeaningOutputs:
    rows: list[dict[str, object]]
    reviewed_count: int
    generated_count: int
    preserved_count: int
    missing_count: int


@dataclass(frozen=True)
class BatchGeneratedMeaningRow:
    row_id: str
    meaning: str


@dataclass(frozen=True)
class IndexedGeneratedMeaningRow:
    row_id: str
    index: int
    row: Mapping[str, object]


@dataclass(frozen=True)
class GeneratedMeaningBatchSnapshot:
    batch_number: int
    attempted_count: int
    generated_count: int
    rows: list[dict[str, object]]


GeneratedMeaningBatchCallback = Callable[[GeneratedMeaningBatchSnapshot], None]


def build_generated_meaning_dictionary_outputs(
    dictionary_entries: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    on_progress: CleanupProgressCallback | None = None,
    on_batch_complete: GeneratedMeaningBatchCallback | None = None,
) -> GeneratedMeaningOutputs:
    indexed_rows = [
        IndexedGeneratedMeaningRow(row_id=f"r{index + 1:04d}", index=index, row=row)
        for index, row in enumerate(dictionary_entries)
        if needs_generated_meaning(row)
    ]
    row_id_by_index = {row.index: row.row_id for row in indexed_rows}
    generated_by_id: dict[str, str] = {}

    for batch_number, batch in enumerate(
        iter_generated_meaning_batches(indexed_rows, on_progress=on_progress),
        start=1,
    ):
        reviews = review_generated_meaning_batch_with_fallback(client, batch)
        for indexed_row in batch:
            generated_by_id[indexed_row.row_id] = reviews[indexed_row.row_id].meaning
        if on_batch_complete is not None:
            on_batch_complete(
                GeneratedMeaningBatchSnapshot(
                    batch_number=batch_number,
                    attempted_count=len(batch),
                    generated_count=len(generated_by_id),
                    rows=materialize_generated_meaning_rows(
                        dictionary_entries,
                        row_id_by_index=row_id_by_index,
                        generated_by_id=generated_by_id,
                    ),
                )
            )

    rows = materialize_generated_meaning_rows(
        dictionary_entries,
        row_id_by_index=row_id_by_index,
        generated_by_id=generated_by_id,
    )
    missing_count = sum(1 for row in rows if not str(row.get("meaning", "")).strip())
    return GeneratedMeaningOutputs(
        rows=rows,
        reviewed_count=len(indexed_rows),
        generated_count=len(generated_by_id),
        preserved_count=len(dictionary_entries) - len(indexed_rows),
        missing_count=missing_count,
    )


def needs_generated_meaning(row: Mapping[str, object]) -> bool:
    return not str(row.get("meaning", "") or "").strip()


def iter_generated_meaning_batches(
    indexed_rows: Sequence[IndexedGeneratedMeaningRow],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> Iterator[Sequence[IndexedGeneratedMeaningRow]]:
    sizes = cleanup_batch_sizes(len(indexed_rows), batch_size=DEFAULT_GENERATED_MEANING_BATCH_SIZE)
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


def review_generated_meaning_batch_with_fallback(
    client: SupportsCompletion,
    batch: Sequence[IndexedGeneratedMeaningRow],
) -> dict[str, BatchGeneratedMeaningRow]:
    try:
        return review_generated_meaning_batch(client, batch)
    except (LLMRequestError, LLMResponseError):
        if len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        first = review_generated_meaning_batch_with_fallback(client, batch[:midpoint])
        second = review_generated_meaning_batch_with_fallback(client, batch[midpoint:])
        return {**first, **second}


def review_generated_meaning_batch(
    client: SupportsCompletion,
    batch_rows: Sequence[IndexedGeneratedMeaningRow],
) -> dict[str, BatchGeneratedMeaningRow]:
    payload_lines = [
        "Candidates:",
        *[
            json.dumps(build_generated_meaning_prompt_packet(row), ensure_ascii=False, sort_keys=True)
            for row in batch_rows
        ],
    ]
    result = client.complete(
        [
            ChatMessage(role="system", content=BATCH_GENERATE_MEANING_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(payload_lines)),
        ],
        temperature=0.2,
        max_output_tokens=max(512, len(batch_rows) * 96),
    )
    parsed_rows = parse_generated_meaning_response(result.content)
    reviews: dict[str, BatchGeneratedMeaningRow | None] = {row.row_id: None for row in batch_rows}
    seen_ids: set[str] = set()
    for row in parsed_rows:
        if row.row_id not in reviews:
            raise LLMResponseError(f"Generated meaning response included an unknown row id '{row.row_id}'.")
        if row.row_id in seen_ids:
            raise LLMResponseError(f"Generated meaning response repeated row id '{row.row_id}'.")
        seen_ids.add(row.row_id)
        reviews[row.row_id] = row
    missing_ids = [row_id for row_id, review in reviews.items() if review is None]
    if missing_ids:
        raise LLMResponseError("Generated meaning response omitted row ids: " + ", ".join(missing_ids))
    return {row_id: review for row_id, review in reviews.items() if review is not None}


def build_generated_meaning_prompt_packet(indexed_row: IndexedGeneratedMeaningRow) -> dict[str, object]:
    row = indexed_row.row
    return {
        "id": indexed_row.row_id,
        "canonical_key": str(row.get("canonical_key", "")),
        "display": str(row.get("display", "") or row.get("canonical_key", "")),
        "example": clean_example_text(str(row.get("example", "") or "")),
    }


def parse_generated_meaning_response(text: str) -> list[BatchGeneratedMeaningRow]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned:
        raise LLMResponseError("Generated meaning response was empty.")

    rows: list[BatchGeneratedMeaningRow] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            raise LLMResponseError(f"Invalid generated meaning line: {raw_line!r}")
        row_id = parts[0].strip()
        meaning = normalize_generated_meaning(parts[1])
        if not meaning:
            raise LLMResponseError(f"Missing generated meaning for row '{row_id}'.")
        rows.append(BatchGeneratedMeaningRow(row_id=row_id, meaning=meaning))
    if not rows:
        raise LLMResponseError("Generated meaning response did not include any rows.")
    return rows


def normalize_generated_meaning(text: str) -> str:
    meaning = normalize_text_result(text)
    for prefix in ("뜻:", "의미:", "meaning:", "Meaning:"):
        if meaning.startswith(prefix):
            meaning = meaning[len(prefix) :].strip()
    return meaning


def materialize_generated_meaning_rows(
    dictionary_entries: Sequence[Mapping[str, object]],
    *,
    row_id_by_index: Mapping[int, str],
    generated_by_id: Mapping[str, str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, entry in enumerate(dictionary_entries):
        row = {
            **migrated_vocabulary_score_row(entry),
            "curation_status": "confirmed",
        }
        row_id = row_id_by_index.get(index)
        if row_id is not None and row_id in generated_by_id:
            row["meaning"] = generated_by_id[row_id]
            row["meaning_generated_with_ollama"] = True
        else:
            row["meaning"] = str(row.get("meaning", "") or "")
            row["meaning_generated_with_ollama"] = False
        rows.append(row)
    return rows
