from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import fitz
from docx import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator

from wordbyword._archive_common import (
    connected_path_components,
    duplicate_resolution_timestamp,
    ensure_inside_root,
    file_first_seen_timestamp,
    file_sha256,
    natural_filename_sort_key,
    relative_path,
    safe_ascii_slug,
)
from wordbyword._json_io import read_json_object, write_json_atomic
from wordbyword.dictionary_build.llm import (
    ChatMessage,
    CompletionResult,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    parse_json_object,
)
from wordbyword.grade_profiles import GRADE_WITH_UNKNOWN_PATTERN, normalize_grade_level as normalize_shared_grade_level

ARCHIVE_SCHEMA_VERSION = "wordbyword.class_session_archive.v1"
DEFAULT_VOCABULARY_INTELLIGENCE_ROOT = Path("wbw-vocabulary-intelligence")
DEFAULT_CLASS_SESSION_ARCHIVE_ROOT = DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "class-session-archive"
DEFAULT_ARCHIVE_LEDGER_FILENAME = ".wordbyword-archive-ledger.json"
SUPPORTED_ARCHIVE_EXTENSIONS = {".docx", ".pdf"}
MAX_ARCHIVE_PROMPT_TEXT_CHARS = 12000
MAX_FILENAME_SLUG_CHARS = 48
MIN_ARCHIVE_DUPLICATE_TEXT_CHARS = 200
MIN_ARCHIVE_NEAR_DUPLICATE_TEXT_CHARS = 600
ARCHIVE_NEAR_DUPLICATE_SHINGLE_CHARS = 5
ARCHIVE_NEAR_DUPLICATE_DICE_THRESHOLD = 0.88
ARCHIVE_NEAR_DUPLICATE_CONTAINMENT_THRESHOLD = 0.94
ARCHIVE_NEAR_DUPLICATE_MIN_LENGTH_RATIO = 0.60
ARCHIVE_NEAR_DUPLICATE_ACTION_CONFIDENCE = 0.86
ARCHIVE_NEAR_DUPLICATE_QUALITY_DEADBAND = 0.05
MAX_ARCHIVE_NEAR_DUPLICATE_CANDIDATES = 24
MAX_ARCHIVE_NEAR_DUPLICATE_REVIEW_TEXT_CHARS = 8000
DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME = ".wordbyword-duplicates"
ARCHIVE_DUPLICATE_MANIFEST_SCHEMA_VERSION = "wordbyword.class_session_archive_duplicates.v1"
DUPLICATE_REASON_SAME_FORMAT_SHA256 = "same_format_sha256"
DUPLICATE_REASON_CROSS_FORMAT_TEXT = "cross_format_text_fingerprint"
DUPLICATE_REASON_NEAR_TEXT_MODEL_REVIEW = "near_text_model_review"
ARCHIVE_NAME_PATTERN = re.compile(
    rf"^\d{{4}}-\d{{2}}-\d{{2}}_{GRADE_WITH_UNKNOWN_PATTERN}-[a-z0-9]+(?:-[a-z0-9]+)*$"
)

ARCHIVE_RENAME_SYSTEM_PROMPT = """You label archived Korean class printouts for safe filenames.
Use only the supplied document text and file metadata.
Infer the Korean K-12 grade difficulty when possible.
Return exactly one JSON object with these keys:
- grade_level: one of grade1 through grade12, or grade-unknown
- topic_slug: lowercase ASCII hyphen slug, broad topic, 1 to 5 words
- short_title_slug: lowercase ASCII hyphen slug, specific material title, 2 to 8 words
- korean_title: compact Korean title for audit only
- confidence: number from 0.0 to 1.0
Do not include markdown fences or commentary."""

