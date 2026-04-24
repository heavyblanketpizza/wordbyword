from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kiwipiepy import Kiwi

from wordbyword.analyzer import KoreanAnalyzer
from wordbyword.readers import discover_documents, document_id_for, read_document
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
    kiwi = Kiwi()
    analyzer = KoreanAnalyzer(
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
        kiwi=kiwi,
    )

    pages = [
        page
        for document in documents
        for page in read_document(document, document_id_for(document, input_dir))
    ]
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
        kiwi=kiwi,
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
