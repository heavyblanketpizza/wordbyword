from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Mapping

ArtifactCallback = Callable[[str, Sequence[Mapping[str, object]]], None]

from kiwipiepy import Kiwi

from wordbyword.candidates import build_candidate_outputs
from wordbyword.canonical_keys import join_keys, ordered_unique, sentence_candidate_keys
from wordbyword.dictionary_cleanup import CleanedDictionaryOutputs, build_cleaned_dictionary_candidate_outputs
from wordbyword.example_assignment import LinkedDictionaryOutputs, build_linked_dictionary_candidate_outputs
from wordbyword.example_cleanup import (
    CleanedExampleOutputs,
    CleanupProgressCallback,
    build_cleaned_example_sentence_outputs,
    cleaned_example_sentence_fields,
)
from wordbyword.llm import SupportsCompletion
from wordbyword.models import CountEntry, PageText
from wordbyword.text import clean_example_text, strip_edge_curly_quotes

FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM = 0.0135
EXAMPLE_SENTENCE_FINAL_PUNCTUATION = (".", "?", "!", '"')
EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM = 5
EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM = 15

BASE_OUTPUT_FILES = [
    "00_summary.json",
    "01_extracted_pages.csv",
    "02_sentences.csv",
    "03_tokens.csv",
    "04_surface_words.csv",
    "05_vocabulary_raw.csv",
    "06_candidate_terms.csv",
    "07_rejected_terms.csv",
    "08_dictionary_candidates.jsonl",
    "09_example_sentences.csv",
    "README.md",
]

OPTIONAL_OUTPUT_FILES = [
    "10_cleaned_example_sentences.csv",
    "11_cleaned_dictionary_candidates.jsonl",
    "12_dictionary_candidates_with_cleaned_examples.jsonl",
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
    "filtered_example_sentence.csv",
]

BASE_PIPELINE_STAGES = [
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
    {
        "stage": "09",
        "file": "09_example_sentences.csv",
        "description": "High-signal sentence catalog filtered by canonical-key coverage, density, sentence shape, and normalized quality scores.",
    },
]

