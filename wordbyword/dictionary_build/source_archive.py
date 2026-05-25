from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import fitz
from pydantic import BaseModel, ConfigDict, Field, field_validator

from wordbyword._archive_common import (
    connected_path_components,
    duplicate_resolution_timestamp,
    ensure_inside_root,
    file_first_seen_timestamp,
    file_sha256,
    natural_path_parts_sort_key,
    relative_path,
    safe_ascii_slug,
)
from wordbyword._json_io import read_json_object, write_json_atomic
from wordbyword.dictionary_build.llm import (
    ChatMessage,
    CompletionResult,
    LLMResponseError,
    SupportsCompletion,
    parse_json_object,
)
from wordbyword.dictionary_build.readers import SUPPORTED_EXTENSIONS, read_text_file
from wordbyword.dictionary_build.text import clean_layout_text
from wordbyword.grade_profiles import (
    DICTIONARY_SOURCE_ROOT,
    GRADE_WITH_UNKNOWN_PATTERN,
    UNKNOWN_GRADE,
    normalize_target_grade,
    normalize_grade_level as normalize_shared_grade_level,
)

DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION = "wordbyword.dictionary_source_archive.v1"
DICTIONARY_SOURCE_DUPLICATE_MANIFEST_SCHEMA_VERSION = "wordbyword.dictionary_source_archive_duplicates.v1"
DEFAULT_DICTIONARY_SOURCE_ROOT = DICTIONARY_SOURCE_ROOT
DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME = ".wordbyword-dictionary-source-ledger.json"
DEFAULT_DICTIONARY_SOURCE_DUPLICATE_QUARANTINE_DIRNAME = ".wordbyword-dictionary-source-duplicates"
MIN_DICTIONARY_SOURCE_CLASSIFICATION_CONFIDENCE = 0.80
MAX_DICTIONARY_SOURCE_PROMPT_TEXT_CHARS = 12000
MAX_DICTIONARY_SOURCE_SLUG_CHARS = 56

SOURCE_TYPES = {
    "textbook",
    "official-reading-list",
    "test",
    "worksheet",
    "assessment",
    "reference",
    "other",
}
AUTHORITIES = {"government", "school", "publisher", "teacher", "unknown"}
FORMAT_RANK = {".pdf": 0, ".md": 1, ".txt": 2}
MANAGED_DICTIONARY_SOURCE_NAME_PATTERN = re.compile(
    r"^(?:textbook|official-reading-list|test|worksheet|assessment|reference|other)_"
    r"(?:government|school|publisher|teacher|unknown)_"
    rf"{GRADE_WITH_UNKNOWN_PATTERN}_"
    r"[a-z0-9]+(?:-[a-z0-9]+)*_"
    r"(?:\d{4}|year-unknown)_"
    r"[a-z0-9]+(?:-[a-z0-9]+)*_"
    r"[a-z0-9]+(?:-[a-z0-9]+)*$"
)

DICTIONARY_SOURCE_RENAME_SYSTEM_PROMPT = """You label canonical Korean dictionary source materials for safe filenames.
Use only the supplied document text and file metadata.
Return exactly one JSON object with these keys:
- source_type: one of textbook, official-reading-list, test, worksheet, assessment, reference, other
- authority: one of government, school, publisher, teacher, unknown
- grade_level: one of grade1 through grade12, or grade-unknown
- subject_slug: lowercase ASCII hyphen slug, broad subject, 1 to 4 words
- curriculum_year: four digit year if clearly present, otherwise year-unknown
- term_slug: lowercase ASCII hyphen slug such as term1, term2, semester1, unit3, or term-unknown
- title_slug: lowercase ASCII hyphen slug, specific material title, 2 to 8 words
- korean_title: compact Korean title for audit only
- confidence: number from 0.0 to 1.0
Do not include markdown fences or commentary."""


class DictionarySourceArchiveError(RuntimeError):
    """Raised when the managed dictionary source archive is invalid or unsafe to mutate."""


class DictionarySourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal[
        "textbook",
        "official-reading-list",
        "test",
        "worksheet",
        "assessment",
        "reference",
        "other",
    ] = "other"
    authority: Literal["government", "school", "publisher", "teacher", "unknown"] = "unknown"
    grade_level: str = "grade-unknown"
    subject_slug: str = "subject-unknown"
    curriculum_year: str = "year-unknown"
    term_slug: str = "term-unknown"
    title_slug: str = "material"
    korean_title: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("source_type", mode="before")
    @classmethod
    def normalize_source_type(cls, value: object) -> str:
        slug = safe_slug(value, fallback="other")
        return slug if slug in SOURCE_TYPES else "other"

    @field_validator("authority", mode="before")
    @classmethod
    def normalize_authority(cls, value: object) -> str:
        slug = safe_slug(value, fallback="unknown")
        return slug if slug in AUTHORITIES else "unknown"

    @field_validator("grade_level", mode="before")
    @classmethod
    def normalize_grade(cls, value: object) -> str:
        return normalize_grade_level(value)

    @field_validator("subject_slug", mode="before")
    @classmethod
    def normalize_subject_slug(cls, value: object) -> str:
        return safe_slug(value, fallback="subject-unknown")

    @field_validator("curriculum_year", mode="before")
    @classmethod
    def normalize_curriculum_year(cls, value: object) -> str:
        text = str(value or "").strip()
        match = re.search(r"\b(19|20)\d{2}\b", text)
        if match:
            return match.group(0)
        return "year-unknown"

    @field_validator("term_slug", mode="before")
    @classmethod
    def normalize_term_slug(cls, value: object) -> str:
        return safe_slug(value, fallback="term-unknown")

    @field_validator("title_slug", mode="before")
    @classmethod
    def normalize_title_slug(cls, value: object) -> str:
        return safe_slug(value, fallback="material")

    @field_validator("korean_title", mode="before")
    @classmethod
    def normalize_korean_title(cls, value: object) -> str:
        return str(value or "").strip()[:160]


