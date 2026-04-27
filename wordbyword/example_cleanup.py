from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

from kiwipiepy import Kiwi

from wordbyword.canonical_keys import join_keys, ordered_unique, sentence_candidate_keys, split_joined_keys
from wordbyword.llm import (
    ChatMessage,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    normalized_confidence,
    normalize_text_result,
    strip_markdown_fence,
)
from wordbyword.text import clean_example_text

DEFAULT_EXAMPLE_CLEANUP_MODEL = "gemma4:31b"
DEFAULT_CLEANUP_BATCH_SIZE = 50
MINIMUM_CLEANED_COVERED_KEY_COUNT = 3
LIGHT_FIX_MINIMUM_SIMILARITY = 0.72
FINAL_PUNCTUATION = (".", "?", "!", '"')
UNRESOLVED_LAYOUT_MARKERS = ("◯", "○", "□", "…")
ACTION_ALIASES = {
    "keep": "keep",
    "light_fix": "light_fix",
    "lightfix": "light_fix",
    "fix": "light_fix",
    "rebuild": "rebuild",
    "rewrite": "rebuild",
    "reject": "reject",
    "drop": "reject",
}

BATCH_CLEAN_EXAMPLE_SENTENCE_SYSTEM_PROMPT = """You curate Korean source example sentences for learners.
For each input row, decide whether to keep, lightly fix, rebuild, or reject the sentence.
Prefer keep.
Preserve the original wording, structure, tense, register, and rich context whenever possible.
Use light_fix only for obvious OCR noise, spacing, punctuation, or typo repair.
Use rebuild only when the sentence is clearly broken but the intended meaning is still recoverable from the source.
Reject low-signal, low-value, list-like, instructional, fragmentary, or guess-heavy sentences.
Reject textbook, workbook, and school-test directions or comprehension questions, even when they are grammatical Korean.
Reject numbered prompts, answer-eliciting questions, and classroom tasks such as "(3) 장기려 선생님의 이야기를 듣고 기오는 어떤 마음을 가지게 되었나요?" or "일이 일어난 차례에 맞게 번호를 써 보세요."
Reject fill-in-the-blank, matching, ordering, choosing, explaining, and write/say/discuss prompts unless they are clearly natural dialogue instead of classroom instructions.
Return TSV lines only for rows that are not keep.
Columns: id<TAB>action<TAB>cleaned_sentence
Rules:
- keep: omit the row entirely
- reject: leave cleaned_sentence empty
- light_fix or rebuild: cleaned_sentence must be exactly one cleaned Korean sentence
- if every row is keep, return exactly EMPTY
- do not add a header
- do not add commentary, numbering, markdown fences, or extra columns
- keep the ids exactly as provided"""


@dataclass(frozen=True)
class CleanedExampleOutputs:
    rows: list[dict[str, object]]
    reviewed_count: int
    rejected_count: int
    action_counts: dict[str, int]


@dataclass(frozen=True)
class BatchReviewRow:
    row_id: str
    action: str
    cleaned_sentence: str
    reason: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class CleanupBatchProgress:
    phase: str
    batch_index: int
    total_batches: int
    completed_batches: int
    completed_rows: int
    total_rows: int
    current_batch_size: int


CleanupProgressCallback = Callable[[CleanupBatchProgress], None]
IndexedCleanupRow = tuple[str, Mapping[str, object]]


def cleaned_example_sentence_fields() -> list[str]:
    return [
        "document",
        "page",
        "sentence_index",
        "source_sentence",
        "cleaned_sentence",
        "cleanup_action",
        "cleanup_reason",
        "model_confidence",
        "source_similarity",
        "source_quality_score",
        "source_matched_canonical_key_count",
        "source_covered_canonical_key_count",
        "source_covered_canonical_keys",
        "source_matched_canonical_keys",
        "cleaned_quality_score",
        "cleaned_matched_canonical_key_count",
        "cleaned_covered_canonical_key_count",
        "cleaned_covered_canonical_keys",
        "cleaned_matched_canonical_keys",
    ]


