from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from wordbyword.dictionary_cleanup import CleanedDictionaryOutputs
from wordbyword.example_assignment import LinkedDictionaryOutputs
from wordbyword.example_cleanup import CleanedExampleOutputs
from wordbyword.example_generation import GeneratedExampleOutputs
from wordbyword.meaning_assignment import MeaningAssignmentOutputs
from wordbyword.models import CountEntry, PageText
from wordbyword.output_catalog import all_output_files, pipeline_stages


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
    include_generated_dictionary_candidates: bool = False,
    include_meaning_dictionary_candidates: bool = False,
) -> None:
    lines = [
        "# wordbyword build-dictionary",
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
        include_generated_dictionary_candidates=include_generated_dictionary_candidates,
        include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
    ):
        lines.append(f"| {stage['stage']} | `{stage['file']}` | {stage['description']} |")

    lines.extend(
        [
            "",
            "When artifact quality looks wrong, start at `01_extracted_pages.csv` and move forward.",
            "The first file where the text looks wrong usually points to the stage that needs work.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def refresh_saved_output_metadata(
    output_dir: Path,
    *,
    cleaned_example_outputs: CleanedExampleOutputs | None = None,
    cleaned_dictionary_outputs: CleanedDictionaryOutputs | None = None,
    linked_dictionary_outputs: LinkedDictionaryOutputs | None = None,
    generated_example_outputs: GeneratedExampleOutputs | None = None,
    meaning_assignment_outputs: MeaningAssignmentOutputs | None = None,
) -> None:
    include_cleaned_example_sentences = (output_dir / "10_cleaned_example_sentences.csv").is_file()
    include_cleaned_dictionary_candidates = (output_dir / "11_cleaned_dictionary_candidates.jsonl").is_file()
    include_linked_dictionary_candidates = (output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl").is_file()
    include_generated_dictionary_candidates = (output_dir / "13_dictionary_candidates_with_generated_examples.jsonl").is_file()
    include_generated_example_attempts = (output_dir / "13_generated_example_attempts.jsonl").is_file()
    include_generated_example_failures = (output_dir / "13_generated_example_failures.jsonl").is_file()
    include_meaning_dictionary_candidates = (output_dir / "14_dictionary_candidates_with_meanings.jsonl").is_file()

    summary_path = output_dir / "00_summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    summary["outputs"] = all_output_files(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
        include_generated_dictionary_candidates=include_generated_dictionary_candidates,
        include_generated_example_attempts=include_generated_example_attempts,
        include_generated_example_failures=include_generated_example_failures,
        include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
    )
    summary["pipeline_stages"] = pipeline_stages(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
        include_generated_dictionary_candidates=include_generated_dictionary_candidates,
        include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
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

    if generated_example_outputs is not None:
        summary["reviewed_missing_dictionary_examples"] = generated_example_outputs.reviewed_count
        summary["generated_dictionary_candidate_examples"] = generated_example_outputs.generated_count
        summary["preserved_dictionary_candidate_examples"] = generated_example_outputs.preserved_count
        summary["failed_dictionary_candidate_examples"] = generated_example_outputs.failed_count
        summary["generated_example_attempt_rounds"] = generated_example_outputs.attempt_round_count
    elif not include_generated_dictionary_candidates:
        for key in (
            "reviewed_missing_dictionary_examples",
            "generated_dictionary_candidate_examples",
            "preserved_dictionary_candidate_examples",
            "failed_dictionary_candidate_examples",
            "generated_example_attempt_rounds",
        ):
            summary.pop(key, None)

    if meaning_assignment_outputs is not None:
        summary["dictionary_candidate_meaning_rows"] = len(meaning_assignment_outputs.rows)
        summary["matched_reference_dictionary_meanings"] = meaning_assignment_outputs.matched_count
        summary["missing_dictionary_candidate_meanings"] = meaning_assignment_outputs.unmatched_count
    elif not include_meaning_dictionary_candidates:
        for key in (
            "dictionary_candidate_meaning_rows",
            "matched_reference_dictionary_meanings",
            "missing_dictionary_candidate_meanings",
        ):
            summary.pop(key, None)

    if summary or summary_path.is_file():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_output_readme(
        output_dir / "README.md",
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
        include_generated_dictionary_candidates=include_generated_dictionary_candidates,
        include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
    )
