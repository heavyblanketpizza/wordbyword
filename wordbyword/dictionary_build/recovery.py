from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from wordbyword.dictionary_build.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupProgressCallback
from wordbyword.dictionary_build.example_generation import RetryGeneratedExampleCallback
from wordbyword.dictionary_build.llm import SupportsCompletion, ollama_client, unload_model_if_supported
from wordbyword.dictionary_build.saved_outputs import write_cleanup_outputs_from_saved

ProgressCallback = Callable[[str, str], None]
ArtifactCallback = Callable[[str, Sequence[Mapping[str, object]]], None]

DEFAULT_SHARED_CLEANUP_TIMEOUT_SECONDS = 300.0
DEFAULT_SHARED_CLEANUP_MAX_OUTPUT_TOKENS = 256


def run_saved_dictionary_cleanup(
    *,
    output_dir: Path,
    clean_example_sentences: bool = False,
    clean_dictionary_candidates: bool = False,
    generate_missing_examples: bool = False,
    retry_generated_examples: bool = False,
    sentence_cleanup_client: SupportsCompletion | None = None,
    dictionary_cleanup_client: SupportsCompletion | None = None,
    generated_example_client: SupportsCompletion | None = None,
    sentence_cleanup_model: str = DEFAULT_EXAMPLE_CLEANUP_MODEL,
    sentence_cleanup_base_url: str | None = None,
    progress: ProgressCallback | None = None,
    cleanup_progress: CleanupProgressCallback | None = None,
    dictionary_cleanup_progress: CleanupProgressCallback | None = None,
    generated_example_progress: CleanupProgressCallback | None = None,
    generated_example_retry_decider: RetryGeneratedExampleCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
) -> None:
    example_cleanup_requested = clean_example_sentences or sentence_cleanup_client is not None
    dictionary_cleanup_requested = clean_dictionary_candidates or dictionary_cleanup_client is not None
    generated_example_requested = (
        generate_missing_examples
        or retry_generated_examples
        or generated_example_client is not None
    )
    if not (example_cleanup_requested or dictionary_cleanup_requested or generated_example_requested):
        return

    shared_cleanup_client = None
    if (
        sentence_cleanup_client is None
        and dictionary_cleanup_client is None
        and generated_example_client is None
    ):
        shared_cleanup_client = ollama_client(
            model=sentence_cleanup_model,
            base_url=sentence_cleanup_base_url,
            timeout_seconds=DEFAULT_SHARED_CLEANUP_TIMEOUT_SECONDS,
            max_output_tokens=DEFAULT_SHARED_CLEANUP_MAX_OUTPUT_TOKENS,
        )

    _report(progress, "cleanup", f"loading saved stage inputs from {output_dir}")
    try:
        write_cleanup_outputs_from_saved(
            output_dir,
            sentence_cleanup_client=(sentence_cleanup_client or shared_cleanup_client)
            if example_cleanup_requested
            else None,
            sentence_cleanup_progress=cleanup_progress if example_cleanup_requested else None,
            dictionary_cleanup_client=(dictionary_cleanup_client or shared_cleanup_client)
            if dictionary_cleanup_requested
            else None,
            dictionary_cleanup_progress=(dictionary_cleanup_progress or cleanup_progress)
            if dictionary_cleanup_requested
            else None,
            generated_example_client=(generated_example_client or shared_cleanup_client)
            if generated_example_requested
            else None,
            generated_example_progress=(generated_example_progress or dictionary_cleanup_progress or cleanup_progress)
            if generated_example_requested
            else None,
            generated_example_retry_decider=generated_example_retry_decider
            if generated_example_requested
            else None,
            retry_generated_examples_only=retry_generated_examples,
            artifact_callback=artifact_callback,
        )
    finally:
        if shared_cleanup_client is not None:
            try:
                unload_model_if_supported(shared_cleanup_client)
            except Exception as exc:  # pragma: no cover - defensive cleanup path
                _report(progress, "cleanup", f"warning: could not unload Ollama model: {exc}")
    _report(progress, "write", f"writing LLM cleanup bundle to {output_dir}")


def _report(progress: ProgressCallback | None, phase: str, detail: str) -> None:
    if progress is not None:
        progress(phase, detail)