@dataclass(frozen=True)
class DictionarySourceMaterial:
    path: Path
    relative_path: str
    name: str
    extension: str
    byte_size: int
    sha256: str
    material_id: str
    first_seen_at: str
    text_excerpt: str


@dataclass(frozen=True)
class DictionarySourceRenamePlan:
    material_id: str
    source_path: Path
    target_path: Path
    original_path: str
    target_path_relative: str
    first_seen_at: str
    metadata: DictionarySourceMetadata
    status: str
    reason: str
    applied: bool = False

    @property
    def original_name(self) -> str:
        return self.source_path.name

    @property
    def target_name(self) -> str:
        return self.target_path.name


@dataclass(frozen=True)
class DictionarySourceRenameResult:
    archive_root: Path
    ledger_path: Path
    dry_run: bool
    plans: tuple[DictionarySourceRenamePlan, ...]

    @property
    def planned_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "planned")

    @property
    def renamed_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "renamed")

    @property
    def registered_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "registered")

    @property
    def skipped_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "skipped")

    @property
    def blocked_count(self) -> int:
        return sum(1 for plan in self.plans if plan.status == "blocked")


@dataclass(frozen=True)
class DictionarySourceDuplicateFile:
    path: Path
    relative_path: str
    suffix: str
    sha256: str
    byte_size: int
    text_fingerprint: str
    text_fingerprint_length: int
    normalized_text: str


@dataclass(frozen=True)
class DictionarySourceDuplicateEvidence:
    reason: str
    detail: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class DictionarySourceDuplicateGroup:
    paths: tuple[Path, ...]
    files: tuple[DictionarySourceDuplicateFile, ...]
    evidences: tuple[DictionarySourceDuplicateEvidence, ...]


@dataclass(frozen=True)
class DictionarySourceDuplicateScanResult:
    duplicate_groups: tuple[DictionarySourceDuplicateGroup, ...]


@dataclass(frozen=True)
class DictionarySourceDuplicateResolution:
    group: DictionarySourceDuplicateGroup
    keep_path: Path
    quarantine_paths: tuple[Path, ...]
    quarantine_destination_paths: tuple[Path, ...]


@dataclass(frozen=True)
class DictionarySourceDuplicateResolutionResult:
    archive_root: Path
    quarantine_root: Path
    manifest_path: Path
    batch_root: Path | None
    resolved_at: str
    duplicate_groups: tuple[DictionarySourceDuplicateGroup, ...]
    resolutions: tuple[DictionarySourceDuplicateResolution, ...]
    applied: bool

    @property
    def duplicate_group_count(self) -> int:
        return len(self.duplicate_groups)

    @property
    def quarantined_count(self) -> int:
        return sum(len(resolution.quarantine_paths) for resolution in self.resolutions)


@dataclass(frozen=True)
class ManagedDictionarySourceValidation:
    archive_root: Path
    ledger_path: Path
    metadata_by_sha256: Mapping[str, Mapping[str, object]]