OPTIONAL_PIPELINE_STAGES = [
    {
        "stage": "10",
        "file": "10_cleaned_example_sentences.csv",
        "description": "Ollama-curated source sentences kept, lightly fixed, rebuilt, or deleted with minimal-edit bias.",
    },
    {
        "stage": "11",
        "file": "11_cleaned_dictionary_candidates.jsonl",
        "description": "Ollama-filtered learner-dictionary candidates with low-signal names and debris removed.",
    },
    {
        "stage": "12",
        "file": "12_dictionary_candidates_with_cleaned_examples.jsonl",
        "description": "Dictionary candidates whose examples are replaced with unique cleaned sentences from stage 10 when available.",
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
    kiwi: Kiwi | None = None,
    sentence_cleanup_client: SupportsCompletion | None = None,
    sentence_cleanup_progress: CleanupProgressCallback | None = None,
    dictionary_cleanup_client: SupportsCompletion | None = None,
    dictionary_cleanup_progress: CleanupProgressCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_outputs(
        output_dir,
        include_cleaned_example_sentences=sentence_cleanup_client is not None,
        include_cleaned_dictionary_candidates=dictionary_cleanup_client is not None,
        include_linked_dictionary_candidates=(
            sentence_cleanup_client is not None or dictionary_cleanup_client is not None
        ),
    )
    candidate_outputs = build_candidate_outputs(
        vocabulary=vocabulary,
        surface_words=surface_words,
        total_document_count=parsed_document_count(pages, documents),
        total_token_occurrences=len(tokens),
        kiwi=kiwi,
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
    example_sentences = example_sentence_rows(sentences, tokens, candidate_outputs.terms)
    write_rows(output_dir / "09_example_sentences.csv", example_sentences, fieldnames=example_sentence_fields())
    if artifact_callback is not None:
        artifact_callback("09_example_sentences", example_sentences)
    cleaned_example_outputs: CleanedExampleOutputs | None = None
    if sentence_cleanup_client is not None:
        cleaned_example_sentence_path = output_dir / "10_cleaned_example_sentences.csv"
        cleaned_example_sentence_fieldnames = cleaned_example_sentence_fields()
        initialize_rows_file(cleaned_example_sentence_path, fieldnames=cleaned_example_sentence_fieldnames)
        cleaned_example_outputs = build_cleaned_example_sentence_outputs(
            example_sentences,
            candidate_outputs.terms,
            client=sentence_cleanup_client,
            kiwi=kiwi,
            on_progress=sentence_cleanup_progress,
            on_batch_rows=lambda batch_rows: append_rows(
                cleaned_example_sentence_path,
                batch_rows,
                fieldnames=cleaned_example_sentence_fieldnames,
            ),
        )
        write_rows(
            cleaned_example_sentence_path,
            cleaned_example_outputs.rows,
            fieldnames=cleaned_example_sentence_fieldnames,
        )
    cleaned_dictionary_outputs: CleanedDictionaryOutputs | None = None
    if dictionary_cleanup_client is not None:
        cleaned_dictionary_candidate_path = output_dir / "11_cleaned_dictionary_candidates.jsonl"
        initialize_jsonl_file(cleaned_dictionary_candidate_path)
        support_sentence_rows = (
            cleaned_example_outputs.rows
            if cleaned_example_outputs is not None
            else example_sentences
        )
        cleaned_dictionary_outputs = build_cleaned_dictionary_candidate_outputs(
            candidate_outputs.dictionary_entries,
            candidate_outputs.terms,
            support_sentence_rows,
            client=dictionary_cleanup_client,
            on_progress=dictionary_cleanup_progress,
            on_batch_rows=lambda batch_rows: append_jsonl(
                cleaned_dictionary_candidate_path,
                batch_rows,
            ),
        )
        write_jsonl(
            cleaned_dictionary_candidate_path,
            cleaned_dictionary_outputs.rows,
        )
        if artifact_callback is not None:
            artifact_callback(
                "11_cleaned_dictionary_candidates",
                cleaned_dictionary_outputs.rows,
            )
    write_summary(
        output_dir / "00_summary.json",
        pages=pages,
        sentences=sentences,
        tokens=tokens,
        vocabulary=vocabulary,
        surface_words=surface_words,
        candidate_term_count=len(candidate_outputs.terms),
        rejected_candidate_count=len(candidate_outputs.rejected),
        example_sentence_count=len(example_sentences),
        document_totals=document_totals,
        documents=documents,
        cleaned_example_outputs=cleaned_example_outputs,
        cleaned_dictionary_outputs=cleaned_dictionary_outputs,
    )
    write_output_readme(
        output_dir / "README.md",
        include_cleaned_example_sentences=cleaned_example_outputs is not None,
        include_cleaned_dictionary_candidates=cleaned_dictionary_outputs is not None,
    )
    refresh_saved_output_metadata(output_dir)


def write_cleanup_outputs_from_saved(
    output_dir: Path,
    *,
    sentence_cleanup_client: SupportsCompletion | None = None,
    sentence_cleanup_progress: CleanupProgressCallback | None = None,
    dictionary_cleanup_client: SupportsCompletion | None = None,
    dictionary_cleanup_progress: CleanupProgressCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
) -> tuple[CleanedExampleOutputs | None, CleanedDictionaryOutputs | None, LinkedDictionaryOutputs | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_example_outputs: CleanedExampleOutputs | None = None
    cleaned_dictionary_outputs: CleanedDictionaryOutputs | None = None
    linked_dictionary_outputs: LinkedDictionaryOutputs | None = None

    if sentence_cleanup_client is None and dictionary_cleanup_client is None:
        linked_dictionary_outputs = write_linked_dictionary_candidates_from_saved(
            output_dir,
            artifact_callback=artifact_callback,
            require_inputs=False,
        )
        refresh_saved_output_metadata(output_dir, linked_dictionary_outputs=linked_dictionary_outputs)
        return None, None, linked_dictionary_outputs

    candidate_terms = read_required_rows(
        output_dir / "06_candidate_terms.csv",
        built_from="06_candidate_terms.csv",
        target="LLM cleanup stages",
    )
    example_sentences: list[dict[str, str]] | None = None

    if sentence_cleanup_client is not None:
        example_sentences = read_required_rows(
            output_dir / "09_example_sentences.csv",
            built_from="09_example_sentences.csv",
            target="10_cleaned_example_sentences.csv",
        )
        if artifact_callback is not None:
            artifact_callback("09_example_sentences", example_sentences)
        cleaned_example_sentence_path = output_dir / "10_cleaned_example_sentences.csv"
        cleaned_example_sentence_fieldnames = cleaned_example_sentence_fields()
        initialize_rows_file(cleaned_example_sentence_path, fieldnames=cleaned_example_sentence_fieldnames)
        cleaned_example_outputs = build_cleaned_example_sentence_outputs(
            example_sentences,
            candidate_terms,
            client=sentence_cleanup_client,
            on_progress=sentence_cleanup_progress,
            on_batch_rows=lambda batch_rows: append_rows(
                cleaned_example_sentence_path,
                batch_rows,
                fieldnames=cleaned_example_sentence_fieldnames,
            ),
        )
        write_rows(
            cleaned_example_sentence_path,
            cleaned_example_outputs.rows,
            fieldnames=cleaned_example_sentence_fieldnames,
        )

    if dictionary_cleanup_client is not None:
        dictionary_entries = read_required_jsonl(
            output_dir / "08_dictionary_candidates.jsonl",
            built_from="08_dictionary_candidates.jsonl",
            target="11_cleaned_dictionary_candidates.jsonl",
        )
        cleaned_dictionary_candidate_path = output_dir / "11_cleaned_dictionary_candidates.jsonl"
        initialize_jsonl_file(cleaned_dictionary_candidate_path)
        support_sentence_rows = resolve_dictionary_support_rows(
            output_dir,
            cleaned_example_outputs=cleaned_example_outputs,
            loaded_example_sentences=example_sentences,
        )
        cleaned_dictionary_outputs = build_cleaned_dictionary_candidate_outputs(
            dictionary_entries,
            candidate_terms,
            support_sentence_rows,
            client=dictionary_cleanup_client,
            on_progress=dictionary_cleanup_progress,
            on_batch_rows=lambda batch_rows: append_jsonl(
                cleaned_dictionary_candidate_path,
                batch_rows,
            ),
        )
        write_jsonl(
            cleaned_dictionary_candidate_path,
            cleaned_dictionary_outputs.rows,
        )
        if artifact_callback is not None:
            artifact_callback(
                "11_cleaned_dictionary_candidates",
                cleaned_dictionary_outputs.rows,
            )

    linked_dictionary_outputs = write_linked_dictionary_candidates_from_saved(
        output_dir,
        artifact_callback=artifact_callback,
        require_inputs=False,
    )
    refresh_saved_output_metadata(
        output_dir,
        cleaned_example_outputs=cleaned_example_outputs,
        cleaned_dictionary_outputs=cleaned_dictionary_outputs,
        linked_dictionary_outputs=linked_dictionary_outputs,
    )
    return cleaned_example_outputs, cleaned_dictionary_outputs, linked_dictionary_outputs


def cleanup_previous_outputs(
    output_dir: Path,
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
) -> None:
    for filename in {
        *BASE_OUTPUT_FILES,
        *(["10_cleaned_example_sentences.csv"] if include_cleaned_example_sentences else []),
        *(["11_cleaned_dictionary_candidates.jsonl"] if include_cleaned_dictionary_candidates else []),
        *(
            ["12_dictionary_candidates_with_cleaned_examples.jsonl"]
            if include_linked_dictionary_candidates
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


def resolve_dictionary_support_rows(
    output_dir: Path,
    *,
    cleaned_example_outputs: CleanedExampleOutputs | None,
    loaded_example_sentences: list[dict[str, str]] | None,
) -> list[Mapping[str, object]]:
    if cleaned_example_outputs is not None:
        return cleaned_example_outputs.rows

    cleaned_example_path = output_dir / "10_cleaned_example_sentences.csv"
    if cleaned_example_path.is_file():
        return read_rows(cleaned_example_path)

    if loaded_example_sentences is not None:
        return loaded_example_sentences

    return read_required_rows(
        output_dir / "09_example_sentences.csv",
        built_from="09_example_sentences.csv",
        target="11_cleaned_dictionary_candidates.jsonl",
    )


def write_linked_dictionary_candidates_from_saved(
    output_dir: Path,
    *,
    artifact_callback: ArtifactCallback | None = None,
    require_inputs: bool = True,
) -> LinkedDictionaryOutputs | None:
    linked_output_path = output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl"
    cleaned_example_path = output_dir / "10_cleaned_example_sentences.csv"
    cleaned_dictionary_path = output_dir / "11_cleaned_dictionary_candidates.jsonl"

    if not cleaned_example_path.is_file() or not cleaned_dictionary_path.is_file():
        if linked_output_path.is_file():
            linked_output_path.unlink()
        if require_inputs:
            missing = []
            if not cleaned_example_path.is_file():
                missing.append("10_cleaned_example_sentences.csv")
            if not cleaned_dictionary_path.is_file():
                missing.append("11_cleaned_dictionary_candidates.jsonl")
            raise ValueError(
                f"Cannot build 12_dictionary_candidates_with_cleaned_examples.jsonl because "
                f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing in {output_dir}. "
                "Run the saved cleanup stages first."
            )
        return None

    cleaned_example_rows = read_rows(cleaned_example_path)
    cleaned_dictionary_rows = read_jsonl(cleaned_dictionary_path)
    linked_outputs = build_linked_dictionary_candidate_outputs(
        cleaned_dictionary_rows,
        cleaned_example_rows,
    )
    write_jsonl(linked_output_path, linked_outputs.rows)
    if artifact_callback is not None:
        artifact_callback(
            "12_dictionary_candidates_with_cleaned_examples",
            linked_outputs.rows,
        )
    return linked_outputs


def refresh_saved_output_metadata(
    output_dir: Path,
    *,
    cleaned_example_outputs: CleanedExampleOutputs | None = None,
    cleaned_dictionary_outputs: CleanedDictionaryOutputs | None = None,
    linked_dictionary_outputs: LinkedDictionaryOutputs | None = None,
) -> None:
    include_cleaned_example_sentences = (output_dir / "10_cleaned_example_sentences.csv").is_file()
    include_cleaned_dictionary_candidates = (output_dir / "11_cleaned_dictionary_candidates.jsonl").is_file()
    include_linked_dictionary_candidates = (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").is_file()

    summary_path = output_dir / "00_summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    summary["outputs"] = all_output_files(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
    )
    summary["pipeline_stages"] = pipeline_stages(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
    )

    if cleaned_example_outputs is not None:
        summary["reviewed_example_sentences"] = cleaned_example_outputs.reviewed_count
        summary["cleaned_example_sentences"] = len(cleaned_example_outputs.rows)
        summary["rejected_cleaned_example_sentences"] = cleaned_example_outputs.rejected_count
        summary["cleaned_example_sentence_actions"] = dict(cleaned_example_outputs.action_counts)
    elif not include_cleaned_example_sentences:
        for key in (
            "reviewed_example_sentences",
            "cleaned_example_sentences",
            "rejected_cleaned_example_sentences",
            "cleaned_example_sentence_actions",
        ):
            summary.pop(key, None)

    if cleaned_dictionary_outputs is not None:
        summary["reviewed_dictionary_candidates"] = cleaned_dictionary_outputs.reviewed_count
        summary["cleaned_dictionary_candidates"] = len(cleaned_dictionary_outputs.rows)
        summary["rejected_cleaned_dictionary_candidates"] = cleaned_dictionary_outputs.rejected_count
        summary["cleaned_dictionary_candidate_actions"] = dict(cleaned_dictionary_outputs.action_counts)
        summary["cleaned_dictionary_candidate_reasons"] = dict(cleaned_dictionary_outputs.rejection_reason_counts)
    elif not include_cleaned_dictionary_candidates:
        for key in (
            "reviewed_dictionary_candidates",
            "cleaned_dictionary_candidates",
            "rejected_cleaned_dictionary_candidates",
            "cleaned_dictionary_candidate_actions",
            "cleaned_dictionary_candidate_reasons",
        ):
            summary.pop(key, None)

    if linked_dictionary_outputs is not None:
        summary["linked_dictionary_candidates"] = len(linked_dictionary_outputs.rows)
        summary["linked_cleaned_example_sentences"] = linked_dictionary_outputs.linked_count
        summary["unlinked_dictionary_candidates"] = linked_dictionary_outputs.unlinked_count
    elif not include_linked_dictionary_candidates:
        for key in (
            "linked_dictionary_candidates",
            "linked_cleaned_example_sentences",
            "unlinked_dictionary_candidates",
        ):
            summary.pop(key, None)

    if summary or summary_path.is_file():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_output_readme(
        output_dir / "README.md",
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
    )


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


def example_sentence_rows(
    sentences: list[dict[str, object]],
    tokens: list[dict[str, object]],
    candidate_terms: list[dict[str, object]],
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

    filtered_rows = sort_example_sentence_rows(normalize_example_sentence_quality_scores(filtered_example_sentence_rows(rows)))
    return filtered_high_signal_example_sentence_rows(filtered_rows)


def filtered_example_sentence_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    filtered_rows: list[dict[str, object]] = []
    seen_matched_key_sets: set[str] = set()
    for row in rows:
        covered_count = int(row["covered_canonical_key_count"])
        if (
            covered_count < EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM
            or covered_count > EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM
        ):
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


def example_sentence_sort_score(row: dict[str, object]) -> float:
    matched_count = int(row["matched_canonical_key_count"])
    if matched_count <= 0:
        return 0.0
    return float(row["quality_score"]) / matched_count


def filtered_high_signal_example_sentence_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [row for row in rows if is_high_signal_example_sentence_row(row)]


def is_high_signal_example_sentence_row(row: dict[str, object]) -> bool:
    return (
        example_sentence_sort_score(row) >= FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM
        and has_example_sentence_terminal_punctuation(row)
        and not ends_with_bopsida(row)
    )


def has_example_sentence_terminal_punctuation(row: dict[str, object]) -> bool:
    sentence = str(row["sentence"]).strip()
    return bool(sentence) and sentence.endswith(EXAMPLE_SENTENCE_FINAL_PUNCTUATION)


def ends_with_bopsida(row: dict[str, object]) -> bool:
    sentence = str(row["sentence"]).strip().rstrip('"').strip()
    return sentence.endswith("봅시다.")


def sentence_row_key(row: dict[str, object]) -> tuple[str, str, str]:
    return (str(row.get("document", "")), str(row.get("page", "")), str(row.get("sentence_index", "")))


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
    example_sentence_count: int,
    document_totals: Counter[str],
    documents: list[Path],
    cleaned_example_outputs: CleanedExampleOutputs | None = None,
    cleaned_dictionary_outputs: CleanedDictionaryOutputs | None = None,
) -> None:
    include_cleaned_example_sentences = cleaned_example_outputs is not None
    include_cleaned_dictionary_candidates = cleaned_dictionary_outputs is not None
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
        "example_sentences": example_sentence_count,
        "tokens_by_document": dict(document_totals),
        "outputs": all_output_files(
            include_cleaned_example_sentences=include_cleaned_example_sentences,
            include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        ),
        "pipeline_stages": pipeline_stages(
            include_cleaned_example_sentences=include_cleaned_example_sentences,
            include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        ),
    }
    if cleaned_example_outputs is not None:
        summary["reviewed_example_sentences"] = cleaned_example_outputs.reviewed_count
        summary["cleaned_example_sentences"] = len(cleaned_example_outputs.rows)
        summary["rejected_cleaned_example_sentences"] = cleaned_example_outputs.rejected_count
        summary["cleaned_example_sentence_actions"] = dict(cleaned_example_outputs.action_counts)
    if cleaned_dictionary_outputs is not None:
        summary["reviewed_dictionary_candidates"] = cleaned_dictionary_outputs.reviewed_count
        summary["cleaned_dictionary_candidates"] = len(cleaned_dictionary_outputs.rows)
        summary["rejected_cleaned_dictionary_candidates"] = cleaned_dictionary_outputs.rejected_count
        summary["cleaned_dictionary_candidate_actions"] = dict(cleaned_dictionary_outputs.action_counts)
        summary["cleaned_dictionary_candidate_reasons"] = dict(cleaned_dictionary_outputs.rejection_reason_counts)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_output_readme(
    path: Path,
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
) -> None:
    lines = [
        "# wordbyword output",
        "",
        "Read these files in numeric order to follow the pipeline from source text to vocabulary candidates.",
        "",
        "| Stage | File | Purpose |",
        "| --- | --- | --- |",
    ]
    for stage in pipeline_stages(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
    ):
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


def all_output_files(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
) -> list[str]:
    return [
        *BASE_OUTPUT_FILES,
        *(
            ["10_cleaned_example_sentences.csv"]
            if include_cleaned_example_sentences
            else []
        ),
        *(
            ["11_cleaned_dictionary_candidates.jsonl"]
            if include_cleaned_dictionary_candidates
            else []
        ),
        *(
            ["12_dictionary_candidates_with_cleaned_examples.jsonl"]
            if include_linked_dictionary_candidates
            else []
        ),
    ]


def pipeline_stages(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
) -> list[dict[str, str]]:
    return [
        *BASE_PIPELINE_STAGES,
        *(
            [OPTIONAL_PIPELINE_STAGES[0]]
            if include_cleaned_example_sentences
            else []
        ),
        *(
            [OPTIONAL_PIPELINE_STAGES[1]]
            if include_cleaned_dictionary_candidates
            else []
        ),
        *(
            [OPTIONAL_PIPELINE_STAGES[2]]
            if include_linked_dictionary_candidates
            else []
        ),
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
