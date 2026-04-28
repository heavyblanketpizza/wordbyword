from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from kiwipiepy import Kiwi

from wordbyword.canonical_keys import sentence_candidate_keys, split_joined_keys
from wordbyword.example_cleanup import (
    CleanupProgressCallback,
    cleanup_batch_sizes,
    emit_cleanup_batch_progress,
    is_acceptable_cleaned_sentence,
    source_similarity,
    tokenize_sentence,
)
from wordbyword.llm import (
    ChatMessage,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    normalize_text_result,
    strip_markdown_fence,
)
from wordbyword.text import clean_example_text

DEFAULT_GENERATED_EXAMPLE_BATCH_SIZE = 25
MAX_PROMPT_VARIANTS = 8
REWRITE_LIKE_SIMILARITY_MAXIMUM = 0.88

BATCH_GENERATE_EXAMPLE_SYSTEM_PROMPT = """You write completely new Korean example sentences for vocabulary headwords.
For each input row, write exactly one natural Korean sentence from scratch for the target headword.
Input rows contain a headword and optional variants for reference.
Use the headword naturally, or use a natural inflected or conjugated form of it.
You may use a listed variant when that is the most natural form.
This is not a cleanup or rewrite task.
Prefer short, concrete, everyday learner-friendly contexts.
Avoid textbook instructions, comprehension questions, numbered prompts, fill-in-the-blank language, fragments, lists, dictionary definitions, and meta commentary.
Return one TSV line for every input row.
Columns: id<TAB>generated_sentence
Rules:
- generated_sentence must be exactly one cleaned Korean sentence
- generated_sentence must include the target headword naturally
- do not add a header
- do not add commentary, numbering, markdown fences, or extra columns
- keep the ids exactly as provided"""


@dataclass(frozen=True)
class GeneratedExampleOutputs:
    rows: list[dict[str, object]]
    reviewed_count: int
    generated_count: int
    preserved_count: int
    failed_count: int
    attempt_round_count: int
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


@dataclass(frozen=True)
class BatchGeneratedExampleRow:
    row_id: str
    generated_sentence: str


@dataclass(frozen=True)
class IndexedGeneratedExampleRow:
    row_id: str
    row: Mapping[str, object]
    prompt_packet: Mapping[str, object]


@dataclass(frozen=True)
class GeneratedExampleAttemptSummary:
    attempt_number: int
    attempted_count: int
    generated_count: int
    failed_count: int
    succeeded_row_ids: list[str]
    failed_row_ids: list[str]
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


RetryGeneratedExampleCallback = Callable[[GeneratedExampleAttemptSummary], bool]


