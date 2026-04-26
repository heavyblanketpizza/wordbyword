from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kiwipiepy import Kiwi

from wordbyword.example_cleanup import DEFAULT_EXAMPLE_CLEANUP_MODEL
from wordbyword.llm import SupportsCompletion, ollama_client
from wordbyword.analyzer import KoreanAnalyzer
from wordbyword.readers import discover_documents, document_id_for, read_document
from wordbyword.writers import write_outputs

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


def run_pipeline(
    *,
    input_dir: Path,
    output_dir: Path,
    include_punctuation: bool = False,
    include_numbers: bool = False,
    clean_example_sentences: bool = False,
    sentence_cleanup_client: SupportsCompletion | None = None,
    sentence_cleanup_model: str = DEFAULT_EXAMPLE_CLEANUP_MODEL,
    sentence_cleanup_base_url: str | None = None,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
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
    cleanup_requested = clean_example_sentences or sentence_cleanup_client is not None
    cleanup_client = sentence_cleanup_client
    if cleanup_requested and cleanup_client is None:
        cleanup_client = ollama_client(
            model=sentence_cleanup_model,
            base_url=sentence_cleanup_base_url,
            timeout_seconds=300.0,
            default_temperature=0.0,
            max_output_tokens=256,
        )
    if cleanup_requested:
        _report(progress, "cleanup", f"preparing example cleanup via {sentence_cleanup_model}")

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
        sentence_cleanup_client=cleanup_client if cleanup_requested else None,
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
