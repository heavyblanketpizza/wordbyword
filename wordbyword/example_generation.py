from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kiwipiepy import Kiwi

from wordbyword.canonical_keys import ordered_unique, sentence_candidate_keys, split_joined_keys
from wordbyword.candidates import is_allowed_variant_tail
from wordbyword.example_cleanup import (
    CleanupProgressCallback,
    cleanup_batch_sizes,
    emit_cleanup_batch_progress,
    source_similarity,
    tokenize_sentence,
    unacceptable_cleaned_sentence_reason,
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

DEFAULT_GENERATED_EXAMPLE_BATCH_SIZE = 40
MAX_PROMPT_VARIANTS = 8
REWRITE_LIKE_SIMILARITY_MAXIMUM = 0.88
SURFACE_COVERAGE_TRIM_CHARS = "\"'“”‘’.,!?;:()[]{}<>《》〈〉…"
RETRY_GENERATED_EXAMPLE_ACTION_ALIASES = {
    "keep": "keep_generated",
    "keep_generated": "keep_generated",
    "keepgenerated": "keep_generated",
    "rewrite_generated": "rewrite_generated",
    "rewritegenerated": "rewrite_generated",
    "rewrite_generated_to_match_source_meaning": "rewrite_generated",
    "rewrite_source": "rewrite_source",
    "rewritesource": "rewrite_source",
    "rewrite_source_for_quality": "rewrite_source",
    "new": "new_example",
    "new_example": "new_example",
    "newexample": "new_example",
    "write_new_from_scratch": "new_example",
}

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

BATCH_REPAIR_GENERATED_EXAMPLE_SYSTEM_PROMPT = """You repair Korean learner example sentences for vocabulary headwords.
For each input row, compare source_example and generated_example for the target headword and choose exactly one action.
Input rows include failure_reason, which explains why generated_example failed validation on the previous attempt.
Actions:
- keep_generated: keep generated_example unchanged
- rewrite_generated: rewrite generated_example so it matches the meaning of the headword in source_example
- rewrite_source: rewrite source_example into a clean, natural, learner-friendly Korean sentence while preserving its meaning
- new_example: ignore both sentences and write a completely new learner-friendly example sentence from scratch
Decision policy:
- Treat failure_reason as authoritative validation feedback and fix that specific problem.
- If failure_reason says the prior sentence was not a usable sentence, duplicated an existing example sentence, or was too similar to the source example, do not use keep_generated.
- If source_example is a directive, classroom instruction, workbook prompt, comprehension question, or otherwise not a proper learner example sentence, use new_example.
- Otherwise, if generated_example is empty, unusable, or clearly broken, choose rewrite_source when source_example is a good learner sentence; otherwise choose new_example.
- Otherwise, if source_example and generated_example use the target headword in materially different senses, use rewrite_generated.
- Otherwise, if source_example is clearly better for learners than generated_example, use rewrite_source.
- Otherwise, use keep_generated.
In this repair pass, do not change a row only because generated_example does not use the exact canonical citation form.
Return one TSV line for every input row.
Columns: id<TAB>action<TAB>sentence
Rules:
- keep_generated: leave sentence empty
- rewrite_generated, rewrite_source, new_example: sentence must be exactly one cleaned Korean sentence
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
class BatchRepairedExampleRow:
    row_id: str
    action: str
    generated_sentence: str


@dataclass(frozen=True)
class IndexedGeneratedExampleRow:
    row_id: str
    row: Mapping[str, object]
    prompt_packet: Mapping[str, object]
    prior_generated_sentence: str = ""
    prior_failure_reason: str = ""


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


@dataclass(frozen=True)
class GeneratedExampleBatchSummary:
    attempt_number: int
    batch_number: int
    attempted_count: int
    generated_count: int
    failed_count: int
    succeeded_row_ids: list[str]
    failed_row_ids: list[str]
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


@dataclass(frozen=True)
class GeneratedExampleBatchSnapshot:
    summary: GeneratedExampleBatchSummary
    rows: list[dict[str, object]]
    unresolved_failure_rows: list[dict[str, object]]


GeneratedExampleBatchCallback = Callable[[GeneratedExampleBatchSnapshot], None]


def build_generated_dictionary_candidate_outputs(
    dictionary_entries: Sequence[Mapping[str, object]],
    candidate_terms: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    kiwi: Kiwi | None = None,
    on_progress: CleanupProgressCallback | None = None,
    on_batch_complete: GeneratedExampleBatchCallback | None = None,
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

    def handle_batch(summary: GeneratedExampleBatchSummary) -> None:
        all_attempt_rows.extend(summary.attempt_rows)
        for row_id in summary.succeeded_row_ids:
            unresolved_failures_by_id.pop(row_id, None)
        for failure_row in summary.failure_rows:
            unresolved_failures_by_id[str(failure_row["row_id"])] = failure_row
        for attempt_row in summary.attempt_rows:
            if bool(attempt_row["accepted"]):
                generated_by_id[str(attempt_row["row_id"])] = str(attempt_row["generated_sentence"])
        if on_batch_complete is not None:
            on_batch_complete(
                GeneratedExampleBatchSnapshot(
                    summary=summary,
                    rows=materialize_generated_dictionary_rows(
                        dictionary_entries,
                        row_id_by_index=row_id_by_index,
                        generated_by_id=generated_by_id,
                    ),
                    unresolved_failure_rows=list(unresolved_failures_by_id.values()),
                )
            )

    while pending_rows:
        attempt_summary = run_generated_example_attempt(
            pending_rows,
            quality_by_key,
            client=client,
            kiwi=resolved_kiwi,
            seen_examples=seen_examples,
            attempt_number=attempt_number,
            on_progress=on_progress,
            on_batch_complete=handle_batch,
        )
        completed_attempts = attempt_summary.attempt_number

        should_retry = on_attempt_complete(attempt_summary) if on_attempt_complete is not None else False
        if not attempt_summary.failure_rows:
            break
        if not should_retry:
            break
        pending_rows = build_retry_generated_example_rows(
            attempt_summary.failure_rows,
            indexed_row_by_id=indexed_row_by_id,
        )
        attempt_number += 1

    rows = materialize_generated_dictionary_rows(
        dictionary_entries,
        row_id_by_index=row_id_by_index,
        generated_by_id=generated_by_id,
    )

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


def retry_generated_dictionary_candidate_outputs(
    dictionary_entries: Sequence[Mapping[str, object]],
    failure_rows: Sequence[Mapping[str, object]],
    candidate_terms: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    starting_attempt_number: int = 2,
    kiwi: Kiwi | None = None,
    on_progress: CleanupProgressCallback | None = None,
    on_batch_complete: GeneratedExampleBatchCallback | None = None,
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

    generated_by_id: dict[str, str] = {}
    for index, row in enumerate(dictionary_entries):
        row_id = row_id_by_index.get(index)
        if row_id is None:
            continue
        if bool(row.get("example_generated_with_ollama")):
            generated_by_id[row_id] = clean_example_text(str(row.get("example", ""))).strip()

    seen_examples = {
        cleaned
        for cleaned in (
            clean_example_text(str(row.get("example", ""))).strip()
            for row in dictionary_entries
        )
        if cleaned
    }
    auto_accepted_by_id, remaining_failure_rows = auto_accept_generated_example_failures(
        failure_rows,
        indexed_row_by_id=indexed_row_by_id,
        quality_by_key=quality_by_key,
        kiwi=resolved_kiwi,
        seen_examples=seen_examples,
    )
    generated_by_id.update(auto_accepted_by_id)
    unresolved_failures_by_id = {
        str(row["row_id"]): dict(row)
        for row in remaining_failure_rows
        if row.get("row_id")
    }
    all_attempt_rows: list[dict[str, object]] = []
    completed_attempts = max(0, starting_attempt_number - 1)
    pending_rows = build_retry_generated_example_rows(
        remaining_failure_rows,
        indexed_row_by_id=indexed_row_by_id,
    )

    def handle_batch(summary: GeneratedExampleBatchSummary) -> None:
        all_attempt_rows.extend(summary.attempt_rows)
        for row_id in summary.succeeded_row_ids:
            unresolved_failures_by_id.pop(row_id, None)
        for failure_row in summary.failure_rows:
            unresolved_failures_by_id[str(failure_row["row_id"])] = failure_row
        for attempt_row in summary.attempt_rows:
            if bool(attempt_row["accepted"]):
                generated_by_id[str(attempt_row["row_id"])] = str(attempt_row["generated_sentence"])
        if on_batch_complete is not None:
            on_batch_complete(
                GeneratedExampleBatchSnapshot(
                    summary=summary,
                    rows=materialize_generated_dictionary_rows(
                        dictionary_entries,
                        row_id_by_index=row_id_by_index,
                        generated_by_id=generated_by_id,
                    ),
                    unresolved_failure_rows=list(unresolved_failures_by_id.values()),
                )
            )

    if pending_rows:
        attempt_summary = run_generated_example_attempt(
            pending_rows,
            quality_by_key,
            client=client,
            kiwi=resolved_kiwi,
            seen_examples=seen_examples,
            attempt_number=starting_attempt_number,
            on_progress=on_progress,
            on_batch_complete=handle_batch,
        )
        completed_attempts = attempt_summary.attempt_number

    rows = materialize_generated_dictionary_rows(
        dictionary_entries,
        row_id_by_index=row_id_by_index,
        generated_by_id=generated_by_id,
    )
    generated_count = sum(1 for row in rows if bool(row.get("example_generated_with_ollama")))
    return GeneratedExampleOutputs(
        rows=rows,
        reviewed_count=len(indexed_rows),
        generated_count=generated_count,
        preserved_count=len(dictionary_entries) - generated_count,
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
    on_batch_complete: Callable[[GeneratedExampleBatchSummary], None] | None = None,
) -> GeneratedExampleAttemptSummary:
    is_repair_attempt = attempt_number > 1
    attempt_kind = "repair" if is_repair_attempt else "generate"
    attempt_rows: list[dict[str, object]] = []
    failure_rows: list[dict[str, object]] = []
    succeeded_row_ids: list[str] = []
    failed_row_ids: list[str] = []

    for batch_number, batch in enumerate(
        iter_generated_example_batches(indexed_rows, on_progress=on_progress),
        start=1,
    ):
        batch_attempt_rows: list[dict[str, object]] = []
        batch_failure_rows: list[dict[str, object]] = []
        batch_succeeded_row_ids: list[str] = []
        batch_failed_row_ids: list[str] = []
        try:
            generated_reviews = None
            repaired_reviews = None
            if is_repair_attempt:
                repaired_reviews = review_repaired_generated_example_batch_with_fallback(client, batch)
            else:
                generated_reviews = review_generated_example_batch_with_fallback(client, batch)
        except (LLMRequestError, LLMResponseError) as exc:
            failure_reason = str(exc)
            for indexed_row in batch:
                attempt_row = build_generated_example_attempt_row(
                    indexed_row,
                    attempt_number=attempt_number,
                    generated_sentence="",
                    accepted=False,
                    attempt_kind=attempt_kind,
                    input_generated_sentence=indexed_row.prior_generated_sentence,
                    failure_reason=failure_reason,
                )
                attempt_rows.append(attempt_row)
                failure_rows.append(attempt_row)
                batch_attempt_rows.append(attempt_row)
                batch_failure_rows.append(attempt_row)
                failed_row_ids.append(indexed_row.row_id)
                batch_failed_row_ids.append(indexed_row.row_id)
            emit_generated_example_batch_summary(
                on_batch_complete,
                attempt_number=attempt_number,
                batch_number=batch_number,
                attempt_rows=batch_attempt_rows,
                failure_rows=batch_failure_rows,
                succeeded_row_ids=batch_succeeded_row_ids,
                failed_row_ids=batch_failed_row_ids,
            )
            continue

        for indexed_row in batch:
            review_action = ""
            raw_generated_sentence = ""
            try:
                if is_repair_attempt:
                    assert repaired_reviews is not None
                    repair_review = repaired_reviews[indexed_row.row_id]
                    review_action = repair_review.action
                    raw_generated_sentence = repair_generated_sentence_candidate(
                        indexed_row,
                        repair_review,
                    )
                    generated_sentence = validate_repaired_generated_example_sentence(
                        indexed_row,
                        repair_review,
                        seen_examples,
                    )
                else:
                    assert generated_reviews is not None
                    raw_generated_sentence = generated_reviews[indexed_row.row_id].generated_sentence
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
                    attempt_kind=attempt_kind,
                    review_action=review_action,
                    input_generated_sentence=indexed_row.prior_generated_sentence,
                    failure_reason=str(exc),
                )
                attempt_rows.append(attempt_row)
                failure_rows.append(attempt_row)
                batch_attempt_rows.append(attempt_row)
                batch_failure_rows.append(attempt_row)
                failed_row_ids.append(indexed_row.row_id)
                batch_failed_row_ids.append(indexed_row.row_id)
                continue

            seen_examples.add(generated_sentence)
            attempt_row = build_generated_example_attempt_row(
                indexed_row,
                attempt_number=attempt_number,
                generated_sentence=generated_sentence,
                accepted=True,
                attempt_kind=attempt_kind,
                review_action=review_action,
                input_generated_sentence=indexed_row.prior_generated_sentence,
            )
            attempt_rows.append(attempt_row)
            batch_attempt_rows.append(attempt_row)
            succeeded_row_ids.append(indexed_row.row_id)
            batch_succeeded_row_ids.append(indexed_row.row_id)

        emit_generated_example_batch_summary(
            on_batch_complete,
            attempt_number=attempt_number,
            batch_number=batch_number,
            attempt_rows=batch_attempt_rows,
            failure_rows=batch_failure_rows,
            succeeded_row_ids=batch_succeeded_row_ids,
            failed_row_ids=batch_failed_row_ids,
        )

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


def review_repaired_generated_example_batch_with_fallback(
    client: SupportsCompletion,
    batch: Sequence[IndexedGeneratedExampleRow],
) -> dict[str, BatchRepairedExampleRow]:
    try:
        return review_repaired_generated_example_batch(client, batch)
    except (LLMRequestError, LLMResponseError):
        if len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        first = review_repaired_generated_example_batch_with_fallback(client, batch[:midpoint])
        second = review_repaired_generated_example_batch_with_fallback(client, batch[midpoint:])
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


def review_repaired_generated_example_batch(
    client: SupportsCompletion,
    batch_rows: Sequence[IndexedGeneratedExampleRow],
) -> dict[str, BatchRepairedExampleRow]:
    payload_lines = [
        "Candidates:",
        *[
            json.dumps(
                {"id": row.row_id, **build_repair_prompt_packet(row)},
                ensure_ascii=False,
                sort_keys=True,
            )
            for row in batch_rows
        ],
    ]
    max_output_tokens = max(512, len(batch_rows) * 72)
    result = client.complete(
        [
            ChatMessage(role="system", content=BATCH_REPAIR_GENERATED_EXAMPLE_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(payload_lines)),
        ],
        max_output_tokens=max_output_tokens,
    )
    parsed_rows = parse_repaired_generated_example_response(result.content)
    reviews = {row.row_id: None for row in batch_rows}
    seen_ids: set[str] = set()
    for row in parsed_rows:
        if row.row_id not in reviews:
            raise LLMResponseError(f"Generated example repair response included an unknown row id '{row.row_id}'.")
        if row.row_id in seen_ids:
            raise LLMResponseError(f"Generated example repair response repeated row id '{row.row_id}'.")
        seen_ids.add(row.row_id)
        reviews[row.row_id] = row
    missing_ids = [row_id for row_id, review in reviews.items() if review is None]
    if missing_ids:
        raise LLMResponseError(
            "Generated example repair response omitted row ids: "
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


def parse_repaired_generated_example_response(text: str) -> list[BatchRepairedExampleRow]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned:
        raise LLMResponseError("Generated example repair response was empty.")

    rows: list[BatchRepairedExampleRow] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            raise LLMResponseError(f"Invalid generated example repair line: {raw_line!r}")
        row_id = parts[0].strip()
        action = normalize_retry_generated_example_action(parts[1])
        generated_sentence = normalize_text_result(parts[2]) if len(parts) >= 3 else ""
        if action == "keep_generated":
            generated_sentence = ""
        elif not generated_sentence:
            raise LLMResponseError(
                f"Missing generated sentence for repair action '{action}' in row '{row_id}'."
            )
        rows.append(
            BatchRepairedExampleRow(
                row_id=row_id,
                action=action,
                generated_sentence=generated_sentence,
            )
        )
    if not rows:
        raise LLMResponseError("Generated example repair response did not include any rows.")
    return rows


def normalize_retry_generated_example_action(value: Any) -> str:
    if not isinstance(value, str):
        raise LLMResponseError("Generated example repair response did not include an action.")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    action = RETRY_GENERATED_EXAMPLE_ACTION_ALIASES.get(normalized)
    if action is None:
        supported = ", ".join(sorted(set(RETRY_GENERATED_EXAMPLE_ACTION_ALIASES.values())))
        raise LLMResponseError(
            f"Unsupported generated example repair action '{value}'. Expected one of: {supported}."
        )
    return action


def validate_generated_example_sentence(
    indexed_row: IndexedGeneratedExampleRow,
    generated_sentence: str,
    quality_by_key: Mapping[str, float],
    kiwi: Kiwi,
    seen_examples: set[str],
) -> str:
    cleaned_sentence = clean_example_text(generated_sentence).strip()
    unusable_reason = unacceptable_cleaned_sentence_reason(cleaned_sentence)
    if unusable_reason:
        raise LLMResponseError(
            f"Generated example for row '{indexed_row.row_id}' was not a usable sentence: {unusable_reason}."
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
        if not generated_sentence_covers_target_canonical_key(
            indexed_row,
            cleaned_sentence,
            quality_by_key,
            kiwi,
        ):
            raise LLMResponseError(
                f"Generated example for row '{indexed_row.row_id}' did not cover canonical key '{canonical_key}'."
            )

    return cleaned_sentence


def validate_repaired_generated_example_sentence(
    indexed_row: IndexedGeneratedExampleRow,
    review: BatchRepairedExampleRow,
    seen_examples: set[str],
) -> str:
    cleaned_sentence = repair_generated_sentence_candidate(indexed_row, review)
    if not cleaned_sentence:
        raise LLMResponseError(
            f"Generated example repair for row '{indexed_row.row_id}' did not produce a sentence."
        )
    unusable_reason = unacceptable_cleaned_sentence_reason(cleaned_sentence)
    if unusable_reason:
        raise LLMResponseError(
            f"Generated example repair for row '{indexed_row.row_id}' was not a usable sentence: {unusable_reason}."
        )
    if cleaned_sentence in seen_examples:
        raise LLMResponseError(
            f"Generated example repair for row '{indexed_row.row_id}' duplicated an existing example sentence."
        )
    return cleaned_sentence


def repair_generated_sentence_candidate(
    indexed_row: IndexedGeneratedExampleRow,
    review: BatchRepairedExampleRow,
) -> str:
    if review.action == "keep_generated":
        return clean_example_text(indexed_row.prior_generated_sentence).strip()
    return clean_example_text(review.generated_sentence).strip()


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


def build_repair_prompt_packet(indexed_row: IndexedGeneratedExampleRow) -> dict[str, object]:
    headword = prompt_headword(indexed_row.row)
    canonical_key = clean_example_text(str(indexed_row.row.get("canonical_key", ""))).strip()
    return {
        "headword": headword,
        "variants": prompt_variants(
            indexed_row.prompt_packet.get("variants", []),
            headword=headword,
            canonical_key=canonical_key,
        ),
        "source_example": clean_example_text(str(indexed_row.row.get("example", ""))),
        "generated_example": clean_example_text(indexed_row.prior_generated_sentence),
        "failure_reason": str(indexed_row.prior_failure_reason).strip(),
    }


def build_generated_example_attempt_row(
    indexed_row: IndexedGeneratedExampleRow,
    *,
    attempt_number: int,
    generated_sentence: str,
    accepted: bool,
    attempt_kind: str = "generate",
    review_action: str = "",
    input_generated_sentence: str = "",
    failure_reason: str = "",
) -> dict[str, object]:
    return {
        "attempt_number": attempt_number,
        "attempt_kind": attempt_kind,
        "row_id": indexed_row.row_id,
        "canonical_key": str(indexed_row.row.get("canonical_key", "")),
        "display": str(indexed_row.row.get("display", "")),
        "source_example": clean_example_text(str(indexed_row.row.get("example", ""))),
        "input_generated_sentence": clean_example_text(input_generated_sentence).strip(),
        "review_action": review_action,
        "generated_sentence": generated_sentence,
        "accepted": accepted,
        "failure_reason": failure_reason,
    }


def emit_generated_example_batch_summary(
    on_batch_complete: Callable[[GeneratedExampleBatchSummary], None] | None,
    *,
    attempt_number: int,
    batch_number: int,
    attempt_rows: list[dict[str, object]],
    failure_rows: list[dict[str, object]],
    succeeded_row_ids: list[str],
    failed_row_ids: list[str],
) -> None:
    if on_batch_complete is None:
        return
    on_batch_complete(
        GeneratedExampleBatchSummary(
            attempt_number=attempt_number,
            batch_number=batch_number,
            attempted_count=len(attempt_rows),
            generated_count=len(succeeded_row_ids),
            failed_count=len(failed_row_ids),
            succeeded_row_ids=succeeded_row_ids,
            failed_row_ids=failed_row_ids,
            attempt_rows=attempt_rows,
            failure_rows=failure_rows,
        )
    )


def materialize_generated_dictionary_rows(
    dictionary_entries: Sequence[Mapping[str, object]],
    *,
    row_id_by_index: Mapping[int, str],
    generated_by_id: Mapping[str, str],
) -> list[dict[str, object]]:
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
    return rows


def build_retry_generated_example_rows(
    failure_rows: Sequence[Mapping[str, object]],
    *,
    indexed_row_by_id: Mapping[str, IndexedGeneratedExampleRow],
) -> list[IndexedGeneratedExampleRow]:
    retry_rows: list[IndexedGeneratedExampleRow] = []
    for failure_row in failure_rows:
        row_id = str(failure_row.get("row_id", "")).strip()
        if not row_id or row_id not in indexed_row_by_id:
            continue
        indexed_row = indexed_row_by_id[row_id]
        retry_rows.append(
            IndexedGeneratedExampleRow(
                row_id=row_id,
                row=indexed_row.row,
                prompt_packet=indexed_row.prompt_packet,
                prior_generated_sentence=clean_example_text(
                    str(failure_row.get("generated_sentence", ""))
                ).strip(),
                prior_failure_reason=str(failure_row.get("failure_reason", "")),
            )
        )
    return retry_rows


def auto_accept_generated_example_failures(
    failure_rows: Sequence[Mapping[str, object]],
    *,
    indexed_row_by_id: Mapping[str, IndexedGeneratedExampleRow],
    quality_by_key: Mapping[str, float],
    kiwi: Kiwi,
    seen_examples: set[str],
) -> tuple[dict[str, str], list[dict[str, object]]]:
    accepted_by_id: dict[str, str] = {}
    remaining_rows: list[dict[str, object]] = []
    for failure_row in failure_rows:
        row_id = str(failure_row.get("row_id", "")).strip()
        indexed_row = indexed_row_by_id.get(row_id)
        if indexed_row is None:
            remaining_rows.append(dict(failure_row))
            continue

        generated_sentence = clean_example_text(str(failure_row.get("generated_sentence", ""))).strip()
        if not generated_sentence:
            remaining_rows.append(dict(failure_row))
            continue

        try:
            accepted_sentence = validate_generated_example_sentence(
                indexed_row,
                generated_sentence,
                quality_by_key,
                kiwi,
                seen_examples,
            )
        except LLMResponseError:
            remaining_rows.append(dict(failure_row))
            continue

        seen_examples.add(accepted_sentence)
        accepted_by_id[row_id] = accepted_sentence

    return accepted_by_id, remaining_rows


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


def generated_sentence_covers_target_canonical_key(
    indexed_row: IndexedGeneratedExampleRow,
    cleaned_sentence: str,
    quality_by_key: Mapping[str, float],
    kiwi: Kiwi,
) -> bool:
    canonical_key = clean_example_text(str(indexed_row.row.get("canonical_key", ""))).strip()
    if not canonical_key:
        return True

    token_rows = tokenize_sentence(cleaned_sentence, kiwi)
    covered_keys = set(ordered_unique(sentence_candidate_keys(token_rows, quality_by_key)))
    if canonical_key in covered_keys:
        return True

    hada_key = canonical_key + "하다"
    if hada_key in quality_by_key and hada_key in covered_keys:
        return True

    return sentence_has_target_surface_coverage(
        cleaned_sentence,
        coverage_surface_forms(indexed_row),
        canonical_key=canonical_key,
        kiwi=kiwi,
    )


def coverage_surface_forms(indexed_row: IndexedGeneratedExampleRow) -> list[str]:
    canonical_key = clean_example_text(str(indexed_row.row.get("canonical_key", ""))).strip()
    headword = clean_example_text(str(indexed_row.prompt_packet.get("headword", ""))).strip()
    prompt_variants_value = indexed_row.prompt_packet.get("variants", [])
    if isinstance(prompt_variants_value, list):
        prompt_variants = [
            clean_example_text(str(value)).strip()
            for value in prompt_variants_value
            if clean_example_text(str(value)).strip()
        ]
    else:
        prompt_variants = split_joined_keys(prompt_variants_value)

    forms: list[str] = []
    seen: set[str] = set()
    for value in (canonical_key, headword, *prompt_variants):
        if not value or value in seen:
            continue
        seen.add(value)
        forms.append(value)
    return sorted(forms, key=len, reverse=True)


def sentence_has_target_surface_coverage(
    sentence: str,
    surface_forms: Sequence[str],
    *,
    canonical_key: str,
    kiwi: Kiwi,
) -> bool:
    verbal_key = canonical_key.endswith("다")
    for segment in sentence.split():
        eojeol = segment.strip(SURFACE_COVERAGE_TRIM_CHARS)
        if not eojeol:
            continue
        eojeol_tokens = kiwi.tokenize(eojeol)
        for surface_form in surface_forms:
            if not allows_surface_coverage_form(surface_form, canonical_key=canonical_key):
                continue
            if eojeol == surface_form:
                return True
            if (
                eojeol_tokens
                and eojeol_tokens[0].form == surface_form
                and all(is_surface_coverage_tail(token.tag) for token in eojeol_tokens[1:])
            ):
                return True
            if verbal_key and len(surface_form) >= 2 and eojeol.startswith(surface_form):
                return True
    return False


def allows_surface_coverage_form(surface_form: str, *, canonical_key: str) -> bool:
    return len(surface_form) >= 2 or canonical_key in {"않다", "말다"}


def is_surface_coverage_tail(pos: str) -> bool:
    return is_allowed_variant_tail(pos) and pos not in {"VX", "XSV", "XSA", "XSN"}
