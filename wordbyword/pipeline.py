from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wordbyword.analyzer import KoreanAnalyzer
from wordbyword.readers import discover_documents, read_document
from wordbyword.writers import write_outputs


@dataclass(frozen=True)
class PipelineResult:
    documents: int
    token_occurrences: int
    vocabulary_terms: int
    surface_words: int
    expression_candidates: int
    output_dir: Path


def run_pipeline(
    *,
    input_dir: Path,
    output_dir: Path,
    include_punctuation: bool = False,
    include_numbers: bool = False,
    min_expression_size: int = 2,
    max_expression_size: int = 5,
    min_expression_count: int = 1,
) -> PipelineResult:
    documents = discover_documents(input_dir)
    analyzer = KoreanAnalyzer(
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
        min_expression_size=min_expression_size,
        max_expression_size=max_expression_size,
    )

    pages = (page for document in documents for page in read_document(document))
    analysis = analyzer.analyze_pages(pages)

    write_outputs(
        output_dir,
        tokens=analysis["tokens"],
        vocabulary=analysis["vocabulary"],
        surface_words=analysis["surface_words"],
        expressions=analysis["expressions"],
        document_totals=analysis["document_totals"],
        documents=documents,
        min_expression_count=min_expression_count,
    )

    expression_candidates = sum(
        1 for entry in analysis["expressions"].values() if entry.count >= min_expression_count
    )

    return PipelineResult(
        documents=len(documents),
        token_occurrences=len(analysis["tokens"]),
        vocabulary_terms=len(analysis["vocabulary"]),
        surface_words=len(analysis["surface_words"]),
        expression_candidates=expression_candidates,
        output_dir=output_dir,
    )
