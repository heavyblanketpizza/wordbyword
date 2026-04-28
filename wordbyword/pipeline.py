from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import json

from kiwipiepy import Kiwi

from wordbyword.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL, CleanupProgressCallback
from wordbyword.example_generation import RetryGeneratedExampleCallback
from wordbyword.llm import SupportsCompletion, ollama_client
from wordbyword.analyzer import KoreanAnalyzer
from wordbyword.readers import discover_documents, document_id_for, read_document
from wordbyword.writers import ArtifactCallback, write_cleanup_outputs_from_saved, write_outputs

ProgressCallback = Callable[[str, str], None]


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
) -> PipelineResult:
    example_cleanup_requested = clean_example_sentences or sentence_cleanup_client is not None
    dictionary_cleanup_requested = clean_dictionary_candidates or dictionary_cleanup_client is not None
    generated_example_requested = generate_missing_examples or generated_example_client is not None
    cleanup_requested = (
        example_cleanup_requested
        or dictionary_cleanup_requested
        or generated_example_requested
    )
    if cleanup_requested:
        shared_cleanup_client = None
        if (
            sentence_cleanup_client is None
            and dictionary_cleanup_client is None
            and generated_example_client is None
        ):
            shared_cleanup_client = ollama_client(
                model=sentence_cleanup_model,
                base_url=sentence_cleanup_base_url,
                timeout_seconds=300.0,
                default_temperature=0.0,
                max_output_tokens=256,
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
                dictionary_cleanup_progress=(
                dictionary_cleanup_progress or cleanup_progress
                )
                if dictionary_cleanup_requested
                else None,
                generated_example_client=(generated_example_client or shared_cleanup_client)
                if generated_example_requested
                else None,
                generated_example_progress=(
                    generated_example_progress or dictionary_cleanup_progress or cleanup_progress
                )
                if generated_example_requested
                else None,
                generated_example_retry_decider=generated_example_retry_decider
                if generated_example_requested
                else None,
                artifact_callback=artifact_callback,
            )
        except ValueError as exc:
            raise PipelineUsageError(str(exc)) from exc
        _report(progress, "write", f"writing LLM cleanup bundle to {output_dir}")
        return load_pipeline_result_from_output(output_dir)

    _report(progress, "scan", f"discovering source documents in {input_dir}")
    documents = discover_documents(input_dir)
    kiwi = Kiwi()
    analyzer = KoreanAnalyzer(
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
        kiwi=kiwi,
    )

    _report(progress, "extract", f"reading {len(documents):,} document(s) into page/text units")
    pages = [
        page
        for document in documents
        for page in read_document(document, document_id_for(document, input_dir))
    ]
    _report(progress, "analyze", f"tokenizing {len(pages):,} page/text unit(s)")
    analysis = analyzer.analyze_pages(pages)
    _report(progress, "write", f"writing output bundle to {output_dir}")
    write_outputs(
        output_dir,
        pages=pages,
        sentences=analysis["sentences"],
        tokens=analysis["tokens"],
        vocabulary=analysis["vocabulary"],
        surface_words=analysis["surface_words"],
        document_totals=analysis["document_totals"],
        documents=documents,
        kiwi=kiwi,
        artifact_callback=artifact_callback,
    )

    return PipelineResult(
        documents=len(documents),
        extracted_pages=len(pages),
        sentences=len(analysis["sentences"]),
        token_occurrences=len(analysis["tokens"]),
        vocabulary_terms=len(analysis["vocabulary"]),
        surface_words=len(analysis["surface_words"]),
        output_dir=output_dir,
    )


def _report(progress: ProgressCallback | None, phase: str, detail: str) -> None:
    if progress is not None:
        progress(phase, detail)


def load_pipeline_result_from_output(output_dir: Path) -> PipelineResult:
    summary_path = output_dir / "00_summary.json"
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
