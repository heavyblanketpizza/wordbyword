from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupProgressCallback
from wordbyword.dictionary_build.example_generation import RetryGeneratedExampleCallback
from wordbyword.dictionary_build.llm import SupportsCompletion
from wordbyword.dictionary_build.recovery import (
    ArtifactCallback,
    run_saved_dictionary_cleanup,
)

ProgressCallback = Callable[[str, str], None]
DEFAULT_SHARED_CLEANUP_TIMEOUT_SECONDS = 300.0
DEFAULT_SHARED_CLEANUP_MAX_OUTPUT_TOKENS = 256


@dataclass(frozen=True)
class PipelineResult:
    documents: int
    extracted_pages: int
    sentences: int
    token_occurrences: int
    vocabulary_terms: int
    surface_words: int
    output_dir: Path


class PipelineUsageError(RuntimeError):
    """Raised when a requested pipeline stage is missing its saved prerequisites."""


def run_pipeline(
    *,
    input_dir: Path,
    output_dir: Path,
    include_punctuation: bool = False,
    include_numbers: bool = False,
    clean_example_sentences: bool = False,
    clean_dictionary_candidates: bool = False,
    generate_missing_examples: bool = False,
    retry_generated_examples: bool = False,
    sentence_cleanup_client: SupportsCompletion | None = None,
    dictionary_cleanup_client: SupportsCompletion | None = None,
    generated_example_client: SupportsCompletion | None = None,
    sentence_cleanup_model: str = DEFAULT_EXAMPLE_CLEANUP_MODEL,
    sentence_cleanup_base_url: str | None = None,
    sentence_cleanup_provider: str = "ollama",
    progress: ProgressCallback | None = None,
    cleanup_progress: CleanupProgressCallback | None = None,
    dictionary_cleanup_progress: CleanupProgressCallback | None = None,
    generated_example_progress: CleanupProgressCallback | None = None,
    generated_example_retry_decider: RetryGeneratedExampleCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
) -> PipelineResult:
    """Deprecated compatibility wrapper.

    Use build_resumable_dictionary(..., target_checkpoint="09") for deterministic
    prep builds, or write_cleanup_outputs_from_saved() for saved-stage recovery.
    """
    example_cleanup_requested = clean_example_sentences or sentence_cleanup_client is not None
    dictionary_cleanup_requested = clean_dictionary_candidates or dictionary_cleanup_client is not None
    generated_example_requested = (
        generate_missing_examples
        or retry_generated_examples
        or generated_example_client is not None
    )
    retry_generated_example_requested = retry_generated_examples
    cleanup_requested = (
        example_cleanup_requested
        or dictionary_cleanup_requested
        or generated_example_requested
    )
    if cleanup_requested:
        try:
            run_saved_dictionary_cleanup(
                output_dir=output_dir,
                clean_example_sentences=clean_example_sentences,
                clean_dictionary_candidates=clean_dictionary_candidates,
                generate_missing_examples=generate_missing_examples,
                retry_generated_examples=retry_generated_example_requested,
                sentence_cleanup_client=sentence_cleanup_client,
                dictionary_cleanup_client=dictionary_cleanup_client,
                generated_example_client=generated_example_client,
                sentence_cleanup_model=sentence_cleanup_model,
                sentence_cleanup_base_url=sentence_cleanup_base_url,
                sentence_cleanup_provider=sentence_cleanup_provider,
                progress=progress,
                cleanup_progress=cleanup_progress,
                dictionary_cleanup_progress=dictionary_cleanup_progress,
                generated_example_progress=generated_example_progress,
                generated_example_retry_decider=generated_example_retry_decider,
                artifact_callback=artifact_callback,
            )
        except ValueError as exc:
            raise PipelineUsageError(str(exc)) from exc
        return load_pipeline_result_from_output(output_dir)

    from wordbyword.dictionary_build.resumable import build_resumable_dictionary

    result = build_resumable_dictionary(
        input_dir=input_dir,
        output_dir=output_dir,
        reference_dictionary_path=output_dir / "references" / "reference-dictionary.jsonl",
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
        model=sentence_cleanup_model,
        base_url=sentence_cleanup_base_url,
        provider=sentence_cleanup_provider,
        force=True,
        progress=progress,
        artifact_callback=artifact_callback,
        target_checkpoint="09",
        enforce_managed_sources=False,
    )
    if progress is not None:
        progress("write", f"writing output bundle to {output_dir}")
    return result.pipeline_result


def load_pipeline_result_from_output(output_dir: Path) -> PipelineResult:
    summary_path = artifact_path(output_dir, ArtifactId.SUMMARY)
    summary: dict[str, object] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return PipelineResult(
        documents=int(summary.get("document_count", 0) or 0),
        extracted_pages=int(summary.get("extracted_page_count", 0) or 0),
        sentences=int(summary.get("sentence_count", 0) or 0),
        token_occurrences=int(summary.get("token_occurrences", 0) or 0),
        vocabulary_terms=int(summary.get("unique_vocabulary_terms", 0) or 0),
        surface_words=int(summary.get("unique_surface_words", 0) or 0),
        output_dir=output_dir,
    )
