from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wordbyword.analyzer import KoreanAnalyzer
from wordbyword.readers import discover_documents, read_document
from wordbyword.writers import write_outputs


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
) -> PipelineResult:
    documents = discover_documents(input_dir)
    analyzer = KoreanAnalyzer(
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
    )

    pages = [page for document in documents for page in read_document(document)]
    analysis = analyzer.analyze_pages(pages)

    write_outputs(
        output_dir,
        pages=pages,
        sentences=analysis["sentences"],
        tokens=analysis["tokens"],
        vocabulary=analysis["vocabulary"],
        surface_words=analysis["surface_words"],
        document_totals=analysis["document_totals"],
        documents=documents,
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
