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
from wordbyword.example_generation import (
    GeneratedExampleAttemptSummary,
    GeneratedExampleBatchSnapshot,
    GeneratedExampleOutputs,
    RetryGeneratedExampleCallback,
    build_generated_dictionary_candidate_outputs,
    retry_generated_dictionary_candidate_outputs,
)
from wordbyword.llm import SupportsCompletion
from wordbyword.meaning_assignment import (
    MeaningAssignmentOutputs,
    build_dictionary_candidates_with_meanings,
)
from wordbyword.meaning_generation import (
    GeneratedMeaningOutputs,
    build_generated_meaning_dictionary_outputs,
)
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
    generated_example_client: SupportsCompletion | None = None,
    generated_example_progress: CleanupProgressCallback | None = None,
    generated_example_retry_decider: RetryGeneratedExampleCallback | None = None,
    retry_generated_examples_only: bool = False,
    artifact_callback: ArtifactCallback | None = None,
) -> tuple[
    CleanedExampleOutputs | None,
    CleanedDictionaryOutputs | None,
    LinkedDictionaryOutputs | None,
    GeneratedExampleOutputs | None,
]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_example_outputs: CleanedExampleOutputs | None = None
    cleaned_dictionary_outputs: CleanedDictionaryOutputs | None = None
    linked_dictionary_outputs: LinkedDictionaryOutputs | None = None
    generated_example_outputs: GeneratedExampleOutputs | None = None

    if (
        sentence_cleanup_client is None
        and dictionary_cleanup_client is None
        and generated_example_client is None
    ):
        linked_dictionary_outputs = write_linked_dictionary_candidates_from_saved(
            output_dir,
            artifact_callback=artifact_callback,
            require_inputs=False,
        )
        refresh_saved_output_metadata(output_dir, linked_dictionary_outputs=linked_dictionary_outputs)
        return None, None, linked_dictionary_outputs, None

    candidate_terms: list[dict[str, str]] | None = None
    if (
        sentence_cleanup_client is not None
        or dictionary_cleanup_client is not None
        or generated_example_client is not None
    ):
        candidate_terms = read_required_rows(
            output_dir / "06_candidate_terms.csv",
            built_from="06_candidate_terms.csv",
            target="LLM cleanup stages",
        )
    example_sentences: list[dict[str, str]] | None = None

    if sentence_cleanup_client is not None:
        assert candidate_terms is not None
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
        assert candidate_terms is not None
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

    if (
        not retry_generated_examples_only
        and
        (output_dir / "10_cleaned_example_sentences.csv").is_file()
        and (output_dir / "11_cleaned_dictionary_candidates.jsonl").is_file()
    ):
        linked_dictionary_outputs = write_linked_dictionary_candidates_from_saved(
            output_dir,
            artifact_callback=artifact_callback,
            require_inputs=False,
        )
    if generated_example_client is not None:
        assert candidate_terms is not None
        if retry_generated_examples_only:
            generated_example_outputs = retry_generated_dictionary_candidates_from_saved(
                output_dir,
                generated_example_client=generated_example_client,
                generated_example_progress=generated_example_progress,
                artifact_callback=artifact_callback,
                candidate_terms=candidate_terms,
            )
        else:
            generated_example_outputs = write_generated_dictionary_candidates_from_saved(
                output_dir,
                generated_example_client=generated_example_client,
                generated_example_progress=generated_example_progress,
                generated_example_retry_decider=generated_example_retry_decider,
                artifact_callback=artifact_callback,
                linked_dictionary_outputs=linked_dictionary_outputs,
                candidate_terms=candidate_terms,
            )
    refresh_saved_output_metadata(
        output_dir,
        cleaned_example_outputs=cleaned_example_outputs,
        cleaned_dictionary_outputs=cleaned_dictionary_outputs,
        linked_dictionary_outputs=linked_dictionary_outputs,
        generated_example_outputs=generated_example_outputs,
    )
    return (
        cleaned_example_outputs,
        cleaned_dictionary_outputs,
        linked_dictionary_outputs,
        generated_example_outputs,
    )


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


