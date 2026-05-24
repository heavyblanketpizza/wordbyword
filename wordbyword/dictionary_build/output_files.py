from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from wordbyword.dictionary_build.artifacts import (
    ArtifactId,
    all_output_artifact_ids,
    artifact_filename,
    artifact_path,
    legacy_output_filenames,
    required_artifact_path,
)
from wordbyword.dictionary_build.models import CountEntry, PageText
from wordbyword.dictionary_build.text import clean_example_text


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
    filenames = {
        *(artifact_filename(artifact_id) for artifact_id in all_output_artifact_ids()),
        *legacy_output_filenames(),
    }
    for filename in filenames:
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
        for line_number, line in enumerate(handle, start=1):
            cleaned = line.strip()
            if not cleaned:
                continue
            try:
                row = json.loads(cleaned)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON on line {line_number} of {path}: {error.msg}") from error
            if not isinstance(row, dict):
                raise ValueError(f"Line {line_number} of {path} is not a JSON object.")
            rows.append(row)
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


def read_required_artifact_rows(output_dir: Path, artifact_id: ArtifactId, *, target: str) -> list[dict[str, str]]:
    return read_rows(required_artifact_path(output_dir, artifact_id, target=target))


def read_required_artifact_jsonl(output_dir: Path, artifact_id: ArtifactId, *, target: str) -> list[dict[str, Any]]:
    return read_jsonl(required_artifact_path(output_dir, artifact_id, target=target))


def initialize_artifact_jsonl_file(output_dir: Path, artifact_id: ArtifactId) -> None:
    initialize_jsonl_file(artifact_path(output_dir, artifact_id))


def initialize_artifact_rows_file(output_dir: Path, artifact_id: ArtifactId, *, fieldnames: list[str]) -> None:
    initialize_rows_file(artifact_path(output_dir, artifact_id), fieldnames=fieldnames)


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
        "learning_signal_score",
        "frequency_signal_score",
        "dispersion_signal_score",
        "pos_signal_score",
        "variant_signal_score",
        "frequency_rate_per_10k",
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
