from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from wordbyword.models import CountEntry, PageText
from wordbyword.output_catalog import BASE_OUTPUT_FILES, LEGACY_OUTPUT_FILES, OPTIONAL_OUTPUT_FILES
from wordbyword.text import clean_example_text


def cleanup_previous_outputs(
    output_dir: Path,
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_meaning_dictionary_candidates: bool = False,
) -> None:
    # Optional stages are downstream of the deterministic base artifacts, so any
    # base rebuild invalidates them even when no optional stage is being rerun.
    for filename in {
        *BASE_OUTPUT_FILES,
        *OPTIONAL_OUTPUT_FILES,
        *(["10_cleaned_example_sentences.csv"] if include_cleaned_example_sentences else []),
        *(["11_cleaned_dictionary_candidates.jsonl"] if include_cleaned_dictionary_candidates else []),
        *(
            ["12_dictionary_candidates_with_cleaned_examples.jsonl"]
            if include_linked_dictionary_candidates
            else []
        ),
        *(
            ["13_vocabulary_with_generated_examples.jsonl"]
            if include_generated_dictionary_candidates
            else []
        ),
        *(
            [
                "13_generated_example_attempts.jsonl",
                "13_generated_example_failures.jsonl",
                "14_vocabulary_with_meanings.jsonl",
                "15_final_dictionary.jsonl",
            ]
            if include_generated_dictionary_candidates
            else []
        ),
        *(
            [
                "14_vocabulary_with_meanings.jsonl",
                "15_final_dictionary.jsonl",
            ]
            if include_meaning_dictionary_candidates
            else []
        ),
        *LEGACY_OUTPUT_FILES,
    }:
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
                "document": page.document_id or page.source.name,
                "page": page.page or "",
                "raw_character_count": len(page.raw_text),
                "clean_character_count": len(text),
                "text": text,
            }
        )
    return rows


def parsed_document_count(pages: list[PageText], documents: list[Path]) -> int:
    parsed_sources = {page.document_id or page.source.name for page in pages}
    if parsed_sources:
        return len(parsed_sources)
    return len(documents)


def sentence_fields() -> list[str]:
    return ["document", "page", "sentence_index", "sentence"]


def token_fields() -> list[str]:
    return ["document", "page", "sentence_index", "token_index", "form", "pos", "start", "end", "sentence"]


def write_rows(path: Path, rows: list[dict[str, object]], *, fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def initialize_rows_file(path: Path, *, fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def initialize_jsonl_file(path: Path) -> None:
    path.write_text("", encoding="utf-8")


def append_rows(path: Path, rows: list[dict[str, object]], *, fieldnames: list[str]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writerows(rows)
        handle.flush()


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            cleaned = line.strip()
            if not cleaned:
                continue
            rows.append(json.loads(cleaned))
    return rows


def read_required_rows(path: Path, *, built_from: str, target: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(
            f"Cannot build {target} because {built_from} is missing in {path.parent}. "
            "Run the deterministic pipeline first."
        )
    return read_rows(path)


def read_required_jsonl(path: Path, *, built_from: str, target: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(
            f"Cannot build {target} because {built_from} is missing in {path.parent}. "
            "Run the deterministic pipeline first."
        )
    return read_jsonl(path)


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


def entry_fields() -> list[str]:
    return ["count", "document_count", "example"]


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
        "example",
    ]


def example_sentence_fields() -> list[str]:
    return [
        "document",
        "page",
        "sentence_index",
        "sentence",
        "quality_score",
        "matched_canonical_key_count",
        "covered_canonical_key_count",
        "covered_canonical_keys",
        "matched_canonical_keys",
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def entry_row(entry: CountEntry) -> dict[str, object]:
    return {
        "count": entry.count,
        "document_count": entry.document_count,
        "example": clean_example_text(entry.example),
    }
