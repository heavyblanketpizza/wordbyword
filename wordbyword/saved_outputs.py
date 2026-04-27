from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from wordbyword.dictionary_cleanup import CleanedDictionaryOutputs, build_cleaned_dictionary_candidate_outputs
from wordbyword.example_assignment import LinkedDictionaryOutputs, build_linked_dictionary_candidate_outputs
from wordbyword.example_cleanup import (
    CleanedExampleOutputs,
    CleanupProgressCallback,
    build_cleaned_example_sentence_outputs,
    cleaned_example_sentence_fields,
)
from wordbyword.llm import SupportsCompletion
from wordbyword.output_files import (
    append_jsonl,
    append_rows,
    initialize_jsonl_file,
    initialize_rows_file,
    read_jsonl,
    read_required_jsonl,
    read_required_rows,
    read_rows,
    write_jsonl,
    write_rows,
)
from wordbyword.output_metadata import refresh_saved_output_metadata

ArtifactCallback = Callable[[str, Sequence[Mapping[str, object]]], None]


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
        write_jsonl(cleaned_dictionary_candidate_path, cleaned_dictionary_outputs.rows)
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