def clean_example_sentence(
    client: SupportsCompletion,
    *,
    sentence: str,
    covered_canonical_keys: Sequence[str] = (),
    matched_canonical_keys: Sequence[str] = (),
    quality_score: float | None = None,
    instruction: str | None = None,
) -> dict[str, object]:
    del covered_canonical_keys
    del matched_canonical_keys
    del quality_score
    del instruction
    source_sentence = clean_example_text(sentence)
    review = review_cleanup_batch(client, [("r0001", source_sentence)])["r0001"]
    cleaned_sentence = source_sentence if review.action == "keep" else review.cleaned_sentence
    return {
        "action": review.action,
        "cleaned_sentence": cleaned_sentence,
        "reason": review.reason,
        "confidence": review.confidence,
    }


def build_cleaned_example_sentence_outputs(
    example_sentences: Sequence[Mapping[str, object]],
    candidate_terms: Sequence[Mapping[str, object]],
    *,
    client: SupportsCompletion,
    kiwi: Kiwi | None = None,
    on_progress: CleanupProgressCallback | None = None,
    on_batch_rows: Callable[[list[dict[str, object]]], None] | None = None,
) -> CleanedExampleOutputs:
    resolved_kiwi = kiwi if kiwi is not None else Kiwi()
    quality_by_key = {
        str(row["canonical_key"]): float(row["quality_score"])
        for row in candidate_terms
        if row.get("canonical_key")
    }
    indexed_rows: list[IndexedCleanupRow] = [(f"r{index:04d}", row) for index, row in enumerate(example_sentences, start=1)]

    rows: list[dict[str, object]] = []
    seen_cleaned_sentences: set[str] = set()
    rejected_count = 0
    action_counts = {
        "keep": 0,
        "light_fix": 0,
        "rebuild": 0,
        "reject": 0,
    }

    for batch in iter_cleanup_batches(indexed_rows, on_progress=on_progress):
        reviews = review_cleanup_batch_with_fallback(client, batch)
        batch_rows, batch_rejected_count = build_cleaned_example_batch_rows(
            batch,
            reviews,
            quality_by_key,
            resolved_kiwi,
            seen_cleaned_sentences,
            action_counts,
        )
        rejected_count += batch_rejected_count
        rows.extend(batch_rows)
        if on_batch_rows is not None and batch_rows:
            on_batch_rows(batch_rows)

    normalize_cleaned_quality_scores(rows)
    return CleanedExampleOutputs(
        rows=rows,
        reviewed_count=len(example_sentences),
        rejected_count=rejected_count,
        action_counts=action_counts,
    )


