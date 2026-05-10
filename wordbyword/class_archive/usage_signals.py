from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import fitz
from docx import Document
from kiwipiepy import Kiwi

from wordbyword.class_archive.archive import (
    DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
    DEFAULT_VOCABULARY_INTELLIGENCE_ROOT,
    is_managed_archive_name,
    natural_path_sort_key,
)
from wordbyword.dictionary_build.analyzer import KoreanAnalyzer
from wordbyword.dictionary_build.models import PageText
from wordbyword.dictionary_build.readers import normalize_text
from wordbyword.dictionary_build.writers import ArtifactCallback, write_outputs

DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT = DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "usage-signal-build"
SUPPORTED_USAGE_SIGNAL_EXTENSIONS = {".docx", ".pdf"}

ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class UsageSignalBuildResult:
    archive_root: Path
    output_root: Path
    documents: int
    extracted_pages: int
    sentences: int
    token_occurrences: int
    vocabulary_terms: int
    surface_words: int


class UsageSignalBuildError(RuntimeError):
    """Raised when class archive usage signals cannot be built."""


def build_usage_signals(
    *,
    archive_root: Path = DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
    output_root: Path = DEFAULT_USAGE_SIGNAL_OUTPUT_ROOT,
    include_punctuation: bool = False,
    include_numbers: bool = False,
    progress: ProgressCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
) -> UsageSignalBuildResult:
    if not archive_root.exists():
        raise UsageSignalBuildError(f"class-session archive folder does not exist: {archive_root}")
    if not archive_root.is_dir():
        raise UsageSignalBuildError(f"class-session archive path is not a folder: {archive_root}")

    _report(progress, "scan", f"discovering named class printouts in {archive_root}")
    documents = discover_usage_signal_documents(archive_root)
    kiwi = Kiwi()
    analyzer = KoreanAnalyzer(
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
        kiwi=kiwi,
    )

    _report(progress, "extract", f"reading {len(documents):,} class printout(s) into text units")
    pages = [
        page
        for document in documents
        for page in read_usage_signal_document(document, document_id_for_usage_signal(document, archive_root))
    ]
    _report(progress, "analyze", f"tokenizing {len(pages):,} class printout text unit(s)")
    analysis = analyzer.analyze_pages(pages)
    _report(progress, "write", f"writing usage signal bundle to {output_root}")
    write_outputs(
        output_root,
        pages=pages,
        sentences=analysis["sentences"],
        tokens=analysis["tokens"],
        vocabulary=analysis["vocabulary"],
        surface_words=analysis["surface_words"],
        document_totals=analysis["document_totals"],
        documents=[Path(document_id_for_usage_signal(document, archive_root)) for document in documents],
        kiwi=kiwi,
        artifact_callback=artifact_callback,
    )

    return UsageSignalBuildResult(
        archive_root=archive_root,
        output_root=output_root,
        documents=len(documents),
        extracted_pages=len(pages),
        sentences=len(analysis["sentences"]),
        token_occurrences=len(analysis["tokens"]),
        vocabulary_terms=len(analysis["vocabulary"]),
        surface_words=len(analysis["surface_words"]),
    )


def discover_usage_signal_documents(archive_root: Path) -> list[Path]:
    if not archive_root.exists():
        return []
    return sorted(
        (
            path
            for path in archive_root.iterdir()
            if path.is_file()
            and not path.name.startswith("~$")
            and path.suffix.lower() in SUPPORTED_USAGE_SIGNAL_EXTENSIONS
            and is_managed_archive_name(path.stem)
        ),
        key=natural_path_sort_key,
    )


def document_id_for_usage_signal(path: Path, archive_root: Path) -> str:
    try:
        return path.relative_to(archive_root).as_posix()
    except ValueError:
        return path.name


def read_usage_signal_document(path: Path, document_id: str) -> Iterable[PageText]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        yield from read_usage_signal_pdf(path, document_id)
        return
    if suffix == ".docx":
        raw_text = extract_usage_signal_docx_text(path)
        yield PageText(path, None, normalize_text(raw_text), raw_text, document_id)


def read_usage_signal_pdf(path: Path, document_id: str) -> Iterable[PageText]:
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            raw_text = page.get_text("text", sort=True)
            yield PageText(path, page_number, normalize_text(raw_text), raw_text, document_id)


def extract_usage_signal_docx_text(path: Path) -> str:
    document = Document(str(path))
    parts: list[str] = []
    parts.extend(paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip())
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _report(progress: ProgressCallback | None, phase: str, detail: str) -> None:
    if progress is not None:
        progress(phase, detail)