def rename_dictionary_source_archive(
    archive_root: Path = DEFAULT_DICTIONARY_SOURCE_ROOT,
    *,
    client: SupportsCompletion,
    model: str,
    target_grade: str | None = None,
    apply: bool = False,
    ledger_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    progress: Callable[[str], None] | None = None,
) -> DictionarySourceRenameResult:
    root = archive_root.resolve()
    if not root.is_dir():
        raise DictionarySourceArchiveError(f"dictionary source archive folder does not exist: {archive_root}")
    forced_target_grade = normalize_target_grade(target_grade) if target_grade is not None else None

    resolved_ledger_path = (ledger_path or root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).resolve()
    ensure_inside_archive(root, resolved_ledger_path, allow_equal=False)
    duplicates = scan_dictionary_source_duplicates(root).duplicate_groups
    if duplicates:
        raise DictionarySourceArchiveError(format_dictionary_source_duplicate_message(root, duplicates))

    now = clock or (lambda: datetime.now().astimezone())
    ledger = load_dictionary_source_ledger(resolved_ledger_path)
    entries = ledger.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise DictionarySourceArchiveError(f"Dictionary source ledger has invalid entries object: {resolved_ledger_path}")

    reserved_targets: set[Path] = set()
    plans: list[DictionarySourceRenamePlan] = []
    for material in discover_unmanaged_dictionary_source_materials(root, ledger_entries=entries):
        if progress is not None:
            progress(f"classifying {material.relative_path}")
        metadata = classify_dictionary_source_material(material, client=client)
        if forced_target_grade is not None:
            metadata = metadata.model_copy(update={"grade_level": forced_target_grade})
        target_path = unique_dictionary_source_target_path(
            root,
            directory=material.path.parent,
            filename=dictionary_source_filename(metadata, material.extension),
            current_path=material.path,
            reserved_targets=reserved_targets,
        )
        reserved_targets.add(target_path)
        status = "planned"
        reason = "dry run; no file was renamed"
        if metadata.confidence < MIN_DICTIONARY_SOURCE_CLASSIFICATION_CONFIDENCE:
            status = "blocked"
            reason = (
                "classification confidence is below "
                f"{MIN_DICTIONARY_SOURCE_CLASSIFICATION_CONFIDENCE:.2f}"
            )
        elif target_path == material.path.resolve() and is_managed_dictionary_source_name(material.path.stem):
            status = "registered" if apply else "planned"
            reason = "ledger entry will be registered without renaming" if apply else "dry run; ledger entry would be registered"
        elif apply:
            status = "renamed"
            reason = "renamed"

        plans.append(
            DictionarySourceRenamePlan(
                material_id=material.material_id,
                source_path=material.path,
                target_path=target_path,
                original_path=material.relative_path,
                target_path_relative=dictionary_source_relative_path(root, target_path),
                first_seen_at=material.first_seen_at,
                metadata=metadata,
                status=status,
                reason=reason,
                applied=False,
            )
        )

    if apply:
        blocked = [plan for plan in plans if plan.status == "blocked"]
        if blocked:
            details = "\n".join(
                f"  - {plan.original_path}: confidence {plan.metadata.confidence:.2f}"
                for plan in blocked
            )
            raise DictionarySourceArchiveError(
                "dictionary source archive apply blocked by low-confidence classification:\n"
                f"{details}"
            )
        apply_dictionary_source_rename_plans(
            root=root,
            ledger_path=resolved_ledger_path,
            entries=entries,
            plans=tuple(plans),
            model=model,
            renamed_at=now().isoformat(timespec="seconds"),
        )
        plans = [
            DictionarySourceRenamePlan(
                material_id=plan.material_id,
                source_path=plan.source_path,
                target_path=plan.target_path,
                original_path=plan.original_path,
                target_path_relative=plan.target_path_relative,
                first_seen_at=plan.first_seen_at,
                metadata=plan.metadata,
                status=plan.status,
                reason=plan.reason,
                applied=plan.status in {"renamed", "registered"},
            )
            for plan in plans
        ]

    return DictionarySourceRenameResult(
        archive_root=root,
        ledger_path=resolved_ledger_path,
        dry_run=not apply,
        plans=tuple(plans),
    )


def apply_dictionary_source_rename_plans(
    *,
    root: Path,
    ledger_path: Path,
    entries: dict[str, object],
    plans: tuple[DictionarySourceRenamePlan, ...],
    model: str,
    renamed_at: str,
) -> None:
    moves: list[tuple[Path, Path, DictionarySourceRenamePlan]] = []
    seen_targets: set[Path] = set()
    for plan in plans:
        source = plan.source_path.resolve()
        target = plan.target_path.resolve()
        ensure_inside_archive(root, source, allow_equal=False)
        ensure_inside_archive(root, target, allow_equal=False)
        if not source.is_file():
            raise DictionarySourceArchiveError(f"Cannot manage missing dictionary source file: {source}")
        if target in seen_targets:
            raise DictionarySourceArchiveError(f"Duplicate dictionary source rename target planned: {target}")
        seen_targets.add(target)
        if target != source and target.exists():
            raise DictionarySourceArchiveError(f"Refusing to overwrite existing dictionary source file: {target}")
        if target != source:
            moves.append((source, target, plan))

    moved: list[tuple[Path, Path]] = []
    try:
        for source, target, _plan in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append((target, source))
        for plan in plans:
            update_dictionary_source_ledger_entry(
                entries,
                plan=plan,
                model=model,
                renamed_at=renamed_at,
            )
        write_json_atomic(
            ledger_path,
            {
                "schema_version": DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION,
                "updated_at": renamed_at,
                "entries": entries,
            },
        )
    except Exception as error:
        for target, source in reversed(moved):
            if target.exists() and not source.exists():
                target.rename(source)
        if isinstance(error, DictionarySourceArchiveError):
            raise
        raise DictionarySourceArchiveError(f"Failed to apply dictionary source archive renames: {error}") from error


def update_dictionary_source_ledger_entry(
    entries: dict[str, object],
    *,
    plan: DictionarySourceRenamePlan,
    model: str,
    renamed_at: str,
) -> None:
    entry = entries.get(plan.material_id)
    if not isinstance(entry, dict):
        entry = {}
    original_path = str(entry.get("original_path") or plan.original_path)
    history = entry.get("rename_history")
    if not isinstance(history, list):
        history = []
    if plan.source_path.resolve() != plan.target_path.resolve():
        history.append(
            {
                "from": plan.original_path,
                "to": plan.target_path_relative,
                "renamed_at": renamed_at,
                "model": model,
                "classification": plan.metadata.model_dump(mode="json"),
            }
        )
    stat = plan.target_path.stat()
    entries[plan.material_id] = {
        "material_id": plan.material_id,
        "sha256": plan.material_id.removeprefix("sha256:"),
        "byte_size": stat.st_size,
        "extension": plan.target_path.suffix.lower(),
        "original_path": original_path,
        "current_path": plan.target_path_relative,
        "first_seen_at": str(entry.get("first_seen_at") or plan.first_seen_at),
        "last_seen_at": renamed_at,
        "classification": plan.metadata.model_dump(mode="json"),
        "rename_history": history,
        "status": "active",
    }


