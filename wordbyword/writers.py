from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from wordbyword.candidates import build_candidate_outputs
from wordbyword.models import CountEntry, PageText
from wordbyword.text import clean_example_text

OUTPUT_FILES = [
    "00_summary.json",
    "01_extracted_pages.csv",
    "02_sentences.csv",
    "03_tokens.csv",
    "04_surface_words.csv",
    "05_vocabulary_raw.csv",
    "06_candidate_terms.csv",
    "07_rejected_terms.csv",
    "08_dictionary_candidates.jsonl",
    "README.md",
]

LEGACY_OUTPUT_FILES = [
    "summary.json",
    "sentences.csv",
    "tokens.csv",
    "surface_words.csv",
    "vocabulary.csv",
    "expressions.csv",
    "candidate_terms.csv",
    "candidate_expressions.csv",
    "rejected_candidates.csv",
    "dictionary_candidates.jsonl",
    "06_expressions_raw.csv",
    "07_candidate_terms.csv",
    "08_candidate_expressions.csv",
    "09_rejected_candidates.csv",
    "10_dictionary_candidates.jsonl",
]

PIPELINE_STAGES = [
    {
        "stage": "00",
        "file": "00_summary.json",
        "description": "Run metadata, counts, and this ordered stage map.",
    },
    {
        "stage": "01",
        "file": "01_extracted_pages.csv",
        "description": "Cleaned page-level text extracted from each PDF or text source.",
    },
    {
        "stage": "02",
        "file": "02_sentences.csv",
        "description": "Sentence-like chunks after layout cleanup and noise filtering.",
    },
    {
        "stage": "03",
        "file": "03_tokens.csv",
        "description": "Kiwi tokenizer output with forms, POS tags, offsets, and source sentence.",
    },
    {
        "stage": "04",
        "file": "04_surface_words.csv",
        "description": "Visible word forms as they appeared in the cleaned source text.",
    },
    {
        "stage": "05",
        "file": "05_vocabulary_raw.csv",
        "description": "Raw lexical vocabulary terms counted from Kiwi morphology.",
    },
    {
        "stage": "06",
        "file": "06_candidate_terms.csv",
        "description": "Cleaned, grouped, and scored word candidates.",
    },
    {
        "stage": "07",
        "file": "07_rejected_terms.csv",
        "description": "Vocabulary terms removed by deterministic cleanup rules.",
    },
    {
        "stage": "08",
        "file": "08_dictionary_candidates.jsonl",
        "description": "Vocabulary candidate packets for later manual or LLM curation.",
    },
]


def write_outputs(
    output_dir: Path,
    *,
    pages: list[PageText],
    sentences: list[dict[str, object]],
    tokens: list[dict[str, object]],
    vocabulary: dict[tuple[str, str], CountEntry],
    surface_words: dict[str, CountEntry],
    document_totals: Counter[str],
    documents: list[Path],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_outputs(output_dir)
    candidate_outputs = build_candidate_outputs(
        vocabulary=vocabulary,
        surface_words=surface_words,
    )

    write_rows(output_dir / "01_extracted_pages.csv", page_rows(pages), fieldnames=page_fields())
    write_rows(output_dir / "02_sentences.csv", sentences, fieldnames=sentence_fields())
    write_rows(output_dir / "03_tokens.csv", tokens, fieldnames=list(tokens[0].keys()) if tokens else token_fields())
    write_surface_words(output_dir / "04_surface_words.csv", surface_words)
    write_vocabulary(output_dir / "05_vocabulary_raw.csv", vocabulary)
    write_rows(output_dir / "06_candidate_terms.csv", candidate_outputs.terms, fieldnames=candidate_term_fields())
    write_rows(
        output_dir / "07_rejected_terms.csv",
        candidate_outputs.rejected,
        fieldnames=rejected_candidate_fields(),
    )
    write_jsonl(output_dir / "08_dictionary_candidates.jsonl", candidate_outputs.dictionary_entries)
    write_summary(
        output_dir / "00_summary.json",
        pages=pages,
        sentences=sentences,
        tokens=tokens,
        vocabulary=vocabulary,
        surface_words=surface_words,
        candidate_term_count=len(candidate_outputs.terms),
        rejected_candidate_count=len(candidate_outputs.rejected),
        document_totals=document_totals,
        documents=documents,
    )
    write_output_readme(output_dir / "README.md")


def cleanup_previous_outputs(output_dir: Path) -> None:
    for filename in {*OUTPUT_FILES, *LEGACY_OUTPUT_FILES}:
        path = output_dir / filename
        if path.is_file():
            path.unlink()


def page_fields() -> list[str]:
    return ["document", "page", "raw_character_count", "clean_character_count", "text"]


def page_rows(pages: list[PageText]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for page in pages:
        text = clean_example_text(page.text)
        rows.append(
            {
                "document": page.source.name,
                "page": page.page or "",
                "raw_character_count": len(page.raw_text),
                "clean_character_count": len(text),
                "text": text,
            }
        )
    return rows


def sentence_fields() -> list[str]:
    return ["document", "page", "sentence_index", "sentence"]


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


def write_summary(
    path: Path,
    *,
    pages: list[PageText],
    sentences: list[dict[str, object]],
    tokens: list[dict[str, object]],
    vocabulary: dict[tuple[str, str], CountEntry],
    surface_words: dict[str, CountEntry],
    candidate_term_count: int,
    rejected_candidate_count: int,
    document_totals: Counter[str],
    documents: list[Path],
) -> None:
    summary: dict[str, Any] = {
        "documents": [str(path) for path in documents],
        "document_count": len(documents),
        "extracted_page_count": len(pages),
        "sentence_count": len(sentences),
        "token_occurrences": len(tokens),
        "unique_vocabulary_terms": len(vocabulary),
        "unique_surface_words": len(surface_words),
        "clean_candidate_terms": candidate_term_count,
        "rejected_terms": rejected_candidate_count,
        "tokens_by_document": dict(document_totals),
        "outputs": OUTPUT_FILES,
        "pipeline_stages": PIPELINE_STAGES,
    }
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_output_readme(path: Path) -> None:
    lines = [
        "# wordbyword output",
        "",
        "Read these files in numeric order to follow the pipeline from source text to vocabulary candidates.",
        "",
        "| Stage | File | Purpose |",
        "| --- | --- | --- |",
    ]
    for stage in PIPELINE_STAGES:
        lines.append(f"| {stage['stage']} | `{stage['file']}` | {stage['description']} |")

    lines.extend(
        [
            "",
            "When output quality looks wrong, start at `01_extracted_pages.csv` and move forward.",
            "The first file where the text looks wrong usually points to the stage that needs work.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


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
