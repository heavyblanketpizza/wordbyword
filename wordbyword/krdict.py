from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_KRDICT_DIR_NAME = "krdict_data"
DEFAULT_KRDICT_DOWNLOAD_URL = (
    "https://github.com/spellcheck-ko/korean-dict-nikl-krdict/archive/refs/heads/master.tar.gz"
)
DEFAULT_KRDICT_DOWNLOAD_TIMEOUT_SECONDS = 600.0
KRDICT_INDEX_FILENAME = "index.json"
KRDICT_SOURCE_DIR_NAME = "source"
KRDICT_DOWNLOAD_FILENAME = "krdict_source.tar.gz"

LEMMA_TAG_HINTS = ("word", "lemma", "writtenform", "headword")
DEFINITION_TAG_HINTS = ("definition", "definition_text", "deftext", "sense_definition")
POS_TAG_HINTS = ("pos", "partofspeech", "pos_text")


class KrdictDownloadError(RuntimeError):
    """Raised when the bundled 한국어기초사전 source cannot be downloaded."""


class KrdictParseError(RuntimeError):
    """Raised when the cached 한국어기초사전 XML cannot be parsed."""


@dataclass(frozen=True)
class KrdictEntry:
    headword: str
    pos: str
    definition: str


@dataclass(frozen=True)
class KrdictDataset:
    entries_by_headword: dict[str, list[KrdictEntry]]

    def lookup(self, headword: str) -> list[KrdictEntry]:
        cleaned = headword.strip()
        if not cleaned:
            return []
        return self.entries_by_headword.get(cleaned, [])

    def has(self, headword: str) -> bool:
        return bool(self.lookup(headword))

    def headword_count(self) -> int:
        return len(self.entries_by_headword)


def default_krdict_dir(project_dir: Path | None = None) -> Path:
    base = project_dir if project_dir is not None else Path.cwd()
    return base / DEFAULT_KRDICT_DIR_NAME


def ensure_krdict_dataset(
    krdict_dir: Path,
    *,
    download_url: str | None = None,
    on_status: "KrdictStatusCallback | None" = None,
) -> KrdictDataset:
    krdict_dir.mkdir(parents=True, exist_ok=True)
    index_path = krdict_dir / KRDICT_INDEX_FILENAME
    source_dir = krdict_dir / KRDICT_SOURCE_DIR_NAME

    if index_path.is_file():
        _emit_status(on_status, "load_index", str(index_path))
        return load_krdict_index(index_path)

    if not _has_xml_files(source_dir):
        url = download_url or os.environ.get("WORDBYWORD_KRDICT_URL", DEFAULT_KRDICT_DOWNLOAD_URL)
        _emit_status(on_status, "download_start", url)
        download_krdict_source(url, source_dir, on_status=on_status)
        _emit_status(on_status, "download_complete", str(source_dir))

    _emit_status(on_status, "parse_start", str(source_dir))
    dataset = parse_krdict_source(source_dir)
    write_krdict_index(index_path, dataset)
    _emit_status(on_status, "parse_complete", f"{dataset.headword_count():,} headwords")
    return dataset


KrdictStatusCallback = "object"


def _emit_status(on_status: Any, phase: str, detail: str) -> None:
    if on_status is None:
        return
    on_status(phase, detail)


def _has_xml_files(source_dir: Path) -> bool:
    if not source_dir.is_dir():
        return False
    for _ in source_dir.rglob("*.xml"):
        return True
    return False


def download_krdict_source(
    url: str,
    source_dir: Path,
    *,
    timeout_seconds: float = DEFAULT_KRDICT_DOWNLOAD_TIMEOUT_SECONDS,
    on_status: Any = None,
) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(prefix="krdict_", suffix=".tar.gz", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)
    try:
        try:
            with request.urlopen(url, timeout=timeout_seconds) as response:
                with tmp_path.open("wb") as out_handle:
                    while True:
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        out_handle.write(chunk)
        except error.HTTPError as exc:
            raise KrdictDownloadError(
                f"Failed to download 한국어기초사전 from {url}: HTTP {exc.code}"
            ) from exc
        except error.URLError as exc:
            raise KrdictDownloadError(
                f"Failed to download 한국어기초사전 from {url}: {exc.reason}"
            ) from exc

        _emit_status(on_status, "extract_start", str(tmp_path))
        try:
            with tarfile.open(tmp_path, mode="r:*") as archive:
                _extract_xml_members(archive, source_dir)
        except tarfile.TarError as exc:
            raise KrdictDownloadError(
                f"Failed to extract 한국어기초사전 archive from {tmp_path}: {exc}"
            ) from exc
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    if not _has_xml_files(source_dir):
        raise KrdictDownloadError(
            f"Downloaded archive from {url} did not contain any .xml files."
        )


