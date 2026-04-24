from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import fitz

from wordbyword.models import PageText
from wordbyword.text import clean_layout_text

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")


def discover_documents(input_dir: Path) -> list[Path]:
    """Return supported documents in stable order."""
    if not input_dir.exists():
        return []

    return sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def document_id_for(path: Path, input_dir: Path) -> str:
    try:
        return path.relative_to(input_dir).as_posix()
    except ValueError:
        return path.name


def read_document(path: Path, document_id: str) -> Iterable[PageText]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        yield from read_pdf(path, document_id)
    elif suffix in {".txt", ".md"}:
        raw_text = read_text_file(path)
        yield PageText(path, None, normalize_text(raw_text), raw_text, document_id)


def read_pdf(path: Path, document_id: str) -> Iterable[PageText]:
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            raw_text = page.get_text("text", sort=True)
            yield PageText(path, page_number, normalize_text(raw_text), raw_text, document_id)


def read_text_file(path: Path) -> str:
    last_error: UnicodeDecodeError | None = None
    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    return path.read_text()


def normalize_text(text: str) -> str:
    text = clean_layout_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"(?<![.!?。！？])\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