def discover_unmanaged_dictionary_source_materials(
    archive_root: Path,
    *,
    ledger_entries: Mapping[str, object] | None = None,
) -> tuple[DictionarySourceMaterial, ...]:
    entries = ledger_entries or {}
    managed_paths = active_ledger_current_paths(archive_root, entries)
    materials: list[DictionarySourceMaterial] = []
    for path in discover_dictionary_source_paths(archive_root):
        if path.resolve() in managed_paths and valid_managed_dictionary_source_name_for_path(path):
            continue
        materials.append(build_dictionary_source_material(path, archive_root, ledger_entries=entries))
    return tuple(materials)


def discover_dictionary_source_paths(archive_root: Path) -> tuple[Path, ...]:
    root = archive_root.expanduser()
    if not root.exists():
        return ()
    paths = [
        path
        for path in root.rglob("*")
        if is_supported_dictionary_source_path(path)
        and not is_dictionary_source_archive_management_path(path, root)
    ]
    return tuple(sorted(paths, key=lambda path: natural_path_sort_key(path.relative_to(root))))


def is_supported_dictionary_source_path(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("~$"):
        return False
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_dictionary_source_archive_management_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return DEFAULT_DICTIONARY_SOURCE_DUPLICATE_QUARANTINE_DIRNAME in relative.parts


def build_dictionary_source_material(
    path: Path,
    archive_root: Path,
    *,
    ledger_entries: Mapping[str, object] | None = None,
) -> DictionarySourceMaterial:
    digest = file_sha256(path)
    material_id = f"sha256:{digest}"
    first_seen_at = ""
    entry = (ledger_entries or {}).get(material_id)
    if isinstance(entry, Mapping):
        first_seen_at = str(entry.get("first_seen_at") or "")
    if not first_seen_at:
        first_seen_at = file_first_seen_timestamp(path).isoformat(timespec="seconds")
    return DictionarySourceMaterial(
        path=path.resolve(),
        relative_path=dictionary_source_relative_path(archive_root, path),
        name=path.name,
        extension=path.suffix.lower(),
        byte_size=path.stat().st_size,
        sha256=digest,
        material_id=material_id,
        first_seen_at=first_seen_at,
        text_excerpt=extract_dictionary_source_text(path)[:MAX_DICTIONARY_SOURCE_PROMPT_TEXT_CHARS],
    )


def classify_dictionary_source_material(
    material: DictionarySourceMaterial,
    *,
    client: SupportsCompletion,
) -> DictionarySourceMetadata:
    if not material.text_excerpt.strip():
        return DictionarySourceMetadata(confidence=0.0)
    payload = {
        "original_path": material.relative_path,
        "original_filename": material.name,
        "extension": material.extension,
        "first_seen_date": material.first_seen_at[:10],
        "byte_size": material.byte_size,
        "text_excerpt": material.text_excerpt,
    }
    result = client.complete(
        [
            ChatMessage(role="system", content=DICTIONARY_SOURCE_RENAME_SYSTEM_PROMPT),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ],
        temperature=0.0,
        max_output_tokens=768,
        json_mode=True,
    )
    return parse_dictionary_source_metadata_response(result)


def parse_dictionary_source_metadata_response(result: CompletionResult) -> DictionarySourceMetadata:
    try:
        payload = parse_json_object(result.content)
    except LLMResponseError as error:
        raise DictionarySourceArchiveError(str(error)) from error
    try:
        return DictionarySourceMetadata.model_validate(payload)
    except ValueError as error:
        raise DictionarySourceArchiveError(f"Dictionary source metadata failed validation: {error}") from error


def dictionary_source_filename(metadata: DictionarySourceMetadata, extension: str) -> str:
    parts = [
        metadata.source_type,
        metadata.authority,
        metadata.grade_level,
        metadata.subject_slug,
        metadata.curriculum_year,
        metadata.term_slug,
        metadata.title_slug,
    ]
    return "_".join(parts) + extension.lower()


def unique_dictionary_source_target_path(
    root: Path,
    *,
    directory: Path,
    filename: str,
    current_path: Path,
    reserved_targets: set[Path] | None = None,
) -> Path:
    reserved = reserved_targets or set()
    target = (directory / filename).resolve()
    ensure_inside_archive(root, target, allow_equal=False)
    if target == current_path.resolve() or (not target.exists() and target not in reserved):
        return target
    stem = target.stem
    suffix = target.suffix
    counter = 2
    while True:
        candidate = (directory / f"{stem}-{counter}{suffix}").resolve()
        ensure_inside_archive(root, candidate, allow_equal=False)
        if candidate == current_path.resolve() or (not candidate.exists() and candidate not in reserved):
            return candidate
        counter += 1


def validate_managed_dictionary_source_archive(
    archive_root: Path,
    *,
    documents: Sequence[Path] | None = None,
    ledger_path: Path | None = None,
) -> ManagedDictionarySourceValidation:
    root = archive_root.resolve()
    source_paths = tuple(documents) if documents is not None else discover_dictionary_source_paths(root)
    resolved_ledger_path = (ledger_path or root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME).resolve()
    if not source_paths and not resolved_ledger_path.exists():
        return ManagedDictionarySourceValidation(root, resolved_ledger_path, {})
    if source_paths and not resolved_ledger_path.exists():
        raise DictionarySourceArchiveError(
            "dictionary source archive contains unmanaged source files; run "
            "--rename-dictionary-source-archive first, inspect the dry run, then rerun with "
            "--apply-dictionary-source-renames"
        )
    ensure_inside_archive(root, resolved_ledger_path, allow_equal=False)
    ledger = load_dictionary_source_ledger(resolved_ledger_path)
    entries = ledger.get("entries")
    if not isinstance(entries, Mapping):
        raise DictionarySourceArchiveError(f"Dictionary source ledger has invalid entries object: {resolved_ledger_path}")
    duplicates = scan_dictionary_source_duplicates(root).duplicate_groups
    if duplicates:
        raise DictionarySourceArchiveError(format_dictionary_source_duplicate_message(root, duplicates))

    metadata_by_sha256: dict[str, Mapping[str, object]] = {}
    active_entries = {
        str(key): entry
        for key, entry in entries.items()
        if isinstance(entry, Mapping) and str(entry.get("status") or "active") == "active"
    }
    for material_id, entry in active_entries.items():
        validate_dictionary_source_ledger_entry(root, material_id, entry)

    for path in source_paths:
        if is_dictionary_source_archive_management_path(path, root):
            continue
        digest = file_sha256(path)
        material_id = f"sha256:{digest}"
        entry = active_entries.get(material_id)
        if not isinstance(entry, Mapping):
            raise DictionarySourceArchiveError(
                f"unmanaged dictionary source file: {dictionary_source_relative_path(root, path)}"
            )
        relative_path = dictionary_source_relative_path(root, path)
        if str(entry.get("current_path") or "") != relative_path:
            raise DictionarySourceArchiveError(
                "dictionary source ledger current_path does not match file path: "
                f"{relative_path}"
            )
        if not is_managed_dictionary_source_name(path.stem):
            raise DictionarySourceArchiveError(
                f"dictionary source filename is not managed: {relative_path}"
            )
        metadata = validate_dictionary_source_metadata(entry.get("classification"), material_id=material_id)
        metadata_by_sha256[digest] = metadata

    return ManagedDictionarySourceValidation(root, resolved_ledger_path, metadata_by_sha256)


def validate_dictionary_source_ledger_entry(root: Path, material_id: str, entry: Mapping[str, object]) -> None:
    if not material_id.startswith("sha256:"):
        raise DictionarySourceArchiveError(f"Dictionary source ledger entry key is not a sha256 material id: {material_id}")
    current_path_text = str(entry.get("current_path") or "")
    if not current_path_text:
        raise DictionarySourceArchiveError(f"Dictionary source ledger entry has no current_path: {material_id}")
    path = (root / current_path_text).resolve()
    ensure_inside_archive(root, path, allow_equal=False)
    if not path.is_file():
        raise DictionarySourceArchiveError(f"Dictionary source ledger points to a missing file: {current_path_text}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise DictionarySourceArchiveError(f"Dictionary source ledger points to an unsupported file: {current_path_text}")
    digest = file_sha256(path)
    if digest != material_id.removeprefix("sha256:") or str(entry.get("sha256") or "") != digest:
        raise DictionarySourceArchiveError(f"Dictionary source ledger sha256 changed for: {current_path_text}")
    if int(entry.get("byte_size") or -1) != path.stat().st_size:
        raise DictionarySourceArchiveError(f"Dictionary source ledger byte_size changed for: {current_path_text}")
    if not is_managed_dictionary_source_name(path.stem):
        raise DictionarySourceArchiveError(f"Dictionary source filename is not managed: {current_path_text}")
    validate_dictionary_source_metadata(entry.get("classification"), material_id=material_id)


def validate_dictionary_source_metadata(value: object, *, material_id: str) -> Mapping[str, object]:
    try:
        metadata = DictionarySourceMetadata.model_validate(value)
    except ValueError as error:
        raise DictionarySourceArchiveError(
            f"Dictionary source ledger classification failed validation for {material_id}: {error}"
        ) from error
    return metadata.model_dump(mode="json")


def active_ledger_current_paths(root: Path, entries: Mapping[str, object]) -> set[Path]:
    paths: set[Path] = set()
    for entry in entries.values():
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("status") or "active") != "active":
            continue
        current_path = str(entry.get("current_path") or "")
        if not current_path:
            continue
        paths.add((root / current_path).resolve())
    return paths


def valid_managed_dictionary_source_name_for_path(path: Path) -> bool:
    return is_managed_dictionary_source_name(path.stem)


def is_managed_dictionary_source_name(stem: str) -> bool:
    return bool(MANAGED_DICTIONARY_SOURCE_NAME_PATTERN.fullmatch(stem))


def managed_dictionary_source_grade_from_stem(stem: str) -> str:
    parts = stem.split("_")
    return parts[2] if len(parts) > 2 else UNKNOWN_GRADE


def validate_managed_dictionary_source_grades(source_root: Path, *, target_grade: str) -> None:
    mismatches: list[Path] = []
    if not source_root.exists() or not source_root.is_dir():
        return
    for path in source_root.rglob("*"):
        if not path.is_file() or not is_managed_dictionary_source_name(path.stem):
            continue
        grade = managed_dictionary_source_grade_from_stem(path.stem)
        if grade not in {target_grade, UNKNOWN_GRADE}:
            mismatches.append(path)
    if mismatches:
        details = "\n".join(f"  - {path}" for path in sorted(mismatches, key=str))
        raise DictionarySourceArchiveError(
            f"dictionary source archive contains managed files outside target grade {target_grade}:\n{details}"
        )


def load_dictionary_source_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION, "entries": {}}
    payload = read_json_object(path, error_type=DictionarySourceArchiveError)
    schema_version = payload.get("schema_version")
    if schema_version != DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION:
        raise DictionarySourceArchiveError(
            f"Dictionary source ledger schema is {schema_version!r}, "
            f"expected {DICTIONARY_SOURCE_ARCHIVE_SCHEMA_VERSION!r}: {path}"
        )
    if not isinstance(payload.get("entries"), dict):
        raise DictionarySourceArchiveError(f"Dictionary source ledger has invalid entries object: {path}")
    return payload


