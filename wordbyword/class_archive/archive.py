from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
from docx import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator

from wordbyword._json_io import read_json_object, write_json_atomic
from wordbyword.dictionary_build.llm import (
    ChatMessage,
    CompletionResult,
    LLMResponseError,
    SupportsCompletion,
    parse_json_object,
)

ARCHIVE_SCHEMA_VERSION = "wordbyword.class_session_archive.v1"
DEFAULT_VOCABULARY_INTELLIGENCE_ROOT = Path("wbw-vocabulary-intelligence")
DEFAULT_CLASS_SESSION_ARCHIVE_ROOT = DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "class-session-archive"
DEFAULT_ARCHIVE_LEDGER_FILENAME = ".wordbyword-archive-ledger.json"
SUPPORTED_ARCHIVE_EXTENSIONS = {".docx", ".pdf"}
MAX_ARCHIVE_PROMPT_TEXT_CHARS = 12000
MAX_FILENAME_SLUG_CHARS = 48
ARCHIVE_NAME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}_grade(?:[1-6]|-unknown)-[a-z0-9]+(?:-[a-z0-9]+)*$"
)
NATURAL_SORT_TOKEN_PATTERN = re.compile(r"(\d+)")

ARCHIVE_RENAME_SYSTEM_PROMPT = """You label archived Korean class printouts for safe filenames.
Use only the supplied document text and file metadata.
Infer the Korean elementary grade difficulty when possible.
Return exactly one JSON object with these keys:
- grade_level: one of grade1, grade2, grade3, grade4, grade5, grade6, grade-unknown
- topic_slug: lowercase ASCII hyphen slug, broad topic, 1 to 5 words
- short_title_slug: lowercase ASCII hyphen slug, specific material title, 2 to 8 words
- korean_title: compact Korean title for audit only
- confidence: number from 0.0 to 1.0
Do not include markdown fences or commentary."""


class ClassSessionArchiveError(RuntimeError):
    """Raised when class-session archive files cannot be renamed safely."""


class ArchiveRenameMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade_level: str = "grade-unknown"
    topic_slug: str = "topic-unknown"
    short_title_slug: str = "material"
    korean_title: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("grade_level", mode="before")
    @classmethod
    def normalize_grade_level(cls, value: object) -> str:
        return normalize_grade_level(value)

    @field_validator("topic_slug", mode="before")
    @classmethod
    def normalize_topic_slug(cls, value: object) -> str:
        return safe_slug(value, fallback="topic-unknown")

    @field_validator("short_title_slug", mode="before")
    @classmethod
    def normalize_short_title_slug(cls, value: object) -> str:
        return safe_slug(value, fallback="material")

    @field_validator("korean_title", mode="before")
    @classmethod
    def normalize_korean_title(cls, value: object) -> str:
        return str(value or "").strip()[:120]


@dataclass(frozen=True)
class ArchivedMaterial:
    path: Path
    name: str
    extension: str
    byte_size: int
    sha256: str
    material_id: str
    first_seen_at: str
    text_excerpt: str

    @property
    def first_seen_date(self) -> str:
        return self.first_seen_at[:10]


@dataclass(frozen=True)
class SameFormatArchiveDuplicate:
    suffix: str
    sha256: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class ArchiveRenamePlan:
    material_id: str
    source_path: Path
    target_path: Path | None
    original_name: str
    target_name: str
    first_seen_at: str
    metadata: ArchiveRenameMetadata
    status: str
    reason: str
    applied: bool = False

    def row(self) -> dict[str, object]:
        return {
            "material_id": self.material_id,
            "original_name": self.original_name,
            "target_name": self.target_name,
            "first_seen_at": self.first_seen_at,
            "status": self.status,
            "reason": self.reason,
            "applied": self.applied,
            "classification": self.metadata.model_dump(mode="json"),
        }


@dataclass(frozen=True)
class ArchiveRenameResult:
    archive_root: Path
    ledger_path: Path
    dry_run: bool
    plans: tuple[ArchiveRenamePlan, ...]

    @property
    def planned_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "planned")

    @property
    def renamed_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "renamed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "skipped")


