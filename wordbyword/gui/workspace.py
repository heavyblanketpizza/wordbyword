"""Read-only workspace views and explicitly requested desktop file imports.

The desktop adapter never treats the presence of a file as a verified pipeline
checkpoint.  These views describe saved files; the application layer remains
responsible for deciding whether they can be reused.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import stat
import statistics
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wordbyword.dictionary_build.artifacts import ArtifactId, all_output_artifact_ids, artifact_spec
from wordbyword.grade_profiles import VALID_TARGET_GRADES, grade_workspace, path_grade_tokens

DATA_ROOT_NAMES = ("build-dictionary", "classroom-printout-studio", "wbw-vocabulary-intelligence")
DICTIONARY_EXTENSIONS = frozenset({".pdf", ".txt", ".md"})
CLASS_SOURCE_EXTENSIONS = frozenset(
    {".pdf", ".epub", ".xhtml", ".html", ".htm", ".txt", ".md", ".markdown",
     ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
)
ARCHIVE_EXTENSIONS = frozenset({".pdf", ".docx"})
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv"})
MAX_PREVIEW_CHARS = 20_000
MAX_PREVIEW_BYTES = 256_000
MAX_PREVIEW_TABLE_ROWS = 120
MAX_PREVIEW_TABLE_COLUMNS = 24
MAX_PREVIEW_TABLE_CELL_CHARS = 500
MAX_FILES = 2_500
MAX_SCAN_ENTRIES = 50_000
MAX_SUMMARY_BYTES = 2_000_000
MAX_IMPORT_BYTES = 1_073_741_824
MAX_IMPORT_FILES = 100
MAX_DISTRIBUTION_BYTES = 64_000_000
MAX_DISTRIBUTION_ROWS = 1_000_000
DICTIONARY_DISTRIBUTION_BINS = 24
DESKTOP_TARGET_GRADE = "grade2"

# Table previews are deliberately narrower than the underlying artifacts. Each
# stage keeps the fields a teacher needs to judge that stage; the complete
# record remains available through the raw preview and original file actions.
_PREVIEW_FIELDS_BY_FILENAME: dict[str, tuple[tuple[str, ...], ...]] = {
    "01_extracted_pages.csv": (("document",), ("page",), ("text",)),
    "02_sentences.csv": (("document",), ("page",), ("sentence",)),
    "03_tokens.csv": (("form",), ("pos",), ("sentence",)),
    "04_surface_words.csv": (("surface",), ("count",), ("document_count",), ("example",)),
    "05_vocabulary_raw.csv": (("term",), ("pos",), ("count",), ("example",)),
    "06_candidate_terms.csv": (
        ("canonical_key",), ("learning_signal_score",), ("count",), ("example",),
    ),
    "07_rejected_terms.csv": (("candidate", "canonical_key"), ("reason",), ("count",), ("example",)),
    "08_dictionary_candidates.jsonl": (
        ("canonical_key",), ("learning_signal_score",), ("count",), ("example",),
    ),
    "09_example_sentences.csv": (("sentence",), ("quality_score",), ("covered_canonical_keys",)),
    "10_cleaned_example_sentences.csv": (
        ("source_sentence",), ("cleaned_sentence",), ("cleanup_action",),
        ("cleaned_quality_score",),
    ),
    "11_cleaned_dictionary_candidates.jsonl": (
        ("canonical_key",), ("learning_signal_score",), ("count",), ("example",),
    ),
    "12_dictionary_candidates_with_cleaned_examples.jsonl": (
        ("canonical_key",), ("learning_signal_score",), ("example",),
        ("example_replaced_with_cleaned_sentence",),
    ),
    "13_vocabulary_with_generated_examples.jsonl": (
        ("canonical_key",), ("learning_signal_score",), ("example",),
        ("example_generated_with_ollama",),
    ),
    "13_generated_example_attempts.jsonl": (
        ("canonical_key",), ("attempt_number",), ("generated_sentence",),
        ("accepted",), ("failure_reason",),
    ),
    "13_generated_example_failures.jsonl": (
        ("canonical_key",), ("attempt_number",), ("generated_sentence",), ("failure_reason",),
    ),
    "14_vocabulary_with_meanings.jsonl": (
        ("canonical_key",), ("meaning",), ("example",), ("roi_score", "learning_signal_score"),
    ),
    "15_final_dictionary.jsonl": (
        ("canonical_key",), ("meaning",), ("example",), ("roi_score", "learning_signal_score"),
    ),
}

_PREVIEW_COLUMN_LABELS = {
    "document": "Document",
    "page": "Page",
    "text": "Page text",
    "sentence": "Sentence",
    "form": "Token",
    "pos": "Part of speech",
    "surface": "Surface form",
    "term": "Term",
    "canonical_key": "Canonical key",
    "candidate": "Candidate",
    "count": "Occurrences",
    "document_count": "Source documents",
    "example": "Example sentence",
    "reason": "Exclusion reason",
    "learning_signal_score": "Learning signal",
    "roi_score": "ROI score",
    "curation_status": "Review status",
    "quality_score": "Sentence quality",
    "covered_canonical_keys": "Covered words",
    "source_sentence": "Original sentence",
    "cleaned_sentence": "Cleaned sentence",
    "cleanup_action": "Cleanup decision",
    "model_confidence": "Model confidence",
    "cleaned_quality_score": "Cleaned quality",
    "example_replaced_with_cleaned_sentence": "Uses cleaned example",
    "example_generated_with_ollama": "Model-generated example",
    "attempt_number": "Attempt",
    "generated_sentence": "Generated sentence",
    "accepted": "Accepted",
    "failure_reason": "Failure reason",
    "meaning": "Meaning",
}
DICTIONARY_DISTRIBUTION_ARTIFACT_IDS = (
    ArtifactId.DICTIONARY_CANDIDATES,
    ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
    ArtifactId.LINKED_DICTIONARY_CANDIDATES,
    ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ArtifactId.MEANING_DICTIONARY_CANDIDATES,
    ArtifactId.FINAL_DICTIONARY,
)


class WorkspaceError(ValueError):
    """A desktop operation cannot safely use the requested workspace path."""


def target_grade(value: object) -> str:
    if not isinstance(value, str) or value not in VALID_TARGET_GRADES:
        raise WorkspaceError("Choose a target grade from grade1 through grade12.")
    return value


def workspace_root(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WorkspaceError("The workspace must be an absolute directory path.")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise WorkspaceError("The workspace must be an existing directory.")
    return root


def safe_data_path(root: Path, value: str | Path, *, must_exist: bool = False) -> Path:
    """Validate both lexical and resolved containment, without following links."""
    supplied = Path(value)
    if ".." in supplied.parts:
        raise WorkspaceError("Parent-directory traversal is not allowed.")
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise WorkspaceError("Choose a file inside this workspace's data folders.") from error
    if not relative.parts or relative.parts[0] not in DATA_ROOT_NAMES:
        raise WorkspaceError("Only project data folders can be browsed or changed here.")
    if any(part.startswith(".") for part in relative.parts):
        raise WorkspaceError("Hidden workspace files cannot be accessed here.")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise WorkspaceError("Symbolic links are not allowed in workspace data paths.")
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(root / relative.parts[0]):
        raise WorkspaceError("The requested path leaves its workspace data folder.")
    return resolved


def canonical_paths(root: Path, grade: str) -> dict[str, str]:
    workspace = grade_workspace(grade)
    return {
        field.name: str(root / value)
        for field in fields(workspace)
        if isinstance(value := getattr(workspace, field.name), Path)
    }


def _file_record(path: Path) -> dict[str, object]:
    info = path.stat(follow_symlinks=False)
    return {
        "id": str(path),
        "name": path.name,
        "path": str(path),
        "kind": path.suffix.lower().lstrip("."),
        "size": info.st_size,
        "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }


def _collect_files(
    root: Path,
    directory: Path,
    extensions: frozenset[str],
    warnings: list[str],
    *,
    recursive: bool = True,
    exclude_grade_dirs: bool = False,
) -> list[Path]:
    try:
        directory = safe_data_path(root, directory)
    except (OSError, WorkspaceError) as error:
        warnings.append(str(error))
        return []
    if not directory.is_dir():
        return []
    found: list[Path] = []
    pending = [directory]
    scanned = 0
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            warnings.append(f"Could not read {current.relative_to(root)}.")
            continue
        for path in entries:
            scanned += 1
            if scanned > MAX_SCAN_ENTRIES or len(found) >= MAX_FILES:
                warnings.append(f"The file list for {directory.relative_to(root)} was limited for responsiveness.")
                return sorted(found, key=lambda item: str(item).casefold())
            if path.name.startswith(".") or path.name == "_needs-grade-review" or path.is_symlink():
                continue
            try:
                if path.is_file() and path.suffix.lower() in extensions:
                    found.append(path)
                elif recursive and path.is_dir():
                    if exclude_grade_dirs and path.name in VALID_TARGET_GRADES:
                        continue
                    if len(path.relative_to(directory).parts) < 24:
                        pending.append(path)
            except OSError:
                continue
    return sorted(found, key=lambda item: str(item).casefold())


def _belongs_to_grade(path: Path, grade: str) -> bool:
    tokens = path_grade_tokens(path.name)
    return tokens == {grade} or (not tokens and grade == DESKTOP_TARGET_GRADE)


def _available_file(root: Path, value: str | Path) -> Path | None:
    try:
        path = safe_data_path(root, value)
        return path if path.is_file() else None
    except (OSError, WorkspaceError):
        return None


def _read_summary(root: Path, directory: Path, warnings: list[str]) -> dict[str, Any]:
    path = _available_file(root, directory / "00_summary.json")
    if path is None:
        return {}
    try:
        if path.stat().st_size > MAX_SUMMARY_BYTES:
            warnings.append("The dictionary summary is too large to preview.")
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a summary object")
        return payload
    except (OSError, UnicodeError, ValueError):
        warnings.append("The dictionary summary is unavailable or being updated.")
        return {}


def _entry_count(path: Path | None, fallback: int, warnings: list[str]) -> int:
    if path is None:
        return fallback
    try:
        if path.stat().st_size > 64_000_000:
            return fallback
        with path.open("rb") as handle:
            count = 0
            read_bytes = 0
            while line := handle.readline(MAX_PREVIEW_BYTES + 1):
                read_bytes += len(line)
                if len(line) > MAX_PREVIEW_BYTES or read_bytes > 64_000_000:
                    warnings.append("The vocabulary count uses saved summary metadata for this large artifact.")
                    return fallback
                count += bool(line.strip())
            return count
    except OSError:
        return fallback


def _number(payload: dict[str, Any], *names: str) -> int:
    for name in names:
        value = payload.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def snapshot(root: Path, grade_value: object = DESKTOP_TARGET_GRADE) -> dict[str, object]:
    grade = target_grade(grade_value)
    workspace = grade_workspace(grade)
    run_paths = canonical_paths(root, grade)
    paths = dict(run_paths)
    warnings: list[str] = []
    sources = _collect_files(root, Path(paths["dictionary_input_dir"]), DICTIONARY_EXTENSIONS, warnings)
    class_sources = _collect_files(root, Path(paths["class_source_dir"]), CLASS_SOURCE_EXTENSIONS, warnings)
    archive = _collect_files(root, Path(paths["archive_root"]), ARCHIVE_EXTENSIONS, warnings)

    legacy_sources = [
        path for path in _collect_files(
            root, root / "build-dictionary/original", DICTIONARY_EXTENSIONS, warnings, exclude_grade_dirs=True
        ) if _belongs_to_grade(path, grade)
    ]
    legacy_class_sources = _collect_files(
        root, root / "classroom-printout-studio/class-source-materials/original" / grade,
        CLASS_SOURCE_EXTENSIONS, warnings,
    )
    if grade == DESKTOP_TARGET_GRADE:
        legacy_class_sources.extend(_collect_files(
            root, root / "classroom-printout-studio/class-source-materials",
            CLASS_SOURCE_EXTENSIONS, warnings, recursive=False,
        ))
    legacy_archive = [
        path for path in _collect_files(
            root, root / "wbw-vocabulary-intelligence/class-session-archive", ARCHIVE_EXTENSIONS,
            warnings, recursive=False,
        ) if _belongs_to_grade(path, grade)
    ]
    legacy_layout = bool(legacy_sources or legacy_class_sources or legacy_archive)
    if legacy_sources and not sources:
        paths["dictionary_input_dir"] = str(root / "build-dictionary/original")
    if legacy_archive and not archive:
        paths["archive_root"] = str(root / "wbw-vocabulary-intelligence/class-session-archive")
    if legacy_class_sources and not class_sources:
        paths["class_source_dir"] = str(legacy_class_sources[0].parent)

    # This desktop app is dedicated to grade 2, so saved pre-grade outputs are
    # shown with that workspace. Do not reuse them for another grade or
    # silently redirect a mutating command.
    if grade == DESKTOP_TARGET_GRADE:
        for key, legacy in (
            ("dictionary_output_dir", root / "build-dictionary"),
            ("usage_signal_output_root", root / "wbw-vocabulary-intelligence/usage-signal-build"),
        ):
            legacy_artifacts = any(
                _available_file(root, legacy / artifact_spec(artifact_id).filename)
                for artifact_id in all_output_artifact_ids()
            )
            if legacy_artifacts:
                legacy_layout = True
                canonical_artifacts = any(
                    _available_file(root, Path(paths[key]) / artifact_spec(artifact_id).filename)
                    for artifact_id in all_output_artifact_ids()
                )
                if not canonical_artifacts:
                    paths[key] = str(legacy)
        legacy_parsed = root / "classroom-printout-studio/class-source-materials/parsed"
        try:
            legacy_parsed = safe_data_path(root, legacy_parsed)
        except (OSError, WorkspaceError):
            legacy_parsed = None
        if legacy_parsed is not None and legacy_parsed.is_dir():
            for child in legacy_parsed.iterdir():
                if child.name not in VALID_TARGET_GRADES and not child.name.startswith("."):
                    if _available_file(root, child / "manifest.json"):
                        legacy_layout = True
                        if not Path(paths["source_output_root"]).is_dir():
                            paths["source_output_root"] = str(legacy_parsed)
                        break
    for key, filename in (
        ("live_lexicon_path", f"{grade}-live-lexicon-canonical.jsonl"),
        ("supplemental_lexicon_path", f"{grade}-live-lexicon-supplemental.jsonl"),
    ):
        legacy = root / "wbw-vocabulary-intelligence/live-grade-lexicon" / filename
        if key == "live_lexicon_path" and not _available_file(root, legacy):
            legacy = legacy.with_name(f"{grade}-live-lexicon.jsonl")
        if _available_file(root, legacy):
            legacy_layout = True
            if not _available_file(root, paths[key]):
                paths[key] = str(legacy)
                paths["live_lexicon_dir"] = str(legacy.parent)
    paths["usage_candidates_path"] = str(Path(paths["usage_signal_output_root"]) / "08_dictionary_candidates.jsonl")
    paths["unmatched_usage_output_path"] = str(
        Path(paths["usage_signal_output_root"]) / "10_unmatched_usage_candidates.jsonl"
    )

    groups = {
        "sources": sorted(set(sources + legacy_sources)),
        "class_sources": sorted(set(class_sources + legacy_class_sources)),
        "handouts": _collect_files(root, Path(paths["print_output_root"]), frozenset({".docx"}), warnings),
        "archive": sorted(set(archive + legacy_archive)),
        "reports": _collect_files(root, Path(paths["after_class_report_output_root"]), frozenset({".docx"}), warnings),
    }
    files: dict[str, list[dict[str, object]]] = {}
    for group, candidates in groups.items():
        records: list[dict[str, object]] = []
        for path in candidates[:MAX_FILES]:
            try:
                records.append(_file_record(safe_data_path(root, path, must_exist=True)))
            except (OSError, WorkspaceError):
                continue
        files[group] = records

    output_dir = Path(paths["dictionary_output_dir"])
    saved_summary = _read_summary(root, output_dir, warnings)
    words = _entry_count(
        _available_file(root, output_dir / "15_final_dictionary.jsonl"),
        _number(saved_summary, "vocabulary_meaning_rows", "cleaned_dictionary_candidates", "unique_vocabulary_terms"),
        warnings,
    )
    artifacts: list[dict[str, object]] = []
    for artifact_id in all_output_artifact_ids():
        spec = artifact_spec(artifact_id)
        path = output_dir / spec.filename
        available = _available_file(root, path)
        try:
            size = available.stat().st_size if available else 0
        except OSError:
            available, size = None, 0
        artifacts.append({
            "id": artifact_id.value, "name": spec.filename,
            "label": spec.description or spec.filename.replace("_", " "),
            "path": str(path), "exists": available is not None, "size": size,
        })
    if legacy_layout:
        warnings.insert(0, "Legacy files are shown read-only. Review workspace migration before running or importing.")
    return {
        "workspace": str(root), "grade": grade, "grade_label": workspace.profile.korean_label,
        "legacy_layout": legacy_layout, "warnings": list(dict.fromkeys(warnings)),
        "paths": paths, "run_paths": run_paths,
        "counts": {
            "sources": len(files["sources"]), "words": words,
            "class_sources": len(files["class_sources"]), "handouts": len(files["handouts"]),
            "archived": len(files["archive"]), "reports": len(files["reports"]),
        },
        "files": files, "artifacts": artifacts,
        "summary": {
            "documents": _number(saved_summary, "document_count"),
            "extracted_pages": _number(saved_summary, "extracted_page_count"),
            "sentences": _number(saved_summary, "sentence_count"), "vocabulary_terms": words,
        },
    }


def preview(root: Path, value: str, grade_value: object = DESKTOP_TARGET_GRADE) -> dict[str, object]:
    target_grade(grade_value)
    path = safe_data_path(root, value, must_exist=True)
    if not path.is_file():
        raise WorkspaceError("Choose a saved file to preview, not a folder.")
    metadata: dict[str, object] = {}
    if path.suffix.lower() in TEXT_EXTENSIONS:
        with path.open("rb") as handle:
            content = handle.read(MAX_PREVIEW_BYTES + 1)
        truncated = len(content) > MAX_PREVIEW_BYTES
        text = content[:MAX_PREVIEW_BYTES].decode("utf-8-sig", errors="replace")
        table = _preview_table(path, text, source_truncated=truncated)
        if table is not None:
            truncated = truncated or bool(table["truncated"])
    elif path.suffix.lower() == ".pdf":
        byte_count = path.stat().st_size
        metadata["byte_count"] = byte_count
        text = f"PDF document · {byte_count:,} bytes\n\nOpen the original file to view its pages."
        truncated = False
    elif path.suffix.lower() == ".docx":
        # Read only the bounded text XML, avoiding document rendering or zip
        # extraction and preserving the original file byte-for-byte.
        import zipfile
        from xml.etree import ElementTree

        with zipfile.ZipFile(path) as document:
            info = document.getinfo("word/document.xml")
            if info.file_size > 8_000_000:
                raise WorkspaceError("This Word document is too large to preview. Open the original file.")
            content = document.read(info)
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise WorkspaceError("This Word document contains unsupported XML declarations.")
        xml = ElementTree.fromstring(content)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        text = "\n".join(
            "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            for paragraph in xml.iter(f"{namespace}p")
        )
        truncated = False
    else:
        raise WorkspaceError("Preview supports text, CSV, JSON, JSONL, PDF, and DOCX files.")
    return {
        "name": path.name, "path": str(path), "text": text[:MAX_PREVIEW_CHARS],
        "truncated": truncated or len(text) > MAX_PREVIEW_CHARS,
        "table": table if path.suffix.lower() in TEXT_EXTENSIONS else None,
        **metadata,
    }


def _preview_table(path: Path, text: str, *, source_truncated: bool) -> dict[str, object] | None:
    """Return a bounded, display-ready table for structured text artifacts."""
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            records: list[list[object]] = []
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            for record in reader:
                records.append(list(record))
                if len(records) > MAX_PREVIEW_TABLE_ROWS:
                    break
            if not records:
                return None
            header = [str(value).strip() for value in records[0]]
            data = records[1:MAX_PREVIEW_TABLE_ROWS + 1]
            curated = _curated_preview_columns(path.name, header, data)
            if curated is not None:
                columns, rows = curated
                return {
                    "format": suffix.removeprefix(".").upper(),
                    "columns": columns,
                    "rows": rows,
                    "truncated": source_truncated or len(records) > MAX_PREVIEW_TABLE_ROWS,
                }

            width = min(max((len(record) for record in records), default=0), MAX_PREVIEW_TABLE_COLUMNS)
            if width == 0:
                return None
            columns = [
                str(header[index]).strip() or f"Column {index + 1}"
                if index < len(header) else f"Column {index + 1}"
                for index in range(width)
            ]
            clipped_columns = any(len(record) > width for record in records)
            rows = [_table_row(record, width) for record in data]
            return {
                "format": suffix.removeprefix(".").upper(),
                "columns": columns,
                "rows": rows,
                "truncated": source_truncated or len(records) > MAX_PREVIEW_TABLE_ROWS or clipped_columns,
            }

        if suffix == ".jsonl":
            lines = text.splitlines()
            if source_truncated and text and not text.endswith(("\n", "\r")):
                lines = lines[:-1]
            values: list[object] = []
            for line in lines:
                if not line.strip():
                    continue
                values.append(json.loads(line))
                if len(values) > MAX_PREVIEW_TABLE_ROWS:
                    break
            if not values:
                return None
            return _json_table(values[:MAX_PREVIEW_TABLE_ROWS], "JSONL", filename=path.name,
                               truncated=source_truncated or len(values) > MAX_PREVIEW_TABLE_ROWS)

        if suffix == ".json" and not source_truncated:
            value = json.loads(text)
            if isinstance(value, list):
                truncated = len(value) > MAX_PREVIEW_TABLE_ROWS
                return _json_table(value[:MAX_PREVIEW_TABLE_ROWS], "JSON", filename=path.name,
                                   truncated=truncated)
            if isinstance(value, dict):
                entries = list(value.items())
                rows = [[_table_cell(key), _table_cell(item)]
                        for key, item in entries[:MAX_PREVIEW_TABLE_ROWS]]
                return {
                    "format": "JSON", "columns": ["Field", "Value"], "rows": rows,
                    "truncated": len(entries) > MAX_PREVIEW_TABLE_ROWS,
                }
    except (csv.Error, json.JSONDecodeError, UnicodeError, ValueError):
        # A malformed or partially-written artifact remains inspectable as raw
        # text instead of making the entire preview fail.
        return None
    return None


def _json_table(values: list[object], format_name: str, *, filename: str,
                truncated: bool) -> dict[str, object]:
    if all(isinstance(value, dict) for value in values):
        available = list(dict.fromkeys(
            str(key)
            for value in values
            for key in value  # type: ignore[union-attr]
        ))
        raw_rows = [
            [value.get(column, "") for column in available]  # type: ignore[union-attr]
            for value in values
        ]
        curated = _curated_preview_columns(filename, available, raw_rows)
        if curated is not None:
            columns, rows = curated
            return {
                "format": format_name, "columns": columns, "rows": rows,
                "truncated": truncated,
            }

        columns: list[str] = []
        for value in values:
            for key in value:  # type: ignore[union-attr]
                name = str(key)
                if name not in columns:
                    columns.append(name)
                if len(columns) == MAX_PREVIEW_TABLE_COLUMNS:
                    break
            if len(columns) == MAX_PREVIEW_TABLE_COLUMNS:
                break
        clipped_columns = any(
            any(str(key) not in columns for key in value)  # type: ignore[union-attr]
            for value in values
        )
        rows = [
            [_table_cell(value.get(column, "")) for column in columns]  # type: ignore[union-attr]
            for value in values
        ]
        return {
            "format": format_name, "columns": columns, "rows": rows,
            "truncated": truncated or clipped_columns,
        }
    return {
        "format": format_name,
        "columns": ["Value"],
        "rows": [[_table_cell(value)] for value in values],
        "truncated": truncated,
    }


def _curated_preview_columns(
    filename: str, columns: list[str], rows: list[list[object]],
) -> tuple[list[str], list[list[str]]] | None:
    """Select decision-useful stage fields while preserving the raw source."""
    field_groups = _PREVIEW_FIELDS_BY_FILENAME.get(filename)
    if field_groups is None:
        return None
    column_indices = {column: index for index, column in enumerate(columns)}
    selected = [
        field
        for alternatives in field_groups
        if (field := next((item for item in alternatives if item in column_indices), None)) is not None
    ]
    if not selected:
        return None
    labels = [_PREVIEW_COLUMN_LABELS.get(field, field.replace("_", " ").title()) for field in selected]
    curated_rows = [
        [
            _table_cell(row[column_indices[field]] if column_indices[field] < len(row) else "")
            for field in selected
        ]
        for row in rows
    ]
    return labels, curated_rows


def _table_row(values: list[object], width: int) -> list[str]:
    return [_table_cell(values[index] if index < len(values) else "") for index in range(width)]


def _table_cell(value: object) -> str:
    if value is None:
        result = ""
    elif isinstance(value, bool):
        result = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        result = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        result = str(value)
    if len(result) > MAX_PREVIEW_TABLE_CELL_CHARS:
        return result[:MAX_PREVIEW_TABLE_CELL_CHARS - 1] + "…"
    return result


def dictionary_distribution(root: Path, value: str, grade_value: object = DESKTOP_TARGET_GRADE) -> dict[str, object]:
    """Return a bounded, chart-ready view of one saved dictionary artifact."""
    target_grade(grade_value)
    path = safe_data_path(root, value, must_exist=True)
    allowed = {artifact_spec(artifact_id).filename: artifact_id for artifact_id in DICTIONARY_DISTRIBUTION_ARTIFACT_IDS}
    artifact_id = allowed.get(path.name)
    if artifact_id is None or not path.is_file():
        raise WorkspaceError("Choose a saved dictionary JSONL artifact from stages 08 or 11–15.")
    if path.stat().st_size > MAX_DISTRIBUTION_BYTES:
        raise WorkspaceError("The dictionary artifact is too large to visualize safely in the desktop app.")

    scores: list[float] = []
    counts: list[int] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            if len(line.encode("utf-8")) > MAX_PREVIEW_BYTES:
                raise WorkspaceError(f"Dictionary row {line_number} is too large to visualize safely.")
            if len(scores) >= MAX_DISTRIBUTION_ROWS:
                raise WorkspaceError("The dictionary artifact has too many rows to visualize safely.")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise WorkspaceError(f"Dictionary row {line_number} is not valid JSON.") from error
            if not isinstance(row, dict):
                raise WorkspaceError(f"Dictionary row {line_number} must be a JSON object.")
            scores.append(_distribution_score(row))
            counts.append(_distribution_count(row.get("count")))

    specification = artifact_spec(artifact_id)
    return {
        "artifact_id": artifact_id.value,
        "name": path.name,
        "path": str(path),
        "stage": specification.stage or "",
        "entry_count": len(scores),
        "learning_signal": _linear_distribution_series(scores, bins=DICTIONARY_DISTRIBUTION_BINS),
        "occurrence_count": _log_distribution_series(counts, bins=DICTIONARY_DISTRIBUTION_BINS),
    }


def _distribution_score(row: dict[str, Any]) -> float:
    if "learning_signal_score" in row:
        return min(max(_finite_float(row.get("learning_signal_score")), -0.5), 0.5)
    if "quality_score_v2" in row:
        return min(max(_finite_float(row.get("quality_score_v2")), -0.5), 0.5)
    if "quality_score" in row:
        return min(max(_finite_float(row.get("quality_score")), 0.0), 1.0) - 0.5
    return 0.0


def _finite_float(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _distribution_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


def _linear_distribution_series(values: list[float], *, bins: int) -> dict[str, object]:
    bin_counts = [0] * bins
    for value in values:
        ratio = min(max(value, -0.5), 0.5) + 0.5
        bin_counts[min(int(ratio * bins), bins - 1)] += 1
    return {
        "bins": bin_counts,
        "minimum": min(values) if values else None,
        "median": statistics.median(values) if values else None,
        "maximum": max(values) if values else None,
        "axis_labels": ["-0.50", "0.00", "0.50"],
    }


def _log_distribution_series(values: list[int], *, bins: int) -> dict[str, object]:
    bin_counts = [0] * bins
    positive = [value for value in values if value > 0]
    maximum = max(positive, default=0)
    log_maximum = math.log(maximum) if maximum > 1 else 0.0
    for value in values:
        if value <= 0 or log_maximum <= 0:
            bin_counts[0] += 1
        else:
            ratio = math.log(value) / log_maximum
            bin_counts[max(0, min(bins - 1, int(ratio * (bins - 1))))] += 1
    midpoint = int(round(math.exp(log_maximum / 2))) if maximum > 1 else maximum
    return {
        "bins": bin_counts,
        "minimum": min(positive) if positive else None,
        "median": statistics.median(positive) if positive else None,
        "maximum": maximum if positive else None,
        "axis_labels": ["1", f"{midpoint:,}", f"{maximum:,}"] if positive else ["0", "·", "0"],
    }


def migration_preview(root: Path) -> dict[str, object]:
    from wordbyword.grade_workspace_migration import plan_grade_workspace_migration

    moves = plan_grade_workspace_migration(root)
    issues: list[str] = []
    targets: set[Path] = set()
    lines = ["GRADE WORKSPACE MIGRATION", "", f"{len(moves)} proposed move(s).", "No files have been changed.", ""]
    for move in moves:
        try:
            source = safe_data_path(root, move.source_path, must_exist=True)
            target = safe_data_path(root, move.target_path)
            if source.is_dir():
                for directory, subdirectories, filenames in os.walk(source, followlinks=False):
                    if any((Path(directory) / name).is_symlink() for name in subdirectories + filenames):
                        raise WorkspaceError("A source bundle contains a symbolic link.")
            if target in targets or target.exists():
                raise WorkspaceError("The migration contains a destination collision.")
            targets.add(target)
        except (OSError, WorkspaceError) as error:
            issues.append(str(error))
        lines.extend([
            f"{move.source_path.relative_to(root)}",
            f"  → {move.target_path.relative_to(root)}",
            f"  {move.reason}", "",
        ])
    if not moves:
        lines.append("The workspace already uses grade folders. There is nothing to move.")
    if issues:
        lines.extend(["Migration cannot be applied safely:", *dict.fromkeys(issues)])
    else:
        lines.append("Unclassified sources will be preserved in _needs-grade-review for manual assignment.")
    text = "\n".join(lines)
    truncated = len(text) > MAX_PREVIEW_CHARS
    return {
        "name": "Workspace migration", "path": str(root), "text": text[:MAX_PREVIEW_CHARS],
        "truncated": truncated, "can_apply": bool(moves) and not issues and not truncated,
        "planned_count": len(moves),
    }


def import_files(root: Path, grade_value: object, destination: str, file_values: list[str]) -> dict[str, str]:
    """Copy user-selected originals.  Existing files are never replaced."""
    grade = target_grade(grade_value)
    destinations = {
        "sources": ("dictionary_input_dir", DICTIONARY_EXTENSIONS),
        # Other recognized source formats only receive a placeholder manifest
        # from the parser today; the desktop import promises a usable workflow.
        "class_sources": ("class_source_dir", frozenset({".pdf"})),
        "archive": ("archive_root", ARCHIVE_EXTENSIONS),
    }
    if destination not in destinations:
        raise WorkspaceError("Choose sources, class_sources, or archive as the import destination.")
    if not file_values or len(file_values) > MAX_IMPORT_FILES:
        raise WorkspaceError(f"Choose between 1 and {MAX_IMPORT_FILES} files to import.")
    if snapshot(root, grade)["legacy_layout"]:
        raise WorkspaceError("Review and apply workspace migration before importing files for this grade.")
    key, extensions = destinations[destination]
    target_dir = safe_data_path(root, canonical_paths(root, grade)[key])
    existing_names = {path.name.casefold() for path in target_dir.iterdir()} if target_dir.is_dir() else set()
    pending: list[tuple[Path, Path]] = []
    for value in file_values:
        source = Path(value)
        if not source.is_absolute() or ".." in source.parts:
            raise WorkspaceError("Import files must be absolute paths without parent-directory traversal.")
        if any(part.startswith(".") for part in source.parts):
            raise WorkspaceError("Hidden files cannot be imported.")
        cursor = source
        while cursor != cursor.parent:
            if cursor.is_symlink():
                raise WorkspaceError("Symbolic links cannot be imported.")
            cursor = cursor.parent
        info = source.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError("Import individual files, not folders or special files.")
        if source.suffix.lower() not in extensions:
            allowed = ", ".join(sorted(extensions))
            raise WorkspaceError(f"Unsupported file type for this destination. Supported: {allowed}.")
        if info.st_size > MAX_IMPORT_BYTES:
            raise WorkspaceError("Import files must be smaller than 1 GiB each.")
        if source.name.casefold() in existing_names:
            raise WorkspaceError(f"{source.name} already exists in the import selection or destination; nothing was copied.")
        existing_names.add(source.name.casefold())
        pending.append((source, target_dir / source.name))

    # Validate every source and collision before creating any directory.
    created_directories: list[Path] = []
    cursor = target_dir
    while not cursor.exists():
        created_directories.append(cursor)
        cursor = cursor.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_data_path(root, target_dir, must_exist=True)
    copied: list[Path] = []
    try:
        for source, target in pending:
            source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                    raise WorkspaceError("An import source changed while it was being read.")
                target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                copied.append(target)
                with os.fdopen(target_fd, "wb") as output, os.fdopen(source_fd, "rb", closefd=False) as original:
                    shutil.copyfileobj(original, output, length=1_048_576)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                os.close(source_fd)
    except BaseException:
        for path in copied:
            path.unlink(missing_ok=True)
        for directory in created_directories:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return {f"file_{index}": str(path) for index, path in enumerate(copied, start=1)}