def scan_dictionary_source_duplicates(archive_root: Path) -> DictionarySourceDuplicateScanResult:
    root = archive_root.resolve()
    files = tuple(build_dictionary_source_duplicate_file(path, root) for path in discover_dictionary_source_paths(root))
    evidences = [
        *same_byte_dictionary_source_duplicate_evidences(files),
        *exact_text_dictionary_source_duplicate_evidences(files),
    ]
    return DictionarySourceDuplicateScanResult(dictionary_source_duplicate_groups_from_evidences(files, tuple(evidences)))


def build_dictionary_source_duplicate_file(path: Path, root: Path) -> DictionarySourceDuplicateFile:
    normalized_text = normalize_dictionary_source_duplicate_text(extract_dictionary_source_full_text(path))
    return DictionarySourceDuplicateFile(
        path=path.resolve(),
        relative_path=dictionary_source_relative_path(root, path),
        suffix=path.suffix.lower(),
        sha256=file_sha256(path),
        byte_size=path.stat().st_size,
        text_fingerprint=stable_hash(normalized_text) if normalized_text else "",
        text_fingerprint_length=len(normalized_text),
        normalized_text=normalized_text,
    )


def same_byte_dictionary_source_duplicate_evidences(
    files: Sequence[DictionarySourceDuplicateFile],
) -> tuple[DictionarySourceDuplicateEvidence, ...]:
    grouped: dict[str, list[DictionarySourceDuplicateFile]] = {}
    for file in files:
        grouped.setdefault(file.sha256, []).append(file)
    return tuple(
        DictionarySourceDuplicateEvidence(
            reason="same_byte_sha256",
            detail=f"sha256:{digest}",
            paths=tuple(file.path for file in group),
        )
        for digest, group in sorted(grouped.items())
        if len(group) > 1
    )