def _extract_xml_members(archive: tarfile.TarFile, source_dir: Path) -> None:
    for member in archive.getmembers():
        if not member.isfile():
            continue
        name = member.name
        lower = name.lower()
        if not lower.endswith(".xml"):
            continue
        leaf = Path(name).name
        if not leaf:
            continue
        target_path = source_dir / leaf
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        with extracted as src, target_path.open("wb") as dst:
            dst.write(src.read())


def parse_krdict_source(source_dir: Path) -> KrdictDataset:
    if not source_dir.is_dir():
        raise KrdictParseError(f"한국어기초사전 source directory does not exist: {source_dir}")

    entries_by_headword: dict[str, list[KrdictEntry]] = {}
    saw_any_xml = False
    for xml_path in sorted(source_dir.rglob("*.xml")):
        saw_any_xml = True
        for entry in iter_krdict_entries(xml_path):
            bucket = entries_by_headword.setdefault(entry.headword, [])
            if any(
                existing.definition == entry.definition and existing.pos == entry.pos
                for existing in bucket
            ):
                continue
            bucket.append(entry)

    if not saw_any_xml:
        raise KrdictParseError(f"No .xml files found under {source_dir}.")

    return KrdictDataset(entries_by_headword=entries_by_headword)


def iter_krdict_entries(xml_path: Path) -> Iterator[KrdictEntry]:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise KrdictParseError(f"Failed to parse {xml_path}: {exc}") from exc
    root = tree.getroot()
    yield from _walk_krdict_elements(root)


def _walk_krdict_elements(element: ET.Element) -> Iterator[KrdictEntry]:
    headword = _find_first_text(element, LEMMA_TAG_HINTS)
    if headword:
        pos = _find_first_text(element, POS_TAG_HINTS) or ""
        for definition in _iter_descendant_text(element, DEFINITION_TAG_HINTS):
            cleaned_def = _clean_text(definition)
            if not cleaned_def:
                continue
            yield KrdictEntry(
                headword=headword,
                pos=_clean_text(pos),
                definition=cleaned_def,
            )
        return

    for child in list(element):
        yield from _walk_krdict_elements(child)


def _find_first_text(element: ET.Element, hints: Iterable[str]) -> str:
    hint_set = {hint.lower() for hint in hints}
    for child in element:
        tag = _local_tag(child.tag).lower()
        if tag in hint_set:
            text = _clean_text(child.text or "")
            if text:
                return text
    return ""


def _iter_descendant_text(element: ET.Element, hints: Iterable[str]) -> Iterator[str]:
    hint_set = {hint.lower() for hint in hints}
    for descendant in element.iter():
        tag = _local_tag(descendant.tag).lower()
        if tag in hint_set:
            text = descendant.text or ""
            if text.strip():
                yield text


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def write_krdict_index(index_path: Path, dataset: KrdictDataset) -> None:
    payload = {
        "headword_count": dataset.headword_count(),
        "entries": {
            headword: [
                {"pos": entry.pos, "definition": entry.definition}
                for entry in entries
            ]
            for headword, entries in dataset.entries_by_headword.items()
        },
    }
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_krdict_index(index_path: Path) -> KrdictDataset:
    raw = index_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KrdictParseError(f"Failed to read {index_path}: {exc}") from exc
    raw_entries = payload.get("entries", {})
    if not isinstance(raw_entries, dict):
        raise KrdictParseError(f"{index_path} is missing the 'entries' object.")
    entries_by_headword: dict[str, list[KrdictEntry]] = {}
    for headword, items in raw_entries.items():
        if not isinstance(items, list):
            continue
        bucket: list[KrdictEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            definition = str(item.get("definition", ""))
            if not definition:
                continue
            bucket.append(
                KrdictEntry(
                    headword=str(headword),
                    pos=str(item.get("pos", "")),
                    definition=definition,
                )
            )
        if bucket:
            entries_by_headword[str(headword)] = bucket
    return KrdictDataset(entries_by_headword=entries_by_headword)


def format_meaning(entries: list[KrdictEntry]) -> str:
    if not entries:
        return ""
    if len(entries) == 1:
        return entries[0].definition
    return " | ".join(entry.definition for entry in entries)


def lookup_meaning(dataset: KrdictDataset, *, display: str, canonical_key: str) -> str:
    for candidate in (display, canonical_key):
        cleaned = (candidate or "").strip()
        if not cleaned:
            continue
        entries = dataset.lookup(cleaned)
        if entries:
            return format_meaning(entries)
    return ""
