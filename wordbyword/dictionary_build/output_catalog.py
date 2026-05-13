from __future__ import annotations

from wordbyword.dictionary_build.artifacts import (
    BASE_ARTIFACT_IDS,
    LEGACY_OUTPUT_FILES as _LEGACY_OUTPUT_FILES,
    OPTIONAL_ARTIFACT_IDS,
    artifact_filename,
    output_filenames,
    pipeline_stage_rows,
)

BASE_OUTPUT_FILES = [artifact_filename(artifact_id) for artifact_id in BASE_ARTIFACT_IDS]
OPTIONAL_OUTPUT_FILES = [artifact_filename(artifact_id) for artifact_id in OPTIONAL_ARTIFACT_IDS]
LEGACY_OUTPUT_FILES = list(_LEGACY_OUTPUT_FILES)

BASE_PIPELINE_STAGES = pipeline_stage_rows()
OPTIONAL_PIPELINE_STAGES = pipeline_stage_rows(
    include_cleaned_example_sentences=True,
    include_cleaned_dictionary_candidates=True,
    include_linked_dictionary_candidates=True,
    include_generated_dictionary_candidates=True,
    include_meaning_dictionary_candidates=True,
    include_generated_meaning_dictionary_candidates=True,
)[len(BASE_PIPELINE_STAGES) :]


def all_output_files(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_generated_example_attempts: bool | None = None,
    include_generated_example_failures: bool | None = None,
    include_meaning_dictionary_candidates: bool = False,
    include_generated_meaning_dictionary_candidates: bool = False,
) -> list[str]:
    return output_filenames(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
        include_generated_dictionary_candidates=include_generated_dictionary_candidates,
        include_generated_example_attempts=include_generated_example_attempts,
        include_generated_example_failures=include_generated_example_failures,
        include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
        include_generated_meaning_dictionary_candidates=include_generated_meaning_dictionary_candidates,
    )


def pipeline_stages(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_meaning_dictionary_candidates: bool = False,
    include_generated_meaning_dictionary_candidates: bool = False,
) -> list[dict[str, str]]:
    return pipeline_stage_rows(
        include_cleaned_example_sentences=include_cleaned_example_sentences,
        include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
        include_linked_dictionary_candidates=include_linked_dictionary_candidates,
        include_generated_dictionary_candidates=include_generated_dictionary_candidates,
        include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
        include_generated_meaning_dictionary_candidates=include_generated_meaning_dictionary_candidates,
    )