def exact_text_dictionary_source_duplicate_evidences(
    files: Sequence[DictionarySourceDuplicateFile],
) -> tuple[DictionarySourceDuplicateEvidence, ...]:
    grouped: dict[str, list[DictionarySourceDuplicateFile]] = {}
    for file in files:
        if file.text_fingerprint:
            grouped.setdefault(file.text_fingerprint, []).append(file)
    evidences: list[DictionarySourceDuplicateEvidence] = []
    for fingerprint, group in sorted(grouped.items()):
        suffixes = {file.suffix for file in group}
        sha256s = {file.sha256 for file in group}
        if len(group) <= 1 or len(suffixes) <= 1 or len(sha256s) <= 1:
            continue
        evidences.append(
            DictionarySourceDuplicateEvidence(
                reason="cross_format_exact_text",
                detail=f"text_fingerprint:{fingerprint}",
                paths=tuple(file.path for file in group),
            )
        )
    return tuple(evidences)


def dictionary_source_duplicate_groups_from_evidences(
    files: Sequence[DictionarySourceDuplicateFile],
    evidences: tuple[DictionarySourceDuplicateEvidence, ...],
) -> tuple[DictionarySourceDuplicateGroup, ...]:
    if not evidences:
        return ()
    files_by_path = {file.path: file for file in files}
    groups: list[DictionarySourceDuplicateGroup] = []
    for sorted_paths in connected_path_components(
        (evidence.paths for evidence in evidences),
        sort_key=natural_path_sort_key,
    ):
        group_evidences = tuple(
            evidence
            for evidence in evidences
            if any(path in sorted_paths for path in evidence.paths)
        )
        groups.append(
            DictionarySourceDuplicateGroup(
                paths=sorted_paths,
                files=tuple(files_by_path[path] for path in sorted_paths if path in files_by_path),
                evidences=group_evidences,
            )
        )
    return tuple(sorted(groups, key=lambda group: natural_path_sort_key(group.paths[0])))