def rename_class_session_archive(
    archive_root: Path = DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
    *,
    client: SupportsCompletion,
    model: str,
    apply: bool = False,
    ledger_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    progress: Callable[[str], None] | None = None,
) -> ArchiveRenameResult:
    root = archive_root.resolve()
    if not root.is_dir():
        raise ClassSessionArchiveError(f"class-session archive folder does not exist: {archive_root}")

    resolved_ledger_path = (ledger_path or root / DEFAULT_ARCHIVE_LEDGER_FILENAME).resolve()
    ensure_inside_archive(root, resolved_ledger_path, allow_equal=False)
    duplicates = discover_same_format_archive_duplicates(root)
    if duplicates:
        raise ClassSessionArchiveError(format_same_format_archive_duplicate_message(root, duplicates))
    now = clock or (lambda: datetime.now().astimezone())
    ledger = load_archive_ledger(resolved_ledger_path)
    entries = ledger.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ClassSessionArchiveError(f"Archive ledger has invalid entries object: {resolved_ledger_path}")

    plans: list[ArchiveRenamePlan] = []
    materials = discover_archive_materials(root, ledger_entries=entries)
    for material in materials:
        if progress is not None:
            progress(f"classifying {material.name}")

        metadata = classify_archive_material(material, client=client)
        target_path = unique_target_path(
            root,
            filename=archive_filename(material, metadata),
            current_path=material.path,
        )
        status = "planned"
        reason = "dry run; no file was renamed"
        applied = False
        if target_path == material.path:
            status = "skipped"
            reason = "target name already matches current filename"
        elif apply:
            if progress is not None:
                progress(f"renaming {material.name} -> {target_path.name}")
            material.path.rename(target_path)
            status = "renamed"
            reason = "renamed"
            applied = True

        plan = ArchiveRenamePlan(
            material_id=material.material_id,
            source_path=material.path,
            target_path=target_path,
            original_name=material.name,
            target_name=target_path.name,
            first_seen_at=material.first_seen_at,
            metadata=metadata,
            status=status,
            reason=reason,
            applied=applied,
        )
        plans.append(plan)

        if apply:
            update_ledger_entry(
                entries,
                material=material,
                plan=plan,
                model=model,
                renamed_at=now().isoformat(timespec="seconds"),
            )

    if apply:
        write_json_atomic(
            resolved_ledger_path,
            {
                "schema_version": ARCHIVE_SCHEMA_VERSION,
                "updated_at": now().isoformat(timespec="seconds"),
                "entries": entries,
            },
        )

    return ArchiveRenameResult(
        archive_root=root,
        ledger_path=resolved_ledger_path,
        dry_run=not apply,
        plans=tuple(plans),
    )


def discover_archive_materials(
    archive_root: Path,
    *,
    ledger_entries: Mapping[str, object] | None = None,
) -> tuple[ArchivedMaterial, ...]:
    return tuple(
        build_archived_material(path, ledger_entries=ledger_entries or {})
        for path in discover_archive_material_paths(archive_root)
    )


