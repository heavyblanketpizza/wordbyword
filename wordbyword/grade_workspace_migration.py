from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from wordbyword.grade_profiles import (
    CLASS_PARSED_SOURCE_ROOT,
    CLASS_SESSION_ARCHIVE_ROOT,
    CLASS_SOURCE_MATERIAL_ROOT,
    CLASS_SOURCE_ORIGINAL_ROOT,
    DICTIONARY_OUTPUT_ROOT,
    DICTIONARY_ROOT,
    DICTIONARY_SOURCE_ROOT,
    LIVE_GRADE_LEXICON_ROOT,
    UNKNOWN_GRADE,
    USAGE_SIGNAL_BUILD_ROOT,
    VALID_TARGET_GRADES,
    extract_grade_from_source_id,
    normalize_grade_level,
)

MIGRATION_SCHEMA_VERSION = "wordbyword.grade_workspace_migration.v1"
MIGRATION_MANIFEST_FILENAME = ".wordbyword-grade-workspace-migration.json"
NEEDS_GRADE_REVIEW = "_needs-grade-review"
SUPPORTED_SOURCE_SUFFIXES = {
    ".pdf",
    ".epub",
    ".xhtml",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".markdown",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
}
DICTIONARY_STAGE_PATTERN = re.compile(r"^(?:0[0-9]|1[0-5])_")


MigrationKind = Literal[
    "dictionary_original",
    "dictionary_output",
    "class_source_original",
    "parsed_class_source",
    "class_archive",
    "usage_signal",
    "live_lexicon",
]


@dataclass(frozen=True)
class GradeWorkspaceMigrationMove:
    kind: MigrationKind
    source_path: Path
    target_path: Path
    target_grade: str
    reason: str
    applied: bool = False
    skipped: bool = False

    def row(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_path": str(self.source_path),
            "target_path": str(self.target_path),
            "target_grade": self.target_grade,
            "reason": self.reason,
            "applied": self.applied,
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class GradeWorkspaceMigrationResult:
    root: Path
    manifest_path: Path
    dry_run: bool
    moves: tuple[GradeWorkspaceMigrationMove, ...]

    @property
    def planned_count(self) -> int:
        return sum(1 for move in self.moves if not move.skipped)

    @property
    def applied_count(self) -> int:
        return sum(1 for move in self.moves if move.applied)

    @property
    def skipped_count(self) -> int:
        return sum(1 for move in self.moves if move.skipped)


def migrate_grade_workspaces(*, root: Path = Path("."), apply: bool = False) -> GradeWorkspaceMigrationResult:
    resolved_root = Path(root)
    planned_moves = plan_grade_workspace_migration(resolved_root)
    if apply:
        applied_moves = tuple(apply_grade_workspace_migration_moves(planned_moves))
        manifest_path = write_migration_manifest(resolved_root, applied_moves, dry_run=False)
        return GradeWorkspaceMigrationResult(
            root=resolved_root,
            manifest_path=manifest_path,
            dry_run=False,
            moves=applied_moves,
        )
    manifest_path = resolved_root / MIGRATION_MANIFEST_FILENAME
    return GradeWorkspaceMigrationResult(
        root=resolved_root,
        manifest_path=manifest_path,
        dry_run=True,
        moves=tuple(planned_moves),
    )


def plan_grade_workspace_migration(root: Path) -> list[GradeWorkspaceMigrationMove]:
    moves: list[GradeWorkspaceMigrationMove] = []
    moves.extend(plan_dictionary_original_moves(root))
    moves.extend(plan_dictionary_output_moves(root))
    moves.extend(plan_class_source_original_moves(root))
    moves.extend(plan_parsed_class_source_moves(root))
    moves.extend(plan_archive_moves(root))
    moves.extend(plan_usage_signal_moves(root))
    moves.extend(plan_live_lexicon_moves(root))
    return moves


def plan_dictionary_original_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / DICTIONARY_SOURCE_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in {*VALID_TARGET_GRADES, NEEDS_GRADE_REVIEW}:
            continue
        grade = infer_grade_for_path(path)
        if grade in VALID_TARGET_GRADES:
            target = unique_destination(base / grade / path.name)
            reason = "clear grade token"
        else:
            target = unique_destination(base / NEEDS_GRADE_REVIEW / flattened_relative_name(relative))
            reason = "needs grade review"
            grade = UNKNOWN_GRADE
        moves.append(migration_move("dictionary_original", path, target, grade, reason))
    return moves


def plan_dictionary_output_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / DICTIONARY_ROOT
    output_base = root / DICTIONARY_OUTPUT_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.iterdir()):
        if path.name in {"original", "output", "references"} or path.name.startswith("."):
            continue
        if path.is_file() and is_dictionary_output_file(path):
            moves.append(migration_move("dictionary_output", path, unique_destination(output_base / "grade1" / path.name), "grade1", "legacy grade1 dictionary output"))
        elif path.is_dir() and DICTIONARY_STAGE_PATTERN.match(path.name):
            moves.append(migration_move("dictionary_output", path, unique_destination(output_base / "grade1" / path.name), "grade1", "legacy grade1 dictionary stage directory"))
    return moves