ARCHIVE_NEAR_DUPLICATE_REVIEW_SYSTEM_PROMPT = """You review suspected duplicate Korean class-session archive files.
Use only the supplied extracted text and metadata.
Decide whether the files are the same class material, related but distinct materials, or uncertain.
Do not prefer a file because of filename, format, age, or whether it is already managed.
Score content_quality from 0.0 to 1.0 for each file based on useful learner-visible content, completeness, clarity, and extraction quality.
Return exactly one JSON object with these keys:
- verdict: one of same_material, related_but_distinct, uncertain
- confidence: number from 0.0 to 1.0
- reason: concise explanation
- files: array with exactly one object per supplied file; each object has filename, content_quality, reason
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


class ArchiveNearDuplicateFileReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content_quality: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("filename", mode="before")
    @classmethod
    def normalize_filename(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        return str(value or "").strip()[:300]


class ArchiveNearDuplicateReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["same_material", "related_but_distinct", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    files: list[ArchiveNearDuplicateFileReviewPayload]

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        return str(value or "").strip()[:500]


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
class ArchiveDuplicateFile:
    path: Path
    suffix: str
    sha256: str
    first_seen_at: str
    text_fingerprint: str
    text_fingerprint_length: int
    normalized_text: str
    review_text: str


@dataclass(frozen=True)
class ArchiveDuplicateEvidence:
    reason: str
    detail: str
    paths: tuple[Path, ...]
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ArchiveNearDuplicateCandidate:
    paths: tuple[Path, Path]
    dice_similarity: float
    containment_similarity: float
    length_ratio: float


@dataclass(frozen=True)
class ArchiveNearDuplicateFileScore:
    path: Path
    content_quality: float
    reason: str


@dataclass(frozen=True)
class ArchiveNearDuplicateReview:
    candidate: ArchiveNearDuplicateCandidate
    verdict: str
    confidence: float
    reason: str
    model: str
    file_scores: tuple[ArchiveNearDuplicateFileScore, ...]

    @property
    def actionable(self) -> bool:
        return self.verdict == "same_material" and self.confidence >= ARCHIVE_NEAR_DUPLICATE_ACTION_CONFIDENCE


@dataclass(frozen=True)
class ArchiveDuplicateGroup:
    paths: tuple[Path, ...]
    files: tuple[ArchiveDuplicateFile, ...]
    evidences: tuple[ArchiveDuplicateEvidence, ...]


@dataclass(frozen=True)
class ArchiveDuplicateScanResult:
    duplicate_groups: tuple[ArchiveDuplicateGroup, ...]
    near_duplicate_reviews: tuple[ArchiveNearDuplicateReview, ...] = ()


@dataclass(frozen=True)
class ArchiveDuplicateResolution:
    group: ArchiveDuplicateGroup
    keep_path: Path
    quarantine_paths: tuple[Path, ...]
    quarantine_destination_paths: tuple[Path, ...]


@dataclass(frozen=True)
class ArchiveDuplicateResolutionResult:
    archive_root: Path
    quarantine_root: Path
    manifest_path: Path
    batch_root: Path | None
    resolved_at: str
    duplicate_groups: tuple[ArchiveDuplicateGroup, ...]
    resolutions: tuple[ArchiveDuplicateResolution, ...]
    applied: bool
    near_duplicate_reviews: tuple[ArchiveNearDuplicateReview, ...] = ()

    @property
    def duplicate_group_count(self) -> int:
        return len(self.duplicate_groups)

    @property
    def quarantined_count(self) -> int:
        return sum(len(resolution.quarantine_paths) for resolution in self.resolutions)


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
    duplicates = scan_archive_duplicates(
        root,
        include_near_duplicates=True,
        near_duplicate_client=client,
        near_duplicate_model=model,
    ).duplicate_groups
    if duplicates:
        raise ClassSessionArchiveError(format_archive_duplicate_message(root, duplicates))
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


def discover_archive_duplicates(
    archive_root: Path,
    *,
    include_near_duplicates: bool = False,
    near_duplicate_client: SupportsCompletion | None = None,
    near_duplicate_model: str | None = None,
) -> tuple[ArchiveDuplicateGroup, ...]:
    return scan_archive_duplicates(
        archive_root,
        include_near_duplicates=include_near_duplicates,
        near_duplicate_client=near_duplicate_client,
        near_duplicate_model=near_duplicate_model,
    ).duplicate_groups


def scan_archive_duplicates(
    archive_root: Path,
    *,
    include_near_duplicates: bool = False,
    near_duplicate_client: SupportsCompletion | None = None,
    near_duplicate_model: str | None = None,
) -> ArchiveDuplicateScanResult:
    files = tuple(build_archive_duplicate_file(path) for path in discover_archive_duplicate_scan_paths(archive_root))
    evidences = [
        *same_format_duplicate_evidences(files),
        *cross_format_text_duplicate_evidences(files),
    ]
    if evidences:
        return ArchiveDuplicateScanResult(archive_duplicate_groups_from_evidences(files, tuple(evidences)))

    near_duplicate_reviews: tuple[ArchiveNearDuplicateReview, ...] = ()
    if include_near_duplicates:
        candidates = discover_archive_near_duplicate_candidates_from_files(files)
        if len(candidates) > MAX_ARCHIVE_NEAR_DUPLICATE_CANDIDATES:
            raise ClassSessionArchiveError(
                "Too many suspected near-duplicate class-session archive pairs to review "
                f"({len(candidates)} candidates; limit is {MAX_ARCHIVE_NEAR_DUPLICATE_CANDIDATES}). "
                "Resolve obvious duplicates or split the archive, then rerun."
            )
        if candidates:
            if near_duplicate_client is None:
                raise ClassSessionArchiveError(
                    "Near-duplicate class-session archive candidates require Ollama review; "
                    "rerun through the CLI or provide a near-duplicate review client."
                )
            near_duplicate_reviews = review_archive_near_duplicate_candidates(
                candidates,
                files=files,
                client=near_duplicate_client,
                model=near_duplicate_model or "",
            )
            evidences.extend(near_duplicate_duplicate_evidences(near_duplicate_reviews))

    return ArchiveDuplicateScanResult(
        duplicate_groups=archive_duplicate_groups_from_evidences(files, tuple(evidences)),
        near_duplicate_reviews=near_duplicate_reviews,
    )


def archive_duplicate_groups_from_evidences(
    files: tuple[ArchiveDuplicateFile, ...],
    evidences: tuple[ArchiveDuplicateEvidence, ...],
) -> tuple[ArchiveDuplicateGroup, ...]:
    if not evidences:
        return ()
    file_by_path = {file.path: file for file in files}
    groups: list[ArchiveDuplicateGroup] = []
    for paths in connected_path_components((evidence.paths for evidence in evidences), sort_key=natural_path_sort_key):
        path_set = set(paths)
        component_evidences = tuple(
            sorted(
                (evidence for evidence in evidences if any(path in path_set for path in evidence.paths)),
                key=lambda evidence: (evidence.reason, evidence.detail, tuple(natural_path_sort_key(path) for path in evidence.paths)),
            )
        )
        if not component_evidences:
            continue
        groups.append(
            ArchiveDuplicateGroup(
                paths=paths,
                files=tuple(file_by_path[path] for path in paths),
                evidences=component_evidences,
            )
        )
    return tuple(sorted(groups, key=lambda group: tuple(natural_path_sort_key(path) for path in group.paths)))


def discover_archive_duplicate_scan_paths(archive_root: Path) -> tuple[Path, ...]:
    if not archive_root.exists():
        return ()
    return tuple(
        sorted(
            (path for path in archive_root.iterdir() if is_supported_archive_material_path(path)),
            key=natural_path_sort_key,
        )
    )


def build_archive_duplicate_file(path: Path) -> ArchiveDuplicateFile:
    full_text = extract_archive_full_text(path)
    compact_text = normalize_archive_duplicate_text(full_text)
    text_fingerprint = ""
    if len(compact_text) >= MIN_ARCHIVE_DUPLICATE_TEXT_CHARS:
        text_fingerprint = hashlib.sha256(compact_text.encode("utf-8")).hexdigest()
    return ArchiveDuplicateFile(
        path=path,
        suffix=path.suffix.lower(),
        sha256=file_sha256(path),
        first_seen_at=file_first_seen_timestamp(path).isoformat(timespec="seconds"),
        text_fingerprint=text_fingerprint,
        text_fingerprint_length=len(compact_text),
        normalized_text=compact_text,
        review_text=full_text[:MAX_ARCHIVE_NEAR_DUPLICATE_REVIEW_TEXT_CHARS],
    )


def same_format_duplicate_evidences(files: tuple[ArchiveDuplicateFile, ...]) -> tuple[ArchiveDuplicateEvidence, ...]:
    groups: dict[tuple[str, str], list[ArchiveDuplicateFile]] = {}
    for file in files:
        groups.setdefault((file.suffix, file.sha256), []).append(file)

    evidences: list[ArchiveDuplicateEvidence] = []
    for (suffix, digest), grouped_files in groups.items():
        if len(grouped_files) < 2:
            continue
        paths = tuple(sorted((file.path for file in grouped_files), key=natural_path_sort_key))
        evidences.append(
            ArchiveDuplicateEvidence(
                reason=DUPLICATE_REASON_SAME_FORMAT_SHA256,
                detail=f"{suffix} sha256:{digest}",
                paths=paths,
            )
        )
    return tuple(sorted(evidences, key=lambda evidence: (evidence.detail, tuple(natural_path_sort_key(path) for path in evidence.paths))))


def cross_format_text_duplicate_evidences(files: tuple[ArchiveDuplicateFile, ...]) -> tuple[ArchiveDuplicateEvidence, ...]:
    groups: dict[str, list[ArchiveDuplicateFile]] = {}
    for file in files:
        if not file.text_fingerprint:
            continue
        groups.setdefault(file.text_fingerprint, []).append(file)

    evidences: list[ArchiveDuplicateEvidence] = []
    for fingerprint, grouped_files in groups.items():
        suffixes = {file.suffix for file in grouped_files}
        if len(grouped_files) < 2 or not {".docx", ".pdf"}.issubset(suffixes):
            continue
        paths = tuple(sorted((file.path for file in grouped_files), key=natural_path_sort_key))
        evidences.append(
            ArchiveDuplicateEvidence(
                reason=DUPLICATE_REASON_CROSS_FORMAT_TEXT,
                detail=f"text_sha256:{fingerprint}",
                paths=paths,
            )
        )
    return tuple(sorted(evidences, key=lambda evidence: (evidence.detail, tuple(natural_path_sort_key(path) for path in evidence.paths))))


def discover_archive_near_duplicate_candidates(archive_root: Path) -> tuple[ArchiveNearDuplicateCandidate, ...]:
    files = tuple(build_archive_duplicate_file(path) for path in discover_archive_duplicate_scan_paths(archive_root))
    return discover_archive_near_duplicate_candidates_from_files(files)


def discover_archive_near_duplicate_candidates_from_files(
    files: tuple[ArchiveDuplicateFile, ...],
) -> tuple[ArchiveNearDuplicateCandidate, ...]:
    candidate_files = tuple(
        file
        for file in files
        if len(file.normalized_text) >= MIN_ARCHIVE_NEAR_DUPLICATE_TEXT_CHARS
    )
    shingles_by_path = {
        file.path: archive_text_shingles(file.normalized_text)
        for file in candidate_files
    }
    candidates: list[ArchiveNearDuplicateCandidate] = []
    for left_index, left in enumerate(candidate_files):
        left_shingles = shingles_by_path[left.path]
        if not left_shingles:
            continue
        for right in candidate_files[left_index + 1 :]:
            right_shingles = shingles_by_path[right.path]
            if not right_shingles:
                continue
            left_length = len(left.normalized_text)
            right_length = len(right.normalized_text)
            length_ratio = min(left_length, right_length) / max(left_length, right_length)
            if length_ratio < ARCHIVE_NEAR_DUPLICATE_MIN_LENGTH_RATIO:
                continue
            overlap = len(left_shingles & right_shingles)
            dice_similarity = (2.0 * overlap) / (len(left_shingles) + len(right_shingles))
            containment_similarity = overlap / min(len(left_shingles), len(right_shingles))
            if (
                dice_similarity >= ARCHIVE_NEAR_DUPLICATE_DICE_THRESHOLD
                or containment_similarity >= ARCHIVE_NEAR_DUPLICATE_CONTAINMENT_THRESHOLD
            ):
                paths = tuple(sorted((left.path, right.path), key=natural_path_sort_key))
                candidates.append(
                    ArchiveNearDuplicateCandidate(
                        paths=(paths[0], paths[1]),
                        dice_similarity=dice_similarity,
                        containment_similarity=containment_similarity,
                        length_ratio=length_ratio,
                    )
                )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -candidate.dice_similarity,
                -candidate.containment_similarity,
                tuple(natural_path_sort_key(path) for path in candidate.paths),
            ),
        )
    )


def archive_text_shingles(text: str) -> set[str]:
    if len(text) < ARCHIVE_NEAR_DUPLICATE_SHINGLE_CHARS:
        return set()
    return {
        text[index : index + ARCHIVE_NEAR_DUPLICATE_SHINGLE_CHARS]
        for index in range(0, len(text) - ARCHIVE_NEAR_DUPLICATE_SHINGLE_CHARS + 1)
    }


def review_archive_near_duplicate_candidates(
    candidates: tuple[ArchiveNearDuplicateCandidate, ...],
    *,
    files: tuple[ArchiveDuplicateFile, ...],
    client: SupportsCompletion,
    model: str,
) -> tuple[ArchiveNearDuplicateReview, ...]:
    file_by_path = {file.path: file for file in files}
    reviews = [
        review_archive_near_duplicate_candidate(
            candidate,
            files=(file_by_path[candidate.paths[0]], file_by_path[candidate.paths[1]]),
            client=client,
            model=model,
        )
        for candidate in candidates
    ]
    return tuple(reviews)


def review_archive_near_duplicate_candidate(
    candidate: ArchiveNearDuplicateCandidate,
    *,
    files: tuple[ArchiveDuplicateFile, ArchiveDuplicateFile],
    client: SupportsCompletion,
    model: str,
) -> ArchiveNearDuplicateReview:
    payload = {
        "deterministic_similarity": {
            "dice_similarity": round(candidate.dice_similarity, 6),
            "containment_similarity": round(candidate.containment_similarity, 6),
            "length_ratio": round(candidate.length_ratio, 6),
            "normalized_text_min_chars": MIN_ARCHIVE_NEAR_DUPLICATE_TEXT_CHARS,
        },
        "files": [
            {
                "filename": file.path.name,
                "suffix": file.suffix,
                "byte_size": file.path.stat().st_size,
                "first_seen_at": file.first_seen_at,
                "managed_filename": is_managed_archive_name(file.path.stem),
                "normalized_text_length": file.text_fingerprint_length,
                "extracted_text_excerpt": file.review_text,
            }
            for file in files
        ],
    }
    try:
        result = client.complete(
            [
                ChatMessage(role="system", content=ARCHIVE_NEAR_DUPLICATE_REVIEW_SYSTEM_PROMPT),
                ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            temperature=0.0,
            max_output_tokens=1024,
            json_mode=True,
        )
    except (LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        raise ClassSessionArchiveError(f"Archive near-duplicate review failed: {error}") from error
    return parse_archive_near_duplicate_review(result, candidate=candidate, files=files, model=model or result.model)


def parse_archive_near_duplicate_review(
    result: CompletionResult,
    *,
    candidate: ArchiveNearDuplicateCandidate,
    files: tuple[ArchiveDuplicateFile, ArchiveDuplicateFile],
    model: str,
) -> ArchiveNearDuplicateReview:
    try:
        payload = parse_json_object(result.content)
    except LLMResponseError as error:
        raise ClassSessionArchiveError(str(error)) from error
    try:
        review_payload = ArchiveNearDuplicateReviewPayload.model_validate(payload)
    except ValueError as error:
        raise ClassSessionArchiveError(f"Archive near-duplicate review failed validation: {error}") from error

    expected_filenames = {file.path.name for file in files}
    reviewed_filenames = [file.filename for file in review_payload.files]
    if set(reviewed_filenames) != expected_filenames or len(reviewed_filenames) != len(expected_filenames):
        expected = ", ".join(sorted(expected_filenames))
        received = ", ".join(sorted(reviewed_filenames))
        raise ClassSessionArchiveError(
            "Archive near-duplicate review returned file scores for the wrong files "
            f"(expected: {expected}; received: {received})."
        )

    review_by_filename = {file.filename: file for file in review_payload.files}
    file_scores = tuple(
        ArchiveNearDuplicateFileScore(
            path=file.path,
            content_quality=review_by_filename[file.path.name].content_quality,
            reason=review_by_filename[file.path.name].reason,
        )
        for file in files
    )
    return ArchiveNearDuplicateReview(
        candidate=candidate,
        verdict=review_payload.verdict,
        confidence=review_payload.confidence,
        reason=review_payload.reason,
        model=model or result.model,
        file_scores=file_scores,
    )


def near_duplicate_duplicate_evidences(
    reviews: tuple[ArchiveNearDuplicateReview, ...],
) -> tuple[ArchiveDuplicateEvidence, ...]:
    evidences: list[ArchiveDuplicateEvidence] = []
    for review in reviews:
        if not review.actionable:
            continue
        detail = (
            f"dice:{review.candidate.dice_similarity:.3f} "
            f"containment:{review.candidate.containment_similarity:.3f} "
            f"length_ratio:{review.candidate.length_ratio:.3f} "
            f"confidence:{review.confidence:.3f} "
            f"model:{review.model or 'unknown'}"
        )
        evidences.append(
            ArchiveDuplicateEvidence(
                reason=DUPLICATE_REASON_NEAR_TEXT_MODEL_REVIEW,
                detail=detail,
                paths=review.candidate.paths,
                metadata=archive_near_duplicate_review_record(review),
            )
        )
    return tuple(
        sorted(
            evidences,
            key=lambda evidence: (
                evidence.detail,
                tuple(natural_path_sort_key(path) for path in evidence.paths),
            ),
        )
    )


def archive_near_duplicate_review_record(review: ArchiveNearDuplicateReview) -> dict[str, object]:
    return {
        "verdict": review.verdict,
        "confidence": review.confidence,
        "model": review.model,
        "reason": review.reason,
        "deterministic_similarity": {
            "dice_similarity": review.candidate.dice_similarity,
            "containment_similarity": review.candidate.containment_similarity,
            "length_ratio": review.candidate.length_ratio,
            "shingle_chars": ARCHIVE_NEAR_DUPLICATE_SHINGLE_CHARS,
            "min_text_chars": MIN_ARCHIVE_NEAR_DUPLICATE_TEXT_CHARS,
        },
        "files": [
            {
                "name": score.path.name,
                "content_quality": score.content_quality,
                "reason": score.reason,
            }
            for score in review.file_scores
        ],
    }


def discover_same_format_archive_duplicates(archive_root: Path) -> tuple[SameFormatArchiveDuplicate, ...]:
    duplicates: list[SameFormatArchiveDuplicate] = []
    files = tuple(build_archive_duplicate_file(path) for path in discover_archive_duplicate_scan_paths(archive_root))
    for evidence in same_format_duplicate_evidences(files):
        suffix, digest = parse_same_format_duplicate_detail(evidence.detail)
        duplicates.append(
            SameFormatArchiveDuplicate(
                suffix=suffix,
                sha256=digest,
                paths=evidence.paths,
            )
        )
    return tuple(duplicates)


def parse_same_format_duplicate_detail(detail: str) -> tuple[str, str]:
    suffix, digest = detail.split(" sha256:", 1)
    return suffix, digest


def format_archive_duplicate_message(archive_root: Path, duplicates: tuple[ArchiveDuplicateGroup, ...]) -> str:
    resolver_command = "--resolve-archive-duplicates"
    if any(
        evidence.reason == DUPLICATE_REASON_NEAR_TEXT_MODEL_REVIEW
        for duplicate in duplicates
        for evidence in duplicate.evidences
    ):
        resolver_command = "--resolve-archive-duplicates --include-near-duplicates"
    lines = [
        (
            "duplicate class-session archive files detected in "
            f"{archive_root}; run {resolver_command} before renaming or building usage signals:"
        )
    ]
    for index, duplicate in enumerate(duplicates, start=1):
        lines.append(f"  group {index}:")
        for evidence in duplicate.evidences:
            lines.append(f"    reason: {evidence.reason} {evidence.detail}")
        for path in duplicate.paths:
            lines.append(f"    - {path.name}")
    return "\n".join(lines)


def format_same_format_archive_duplicate_message(
    archive_root: Path,
    duplicates: tuple[SameFormatArchiveDuplicate, ...],
) -> str:
    groups = tuple(
        ArchiveDuplicateGroup(
            paths=duplicate.paths,
            files=(),
            evidences=(
                ArchiveDuplicateEvidence(
                    reason=DUPLICATE_REASON_SAME_FORMAT_SHA256,
                    detail=f"{duplicate.suffix} sha256:{duplicate.sha256}",
                    paths=duplicate.paths,
                ),
            ),
        )
        for duplicate in duplicates
    )
    return format_archive_duplicate_message(archive_root, groups)


def normalize_archive_duplicate_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    compact: list[str] = []
    for character in normalized:
        if character.isspace():
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            continue
        compact.append(character)
    return "".join(compact)


def resolve_archive_duplicates(
    archive_root: Path = DEFAULT_CLASS_SESSION_ARCHIVE_ROOT,
    *,
    apply: bool = False,
    include_near_duplicates: bool = False,
    near_duplicate_client: SupportsCompletion | None = None,
    near_duplicate_model: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ArchiveDuplicateResolutionResult:
    root = archive_root.resolve()
    if not root.is_dir():
        raise ClassSessionArchiveError(f"class-session archive folder does not exist: {archive_root}")

    now = clock or (lambda: datetime.now().astimezone())
    resolved_time = now()
    resolved_at = resolved_time.isoformat(timespec="seconds")
    timestamp = archive_duplicate_resolution_timestamp(resolved_time)
    quarantine_root = (root / DEFAULT_ARCHIVE_DUPLICATE_QUARANTINE_DIRNAME).resolve()
    manifest_path = (quarantine_root / "manifest.json").resolve()
    batch_root = (quarantine_root / timestamp).resolve()
    ensure_inside_archive(root, quarantine_root, allow_equal=False)
    ensure_inside_archive(root, manifest_path, allow_equal=False)
    ensure_inside_archive(root, batch_root, allow_equal=False)

    scan_result = scan_archive_duplicates(
        root,
        include_near_duplicates=include_near_duplicates,
        near_duplicate_client=near_duplicate_client,
        near_duplicate_model=near_duplicate_model,
    )
    duplicate_groups = scan_result.duplicate_groups
    resolutions = tuple(build_archive_duplicate_resolution(root, batch_root, group) for group in duplicate_groups)
    result = ArchiveDuplicateResolutionResult(
        archive_root=root,
        quarantine_root=quarantine_root,
        manifest_path=manifest_path,
        batch_root=batch_root if duplicate_groups else None,
        resolved_at=resolved_at,
        duplicate_groups=duplicate_groups,
        resolutions=resolutions,
        applied=False,
        near_duplicate_reviews=scan_result.near_duplicate_reviews,
    )
    if not apply or not resolutions:
        return result

    apply_archive_duplicate_resolutions(root, manifest_path, batch_root, resolutions, resolved_at)
    return ArchiveDuplicateResolutionResult(
        archive_root=root,
        quarantine_root=quarantine_root,
        manifest_path=manifest_path,
        batch_root=batch_root,
        resolved_at=resolved_at,
        duplicate_groups=duplicate_groups,
        resolutions=resolutions,
        applied=True,
        near_duplicate_reviews=scan_result.near_duplicate_reviews,
    )


def apply_archive_duplicate_resolution_result(
    result: ArchiveDuplicateResolutionResult,
) -> ArchiveDuplicateResolutionResult:
    if result.applied or not result.resolutions:
        return result
    if result.batch_root is None:
        raise ClassSessionArchiveError("Archive duplicate resolution result has no quarantine batch root.")
    apply_archive_duplicate_resolutions(
        result.archive_root,
        result.manifest_path,
        result.batch_root,
        result.resolutions,
        result.resolved_at,
    )
    return ArchiveDuplicateResolutionResult(
        archive_root=result.archive_root,
        quarantine_root=result.quarantine_root,
        manifest_path=result.manifest_path,
        batch_root=result.batch_root,
        resolved_at=result.resolved_at,
        duplicate_groups=result.duplicate_groups,
        resolutions=result.resolutions,
        applied=True,
        near_duplicate_reviews=result.near_duplicate_reviews,
    )


def build_archive_duplicate_resolution(
    archive_root: Path,
    batch_root: Path,
    group: ArchiveDuplicateGroup,
) -> ArchiveDuplicateResolution:
    keep_path = recommended_archive_duplicate_survivor(group)
    quarantine_paths = tuple(path for path in group.paths if path != keep_path)
    destination_paths = tuple((batch_root / path.name).resolve() for path in quarantine_paths)
    for destination in destination_paths:
        ensure_inside_archive(archive_root, destination, allow_equal=False)
    return ArchiveDuplicateResolution(
        group=group,
        keep_path=keep_path,
        quarantine_paths=quarantine_paths,
        quarantine_destination_paths=destination_paths,
    )


def recommended_archive_duplicate_survivor(group: ArchiveDuplicateGroup) -> Path:
    near_quality_by_path = archive_near_duplicate_quality_by_path(group)
    if near_quality_by_path:
        highest_quality = max(near_quality_by_path.values())
        near_tied_paths = tuple(
            path
            for path in group.paths
            if highest_quality - near_quality_by_path.get(path, 0.0) <= ARCHIVE_NEAR_DUPLICATE_QUALITY_DEADBAND
        )
        if len(near_tied_paths) == 1:
            return near_tied_paths[0]
        return deterministic_archive_duplicate_survivor(group, near_tied_paths)
    return deterministic_archive_duplicate_survivor(group, group.paths)


def deterministic_archive_duplicate_survivor(
    group: ArchiveDuplicateGroup,
    paths: tuple[Path, ...],
) -> Path:
    first_seen_by_path = {file.path: file.first_seen_at for file in group.files}
    return min(
        paths,
        key=lambda path: (
            0 if path.suffix.lower() == ".docx" else 1,
            0 if is_managed_archive_name(path.stem) else 1,
            first_seen_by_path.get(path, file_first_seen_timestamp(path).isoformat(timespec="seconds")),
            natural_path_sort_key(path),
        ),
    )


def archive_near_duplicate_quality_by_path(group: ArchiveDuplicateGroup) -> dict[Path, float]:
    scores: dict[Path, list[float]] = {}
    for evidence in group.evidences:
        if evidence.reason != DUPLICATE_REASON_NEAR_TEXT_MODEL_REVIEW:
            continue
        metadata = evidence.metadata or {}
        files = metadata.get("files")
        if not isinstance(files, list):
            continue
        for file_score in files:
            if not isinstance(file_score, Mapping):
                continue
            name = str(file_score.get("name") or "")
            quality = file_score.get("content_quality")
            if not isinstance(quality, (int, float)):
                continue
            matching_path = next((path for path in group.paths if path.name == name), None)
            if matching_path is None:
                continue
            scores.setdefault(matching_path, []).append(float(quality))
    return {
        path: sum(path_scores) / len(path_scores)
        for path, path_scores in scores.items()
        if path_scores
    }


def apply_archive_duplicate_resolutions(
    archive_root: Path,
    manifest_path: Path,
    batch_root: Path,
    resolutions: tuple[ArchiveDuplicateResolution, ...],
    resolved_at: str,
) -> None:
    moves: list[tuple[Path, Path, ArchiveDuplicateResolution]] = []
    seen_destinations: set[Path] = set()
    for resolution in resolutions:
        for source, destination in zip(resolution.quarantine_paths, resolution.quarantine_destination_paths, strict=True):
            if destination in seen_destinations:
                raise ClassSessionArchiveError(f"Duplicate quarantine destination planned: {destination}")
            seen_destinations.add(destination)
            ensure_inside_archive(archive_root, source.resolve(), allow_equal=False)
            ensure_inside_archive(archive_root, destination, allow_equal=False)
            if not source.is_file():
                raise ClassSessionArchiveError(f"Cannot quarantine missing duplicate file: {source}")
            if destination.exists():
                raise ClassSessionArchiveError(f"Refusing to overwrite existing quarantine file: {destination}")
            moves.append((source, destination, resolution))

    moved: list[tuple[Path, Path]] = []
    try:
        batch_root.mkdir(parents=True, exist_ok=False)
        for source, destination, _resolution in moves:
            source.rename(destination)
            moved.append((destination, source))
        write_archive_duplicate_manifest(
            archive_root=archive_root,
            manifest_path=manifest_path,
            resolutions=resolutions,
            resolved_at=resolved_at,
        )
    except Exception as error:
        for destination, source in reversed(moved):
            if destination.exists() and not source.exists():
                destination.rename(source)
        if isinstance(error, ClassSessionArchiveError):
            raise
        raise ClassSessionArchiveError(f"Failed to resolve class-session archive duplicates: {error}") from error


def write_archive_duplicate_manifest(
    *,
    archive_root: Path,
    manifest_path: Path,
    resolutions: tuple[ArchiveDuplicateResolution, ...],
    resolved_at: str,
) -> None:
    if manifest_path.exists():
        manifest = read_json_object(manifest_path, error_type=ClassSessionArchiveError)
        if manifest.get("schema_version") != ARCHIVE_DUPLICATE_MANIFEST_SCHEMA_VERSION:
            raise ClassSessionArchiveError(
                f"Archive duplicate manifest schema is {manifest.get('schema_version')!r}, "
                f"expected {ARCHIVE_DUPLICATE_MANIFEST_SCHEMA_VERSION!r}: {manifest_path}"
            )
        existing_resolutions = manifest.get("resolutions")
        if not isinstance(existing_resolutions, list):
            raise ClassSessionArchiveError(f"Archive duplicate manifest has invalid resolutions list: {manifest_path}")
    else:
        manifest = {
            "schema_version": ARCHIVE_DUPLICATE_MANIFEST_SCHEMA_VERSION,
            "archive_root": archive_root.as_posix(),
            "resolutions": [],
        }
        existing_resolutions = manifest["resolutions"]

    existing_resolutions.extend(
        archive_duplicate_resolution_record(archive_root, resolution, resolved_at)
        for resolution in resolutions
    )
    manifest["updated_at"] = resolved_at
    write_json_atomic(manifest_path, manifest)


def archive_duplicate_resolution_record(
    archive_root: Path,
    resolution: ArchiveDuplicateResolution,
    resolved_at: str,
) -> dict[str, object]:
    file_by_path = {file.path: file for file in resolution.group.files}
    return {
        "resolved_at": resolved_at,
        "reasons": [
            {
                "reason": evidence.reason,
                "detail": evidence.detail,
                "files": [archive_relative_path(archive_root, path) for path in evidence.paths],
                **({"metadata": evidence.metadata} if evidence.metadata is not None else {}),
            }
            for evidence in resolution.group.evidences
        ],
        "kept": archive_duplicate_file_record(archive_root, file_by_path[resolution.keep_path]),
        "quarantined": [
            {
                **archive_duplicate_file_record(archive_root, file_by_path[source]),
                "quarantine_path": archive_relative_path(archive_root, destination),
                "quarantine_batch": archive_relative_path(archive_root, destination.parent),
            }
            for source, destination in zip(
                resolution.quarantine_paths,
                resolution.quarantine_destination_paths,
                strict=True,
            )
        ],
    }


def archive_duplicate_file_record(archive_root: Path, file: ArchiveDuplicateFile) -> dict[str, object]:
    return {
        "path": archive_relative_path(archive_root, file.path),
        "name": file.path.name,
        "suffix": file.suffix,
        "sha256": file.sha256,
        "text_fingerprint": file.text_fingerprint,
        "text_fingerprint_length": file.text_fingerprint_length,
        "first_seen_at": file.first_seen_at,
    }


def archive_relative_path(root: Path, path: Path) -> str:
    return relative_path(root, path)


def archive_duplicate_resolution_timestamp(value: datetime) -> str:
    return duplicate_resolution_timestamp(value)


def natural_path_sort_key(path: Path) -> tuple[tuple[int, int | str], ...]:
    return natural_filename_sort_key(path)


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
    return extract_archive_full_text(path, max_chars=MAX_ARCHIVE_PROMPT_TEXT_CHARS)


def extract_archive_full_text(path: Path, *, max_chars: int | None = None) -> str:
    extension = path.suffix.lower()
    if extension == ".docx":
        return extract_docx_text(path)
    if extension == ".pdf":
        return extract_pdf_text(path, max_chars=max_chars)
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


def extract_pdf_text(path: Path, *, max_chars: int | None = MAX_ARCHIVE_PROMPT_TEXT_CHARS) -> str:
    parts: list[str] = []
    with fitz.open(path) as document:
        for page in document:
            text = page.get_text("text", sort=True).strip()
            if text:
                parts.append(text)
            if max_chars is not None and sum(len(part) for part in parts) >= max_chars:
                break
    return "\n\n".join(parts)


def is_managed_archive_name(stem: str) -> bool:
    return bool(ARCHIVE_NAME_PATTERN.fullmatch(stem))


def normalize_grade_level(value: object) -> str:
    return normalize_shared_grade_level(value)


def safe_slug(value: object, *, fallback: str) -> str:
    return safe_ascii_slug(value, fallback=fallback, max_chars=MAX_FILENAME_SLUG_CHARS)


def ensure_inside_archive(root: Path, path: Path, *, allow_equal: bool) -> None:
    ensure_inside_root(
        root,
        path,
        allow_equal=allow_equal,
        error_type=ClassSessionArchiveError,
        label="class-session archive",
    )