def discover_archive_material_paths(archive_root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for path in archive_root.iterdir():
        if not is_supported_archive_material_path(path):
            continue
        if is_managed_archive_name(path.stem):
            continue
        paths.append(path)
    return tuple(sorted(paths, key=natural_path_sort_key))


def is_supported_archive_material_path(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("~$"):
        return False
    return path.suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS


def discover_same_format_archive_duplicates(archive_root: Path) -> tuple[SameFormatArchiveDuplicate, ...]:
    groups: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(archive_root.iterdir(), key=natural_path_sort_key):
        if not is_supported_archive_material_path(path):
            continue
        suffix = path.suffix.lower()
        digest = file_sha256(path)
        groups.setdefault((suffix, digest), []).append(path)

    duplicates: list[SameFormatArchiveDuplicate] = []
    for (suffix, digest), paths in groups.items():
        if len(paths) < 2:
            continue
        duplicates.append(
            SameFormatArchiveDuplicate(
                suffix=suffix,
                sha256=digest,
                paths=tuple(sorted(paths, key=natural_path_sort_key)),
            )
        )
    return tuple(
        sorted(
            duplicates,
            key=lambda duplicate: (
                duplicate.suffix,
                duplicate.sha256,
                tuple(natural_path_sort_key(path) for path in duplicate.paths),
            ),
        )
    )


def format_same_format_archive_duplicate_message(
    archive_root: Path,
    duplicates: tuple[SameFormatArchiveDuplicate, ...],
) -> str:
    lines = [
        (
            "duplicate same-format class-session archive files detected in "
            f"{archive_root}; remove or move duplicates before renaming or building usage signals:"
        )
    ]
    for duplicate in duplicates:
        lines.append(f"  {duplicate.suffix} sha256:{duplicate.sha256}")
        for path in duplicate.paths:
            lines.append(f"    - {path.name}")
    return "\n".join(lines)


def natural_path_sort_key(path: Path) -> tuple[tuple[int, int | str], ...]:
    tokens: list[tuple[int, int | str]] = []
    for token in NATURAL_SORT_TOKEN_PATTERN.split(path.stem.lower()):
        if not token:
            continue
        if token.isdigit():
            tokens.append((1, int(token)))
            continue
        text = re.sub(r"[\s_.-]+", " ", token).strip()
        if text:
            tokens.append((0, text))
    tokens.append((0, path.suffix.lower()))
    return tuple(tokens)


def build_archived_material(
    path: Path,
    *,
    ledger_entries: Mapping[str, object],
) -> ArchivedMaterial:
    digest = file_sha256(path)
    material_id = f"sha256:{digest}"
    ledger_entry = ledger_entries.get(material_id)
    first_seen_at = ""
    if isinstance(ledger_entry, Mapping):
        first_seen_at = str(ledger_entry.get("first_seen_at") or "")
    if not first_seen_at:
        first_seen_at = file_first_seen_timestamp(path).isoformat(timespec="seconds")
    return ArchivedMaterial(
        path=path,
        name=path.name,
        extension=path.suffix.lower(),
        byte_size=path.stat().st_size,
        sha256=digest,
        material_id=material_id,
        first_seen_at=first_seen_at,
        text_excerpt=extract_archive_text(path)[:MAX_ARCHIVE_PROMPT_TEXT_CHARS],
    )


def classify_archive_material(
    material: ArchivedMaterial,
    *,
    client: SupportsCompletion,
) -> ArchiveRenameMetadata:
    if not material.text_excerpt.strip():
        return ArchiveRenameMetadata(
            grade_level="grade-unknown",
            topic_slug="topic-unknown",
            short_title_slug="material",
            korean_title="",
            confidence=0.0,
        )

    payload = {
        "original_filename": material.name,
        "extension": material.extension,
        "first_seen_date": material.first_seen_date,
        "byte_size": material.byte_size,
        "text_excerpt": material.text_excerpt,
    }
    result = client.complete(
        [
            ChatMessage(role="system", content=ARCHIVE_RENAME_SYSTEM_PROMPT),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ],
        temperature=0.0,
        max_output_tokens=512,
        json_mode=True,
    )
    return parse_archive_metadata_response(result)


def parse_archive_metadata_response(result: CompletionResult) -> ArchiveRenameMetadata:
    try:
        payload = parse_json_object(result.content)
    except LLMResponseError as error:
        raise ClassSessionArchiveError(str(error)) from error
    try:
        return ArchiveRenameMetadata.model_validate(payload)
    except ValueError as error:
        raise ClassSessionArchiveError(f"Archive rename metadata failed validation: {error}") from error


def archive_filename(material: ArchivedMaterial, metadata: ArchiveRenameMetadata) -> str:
    descriptor_parts = [metadata.grade_level, metadata.topic_slug, metadata.short_title_slug]
    compact_parts: list[str] = []
    for part in descriptor_parts:
        if part and part not in compact_parts:
            compact_parts.append(part)
    descriptor = "-".join(compact_parts) or "grade-unknown-topic-unknown-material"
    return f"{material.first_seen_date}_{descriptor}{material.extension}"


def unique_target_path(root: Path, *, filename: str, current_path: Path) -> Path:
    target = (root / filename).resolve()
    ensure_inside_archive(root, target, allow_equal=False)
    if target == current_path.resolve() or not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    counter = 2
    while True:
        candidate = (root / f"{stem}-{counter}{suffix}").resolve()
        ensure_inside_archive(root, candidate, allow_equal=False)
        if candidate == current_path.resolve() or not candidate.exists():
            return candidate
        counter += 1


def load_archive_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": ARCHIVE_SCHEMA_VERSION, "entries": {}}
    payload = read_json_object(path, error_type=ClassSessionArchiveError)
    schema_version = payload.get("schema_version")
    if schema_version != ARCHIVE_SCHEMA_VERSION:
        raise ClassSessionArchiveError(
            f"Archive ledger schema is {schema_version!r}, expected {ARCHIVE_SCHEMA_VERSION!r}: {path}"
        )
    return payload


def update_ledger_entry(
    entries: dict[str, object],
    *,
    material: ArchivedMaterial,
    plan: ArchiveRenamePlan,
    model: str,
    renamed_at: str,
) -> None:
    existing = entries.get(material.material_id)
    if isinstance(existing, dict):
        entry = existing
    else:
        entry = {}
    original_name = str(entry.get("original_name") or material.name)
    history = entry.get("rename_history")
    if not isinstance(history, list):
        history = []
    if plan.applied:
        history.append(
            {
                "from": material.name,
                "to": plan.target_name,
                "renamed_at": renamed_at,
                "model": model,
                "classification": plan.metadata.model_dump(mode="json"),
            }
        )
    entry.update(
        {
            "material_id": material.material_id,
            "sha256": material.sha256,
            "byte_size": material.byte_size,
            "extension": material.extension,
            "original_name": original_name,
            "current_name": plan.target_name,
            "first_seen_at": material.first_seen_at,
            "last_seen_at": renamed_at,
            "classification": plan.metadata.model_dump(mode="json"),
            "rename_history": history,
        }
    )
    entries[material.material_id] = entry


def extract_archive_text(path: Path) -> str:
    extension = path.suffix.lower()
    if extension == ".docx":
        return extract_docx_text(path)
    if extension == ".pdf":
        return extract_pdf_text(path)
    return ""


def extract_docx_text(path: Path) -> str:
    document = Document(str(path))
    parts: list[str] = []
    parts.extend(paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip())
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_pdf_text(path: Path) -> str:
    parts: list[str] = []
    with fitz.open(path) as document:
        for page in document:
            text = page.get_text("text").strip()
            if text:
                parts.append(text)
            if sum(len(part) for part in parts) >= MAX_ARCHIVE_PROMPT_TEXT_CHARS:
                break
    return "\n\n".join(parts)


def is_managed_archive_name(stem: str) -> bool:
    return bool(ARCHIVE_NAME_PATTERN.fullmatch(stem))


def normalize_grade_level(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    if text in {"", "unknown", "gradeunknown", "grade-unknown", "학년모름", "미상"}:
        return "grade-unknown"
    digit_match = re.search(r"([1-6])", text)
    if digit_match and ("grade" in text or "학년" in text or text == digit_match.group(1)):
        return f"grade{digit_match.group(1)}"
    if text in {f"grade{grade}" for grade in range(1, 7)}:
        return text
    return "grade-unknown"


def safe_slug(value: object, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return fallback
    if len(slug) <= MAX_FILENAME_SLUG_CHARS:
        return slug
    shortened = slug[:MAX_FILENAME_SLUG_CHARS].rstrip("-")
    return shortened or fallback


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_first_seen_timestamp(path: Path) -> datetime:
    stat = path.stat()
    timestamp = getattr(stat, "st_birthtime", None)
    if timestamp is None:
        timestamp = min(stat.st_ctime, stat.st_mtime)
    return datetime.fromtimestamp(float(timestamp)).astimezone()


def ensure_inside_archive(root: Path, path: Path, *, allow_equal: bool) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if allow_equal and resolved_path == resolved_root:
        return
    if resolved_root not in resolved_path.parents:
        raise ClassSessionArchiveError(f"Refusing to write outside class-session archive: {path}")