def resolve_dictionary_source_duplicates(
    archive_root: Path = DEFAULT_DICTIONARY_SOURCE_ROOT,
    *,
    apply: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> DictionarySourceDuplicateResolutionResult:
    root = archive_root.resolve()
    if not root.is_dir():
        raise DictionarySourceArchiveError(f"dictionary source archive folder does not exist: {archive_root}")
    now = clock or (lambda: datetime.now().astimezone())
    resolved_time = now()
    resolved_at = resolved_time.isoformat(timespec="seconds")
    timestamp = duplicate_resolution_timestamp(resolved_time)
    quarantine_root = (root / DEFAULT_DICTIONARY_SOURCE_DUPLICATE_QUARANTINE_DIRNAME).resolve()
    manifest_path = (quarantine_root / "manifest.json").resolve()
    batch_root = (quarantine_root / timestamp).resolve()
    ensure_inside_archive(root, quarantine_root, allow_equal=False)
    ensure_inside_archive(root, manifest_path, allow_equal=False)
    ensure_inside_archive(root, batch_root, allow_equal=False)
    scan_result = scan_dictionary_source_duplicates(root)
    ledger = load_dictionary_source_ledger(root / DEFAULT_DICTIONARY_SOURCE_LEDGER_FILENAME)
    entries = ledger.get("entries", {})
    active_paths = active_ledger_current_paths(root, entries if isinstance(entries, Mapping) else {})
    resolutions = tuple(
        build_dictionary_source_duplicate_resolution(root, batch_root, group, active_paths=active_paths)
        for group in scan_result.duplicate_groups
    )
    result = DictionarySourceDuplicateResolutionResult(
        archive_root=root,
        quarantine_root=quarantine_root,
        manifest_path=manifest_path,
        batch_root=batch_root if resolutions else None,
        resolved_at=resolved_at,
        duplicate_groups=scan_result.duplicate_groups,
        resolutions=resolutions,
        applied=False,
    )
    if not apply or not resolutions:
        return result
    apply_dictionary_source_duplicate_resolution_result(result)
    return DictionarySourceDuplicateResolutionResult(
        archive_root=root,
        quarantine_root=quarantine_root,
        manifest_path=manifest_path,
        batch_root=batch_root,
        resolved_at=resolved_at,
        duplicate_groups=scan_result.duplicate_groups,
        resolutions=resolutions,
        applied=True,
    )


def apply_dictionary_source_duplicate_resolution_result(
    result: DictionarySourceDuplicateResolutionResult,
) -> DictionarySourceDuplicateResolutionResult:
    if result.applied or not result.resolutions:
        return result
    if result.batch_root is None:
        raise DictionarySourceArchiveError("Dictionary source duplicate resolution result has no quarantine batch root.")
    apply_dictionary_source_duplicate_resolutions(
        archive_root=result.archive_root,
        manifest_path=result.manifest_path,
        batch_root=result.batch_root,
        resolutions=result.resolutions,
        resolved_at=result.resolved_at,
    )
    return DictionarySourceDuplicateResolutionResult(
        archive_root=result.archive_root,
        quarantine_root=result.quarantine_root,
        manifest_path=result.manifest_path,
        batch_root=result.batch_root,
        resolved_at=result.resolved_at,
        duplicate_groups=result.duplicate_groups,
        resolutions=result.resolutions,
        applied=True,
    )


def build_dictionary_source_duplicate_resolution(
    archive_root: Path,
    batch_root: Path,
    group: DictionarySourceDuplicateGroup,
    *,
    active_paths: set[Path],
) -> DictionarySourceDuplicateResolution:
    keep_path = recommended_dictionary_source_duplicate_survivor(group, active_paths=active_paths)
    quarantine_paths = tuple(path for path in group.paths if path != keep_path)
    destinations = unique_quarantine_destination_paths(archive_root, batch_root, quarantine_paths)
    return DictionarySourceDuplicateResolution(
        group=group,
        keep_path=keep_path,
        quarantine_paths=quarantine_paths,
        quarantine_destination_paths=destinations,
    )


def recommended_dictionary_source_duplicate_survivor(
    group: DictionarySourceDuplicateGroup,
    *,
    active_paths: set[Path],
) -> Path:
    return min(
        group.paths,
        key=lambda path: (
            0 if path.resolve() in active_paths else 1,
            0 if is_managed_dictionary_source_name(path.stem) else 1,
            FORMAT_RANK.get(path.suffix.lower(), 99),
            natural_path_sort_key(path),
        ),
    )


def unique_quarantine_destination_paths(
    archive_root: Path,
    batch_root: Path,
    quarantine_paths: tuple[Path, ...],
) -> tuple[Path, ...]:
    destinations: list[Path] = []
    seen: set[Path] = set()
    for source in quarantine_paths:
        try:
            relative = source.relative_to(archive_root)
        except ValueError:
            relative = Path(source.name)
        candidate_name = "__".join(relative.parts)
        destination = (batch_root / candidate_name).resolve()
        counter = 2
        while destination in seen or destination.exists():
            destination = (batch_root / f"{Path(candidate_name).stem}-{counter}{source.suffix}").resolve()
            counter += 1
        ensure_inside_archive(archive_root, destination, allow_equal=False)
        seen.add(destination)
        destinations.append(destination)
    return tuple(destinations)


def apply_dictionary_source_duplicate_resolutions(
    *,
    archive_root: Path,
    manifest_path: Path,
    batch_root: Path,
    resolutions: tuple[DictionarySourceDuplicateResolution, ...],
    resolved_at: str,
) -> None:
    moves: list[tuple[Path, Path]] = []
    seen_destinations: set[Path] = set()
    for resolution in resolutions:
        for source, destination in zip(resolution.quarantine_paths, resolution.quarantine_destination_paths, strict=True):
            if destination in seen_destinations:
                raise DictionarySourceArchiveError(f"Duplicate quarantine destination planned: {destination}")
            seen_destinations.add(destination)
            ensure_inside_archive(archive_root, source.resolve(), allow_equal=False)
            ensure_inside_archive(archive_root, destination, allow_equal=False)
            if not source.is_file():
                raise DictionarySourceArchiveError(f"Cannot quarantine missing duplicate file: {source}")
            if destination.exists():
                raise DictionarySourceArchiveError(f"Refusing to overwrite existing quarantine file: {destination}")
            moves.append((source, destination))

    moved: list[tuple[Path, Path]] = []
    try:
        batch_root.mkdir(parents=True, exist_ok=False)
        for source, destination in moves:
            source.rename(destination)
            moved.append((destination, source))
        write_dictionary_source_duplicate_manifest(
            archive_root=archive_root,
            manifest_path=manifest_path,
            resolutions=resolutions,
            resolved_at=resolved_at,
        )
    except Exception as error:
        for destination, source in reversed(moved):
            if destination.exists() and not source.exists():
                destination.rename(source)
        if isinstance(error, DictionarySourceArchiveError):
            raise
        raise DictionarySourceArchiveError(f"Failed to resolve dictionary source duplicates: {error}") from error


def write_dictionary_source_duplicate_manifest(
    *,
    archive_root: Path,
    manifest_path: Path,
    resolutions: tuple[DictionarySourceDuplicateResolution, ...],
    resolved_at: str,
) -> None:
    if manifest_path.exists():
        manifest = read_json_object(manifest_path, error_type=DictionarySourceArchiveError)
        if manifest.get("schema_version") != DICTIONARY_SOURCE_DUPLICATE_MANIFEST_SCHEMA_VERSION:
            raise DictionarySourceArchiveError(
                f"Dictionary source duplicate manifest schema is {manifest.get('schema_version')!r}, "
                f"expected {DICTIONARY_SOURCE_DUPLICATE_MANIFEST_SCHEMA_VERSION!r}: {manifest_path}"
            )
        existing_resolutions = manifest.get("resolutions")
        if not isinstance(existing_resolutions, list):
            raise DictionarySourceArchiveError(
                f"Dictionary source duplicate manifest has invalid resolutions list: {manifest_path}"
            )
    else:
        manifest = {
            "schema_version": DICTIONARY_SOURCE_DUPLICATE_MANIFEST_SCHEMA_VERSION,
            "archive_root": archive_root.as_posix(),
            "resolutions": [],
        }
        existing_resolutions = manifest["resolutions"]
    existing_resolutions.extend(
        dictionary_source_duplicate_resolution_record(archive_root, resolution, resolved_at)
        for resolution in resolutions
    )
    manifest["updated_at"] = resolved_at
    write_json_atomic(manifest_path, manifest)


def dictionary_source_duplicate_resolution_record(
    archive_root: Path,
    resolution: DictionarySourceDuplicateResolution,
    resolved_at: str,
) -> dict[str, object]:
    file_by_path = {file.path: file for file in resolution.group.files}
    return {
        "resolved_at": resolved_at,
        "reasons": [
            {
                "reason": evidence.reason,
                "detail": evidence.detail,
                "files": [dictionary_source_relative_path(archive_root, path) for path in evidence.paths],
            }
            for evidence in resolution.group.evidences
        ],
        "kept": dictionary_source_duplicate_file_record(archive_root, file_by_path[resolution.keep_path]),
        "quarantined": [
            {
                **dictionary_source_duplicate_file_record(archive_root, file_by_path[source]),
                "quarantine_path": dictionary_source_relative_path(archive_root, destination),
                "quarantine_batch": dictionary_source_relative_path(archive_root, destination.parent),
            }
            for source, destination in zip(
                resolution.quarantine_paths,
                resolution.quarantine_destination_paths,
                strict=True,
            )
        ],
    }


def dictionary_source_duplicate_file_record(
    archive_root: Path,
    file: DictionarySourceDuplicateFile,
) -> dict[str, object]:
    return {
        "path": dictionary_source_relative_path(archive_root, file.path),
        "name": file.path.name,
        "suffix": file.suffix,
        "sha256": file.sha256,
        "text_fingerprint": file.text_fingerprint,
        "text_fingerprint_length": file.text_fingerprint_length,
        "byte_size": file.byte_size,
    }


def extract_dictionary_source_text(path: Path) -> str:
    return extract_dictionary_source_full_text(path, max_chars=MAX_DICTIONARY_SOURCE_PROMPT_TEXT_CHARS)


def extract_dictionary_source_full_text(path: Path, *, max_chars: int | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        chunks: list[str] = []
        with fitz.open(path) as document:
            for page in document:
                chunks.append(page.get_text("text", sort=True))
                text = "\n".join(chunks)
                if max_chars is not None and len(text) >= max_chars:
                    return clean_layout_text(text)[:max_chars]
        return clean_layout_text("\n".join(chunks))
    if suffix in {".txt", ".md"}:
        text = read_text_file(path)
        cleaned = clean_layout_text(text)
        return cleaned[:max_chars] if max_chars is not None else cleaned
    return ""


def normalize_dictionary_source_duplicate_text(text: str) -> str:
    text = clean_layout_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def format_dictionary_source_duplicate_message(
    archive_root: Path,
    duplicates: tuple[DictionarySourceDuplicateGroup, ...],
) -> str:
    lines = [
        "duplicate dictionary source archive files detected in "
        f"{archive_root}; run --resolve-dictionary-source-duplicates before managing or building dictionary sources:"
    ]
    for group in duplicates:
        for evidence in group.evidences:
            lines.append(f"  reason: {evidence.reason} {evidence.detail}")
        for path in group.paths:
            lines.append(f"    - {dictionary_source_relative_path(archive_root, path)}")
    return "\n".join(lines)


def dictionary_source_relative_path(root: Path, path: Path) -> str:
    return relative_path(root, path)


def normalize_grade_level(value: object) -> str:
    return normalize_shared_grade_level(value)


def safe_slug(value: object, *, fallback: str) -> str:
    return safe_ascii_slug(value, fallback=fallback, max_chars=MAX_DICTIONARY_SOURCE_SLUG_CHARS)


def natural_path_sort_key(path: Path) -> tuple[tuple[int, int | str], ...]:
    return natural_path_parts_sort_key(path)


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_inside_archive(root: Path, path: Path, *, allow_equal: bool) -> None:
    ensure_inside_root(
        root,
        path,
        allow_equal=allow_equal,
        error_type=DictionarySourceArchiveError,
        label="dictionary source archive",
    )
