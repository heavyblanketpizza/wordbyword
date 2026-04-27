"""Compatibility layer for output-writing helpers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from kiwipiepy import Kiwi

from wordbyword.candidates import build_candidate_outputs
from wordbyword.dictionary_cleanup import CleanedDictionaryOutputs, build_cleaned_dictionary_candidate_outputs
from wordbyword.example_assignment import LinkedDictionaryOutputs
from wordbyword.example_cleanup import (
    CleanedExampleOutputs,
    CleanupProgressCallback,
    build_cleaned_example_sentence_outputs,
    cleaned_example_sentence_fields,
)
from wordbyword.example_sentences import (
    EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM,
    EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM,
    FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM,
    ends_with_bopsida,
    example_sentence_rows,
    example_sentence_sort_score,
    filtered_example_sentence_rows,
    filtered_high_signal_example_sentence_rows,
)
from wordbyword.llm import SupportsCompletion
from wordbyword.models import CountEntry, PageText
from wordbyword.output_catalog import (
    BASE_OUTPUT_FILES,
    BASE_PIPELINE_STAGES,
    LEGACY_OUTPUT_FILES,
    OPTIONAL_OUTPUT_FILES,
    OPTIONAL_PIPELINE_STAGES,
    all_output_files,
    pipeline_stages,
)
from wordbyword.output_files import (
    append_jsonl,
    append_rows,
    candidate_term_fields,
    cleanup_previous_outputs,
    example_sentence_fields,
    initialize_jsonl_file,
    initialize_rows_file,
    page_fields,
    page_rows,
    parsed_document_count,
    read_jsonl,
    read_required_jsonl,
    read_required_rows,
    read_rows,
    rejected_candidate_fields,
    sentence_fields,
    token_fields,
    write_jsonl,
    write_rows,
    write_surface_words,
    write_vocabulary,
)
from wordbyword.output_metadata import (
    refresh_saved_output_metadata,
    write_output_readme,
    write_summary,
)
from wordbyword.saved_outputs import (
    resolve_dictionary_support_rows,
    write_cleanup_outputs_from_saved,
    write_linked_dictionary_candidates_from_saved,
)

ArtifactCallback = Callable[[str, Sequence[Mapping[str, object]]], None]

__all__ = [
    "ArtifactCallback",
    "BASE_OUTPUT_FILES",
    "BASE_PIPELINE_STAGES",
    "EXAMPLE_SENTENCE_COVERED_KEY_MAXIMUM",
    "EXAMPLE_SENTENCE_COVERED_KEY_MINIMUM",
    "FILTERED_EXAMPLE_SENTENCE_DENSITY_MINIMUM",
    "LEGACY_OUTPUT_FILES",
    "OPTIONAL_OUTPUT_FILES",
    "OPTIONAL_PIPELINE_STAGES",
    "all_output_files",
    "append_jsonl",
    "append_rows",
    "candidate_term_fields",
    "cleanup_previous_outputs",
    "ends_with_bopsida",
    "example_sentence_fields",
    "example_sentence_rows",
    "example_sentence_sort_score",
    "filtered_example_sentence_rows",
    "filtered_high_signal_example_sentence_rows",
    "initialize_jsonl_file",
    "initialize_rows_file",
    "page_fields",
    "page_rows",
    "parsed_document_count",
    "pipeline_stages",
    "read_jsonl",
    "read_required_jsonl",
    "read_required_rows",
    "read_rows",
    "refresh_saved_output_metadata",
    "rejected_candidate_fields",
    "resolve_dictionary_support_rows",
    "sentence_fields",
    "token_fields",
    "write_cleanup_outputs_from_saved",
    "write_jsonl",
    "write_linked_dictionary_candidates_from_saved",
    "write_output_readme",
    "write_outputs",
    "write_rows",
    "write_summary",
    "write_surface_words",
    "write_vocabulary",
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
        write_jsonl(cleaned_dictionary_candidate_path, cleaned_dictionary_outputs.rows)
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
