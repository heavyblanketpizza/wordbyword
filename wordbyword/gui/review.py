from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.gui.pipeline import DEFAULT_OUTPUT_DIR

REVIEW_STATUS_FAILED = "failed"
REVIEW_STATUS_NEEDS_REVIEW = "needs_review"
REVIEW_STATUS_READY = "ready"
REVIEW_STATUS_MISSING = "missing"

REVIEW_KIND_DICTIONARY = "dictionary"
REVIEW_KIND_CLASS_PRINTOUT = "class_printout"
REVIEW_KIND_ARCHIVED_HANDOUT = "archived_handout"
REVIEW_KIND_LIVE_LEXICON = "live_lexicon"
REVIEW_KIND_AFTER_CLASS_REPORT = "after_class_report"

HANDOUT_SUFFIXES = {".docx", ".pdf"}


def build_review_snapshot(settings: Mapping[str, object]) -> dict[str, object]:
    items: list[dict[str, object]] = []
    items.append(_dictionary_item(settings))
    items.extend(_class_printout_items(settings))
    items.extend(_archived_handout_items(settings))
    items.append(_live_lexicon_item(settings))
    items.extend(_after_class_report_items(settings))
    ordered_items = sorted(items, key=_review_sort_key)
    return {
        "summary": _summary(ordered_items),
        "items": ordered_items,
    }


def _dictionary_item(settings: Mapping[str, object]) -> dict[str, object]:
    output_dir = _path(settings, "dictionary_output_dir", DEFAULT_OUTPUT_DIR)
    primary_path = artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)
    summary_path = artifact_path(output_dir, ArtifactId.SUMMARY)
    line_count = _count_nonempty_lines(primary_path)
    summary, summary_error = _read_json_object(summary_path)
    metrics = _metrics(
        ("rows", line_count if primary_path.is_file() else None),
        ("documents", _int_or_none(summary.get("document_count"))),
        ("terms", _int_or_none(summary.get("unique_vocabulary_terms"))),
    )
    status = REVIEW_STATUS_READY if primary_path.is_file() else REVIEW_STATUS_MISSING
    if summary_error and primary_path.is_file():
        status = REVIEW_STATUS_FAILED
        metrics.append(_metric("issue", "summary"))
    return _item(
        item_id="dictionary:final",
        kind=REVIEW_KIND_DICTIONARY,
        title="final_dictionary",
        subtitle=output_dir.as_posix(),
        status=status,
        primary_path=primary_path,
        folder_path=output_dir,
        metrics=metrics,
        available_actions=_actions(primary_path=primary_path, folder_path=output_dir, extra=("rebuild",)),
    )


def _class_printout_items(settings: Mapping[str, object]) -> list[dict[str, object]]:
    root = _path(settings, "print_output_root")
    bundle_dirs = _bundle_dirs(root, primary_filename="material.docx")
    return [_generated_docx_item(bundle_dir, kind=REVIEW_KIND_CLASS_PRINTOUT) for bundle_dir in bundle_dirs]


def _archived_handout_items(settings: Mapping[str, object]) -> list[dict[str, object]]:
    archive_root = _path(settings, "archive_root")
    if not archive_root.is_dir():
        return []
    items: list[dict[str, object]] = []
    for path in sorted(archive_root.iterdir()):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in HANDOUT_SUFFIXES:
            continue
        items.append(
            _item(
                item_id=f"archived_handout:{path.name}",
                kind=REVIEW_KIND_ARCHIVED_HANDOUT,
                title=path.stem,
                subtitle=archive_root.as_posix(),
                status=REVIEW_STATUS_READY,
                primary_path=path,
                folder_path=archive_root,
                metrics=_metrics(("type", path.suffix.lower().removeprefix("."))),
                available_actions=("open", "open_folder", "select_report"),
            )
        )
    return items


def _live_lexicon_item(settings: Mapping[str, object]) -> dict[str, object]:
    primary_path = _path(settings, "live_lexicon_path")
    supplemental_path = _path(settings, "supplemental_lexicon_path")
    folder_path = primary_path.parent if primary_path.name else _path(settings, "usage_signal_output_root")
    metrics = _metrics(
        ("rows", _count_nonempty_lines(primary_path) if primary_path.is_file() else None),
        ("supplemental", _count_nonempty_lines(supplemental_path) if supplemental_path.is_file() else None),
    )
    return _item(
        item_id="live_lexicon:canonical",
        kind=REVIEW_KIND_LIVE_LEXICON,
        title="live_lexicon",
        subtitle=folder_path.as_posix(),
        status=REVIEW_STATUS_READY if primary_path.is_file() else REVIEW_STATUS_MISSING,
        primary_path=primary_path,
        folder_path=folder_path,
        metrics=metrics,
        available_actions=_actions(primary_path=primary_path, folder_path=folder_path),
    )


def _after_class_report_items(settings: Mapping[str, object]) -> list[dict[str, object]]:
    root = _path(settings, "after_class_report_output_root")
    bundle_dirs = _bundle_dirs(root, primary_filename="after_class_report.docx")
    return [_generated_docx_item(bundle_dir, kind=REVIEW_KIND_AFTER_CLASS_REPORT) for bundle_dir in bundle_dirs]