def build_generated_dictionary_candidate_outputs(
    dictionary_entries: Sequence[Mapping[str, object]],
    candidate_terms: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    kiwi: Kiwi | None = None,
    on_progress: CleanupProgressCallback | None = None,
    on_attempt_complete: RetryGeneratedExampleCallback | None = None,
) -> GeneratedExampleOutputs:
    resolved_kiwi = kiwi if kiwi is not None else Kiwi()
    quality_by_key = {
        str(row["canonical_key"]): float(row["quality_score"])
        for row in candidate_terms
        if row.get("canonical_key")
    }
    term_by_key = {
        str(row["canonical_key"]): row
        for row in candidate_terms
        if row.get("canonical_key")
    }

    indexed_rows: list[IndexedGeneratedExampleRow] = []
    row_id_by_index: dict[int, str] = {}
    indexed_row_by_id: dict[str, IndexedGeneratedExampleRow] = {}
    for index, row in enumerate(dictionary_entries):
        if not needs_generated_example(row):
            continue
        row_id = f"r{index + 1:04d}"
        indexed_row = IndexedGeneratedExampleRow(
            row_id=row_id,
            row=row,
            prompt_packet=build_generation_prompt_packet(
                row,
                term_by_key.get(str(row.get("canonical_key", ""))),
            ),
        )
        indexed_rows.append(indexed_row)
        row_id_by_index[index] = row_id
        indexed_row_by_id[row_id] = indexed_row

    seen_examples = {
        cleaned
        for cleaned in (
            clean_example_text(str(row.get("example", ""))).strip()
            for row in dictionary_entries
        )
        if cleaned
    }
    generated_by_id: dict[str, str] = {}
    unresolved_failures_by_id: dict[str, dict[str, object]] = {}
    all_attempt_rows: list[dict[str, object]] = []
    pending_rows = indexed_rows
    attempt_number = 1
    completed_attempts = 0

    while pending_rows:
        attempt_summary = run_generated_example_attempt(
            pending_rows,
            quality_by_key,
            client=client,
            kiwi=resolved_kiwi,
            seen_examples=seen_examples,
            attempt_number=attempt_number,
            on_progress=on_progress,
        )
        completed_attempts = attempt_summary.attempt_number
        all_attempt_rows.extend(attempt_summary.attempt_rows)
        for row_id in attempt_summary.succeeded_row_ids:
            unresolved_failures_by_id.pop(row_id, None)
        for failure_row in attempt_summary.failure_rows:
            unresolved_failures_by_id[str(failure_row["row_id"])] = failure_row
        for attempt_row in attempt_summary.attempt_rows:
            if bool(attempt_row["accepted"]):
                generated_by_id[str(attempt_row["row_id"])] = str(attempt_row["generated_sentence"])

        should_retry = on_attempt_complete(attempt_summary) if on_attempt_complete is not None else False
        if not attempt_summary.failure_rows:
            break
        if not should_retry:
            break
        pending_rows = [indexed_row_by_id[row_id] for row_id in attempt_summary.failed_row_ids]
        attempt_number += 1

    rows: list[dict[str, object]] = []
    for index, row in enumerate(dictionary_entries):
        output_row = dict(row)
        row_id = row_id_by_index.get(index)
        if row_id is not None and row_id in generated_by_id:
            output_row["example"] = generated_by_id[row_id]
            output_row["example_generated_with_ollama"] = True
        else:
            output_row["example_generated_with_ollama"] = False
        rows.append(output_row)

    return GeneratedExampleOutputs(
        rows=rows,
        reviewed_count=len(indexed_rows),
        generated_count=len(generated_by_id),
        preserved_count=len(dictionary_entries) - len(generated_by_id),
        failed_count=len(unresolved_failures_by_id),
        attempt_round_count=completed_attempts,
        attempt_rows=all_attempt_rows,
        failure_rows=list(unresolved_failures_by_id.values()),
    )


def run_generated_example_attempt(
    indexed_rows: Sequence[IndexedGeneratedExampleRow],
    quality_by_key: Mapping[str, float],
    *,
    client: SupportsCompletion,
    kiwi: Kiwi,
    seen_examples: set[str],
    attempt_number: int,
    on_progress: CleanupProgressCallback | None = None,
) -> GeneratedExampleAttemptSummary:
    attempt_rows: list[dict[str, object]] = []
    failure_rows: list[dict[str, object]] = []
    succeeded_row_ids: list[str] = []
    failed_row_ids: list[str] = []

    for batch in iter_generated_example_batches(indexed_rows, on_progress=on_progress):
        try:
            reviews = review_generated_example_batch_with_fallback(client, batch)
        except (LLMRequestError, LLMResponseError) as exc:
            failure_reason = str(exc)
            for indexed_row in batch:
                attempt_row = build_generated_example_attempt_row(
                    indexed_row,
                    attempt_number=attempt_number,
                    generated_sentence="",
                    accepted=False,
                    failure_reason=failure_reason,
                )
                attempt_rows.append(attempt_row)
                failure_rows.append(attempt_row)
                failed_row_ids.append(indexed_row.row_id)
            continue

        for indexed_row in batch:
            raw_generated_sentence = reviews[indexed_row.row_id].generated_sentence
            try:
                generated_sentence = validate_generated_example_sentence(
                    indexed_row,
                    raw_generated_sentence,
                    quality_by_key,
                    kiwi,
                    seen_examples,
                )
            except LLMResponseError as exc:
                attempt_row = build_generated_example_attempt_row(
                    indexed_row,
                    attempt_number=attempt_number,
                    generated_sentence=clean_example_text(raw_generated_sentence).strip(),
                    accepted=False,
                    failure_reason=str(exc),
                )
                attempt_rows.append(attempt_row)
                failure_rows.append(attempt_row)
                failed_row_ids.append(indexed_row.row_id)
                continue

            seen_examples.add(generated_sentence)
            attempt_row = build_generated_example_attempt_row(
                indexed_row,
                attempt_number=attempt_number,
                generated_sentence=generated_sentence,
                accepted=True,
            )
            attempt_rows.append(attempt_row)
            succeeded_row_ids.append(indexed_row.row_id)

    return GeneratedExampleAttemptSummary(
        attempt_number=attempt_number,
        attempted_count=len(indexed_rows),
        generated_count=len(succeeded_row_ids),
        failed_count=len(failed_row_ids),
        succeeded_row_ids=succeeded_row_ids,
        failed_row_ids=failed_row_ids,
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
    )


