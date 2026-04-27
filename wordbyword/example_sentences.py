from __future__ import annotations

from collections.abc import Mapping, Sequence

from wordbyword.canonical_keys import join_keys, ordered_unique, sentence_candidate_keys
from wordbyword.text import clean_example_text, strip_edge_curly_quotes

FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM = 0.0135
EXAMPLE_SENTENCE_FINAL_PUNCTUATION = (".", "?", "!", '"')
EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM = 5
EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM = 15


def example_sentence_rows(
    sentences: list[dict[str, object]],
    tokens: list[dict[str, object]],
    candidate_terms: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    quality_by_key = {
        str(row["canonical_key"]): float(row["quality_score"])
        for row in candidate_terms
        if row.get("canonical_key")
    }
    tokens_by_sentence: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for token in tokens:
        tokens_by_sentence.setdefault(sentence_row_key(token), []).append(token)

    rows: list[dict[str, object]] = []
    for sentence in sentences:
        token_rows = sorted(
            tokens_by_sentence.get(sentence_row_key(sentence), []),
            key=lambda token: int(token.get("token_index") or 0),
        )
        matched_keys = sentence_candidate_keys(token_rows, quality_by_key)
        covered_keys = ordered_unique(matched_keys)
        quality_score = round(sum(quality_by_key[key] for key in covered_keys), 3)
        rows.append(
            {
                "document": sentence["document"],
                "page": sentence["page"],
                "sentence_index": sentence["sentence_index"],
                "sentence": normalize_example_sentence_text(str(sentence["sentence"])),
                "quality_score": quality_score,
                "matched_canonical_key_count": len(matched_keys),
                "covered_canonical_key_count": len(covered_keys),
                "covered_canonical_keys": join_keys(covered_keys),
                "matched_canonical_keys": join_keys(matched_keys),
            }
        )

    filtered_rows = filtered_example_sentence_rows(rows)
    normalized_rows = normalize_example_sentence_quality_scores(filtered_rows)
    sorted_rows = sort_example_sentence_rows(normalized_rows)
    return filtered_high_signal_example_sentence_rows(sorted_rows)


def filtered_example_sentence_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    filtered_rows: list[dict[str, object]] = []
    seen_matched_key_sets: set[str] = set()
    for row in rows:
        covered_count = int(row["covered_canonical_key_count"])
        if covered_count < EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM:
            continue
        if covered_count > EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM:
            continue

        matched_keys = str(row["matched_canonical_keys"])
        if matched_keys in seen_matched_key_sets:
            continue

        seen_matched_key_sets.add(matched_keys)
        filtered_rows.append(row)

    return filtered_rows


def normalize_example_sentence_text(text: str) -> str:
    return strip_edge_curly_quotes(clean_example_text(text))


def normalize_example_sentence_quality_scores(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not rows:
        return rows

    max_quality = max(float(row["quality_score"]) for row in rows)
    if max_quality <= 0:
        for row in rows:
            row["quality_score"] = 0.0
        return rows

    for row in rows:
        row["quality_score"] = round(float(row["quality_score"]) / max_quality, 3)
    return rows


def sort_example_sentence_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rows,
        key=lambda row: (
            -example_sentence_sort_score(row),
            -float(row["quality_score"]),
            -int(row["covered_canonical_key_count"]),
            -int(row["matched_canonical_key_count"]),
            str(row["document"]),
            int(row["page"] or 0),
            int(row["sentence_index"]),
            str(row["sentence"]),
        ),
    )


def example_sentence_sort_score(row: Mapping[str, object]) -> float:
    matched_count = int(row["matched_canonical_key_count"])
    if matched_count <= 0:
        return 0.0
    return float(row["quality_score"]) / matched_count


def filtered_high_signal_example_sentence_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [row for row in rows if is_high_signal_example_sentence_row(row)]


def is_high_signal_example_sentence_row(row: Mapping[str, object]) -> bool:
    return (
        example_sentence_sort_score(row) >= FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM
        and has_example_sentence_terminal_punctuation(row)
        and not ends_with_bopsida(row)
    )


def has_example_sentence_terminal_punctuation(row: Mapping[str, object]) -> bool:
    sentence = str(row["sentence"]).strip()
    return bool(sentence) and sentence.endswith(EXAMPLE_SENTENCE_FINAL_PUNCTUATION)


def ends_with_bopsida(row: Mapping[str, object]) -> bool:
    sentence = str(row["sentence"]).strip().rstrip('"').strip()
    return sentence.endswith("봅시다.")


def sentence_row_key(row: Mapping[str, object]) -> tuple[str, str, str]:
    return (str(row.get("document", "")), str(row.get("page", "")), str(row.get("sentence_index", "")))