def plan_class_source_original_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / CLASS_SOURCE_MATERIAL_ROOT
    legacy_original_base = root / CLASS_SOURCE_ORIGINAL_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.iterdir()):
        if path.name in {"original", "parsed", *VALID_TARGET_GRADES, NEEDS_GRADE_REVIEW} or path.name.startswith("."):
            continue
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
            continue
        moves.append(
            migration_move(
                "class_source_original",
                path,
                unique_destination(base / "grade1" / path.name),
                "grade1",
                "legacy grade1 class source",
            )
        )
    if legacy_original_base.is_dir():
        for grade in VALID_TARGET_GRADES:
            legacy_grade_dir = legacy_original_base / grade
            if not legacy_grade_dir.is_dir():
                continue
            for path in sorted(legacy_grade_dir.rglob("*")):
                if not path.is_file() or path.name.startswith("."):
                    continue
                if path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
                    continue
                relative = path.relative_to(legacy_grade_dir)
                moves.append(
                    migration_move(
                        "class_source_original",
                        path,
                        unique_destination(base / grade / flattened_relative_name(relative)),
                        grade,
                        "legacy original class source",
                    )
                )
    return moves


def plan_parsed_class_source_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / CLASS_PARSED_SOURCE_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.iterdir()):
        if path.name in {*VALID_TARGET_GRADES, NEEDS_GRADE_REVIEW} or path.name.startswith("."):
            continue
        if path.is_dir() and (path / "manifest.json").is_file():
            moves.append(
                migration_move(
                    "parsed_class_source",
                    path,
                    unique_destination(base / "grade1" / path.name),
                    "grade1",
                    "legacy grade1 parsed source bundle",
                )
            )
    return moves


def plan_archive_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / CLASS_SESSION_ARCHIVE_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.iterdir()):
        if path.name in {*VALID_TARGET_GRADES, NEEDS_GRADE_REVIEW} or path.name.startswith("."):
            continue
        if not path.is_file() or path.suffix.lower() not in {".docx", ".pdf"}:
            continue
        grade = extract_grade_from_source_id(path.stem)
        if grade in VALID_TARGET_GRADES:
            target = unique_destination(base / grade / path.name)
            reason = "managed archive filename grade"
        else:
            target = unique_destination(base / NEEDS_GRADE_REVIEW / path.name)
            reason = "needs grade review"
            grade = UNKNOWN_GRADE
        moves.append(migration_move("class_archive", path, target, grade, reason))
    return moves


def plan_usage_signal_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / USAGE_SIGNAL_BUILD_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.iterdir()):
        if path.name in {*VALID_TARGET_GRADES, NEEDS_GRADE_REVIEW} or path.name.startswith("."):
            continue
        if path.is_file() or path.is_dir():
            moves.append(
                migration_move(
                    "usage_signal",
                    path,
                    unique_destination(base / "grade1" / path.name),
                    "grade1",
                    "legacy grade1 usage signal artifact",
                )
            )
    return moves