def needs_generated_example(row: Mapping[str, object]) -> bool:
    return not bool(row.get("example_replaced_with_cleaned_sentence"))


def iter_generated_example_batches(
    indexed_rows: Sequence[IndexedGeneratedExampleRow],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> Iterator[Sequence[IndexedGeneratedExampleRow]]:
    sizes = cleanup_batch_sizes(len(indexed_rows), batch_size=DEFAULT_GENERATED_EXAMPLE_BATCH_SIZE)
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


def review_generated_example_batch_with_fallback(
    client: SupportsCompletion,
    batch: Sequence[IndexedGeneratedExampleRow],
) -> dict[str, BatchGeneratedExampleRow]:
    try:
        return review_generated_example_batch(client, batch)
    except (LLMRequestError, LLMResponseError):
        if len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        first = review_generated_example_batch_with_fallback(client, batch[:midpoint])
        second = review_generated_example_batch_with_fallback(client, batch[midpoint:])
        return {**first, **second}


def review_generated_example_batch(
    client: SupportsCompletion,
    batch_rows: Sequence[IndexedGeneratedExampleRow],
) -> dict[str, BatchGeneratedExampleRow]:
    payload_lines = [
        "Candidates:",
        *[
            json.dumps({"id": row.row_id, **row.prompt_packet}, ensure_ascii=False, sort_keys=True)
            for row in batch_rows
        ],
    ]
    max_output_tokens = max(512, len(batch_rows) * 48)
    result = client.complete(
        [
            ChatMessage(role="system", content=BATCH_GENERATE_EXAMPLE_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(payload_lines)),
        ],
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )
    parsed_rows = parse_generated_example_response(result.content)
    reviews = {row.row_id: None for row in batch_rows}
    seen_ids: set[str] = set()
    for row in parsed_rows:
        if row.row_id not in reviews:
            raise LLMResponseError(f"Generated example response included an unknown row id '{row.row_id}'.")
        if row.row_id in seen_ids:
            raise LLMResponseError(f"Generated example response repeated row id '{row.row_id}'.")
        seen_ids.add(row.row_id)
        reviews[row.row_id] = row
    missing_ids = [row_id for row_id, review in reviews.items() if review is None]
    if missing_ids:
        raise LLMResponseError(
            "Generated example response omitted row ids: "
            + ", ".join(missing_ids)
        )
    return {row_id: review for row_id, review in reviews.items() if review is not None}


def parse_generated_example_response(text: str) -> list[BatchGeneratedExampleRow]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned:
        raise LLMResponseError("Generated example response was empty.")

    rows: list[BatchGeneratedExampleRow] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            raise LLMResponseError(f"Invalid generated example line: {raw_line!r}")
        row_id = parts[0].strip()
        generated_sentence = normalize_text_result(parts[1])
        if not generated_sentence:
            raise LLMResponseError(f"Missing generated sentence for row '{row_id}'.")
        rows.append(
            BatchGeneratedExampleRow(
                row_id=row_id,
                generated_sentence=generated_sentence,
            )
        )
    if not rows:
        raise LLMResponseError("Generated example response did not include any rows.")
    return rows


def validate_generated_example_sentence(
    indexed_row: IndexedGeneratedExampleRow,
    generated_sentence: str,
    quality_by_key: Mapping[str, float],
    kiwi: Kiwi,
    seen_examples: set[str],
) -> str:
    cleaned_sentence = clean_example_text(generated_sentence).strip()
    if not is_acceptable_cleaned_sentence(cleaned_sentence):
        raise LLMResponseError(
            f"Generated example for row '{indexed_row.row_id}' was not a usable sentence."
        )

    source_example = clean_example_text(str(indexed_row.row.get("example", ""))).strip()
    if source_example and source_similarity(source_example, cleaned_sentence) >= REWRITE_LIKE_SIMILARITY_MAXIMUM:
        raise LLMResponseError(
            f"Generated example for row '{indexed_row.row_id}' was too similar to the source example."
        )
    if cleaned_sentence in seen_examples:
        raise LLMResponseError(
            f"Generated example for row '{indexed_row.row_id}' duplicated an existing example sentence."
        )

    canonical_key = str(indexed_row.row.get("canonical_key", "")).strip()
    if canonical_key and canonical_key in quality_by_key:
        matched_keys = sentence_candidate_keys(
            tokenize_sentence(cleaned_sentence, kiwi),
            quality_by_key,
        )
        if canonical_key not in matched_keys:
            raise LLMResponseError(
                f"Generated example for row '{indexed_row.row_id}' did not cover canonical key '{canonical_key}'."
            )

    return cleaned_sentence


def build_generation_prompt_packet(
    dictionary_entry: Mapping[str, object],
    candidate_term: Mapping[str, object] | None,
) -> dict[str, object]:
    headword = prompt_headword(dictionary_entry)
    canonical_key = clean_example_text(str(dictionary_entry.get("canonical_key", ""))).strip()
    return {
        "headword": headword,
        "variants": prompt_variants(
            candidate_term.get("variants") if candidate_term is not None else dictionary_entry.get("variants"),
            headword=headword,
            canonical_key=canonical_key,
        ),
    }


def build_generated_example_attempt_row(
    indexed_row: IndexedGeneratedExampleRow,
    *,
    attempt_number: int,
    generated_sentence: str,
    accepted: bool,
    failure_reason: str = "",
) -> dict[str, object]:
    return {
        "attempt_number": attempt_number,
        "row_id": indexed_row.row_id,
        "canonical_key": str(indexed_row.row.get("canonical_key", "")),
        "display": str(indexed_row.row.get("display", "")),
        "source_example": clean_example_text(str(indexed_row.row.get("example", ""))),
        "generated_sentence": generated_sentence,
        "accepted": accepted,
        "failure_reason": failure_reason,
    }


def prompt_headword(dictionary_entry: Mapping[str, object]) -> str:
    for field_name in ("display", "canonical_key"):
        cleaned = clean_example_text(str(dictionary_entry.get(field_name, ""))).strip()
        if cleaned:
            return cleaned
    return ""


def prompt_variants(
    value: object,
    *,
    headword: str = "",
    canonical_key: str = "",
) -> list[str]:
    if isinstance(value, list):
        cleaned_variants = [
            cleaned
            for cleaned in (clean_example_text(str(item)).strip() for item in value)
            if cleaned
        ]
    else:
        cleaned_variants = split_joined_keys(value)

    combined: list[str] = []
    if canonical_key and canonical_key != headword:
        combined.append(canonical_key)
    combined.extend(cleaned_variants)

    ordered_unique: list[str] = []
    seen: set[str] = set()
    for variant in combined:
        if not variant or variant == headword or variant in seen:
            continue
        seen.add(variant)
        ordered_unique.append(variant)
        if len(ordered_unique) >= MAX_PROMPT_VARIANTS:
            break
    return ordered_unique