def write_generated_dictionary_candidates_from_saved(
    output_dir: Path,
    *,
    generated_example_client: SupportsCompletion,
    generated_example_progress: CleanupProgressCallback | None = None,
    generated_example_retry_decider: RetryGeneratedExampleCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
    linked_dictionary_outputs: LinkedDictionaryOutputs | None = None,
    candidate_terms: Sequence[Mapping[str, object]] | None = None,
) -> GeneratedExampleOutputs:
    linked_output_path = output_dir / "12_dictionary_candidates_with_cleaned_examples.jsonl"
    generated_output_path = output_dir / "13_dictionary_candidates_with_generated_examples.jsonl"
    generated_attempts_path = output_dir / "13_generated_example_attempts.jsonl"
    generated_failures_path = output_dir / "13_generated_example_failures.jsonl"

    if linked_dictionary_outputs is not None:
        linked_rows = linked_dictionary_outputs.rows
    elif linked_output_path.is_file():
        linked_rows = read_jsonl(linked_output_path)
    else:
        rebuilt_linked_outputs = write_linked_dictionary_candidates_from_saved(
            output_dir,
            artifact_callback=artifact_callback,
            require_inputs=True,
        )
        assert rebuilt_linked_outputs is not None
        linked_rows = rebuilt_linked_outputs.rows

    resolved_candidate_terms = (
        list(candidate_terms)
        if candidate_terms is not None
        else read_required_rows(
            output_dir / "06_candidate_terms.csv",
            built_from="06_candidate_terms.csv",
            target="13_dictionary_candidates_with_generated_examples.jsonl",
        )
    )

    initialize_jsonl_file(generated_output_path)
    initialize_jsonl_file(generated_attempts_path)
    initialize_jsonl_file(generated_failures_path)
    unresolved_failure_rows: dict[str, dict[str, object]] = {}
    write_jsonl(
        generated_output_path,
        [
            {**dict(row), "example_generated_with_ollama": False}
            for row in linked_rows
        ],
    )

    def on_batch_complete(snapshot: GeneratedExampleBatchSnapshot) -> None:
        append_jsonl(generated_attempts_path, snapshot.summary.attempt_rows)
        for row_id in snapshot.summary.succeeded_row_ids:
            unresolved_failure_rows.pop(row_id, None)
        for failure_row in snapshot.summary.failure_rows:
            unresolved_failure_rows[str(failure_row["row_id"])] = failure_row
        write_jsonl(generated_output_path, snapshot.rows)
        write_jsonl(generated_failures_path, list(unresolved_failure_rows.values()))

    def on_attempt_complete(summary: GeneratedExampleAttemptSummary) -> bool:
        if summary.failed_count <= 0 or generated_example_retry_decider is None:
            return False
        return generated_example_retry_decider(summary)

    generated_outputs = build_generated_dictionary_candidate_outputs(
        linked_rows,
        resolved_candidate_terms,
        client=generated_example_client,
        on_progress=generated_example_progress,
        on_batch_complete=on_batch_complete,
        on_attempt_complete=on_attempt_complete,
    )
    write_jsonl(generated_output_path, generated_outputs.rows)
    write_jsonl(generated_failures_path, generated_outputs.failure_rows)
    if artifact_callback is not None:
        artifact_callback(
            "13_dictionary_candidates_with_generated_examples",
            generated_outputs.rows,
        )
    return generated_outputs


def retry_generated_dictionary_candidates_from_saved(
    output_dir: Path,
    *,
    generated_example_client: SupportsCompletion,
    generated_example_progress: CleanupProgressCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
    candidate_terms: Sequence[Mapping[str, object]] | None = None,
) -> GeneratedExampleOutputs:
    generated_output_path = output_dir / "13_dictionary_candidates_with_generated_examples.jsonl"
    generated_attempts_path = output_dir / "13_generated_example_attempts.jsonl"
    generated_failures_path = output_dir / "13_generated_example_failures.jsonl"

    generated_rows = read_required_jsonl(
        generated_output_path,
        built_from="13_dictionary_candidates_with_generated_examples.jsonl",
        target="retry-generated-examples",
    )
    failure_rows = (
        read_jsonl(generated_failures_path)
        if generated_failures_path.is_file()
        else []
    )

    resolved_candidate_terms = (
        list(candidate_terms)
        if candidate_terms is not None
        else read_required_rows(
            output_dir / "06_candidate_terms.csv",
            built_from="06_candidate_terms.csv",
            target="retry-generated-examples",
        )
    )

    if not failure_rows:
        generated_outputs = retry_generated_dictionary_candidate_outputs(
            generated_rows,
            [],
            resolved_candidate_terms,
            client=generated_example_client,
            starting_attempt_number=_next_generated_example_attempt_number(output_dir),
            on_progress=generated_example_progress,
        )
        write_jsonl(generated_output_path, generated_outputs.rows)
        initialize_jsonl_file(generated_failures_path)
        if artifact_callback is not None:
            artifact_callback(
                "13_dictionary_candidates_with_generated_examples",
                generated_outputs.rows,
            )
        return generated_outputs

    unresolved_failure_rows = {
        str(row["row_id"]): dict(row)
        for row in failure_rows
        if row.get("row_id")
    }

    def on_batch_complete(snapshot: GeneratedExampleBatchSnapshot) -> None:
        append_jsonl(generated_attempts_path, snapshot.summary.attempt_rows)
        for row_id in snapshot.summary.succeeded_row_ids:
            unresolved_failure_rows.pop(row_id, None)
        for failure_row in snapshot.summary.failure_rows:
            unresolved_failure_rows[str(failure_row["row_id"])] = failure_row
        write_jsonl(generated_output_path, snapshot.rows)
        write_jsonl(generated_failures_path, list(unresolved_failure_rows.values()))

    generated_outputs = retry_generated_dictionary_candidate_outputs(
        generated_rows,
        failure_rows,
        resolved_candidate_terms,
        client=generated_example_client,
        starting_attempt_number=_next_generated_example_attempt_number(output_dir),
        on_progress=generated_example_progress,
        on_batch_complete=on_batch_complete,
    )
    write_jsonl(generated_output_path, generated_outputs.rows)
    write_jsonl(generated_failures_path, generated_outputs.failure_rows)
    if artifact_callback is not None:
        artifact_callback(
            "13_dictionary_candidates_with_generated_examples",
            generated_outputs.rows,
        )
    return generated_outputs


