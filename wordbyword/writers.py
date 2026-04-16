from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from wordbyword.candidates import build_candidate_outputs
from wordbyword.models import CountEntry
from wordbyword.text import clean_example_text


def write_outputs(
    output_dir: Path,
    *,
    tokens: list[dict[str, object]],
    vocabulary: dict[tuple[str, str], CountEntry],
    surface_words: dict[str, CountEntry],
    expressions: dict[tuple[str, int], CountEntry],
    document_totals: Counter[str],
    documents: list[Path],
    min_expression_count: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_outputs = build_candidate_outputs(
        vocabulary=vocabulary,
        surface_words=surface_words,
        expressions=expressions,
        min_expression_count=min_expression_count,
    )

    write_rows(output_dir / "tokens.csv", tokens, fieldnames=list(tokens[0].keys()) if tokens else token_fields())
    write_vocabulary(output_dir / "vocabulary.csv", vocabulary)
    write_surface_words(output_dir / "surface_words.csv", surface_words)
    write_expressions(output_dir / "expressions.csv", expressions, min_expression_count)
    write_rows(output_dir / "candidate_terms.csv", candidate_outputs.terms, fieldnames=candidate_term_fields())
    write_rows(
        output_dir / "candidate_expressions.csv",
        candidate_outputs.expressions,
        fieldnames=candidate_expression_fields(),
    )
    write_rows(output_dir / "rejected_candidates.csv", candidate_outputs.rejected, fieldnames=rejected_candidate_fields())
    write_jsonl(output_dir / "dictionary_candidates.jsonl", candidate_outputs.dictionary_entries)
    write_summary(
        output_dir / "summary.json",
        tokens=tokens,
        vocabulary=vocabulary,
        surface_words=surface_words,
        expressions=expressions,
        candidate_term_count=len(candidate_outputs.terms),
        candidate_expression_count=len(candidate_outputs.expressions),
        rejected_candidate_count=len(candidate_outputs.rejected),
        document_totals=document_totals,
        documents=documents,
        min_expression_count=min_expression_count,
    )


def token_fields() -> list[str]:
    return ["document", "page", "sentence_index", "token_index", "form", "pos", "start", "end", "sentence"]


def write_rows(path: Path, rows: list[dict[str, object]], *, fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_vocabulary(path: Path, vocabulary: dict[tuple[str, str], CountEntry]) -> None:
    rows = [
        {
            "term": term,
            "pos": pos,
            **entry_row(entry),
        }
        for (term, pos), entry in sorted(vocabulary.items(), key=lambda item: (-item[1].count, item[0][0], item[0][1]))
    ]
    write_rows(path, rows, fieldnames=["term", "pos", *entry_fields()])


def write_surface_words(path: Path, surface_words: dict[str, CountEntry]) -> None:
    rows = [
        {
            "surface": surface,
            **entry_row(entry),
        }
        for surface, entry in sorted(surface_words.items(), key=lambda item: (-item[1].count, item[0]))
    ]
    write_rows(path, rows, fieldnames=["surface", *entry_fields()])


def write_expressions(
    path: Path,
    expressions: dict[tuple[str, int], CountEntry],
    min_expression_count: int,
) -> None:
    rows = [
        {
            "expression": expression,
            "size": size,
            **entry_row(entry),
        }
        for (expression, size), entry in sorted(
            expressions.items(),
            key=lambda item: (-item[1].count, item[0][1], item[0][0]),
        )
        if entry.count >= min_expression_count
    ]
    write_rows(path, rows, fieldnames=["expression", "size", *entry_fields()])


def write_summary(
    path: Path,
    *,
    tokens: list[dict[str, object]],
    vocabulary: dict[tuple[str, str], CountEntry],
    surface_words: dict[str, CountEntry],
    expressions: dict[tuple[str, int], CountEntry],
    candidate_term_count: int,
    candidate_expression_count: int,
    rejected_candidate_count: int,
    document_totals: Counter[str],
    documents: list[Path],
    min_expression_count: int,
) -> None:
    expression_count = sum(1 for entry in expressions.values() if entry.count >= min_expression_count)
    summary: dict[str, Any] = {
        "documents": [str(path) for path in documents],
        "document_count": len(documents),
        "token_occurrences": len(tokens),
        "unique_vocabulary_terms": len(vocabulary),
        "unique_surface_words": len(surface_words),
        "unique_expression_candidates": expression_count,
        "clean_candidate_terms": candidate_term_count,
        "clean_candidate_expressions": candidate_expression_count,
        "rejected_candidates": rejected_candidate_count,
        "min_expression_count": min_expression_count,
        "tokens_by_document": dict(document_totals),
        "outputs": [
            "tokens.csv",
            "vocabulary.csv",
            "surface_words.csv",
            "expressions.csv",
            "candidate_terms.csv",
            "candidate_expressions.csv",
            "rejected_candidates.csv",
            "dictionary_candidates.jsonl",
            "summary.json",
        ],
    }
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def entry_fields() -> list[str]:
    return ["count", "document_count", "first_source", "first_page", "example"]


def candidate_term_fields() -> list[str]:
    return [
        "canonical_key",
        "display",
        "kind",
        "pos_group",
        "count",
        "surface_count",
        "document_count",
        "variant_count",
        "variants",
        "quality_score",
        "roi_score",
        "first_source",
        "first_page",
        "example",
    ]


def candidate_expression_fields() -> list[str]:
    return [
        "canonical_key",
        "display",
        "kind",
        "size_min",
        "size_max",
        "count",
        "document_count",
        "variant_count",
        "variants",
        "content_terms",
        "pos_pattern",
        "noise_flags",
        "quality_score",
        "roi_score",
        "first_source",
        "first_page",
        "example",
    ]


def rejected_candidate_fields() -> list[str]:
    return [
        "kind",
        "candidate",
        "canonical_key",
        "reason",
        "count",
        "document_count",
        "first_source",
        "first_page",
        "example",
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def entry_row(entry: CountEntry) -> dict[str, object]:
    return {
        "count": entry.count,
        "document_count": entry.document_count,
        "first_source": entry.first_source,
        "first_page": entry.first_page or "",
        "example": clean_example_text(entry.example),
    }