def _generated_docx_item(bundle_dir: Path, *, kind: str) -> dict[str, object]:
    primary_filename = "material.docx" if kind == REVIEW_KIND_CLASS_PRINTOUT else "after_class_report.docx"
    primary_path = bundle_dir / primary_filename
    quality_path = bundle_dir / "quality_report.json"
    failures_path = bundle_dir / "failures.jsonl"
    quality, quality_error = _read_json_object(quality_path)
    failure_count = _count_nonempty_lines(failures_path)
    title = str(quality.get("source_id") or bundle_dir.name)
    quality_status = str(quality.get("status") or "").strip().lower()
    requires_revision = bool(quality.get("requires_human_revision"))
    status = REVIEW_STATUS_READY
    if quality_error or quality_status == "failed" or failure_count > 0 or not primary_path.is_file():
        status = REVIEW_STATUS_FAILED
    elif requires_revision:
        status = REVIEW_STATUS_NEEDS_REVIEW

    if kind == REVIEW_KIND_CLASS_PRINTOUT:
        metrics = _metrics(
            ("sections", _int_or_none(quality.get("section_count"))),
            ("images", _int_or_none(quality.get("selected_image_count"))),
            ("failures", failure_count if failure_count else None),
        )
        actions = _actions(primary_path=primary_path, folder_path=bundle_dir, extra=("rebuild",))
        subtitle = str(quality.get("target_grade") or quality.get("target_profile") or bundle_dir.parent.name)
    else:
        metrics = _metrics(
            ("vocabulary", _int_or_none(quality.get("selected_vocabulary_count"))),
            ("cards", _int_or_none(quality.get("deep_teaching_card_count"))),
            ("failures", failure_count if failure_count else None),
        )
        actions = _actions(primary_path=primary_path, folder_path=bundle_dir)
        subtitle = str(quality.get("grade_label") or quality.get("target_grade") or quality.get("target_profile") or bundle_dir.parent.name)
    if quality_error:
        metrics.append(_metric("issue", "quality_report"))
    return _item(
        item_id=f"{kind}:{bundle_dir.name}",
        kind=kind,
        title=title,
        subtitle=subtitle,
        status=status,
        primary_path=primary_path,
        folder_path=bundle_dir,
        metrics=metrics,
        available_actions=actions,
    )


def _bundle_dirs(root: Path, *, primary_filename: str) -> list[Path]:
    if not root.is_dir():
        return []
    candidates = {path.parent for path in root.rglob("quality_report.json") if path.is_file()}
    candidates.update(path.parent for path in root.rglob(primary_filename) if path.is_file())
    return sorted(candidates)


def _item(
    *,
    item_id: str,
    kind: str,
    title: str,
    subtitle: str,
    status: str,
    primary_path: Path,
    folder_path: Path,
    metrics: list[dict[str, object]],
    available_actions: Iterable[str],
) -> dict[str, object]:
    updated_at = _updated_at(primary_path if primary_path.exists() else folder_path)
    return {
        "id": item_id,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "status": status,
        "primary_path": primary_path.as_posix(),
        "folder_path": folder_path.as_posix(),
        "updated_at": updated_at,
        "metrics": metrics,
        "available_actions": list(available_actions),
    }


def _actions(*, primary_path: Path, folder_path: Path, extra: Iterable[str] = ()) -> tuple[str, ...]:
    actions: list[str] = []
    if primary_path.is_file():
        actions.append("open")
    if folder_path.is_dir():
        actions.append("open_folder")
    actions.extend(extra)
    return tuple(actions)


def _summary(items: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        REVIEW_STATUS_NEEDS_REVIEW: 0,
        REVIEW_STATUS_FAILED: 0,
        REVIEW_STATUS_READY: 0,
        REVIEW_STATUS_MISSING: 0,
        "reports": 0,
        "archived_handouts": 0,
    }
    for item in items:
        status = str(item.get("status") or "")
        if status in summary:
            summary[status] += 1
        kind = str(item.get("kind") or "")
        if kind == REVIEW_KIND_AFTER_CLASS_REPORT:
            summary["reports"] += 1
        elif kind == REVIEW_KIND_ARCHIVED_HANDOUT:
            summary["archived_handouts"] += 1
    return summary


def _review_sort_key(item: Mapping[str, object]) -> tuple[int, int, str]:
    status_order = {
        REVIEW_STATUS_FAILED: 0,
        REVIEW_STATUS_NEEDS_REVIEW: 1,
        REVIEW_STATUS_MISSING: 2,
        REVIEW_STATUS_READY: 3,
    }
    kind_order = {
        REVIEW_KIND_CLASS_PRINTOUT: 0,
        REVIEW_KIND_AFTER_CLASS_REPORT: 1,
        REVIEW_KIND_DICTIONARY: 2,
        REVIEW_KIND_LIVE_LEXICON: 3,
        REVIEW_KIND_ARCHIVED_HANDOUT: 4,
    }
    return (
        status_order.get(str(item.get("status") or ""), 9),
        kind_order.get(str(item.get("kind") or ""), 9),
        str(item.get("title") or ""),
    )


def _path(settings: Mapping[str, object], key: str, default: Path | str = "") -> Path:
    value = settings.get(key)
    text = str(value if value not in {None, ""} else default)
    return Path(text)


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return {}, ""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {}, str(error)
    return (parsed, "") if isinstance(parsed, dict) else ({}, "not_object")


def _count_nonempty_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _metrics(*pairs: tuple[str, object | None]) -> list[dict[str, object]]:
    return [_metric(label, value) for label, value in pairs if value not in {None, ""}]


def _metric(label: str, value: object) -> dict[str, object]:
    return {"label": label, "value": value}


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _updated_at(path: Path) -> str:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