def plan_live_lexicon_moves(root: Path) -> list[GradeWorkspaceMigrationMove]:
    base = root / LIVE_GRADE_LEXICON_ROOT
    moves: list[GradeWorkspaceMigrationMove] = []
    if not base.is_dir():
        return moves
    for path in sorted(base.iterdir()):
        if path.name in {*VALID_TARGET_GRADES, NEEDS_GRADE_REVIEW} or path.name.startswith(".") or not path.is_file():
            continue
        match = re.fullmatch(r"(grade(?:1[0-2]|[1-9]))-live-lexicon-(canonical|supplemental)\.jsonl", path.name)
        legacy_match = re.fullmatch(r"(grade(?:1[0-2]|[1-9]))-live-lexicon\.jsonl", path.name)
        if match is not None:
            grade = match.group(1)
            filename = "canonical.jsonl" if match.group(2) == "canonical" else "supplemental.jsonl"
            moves.append(migration_move("live_lexicon", path, unique_destination(base / grade / filename), grade, "legacy flat live lexicon"))
        elif legacy_match is not None:
            grade = legacy_match.group(1)
            moves.append(migration_move("live_lexicon", path, unique_destination(base / grade / "canonical.jsonl"), grade, "legacy flat live lexicon"))
    return moves


def apply_grade_workspace_migration_moves(
    moves: list[GradeWorkspaceMigrationMove],
) -> list[GradeWorkspaceMigrationMove]:
    applied_moves: list[GradeWorkspaceMigrationMove] = []
    for move in moves:
        if not move.source_path.exists():
            applied_moves.append(mark_skipped(move, "source missing"))
            continue
        if move.target_path.exists():
            applied_moves.append(mark_skipped(move, "target exists"))
            continue
        move.target_path.parent.mkdir(parents=True, exist_ok=True)
        move.source_path.rename(move.target_path)
        applied_moves.append(
            GradeWorkspaceMigrationMove(
                kind=move.kind,
                source_path=move.source_path,
                target_path=move.target_path,
                target_grade=move.target_grade,
                reason=move.reason,
                applied=True,
                skipped=False,
            )
        )
    return applied_moves


def write_migration_manifest(root: Path, moves: tuple[GradeWorkspaceMigrationMove, ...], *, dry_run: bool) -> Path:
    manifest_path = root / MIGRATION_MANIFEST_FILENAME
    payload = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "dry_run": dry_run,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "planned_count": sum(1 for move in moves if not move.skipped),
        "applied_count": sum(1 for move in moves if move.applied),
        "skipped_count": sum(1 for move in moves if move.skipped),
        "moves": [move.row() for move in moves],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def migration_move(
    kind: MigrationKind,
    source_path: Path,
    target_path: Path,
    target_grade: str,
    reason: str,
) -> GradeWorkspaceMigrationMove:
    return GradeWorkspaceMigrationMove(
        kind=kind,
        source_path=source_path,
        target_path=target_path,
        target_grade=target_grade,
        reason=reason,
    )


def mark_skipped(move: GradeWorkspaceMigrationMove, reason: str) -> GradeWorkspaceMigrationMove:
    return GradeWorkspaceMigrationMove(
        kind=move.kind,
        source_path=move.source_path,
        target_path=move.target_path,
        target_grade=move.target_grade,
        reason=f"{move.reason}; skipped: {reason}",
        applied=False,
        skipped=True,
    )


def infer_grade_for_path(path: Path) -> str:
    normalized = normalize_grade_level(" ".join(path.parts), allow_unknown=True)
    return normalized


def flattened_relative_name(relative: Path) -> str:
    return "__".join(relative.parts)


def is_dictionary_output_file(path: Path) -> bool:
    return path.suffix.lower() in {".csv", ".json", ".jsonl"} and (
        DICTIONARY_STAGE_PATTERN.match(path.name) is not None or path.name == "metadata.json"
    )


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(2, 10_000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a unique migration destination for {path}")
