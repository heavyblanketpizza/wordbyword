from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GuiJobEvent:
    workflow: str
    phase: str
    detail: str
    status: str = "running"
    current: int = 0
    total: int = 0
    artifacts: Mapping[str, str] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow": self.workflow,
            "phase": self.phase,
            "detail": self.detail,
            "status": self.status,
            "current": self.current,
            "total": self.total,
            "artifacts": dict(self.artifacts),
            "error": self.error,
        }


def event(
    workflow: str,
    phase: str,
    detail: str,
    *,
    status: str = "running",
    current: int = 0,
    total: int = 0,
    artifacts: Mapping[str, str] | None = None,
    error: str = "",
) -> GuiJobEvent:
    return GuiJobEvent(
        workflow=workflow,
        phase=phase,
        detail=detail,
        status=status,
        current=current,
        total=total,
        artifacts={} if artifacts is None else artifacts,
        error=error,
    )


def stage_event(workflow: str, phase: str, detail: str) -> GuiJobEvent:
    return event(workflow, phase, detail)


def checkpoint_event(workflow: str, status: object) -> GuiJobEvent:
    action = getattr(getattr(status, "action", ""), "value", str(getattr(status, "action", "")))
    key = str(getattr(status, "key", ""))
    label = str(getattr(status, "label", ""))
    reason = str(getattr(status, "reason", ""))
    return event(workflow, action or "checkpoint", f"{key} {label}: {reason}".strip())


def cleanup_event(workflow: str, progress: object, *, label: str) -> GuiJobEvent:
    phase = str(getattr(progress, "phase", "cleanup"))
    current = int(getattr(progress, "completed_rows", 0))
    total = int(getattr(progress, "total_rows", 0))
    completed_batches = int(getattr(progress, "completed_batches", 0))
    total_batches = int(getattr(progress, "total_batches", 0))
    detail = f"{label}: batch {completed_batches}/{total_batches}, rows {current}/{total}"
    return event(workflow, phase, detail, current=current, total=total)


def source_material_event(workflow: str, progress: object) -> GuiJobEvent:
    source_path = Path(str(getattr(progress, "source_path", "")))
    completed_pages = int(getattr(progress, "completed_pages", 0))
    total_pages = int(getattr(progress, "total_pages", 0))
    source_index = int(getattr(progress, "source_index", 0))
    total_sources = int(getattr(progress, "total_sources", 0))
    current = completed_pages if total_pages else source_index
    total = total_pages if total_pages else total_sources
    detail = str(getattr(progress, "detail", ""))
    if source_path.name:
        detail = f"{source_path.name}: {detail}"
    return event(workflow, str(getattr(progress, "phase", "parse")), detail, current=current, total=total)


def source_rewrite_event(workflow: str, progress: object) -> GuiJobEvent:
    artifacts = _path_artifacts(output_dir=getattr(progress, "output_dir", None))
    detail = str(getattr(progress, "detail", ""))
    source_id = str(getattr(progress, "source_id", ""))
    if source_id:
        detail = f"{source_id}: {detail}"
    return event(
        workflow,
        str(getattr(progress, "phase", "rewrite")),
        detail,
        current=int(getattr(progress, "source_index", 0)),
        total=int(getattr(progress, "total_sources", 0)),
        artifacts=artifacts,
    )


def printable_event(workflow: str, progress: object) -> GuiJobEvent:
    artifacts = _path_artifacts(output_dir=getattr(progress, "output_dir", None))
    detail = str(getattr(progress, "detail", ""))
    source_id = str(getattr(progress, "source_id", ""))
    if source_id:
        detail = f"{source_id}: {detail}"
    return event(
        workflow,
        str(getattr(progress, "phase", "print")),
        detail,
        current=int(getattr(progress, "source_index", 0)),
        total=int(getattr(progress, "total_sources", 0)),
        artifacts=artifacts,
    )


def after_class_report_event(workflow: str, progress: object) -> GuiJobEvent:
    artifacts = _path_artifacts(
        output_dir=getattr(progress, "output_dir", None),
        heuristic_analysis_dir=getattr(progress, "heuristic_analysis_dir", None),
        docx_path=getattr(progress, "docx_path", None),
    )
    detail = str(getattr(progress, "detail", ""))
    source_id = str(getattr(progress, "source_id", ""))
    if source_id:
        detail = f"{source_id}: {detail}"
    return event(workflow, str(getattr(progress, "phase", "report")), detail, artifacts=artifacts)


def artifact_event(workflow: str, stage: str, entries: object) -> GuiJobEvent:
    count = len(entries) if hasattr(entries, "__len__") else 0
    return event(workflow, "artifact", f"{stage}: {count:,} row(s)")


def result_event(workflow: str, detail: str, artifacts: Mapping[str, str] | None = None) -> GuiJobEvent:
    return event(workflow, "complete", detail, status="complete", artifacts=artifacts)


def error_event(workflow: str, detail: str, error: BaseException) -> GuiJobEvent:
    return event(workflow, "error", detail, status="failed", error=str(error))


def phase_detail_event(workflow: str, phase: str, detail: str) -> GuiJobEvent:
    return event(workflow, phase, detail)


def _path_artifacts(**paths: object) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key, path in paths.items():
        if path is None:
            continue
        text = str(path)
        if text:
            artifacts[key] = text
    return artifacts