def write_meaning_dictionary_candidates_from_saved(
    output_dir: Path,
    *,
    reference_dictionary_path: Path,
    artifact_callback: ArtifactCallback | None = None,
) -> MeaningAssignmentOutputs:
    generated_output_path = output_dir / "13_dictionary_candidates_with_generated_examples.jsonl"
    meaning_output_path = output_dir / "14_dictionary_candidates_with_meanings.jsonl"

    generated_rows = read_required_jsonl(
        generated_output_path,
        built_from="13_dictionary_candidates_with_generated_examples.jsonl",
        target="14_dictionary_candidates_with_meanings.jsonl",
    )
    reference_rows = read_required_jsonl(
        reference_dictionary_path,
        built_from=str(reference_dictionary_path),
        target="14_dictionary_candidates_with_meanings.jsonl",
    )

    remove_stage_thirteen_sidecars_before_meaning_assignment(output_dir)
    generated_meaning_output_path = output_dir / "15_dictionary_candidates_with_generated_meanings.jsonl"
    if generated_meaning_output_path.is_file():
        generated_meaning_output_path.unlink()
    initialize_jsonl_file(meaning_output_path)
    meaning_outputs = build_dictionary_candidates_with_meanings(
        generated_rows,
        reference_rows,
        on_row=lambda row: append_jsonl(meaning_output_path, [row]),
    )
    write_jsonl(meaning_output_path, meaning_outputs.rows)
    if artifact_callback is not None:
        artifact_callback(
            "14_dictionary_candidates_with_meanings",
            meaning_outputs.rows,
        )
    refresh_saved_output_metadata(
        output_dir,
        meaning_assignment_outputs=meaning_outputs,
    )
    return meaning_outputs


def write_generated_meaning_dictionary_candidates_from_saved(
    output_dir: Path,
    *,
    generated_meaning_client: SupportsCompletion,
    generated_meaning_progress: CleanupProgressCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
) -> GeneratedMeaningOutputs:
    meaning_output_path = output_dir / "14_dictionary_candidates_with_meanings.jsonl"
    generated_meaning_output_path = output_dir / "15_dictionary_candidates_with_generated_meanings.jsonl"

    meaning_rows = read_required_jsonl(
        meaning_output_path,
        built_from="14_dictionary_candidates_with_meanings.jsonl",
        target="15_dictionary_candidates_with_generated_meanings.jsonl",
    )
    initialize_jsonl_file(generated_meaning_output_path)
    write_jsonl(
        generated_meaning_output_path,
        [
            {
                **dict(row),
                "curation_status": "confirmed",
                "meaning": str(row.get("meaning", "") or ""),
                "meaning_generated_with_ollama": False,
            }
            for row in meaning_rows
        ],
    )

    generated_meaning_outputs = build_generated_meaning_dictionary_outputs(
        meaning_rows,
        client=generated_meaning_client,
        on_progress=generated_meaning_progress,
        on_batch_complete=lambda snapshot: write_jsonl(
            generated_meaning_output_path,
            snapshot.rows,
        ),
    )
    write_jsonl(generated_meaning_output_path, generated_meaning_outputs.rows)
    if artifact_callback is not None:
        artifact_callback(
            "15_dictionary_candidates_with_generated_meanings",
            generated_meaning_outputs.rows,
        )
    refresh_saved_output_metadata(
        output_dir,
        generated_meaning_outputs=generated_meaning_outputs,
    )
    return generated_meaning_outputs


def remove_stage_thirteen_sidecars_before_meaning_assignment(output_dir: Path) -> None:
    generated_attempts_path = output_dir / "13_generated_example_attempts.jsonl"
    if generated_attempts_path.is_file():
        generated_attempts_path.unlink()
    remove_empty_generated_example_failures(output_dir)


def remove_empty_generated_example_failures(output_dir: Path) -> bool:
    generated_failures_path = output_dir / "13_generated_example_failures.jsonl"
    if not generated_failures_path.is_file():
        return False
    if generated_failures_path.read_text(encoding="utf-8").strip():
        return False
    generated_failures_path.unlink()
    return True


def _next_generated_example_attempt_number(output_dir: Path) -> int:
    attempts_path = output_dir / "13_generated_example_attempts.jsonl"
    if not attempts_path.is_file():
        return 2

    max_attempt_number = 1
    for row in read_jsonl(attempts_path):
        try:
            attempt_number = int(row.get("attempt_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if attempt_number > max_attempt_number:
            max_attempt_number = attempt_number
    return max_attempt_number + 1