def review_cleanup_batches(
    client: SupportsCompletion,
    indexed_rows: Sequence[IndexedCleanupRow],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> dict[str, BatchReviewRow]:
    reviews: dict[str, BatchReviewRow] = {}
    for batch in iter_cleanup_batches(indexed_rows, on_progress=on_progress):
        reviews.update(review_cleanup_batch_with_fallback(client, batch))
    return reviews


def iter_cleanup_batches(
    indexed_rows: Sequence[IndexedCleanupRow],
    *,
    on_progress: CleanupProgressCallback | None = None,
) -> Iterator[Sequence[IndexedCleanupRow]]:
    sizes = cleanup_batch_sizes(len(indexed_rows))
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


def emit_cleanup_batch_progress(
    on_progress: CleanupProgressCallback | None,
    *,
    phase: str,
    batch_index: int,
    total_batches: int,
    completed_batches: int,
    completed_rows: int,
    total_rows: int,
    current_batch_size: int,
) -> None:
    if on_progress is None:
        return
    on_progress(
        CleanupBatchProgress(
            phase=phase,
            batch_index=batch_index,
            total_batches=total_batches,
            completed_batches=completed_batches,
            completed_rows=completed_rows,
            total_rows=total_rows,
            current_batch_size=current_batch_size,
        )
    )


def build_cleaned_example_batch_rows(
    batch: Sequence[IndexedCleanupRow],
    reviews: Mapping[str, BatchReviewRow],
    quality_by_key: Mapping[str, float],
    kiwi: Kiwi,
    seen_cleaned_sentences: set[str],
    action_counts: dict[str, int],
) -> tuple[list[dict[str, object]], int]:
    batch_rows: list[dict[str, object]] = []
    rejected_count = 0

    for row_id, row in batch:
        source_sentence = clean_example_text(str(row["sentence"]))
        review = reviews[row_id]
        action = review.action
        if action == "reject":
            rejected_count += 1
            action_counts["reject"] += 1
            continue

        cleaned_sentence = source_sentence if action == "keep" else review.cleaned_sentence
        similarity = source_similarity(source_sentence, cleaned_sentence)
        if action == "light_fix" and similarity < LIGHT_FIX_MINIMUM_SIMILARITY:
            rejected_count += 1
            action_counts["reject"] += 1
            continue
        if not is_acceptable_cleaned_sentence(cleaned_sentence):
            rejected_count += 1
            action_counts["reject"] += 1
            continue
        if cleaned_sentence in seen_cleaned_sentences:
            continue

        matched_keys = sentence_candidate_keys(tokenize_sentence(cleaned_sentence, kiwi), quality_by_key)
        covered_keys = ordered_unique(matched_keys)
        if len(covered_keys) < MINIMUM_CLEANED_COVERED_KEY_COUNT:
            rejected_count += 1
            action_counts["reject"] += 1
            continue

        seen_cleaned_sentences.add(cleaned_sentence)
        action_counts[action] += 1
        batch_rows.append(
            {
                "document": row["document"],
                "page": row["page"],
                "sentence_index": row["sentence_index"],
                "source_sentence": source_sentence,
                "cleaned_sentence": cleaned_sentence,
                "cleanup_action": action,
                "cleanup_reason": review.reason,
                "model_confidence": review.confidence,
                "source_similarity": similarity,
                "source_quality_score": float(row["quality_score"]),
                "source_matched_canonical_key_count": int(row["matched_canonical_key_count"]),
                "source_covered_canonical_key_count": int(row["covered_canonical_key_count"]),
                "source_covered_canonical_keys": row["covered_canonical_keys"],
                "source_matched_canonical_keys": row["matched_canonical_keys"],
                "cleaned_quality_score": round(sum(quality_by_key[key] for key in covered_keys), 3),
                "cleaned_matched_canonical_key_count": len(matched_keys),
                "cleaned_covered_canonical_key_count": len(covered_keys),
                "cleaned_covered_canonical_keys": join_keys(covered_keys),
                "cleaned_matched_canonical_keys": join_keys(matched_keys),
            }
        )

    return batch_rows, rejected_count


def cleanup_batch_sizes(
    total_rows: int,
    *,
    batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
) -> list[int]:
    if total_rows <= 0:
        return []
    if total_rows <= batch_size:
        return [total_rows]
    full_batches, remainder = divmod(total_rows, batch_size)
    sizes = [batch_size] * full_batches
    if remainder:
        sizes.append(remainder)
    return sizes


def review_cleanup_batch_with_fallback(
    client: SupportsCompletion,
    batch: Sequence[tuple[str, Mapping[str, object]]],
) -> dict[str, BatchReviewRow]:
    try:
        return review_cleanup_batch(client, [(row_id, clean_example_text(str(row["sentence"]))) for row_id, row in batch])
    except (LLMRequestError, LLMResponseError):
        if len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        first = review_cleanup_batch_with_fallback(client, batch[:midpoint])
        second = review_cleanup_batch_with_fallback(client, batch[midpoint:])
        return {**first, **second}


def review_cleanup_batch(
    client: SupportsCompletion,
    batch_rows: Sequence[tuple[str, str]],
) -> dict[str, BatchReviewRow]:
    payload_lines = [
        "Rows:",
        *[f"{row_id}\t{clean_example_text(sentence)}" for row_id, sentence in batch_rows],
    ]
    max_output_tokens = max(512, len(batch_rows) * 40)
    result = client.complete(
        [
            ChatMessage(role="system", content=BATCH_CLEAN_EXAMPLE_SENTENCE_SYSTEM_PROMPT),
            ChatMessage(role="user", content="\n".join(payload_lines)),
        ],
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )
    parsed_rows = parse_batch_cleanup_response(result.content)
    expected_ids = [row_id for row_id, _ in batch_rows]
    reviews = {
        row_id: BatchReviewRow(row_id=row_id, action="keep", cleaned_sentence="")
        for row_id in expected_ids
    }
    seen_ids: set[str] = set()
    for row in parsed_rows:
        if row.row_id not in reviews:
            raise LLMResponseError(f"Batch cleanup response included an unknown row id '{row.row_id}'.")
        if row.row_id in seen_ids:
            raise LLMResponseError(f"Batch cleanup response repeated row id '{row.row_id}'.")
        seen_ids.add(row.row_id)
        reviews[row.row_id] = row
    return reviews


def parse_batch_cleanup_response(text: str) -> list[BatchReviewRow]:
    cleaned = strip_markdown_fence(text).strip()
    if not cleaned or cleaned.upper() == "EMPTY":
        return []

    rows: list[BatchReviewRow] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            raise LLMResponseError(f"Invalid batch cleanup line: {raw_line!r}")
        row_id = parts[0].strip()
        action = normalize_action(parts[1])
        cleaned_sentence = normalize_text_result(parts[2]) if len(parts) >= 3 else ""
        if action in {"keep", "reject"}:
            cleaned_sentence = ""
        if action in {"light_fix", "rebuild"} and not cleaned_sentence:
            raise LLMResponseError(f"Missing cleaned sentence for action '{action}' in row '{row_id}'.")
        rows.append(
            BatchReviewRow(
                row_id=row_id,
                action=action,
                cleaned_sentence=cleaned_sentence,
            )
        )
    return rows


def normalize_action(value: Any) -> str:
    if not isinstance(value, str):
        raise LLMResponseError("Cleaned example sentence response did not include an action.")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    action = ACTION_ALIASES.get(normalized)
    if action is None:
        supported = ", ".join(sorted(set(ACTION_ALIASES.values())))
        raise LLMResponseError(f"Unsupported cleaned example sentence action '{value}'. Expected one of: {supported}.")
    return action


def tokenize_sentence(sentence: str, kiwi: Kiwi) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, token in enumerate(kiwi.tokenize(sentence), start=1):
        rows.append(
            {
                "token_index": index,
                "form": token.form,
                "pos": token.tag,
            }
        )
    return rows


def source_similarity(source_sentence: str, cleaned_sentence: str) -> float:
    return round(SequenceMatcher(None, source_sentence, cleaned_sentence).ratio(), 3)


def is_acceptable_cleaned_sentence(sentence: str) -> bool:
    cleaned = clean_example_text(sentence).strip()
    return (
        bool(cleaned)
        and cleaned.endswith(FINAL_PUNCTUATION)
        and not cleaned.rstrip('"').strip().endswith("봅시다.")
        and not any(marker in cleaned for marker in UNRESOLVED_LAYOUT_MARKERS)
    )


def normalize_cleaned_quality_scores(rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        return

    max_quality = max(float(row["cleaned_quality_score"]) for row in rows)
    if max_quality <= 0:
        for row in rows:
            row["cleaned_quality_score"] = 0.0
        return

    for row in rows:
        row["cleaned_quality_score"] = round(float(row["cleaned_quality_score"]) / max_quality, 3)
