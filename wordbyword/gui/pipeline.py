from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from wordbyword.classroom_printout_studio.class_printing.printable import DEFAULT_PRINT_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    DEFAULT_PARSED_SOURCE_ROOT,
    SUPPORTED_SOURCE_EXTENSIONS,
)
from wordbyword.cli.options import DEFAULT_OUTPUT_DIR
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.gui.i18n import text as localized_text

ReadinessStatus = str

STATUS_READY: ReadinessStatus = "ready"
STATUS_MISSING_INPUT: ReadinessStatus = "missing_input"
STATUS_DONE: ReadinessStatus = "done"
STATUS_RUNNING: ReadinessStatus = "running"
STATUS_FAILED: ReadinessStatus = "failed"

DICTIONARY_SOURCE_EXTENSIONS = {".pdf", ".txt", ".md"}
HANDOUT_EXTENSIONS = {".docx", ".pdf"}


@dataclass(frozen=True)
class WorkflowStage:
    label: str
    detail: str
    icon_name: str = ""
    input_label: str = ""
    input_path_key: str = ""
    output_label: str = ""
    output_path_key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "detail": self.detail,
            "icon_name": self.icon_name,
            "input_label": self.input_label,
            "input_path_key": self.input_path_key,
            "output_label": self.output_label,
            "output_path_key": self.output_path_key,
        }


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    number: str
    title: str
    purpose: str
    primary_action_label: str
    primary_workflow_id: str
    icon_name: str = ""
    secondary_action_label: str = ""
    secondary_workflow_id: str = ""
    expected_output_key: str = ""
    stages: tuple[WorkflowStage, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "number": self.number,
            "title": self.title,
            "purpose": self.purpose,
            "icon_name": self.icon_name,
            "primary_action_label": self.primary_action_label,
            "primary_workflow_id": self.primary_workflow_id,
            "secondary_action_label": self.secondary_action_label,
            "secondary_workflow_id": self.secondary_workflow_id,
            "expected_output_key": self.expected_output_key,
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass(frozen=True)
class WorkflowReadiness:
    key: str
    status: ReadinessStatus
    label: str
    detail: str
    next_action: str
    missing: tuple[str, ...] = ()
    artifacts: Mapping[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "status": self.status,
            "label": self.label,
            "detail": self.detail,
            "next_action": self.next_action,
            "missing": list(self.missing),
            "artifacts": dict(self.artifacts or {}),
        }


def workflow_definitions(language: str = "en") -> tuple[WorkflowDefinition, ...]:
    t = _translator(language)
    return (
        WorkflowDefinition(
            key="before_start",
            number="00",
            title=t("workflow.before.title"),
            purpose=t("workflow.before.purpose"),
            primary_action_label=t("workflow.before.primary"),
            primary_workflow_id="",
            icon_name="list-checks",
            stages=(
                WorkflowStage(
                    t("stage.before.dictionary_sources.label"),
                    t("stage.before.dictionary_sources.detail"),
                    icon_name="files",
                    output_label=t("stage.before.dictionary_sources.output"),
                    output_path_key="dictionary_input_dir",
                ),
                WorkflowStage(
                    t("stage.before.class_sources.label"),
                    t("stage.before.class_sources.detail"),
                    icon_name="files",
                    output_label=t("stage.before.dictionary_sources.output"),
                    output_path_key="class_source_dir",
                ),
                WorkflowStage(
                    t("stage.before.llm_runtime.label"),
                    t("stage.before.llm_runtime.detail"),
                    icon_name="cpu",
                    output_label=t("stage.before.llm_runtime.output"),
                    output_path_key="model",
                ),
            ),
        ),
        WorkflowDefinition(
            key="dictionary",
            number="01",
            title=t("workflow.dictionary.title"),
            purpose=t("workflow.dictionary.purpose"),
            primary_action_label=t("workflow.dictionary.primary"),
            primary_workflow_id="dictionary_build",
            icon_name="book-open",
            secondary_action_label=t("workflow.dictionary.secondary"),
            secondary_workflow_id="dictionary_prep",
            expected_output_key="dictionary_final",
            stages=(
                WorkflowStage(
                    t("stage.dictionary.source_files.label"),
                    t("stage.dictionary.source_files.detail"),
                    icon_name="files",
                    input_label=t("stage.dictionary.source_files.input"),
                    input_path_key="dictionary_input_dir",
                    output_label=t("stage.dictionary.source_files.output"),
                    output_path_key="dictionary_output_dir",
                ),
                WorkflowStage(
                    t("stage.dictionary.stages_00_09.label"),
                    t("stage.dictionary.stages_00_09.detail"),
                    icon_name="list-ordered",
                    output_label=t("stage.dictionary.stages_00_09.output"),
                    output_path_key="dictionary_output_dir",
                ),
                WorkflowStage(
                    t("stage.dictionary.stages_10_13.label"),
                    t("stage.dictionary.stages_10_13.detail"),
                    icon_name="sparkles",
                    output_label=t("stage.dictionary.stages_10_13.output"),
                    output_path_key="dictionary_output_dir",
                ),
                WorkflowStage(
                    t("stage.dictionary.stages_14_15.label"),
                    t("stage.dictionary.stages_14_15.detail"),
                    icon_name="book-check",
                    input_label=t("stage.dictionary.stages_14_15.input"),
                    input_path_key="reference_dictionary_path",
                    output_label=t("stage.dictionary.stages_14_15.output"),
                    output_path_key="dictionary_final",
                ),
            ),
        ),
        WorkflowDefinition(
            key="class_printouts",
            number="02",
            title=t("workflow.class.title"),
            purpose=t("workflow.class.purpose"),
            primary_action_label=t("workflow.class.primary"),
            primary_workflow_id="class_printouts",
            icon_name="printer",
            expected_output_key="print_output_root",
            stages=(
                WorkflowStage(
                    t("stage.class.source_pdfs.label"),
                    t("stage.class.source_pdfs.detail"),
                    icon_name="files",
                    input_label=t("stage.class.source_pdfs.input"),
                    input_path_key="class_source_dir",
                    output_label=t("stage.class.source_pdfs.output"),
                    output_path_key="source_output_root",
                ),
                WorkflowStage(
                    t("stage.class.ocr_parse.label"),
                    t("stage.class.ocr_parse.detail"),
                    icon_name="scan-text",
                    output_label=t("stage.class.ocr_parse.output"),
                    output_path_key="source_output_root",
                ),
                WorkflowStage(
                    t("stage.class.rewrite.label"),
                    t("stage.class.rewrite.detail"),
                    icon_name="pencil-line",
                    input_label=t("stage.class.rewrite.input"),
                    input_path_key="vocabulary_source",
                    output_label=t("stage.class.rewrite.output"),
                    output_path_key="rewrite_output_root",
                ),
                WorkflowStage(
                    t("stage.class.docx_handouts.label"),
                    t("stage.class.docx_handouts.detail"),
                    icon_name="file-text",
                    output_label=t("stage.class.docx_handouts.output"),
                    output_path_key="print_output_root",
                ),
            ),
        ),
        WorkflowDefinition(
            key="archive",
            number="03",
            title=t("workflow.archive.title"),
            purpose=t("workflow.archive.purpose"),
            primary_action_label=t("workflow.archive.primary"),
            primary_workflow_id="usage_signals",
            icon_name="archive",
            secondary_action_label=t("workflow.archive.secondary"),
            secondary_workflow_id="live_lexicon",
            expected_output_key="live_lexicon_path",
            stages=(
                WorkflowStage(
                    t("stage.archive.used_handouts.label"),
                    t("stage.archive.used_handouts.detail"),
                    icon_name="files",
                    input_label=t("stage.archive.used_handouts.input"),
                    input_path_key="archive_root",
                    output_label=t("stage.archive.used_handouts.output"),
                    output_path_key="archive_root",
                ),
                WorkflowStage(
                    t("stage.archive.safe_names.label"),
                    t("stage.archive.safe_names.detail"),
                    icon_name="tag",
                    output_label=t("stage.archive.safe_names.output"),
                    output_path_key="archive_root",
                ),
                WorkflowStage(
                    t("stage.archive.duplicate_check.label"),
                    t("stage.archive.duplicate_check.detail"),
                    icon_name="copy-check",
                    output_label=t("stage.archive.duplicate_check.output"),
                    output_path_key="archive_root",
                ),
                WorkflowStage(
                    t("stage.archive.usage_signals.label"),
                    t("stage.archive.usage_signals.detail"),
                    icon_name="chart-column",
                    output_label=t("stage.archive.usage_signals.output"),
                    output_path_key="usage_signal_output_root",
                ),
                WorkflowStage(
                    t("stage.archive.live_lexicon.label"),
                    t("stage.archive.live_lexicon.detail"),
                    icon_name="book-marked",
                    output_label=t("stage.archive.live_lexicon.output"),
                    output_path_key="live_lexicon_path",
                ),
            ),
        ),
        WorkflowDefinition(
            key="reports",
            number="04",
            title=t("workflow.reports.title"),
            purpose=t("workflow.reports.purpose"),
            primary_action_label=t("workflow.reports.primary"),
            primary_workflow_id="after_class_report",
            icon_name="clipboard-list",
            expected_output_key="after_class_report_output_root",
            stages=(
                WorkflowStage(
                    t("stage.reports.archived_printout.label"),
                    t("stage.reports.archived_printout.detail"),
                    icon_name="clipboard-check",
                    input_label=t("stage.reports.archived_printout.input"),
                    input_path_key="after_class_printout_path",
                    output_label=t("stage.reports.archived_printout.output"),
                    output_path_key="after_class_report_output_root",
                ),
                WorkflowStage(
                    t("stage.reports.vocabulary_analysis.label"),
                    t("stage.reports.vocabulary_analysis.detail"),
                    icon_name="chart-column",
                    output_label=t("stage.reports.vocabulary_analysis.output"),
                    output_path_key="after_class_report_output_root",
                ),
                WorkflowStage(
                    t("stage.reports.adult_report.label"),
                    t("stage.reports.adult_report.detail"),
                    icon_name="file-text",
                    output_label=t("stage.reports.adult_report.output"),
                    output_path_key="after_class_report_output_root",
                ),
            ),
        ),
    )


def workflow_definition_payload(language: str = "en") -> list[dict[str, object]]:
    return [definition.to_dict() for definition in workflow_definitions(language)]


def readiness_payload(
    settings: Mapping[str, object],
    job_state: Mapping[str, object] | None = None,
    language: str = "en",
) -> dict[str, dict[str, object]]:
    return {
        readiness.key: readiness.to_dict()
        for readiness in workflow_readiness(settings, job_state=job_state, language=language)
    }


def workflow_readiness(
    settings: Mapping[str, object],
    *,
    job_state: Mapping[str, object] | None = None,
    language: str = "en",
) -> tuple[WorkflowReadiness, ...]:
    active = _active_workflow(job_state)
    status = str((job_state or {}).get("status") or "")
    failed = status == STATUS_FAILED
    return (
        _with_job_state(_before_start_readiness(settings, language), active, "before_start", failed, language),
        _with_job_state(_dictionary_readiness(settings, language), active, "dictionary", failed, language),
        _with_job_state(_class_printouts_readiness(settings, language), active, "class_printouts", failed, language),
        _with_job_state(_archive_readiness(settings, language), active, "archive", failed, language),
        _with_job_state(_reports_readiness(settings, language), active, "reports", failed, language),
    )


def path_for_key(settings: Mapping[str, object], key: str) -> str:
    if key == "dictionary_final":
        return str(artifact_path(_path(settings, "dictionary_output_dir", DEFAULT_OUTPUT_DIR), ArtifactId.FINAL_DICTIONARY))
    if key == "vocabulary_source":
        live_lexicon_path = _path(settings, "live_lexicon_path")
        if live_lexicon_path.is_file():
            return str(live_lexicon_path)
        return str(artifact_path(_path(settings, "dictionary_output_dir", DEFAULT_OUTPUT_DIR), ArtifactId.FINAL_DICTIONARY))
    value = settings.get(key)
    return "" if value is None else str(value)


def _before_start_readiness(settings: Mapping[str, object], language: str) -> WorkflowReadiness:
    t = _translator(language)
    missing: list[str] = []
    dictionary_dir = _path(settings, "dictionary_input_dir")
    class_dir = _path(settings, "class_source_dir")
    if not dictionary_dir.exists():
        missing.append(t("readiness.missing.dictionary_source_folder"))
    if not class_dir.exists():
        missing.append(t("readiness.missing.class_source_folder"))
    if not str(settings.get("model") or "").strip():
        missing.append(t("readiness.missing.llm_model"))
    if missing:
        return WorkflowReadiness(
            key="before_start",
            status=STATUS_MISSING_INPUT,
            label=t("readiness.before.missing.label"),
            detail=t("readiness.before.missing.detail"),
            next_action=t("readiness.before.missing.next"),
            missing=tuple(missing),
        )
    return WorkflowReadiness(
        key="before_start",
        status=STATUS_READY,
        label=t("readiness.before.ready.label"),
        detail=t("readiness.before.ready.detail"),
        next_action=t("readiness.before.ready.next"),
    )


def _dictionary_readiness(settings: Mapping[str, object], language: str) -> WorkflowReadiness:
    t = _translator(language)
    input_dir = _path(settings, "dictionary_input_dir")
    output_dir = _path(settings, "dictionary_output_dir", DEFAULT_OUTPUT_DIR)
    final_dictionary = artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)
    artifacts = {"final_dictionary": str(final_dictionary), "output_dir": str(output_dir)}
    if final_dictionary.is_file():
        return WorkflowReadiness(
            key="dictionary",
            status=STATUS_DONE,
            label=t("readiness.dictionary.done.label"),
            detail=t("readiness.dictionary.done.detail"),
            next_action=t("readiness.dictionary.done.next"),
            artifacts=artifacts,
        )
    if not input_dir.is_dir():
        return WorkflowReadiness(
            key="dictionary",
            status=STATUS_MISSING_INPUT,
            label=t("readiness.dictionary.missing_folder.label"),
            detail=t("readiness.dictionary.missing_folder.detail"),
            next_action=t("readiness.dictionary.missing_folder.next"),
            missing=(t("readiness.missing.dictionary_source_folder"),),
            artifacts=artifacts,
        )
    if not _has_file_with_suffix(input_dir, DICTIONARY_SOURCE_EXTENSIONS):
        return WorkflowReadiness(
            key="dictionary",
            status=STATUS_MISSING_INPUT,
            label=t("readiness.dictionary.no_source.label"),
            detail=t("readiness.dictionary.no_source.detail"),
            next_action=t("readiness.dictionary.no_source.next"),
            missing=(t("readiness.missing.dictionary_source_files"),),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="dictionary",
        status=STATUS_READY,
        label=t("readiness.dictionary.ready.label"),
        detail=t("readiness.dictionary.ready.detail"),
        next_action=t("readiness.dictionary.ready.next"),
        artifacts=artifacts,
    )


def _class_printouts_readiness(settings: Mapping[str, object], language: str) -> WorkflowReadiness:
    t = _translator(language)
    source_dir = _path(settings, "class_source_dir")
    print_root = _path(settings, "print_output_root", DEFAULT_PRINT_OUTPUT_ROOT)
    final_dictionary = artifact_path(_path(settings, "dictionary_output_dir", DEFAULT_OUTPUT_DIR), ArtifactId.FINAL_DICTIONARY)
    live_lexicon = _path(settings, "live_lexicon_path")
    artifacts = {
        "parsed": str(_path(settings, "source_output_root", DEFAULT_PARSED_SOURCE_ROOT)),
        "rewritten": str(_path(settings, "rewrite_output_root", DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT)),
        "printouts": str(print_root),
    }
    if _has_file_named(print_root, "material.docx"):
        return WorkflowReadiness(
            key="class_printouts",
            status=STATUS_DONE,
            label=t("readiness.class.done.label"),
            detail=t("readiness.class.done.detail"),
            next_action=t("readiness.class.done.next"),
            artifacts=artifacts,
        )
    missing: list[str] = []
    if not source_dir.is_dir() or not _has_file_with_suffix(source_dir, SUPPORTED_SOURCE_EXTENSIONS):
        missing.append(t("readiness.missing.class_source_materials"))
    if not live_lexicon.is_file() and not final_dictionary.is_file():
        missing.append(t("readiness.missing.live_lexicon_or_final_dictionary"))
    if missing:
        return WorkflowReadiness(
            key="class_printouts",
            status=STATUS_MISSING_INPUT,
            label=t("readiness.class.missing.label"),
            detail=t("readiness.class.missing.detail"),
            next_action=t("readiness.class.missing.next"),
            missing=tuple(missing),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="class_printouts",
        status=STATUS_READY,
        label=t("readiness.class.ready.label"),
        detail=t("readiness.class.ready.detail"),
        next_action=t("readiness.class.ready.next"),
        artifacts=artifacts,
    )


def _archive_readiness(settings: Mapping[str, object], language: str) -> WorkflowReadiness:
    t = _translator(language)
    archive_root = _path(settings, "archive_root")
    usage_output_root = _path(settings, "usage_signal_output_root")
    live_lexicon = _path(settings, "live_lexicon_path")
    artifacts = {"usage_output": str(usage_output_root), "live_lexicon": str(live_lexicon)}
    if live_lexicon.is_file():
        return WorkflowReadiness(
            key="archive",
            status=STATUS_DONE,
            label=t("readiness.archive.done.label"),
            detail=t("readiness.archive.done.detail"),
            next_action=t("readiness.archive.done.next"),
            artifacts=artifacts,
        )
    if not archive_root.is_dir() or not _has_file_with_suffix(archive_root, HANDOUT_EXTENSIONS):
        return WorkflowReadiness(
            key="archive",
            status=STATUS_MISSING_INPUT,
            label=t("readiness.archive.missing.label"),
            detail=t("readiness.archive.missing.detail"),
            next_action=t("readiness.archive.missing.next"),
            missing=(t("readiness.missing.archived_handouts"),),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="archive",
        status=STATUS_READY,
        label=t("readiness.archive.ready.label"),
        detail=t("readiness.archive.ready.detail"),
        next_action=t("readiness.archive.ready.next"),
        artifacts=artifacts,
    )


def _reports_readiness(settings: Mapping[str, object], language: str) -> WorkflowReadiness:
    t = _translator(language)
    printout = _path(settings, "after_class_printout_path")
    output_root = _path(settings, "after_class_report_output_root")
    artifacts = {"report_output": str(output_root)}
    if output_root.is_dir() and _has_file_named(output_root, "after_class_report.docx"):
        return WorkflowReadiness(
            key="reports",
            status=STATUS_DONE,
            label=t("readiness.reports.done.label"),
            detail=t("readiness.reports.done.detail"),
            next_action=t("readiness.reports.done.next"),
            artifacts=artifacts,
        )
    if not printout.is_file():
        return WorkflowReadiness(
            key="reports",
            status=STATUS_MISSING_INPUT,
            label=t("readiness.reports.missing.label"),
            detail=t("readiness.reports.missing.detail"),
            next_action=t("readiness.reports.missing.next"),
            missing=(t("readiness.missing.archived_printout"),),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="reports",
        status=STATUS_READY,
        label=t("readiness.reports.ready.label"),
        detail=t("readiness.reports.ready.detail"),
        next_action=t("readiness.reports.ready.next"),
        artifacts=artifacts,
    )


def _with_job_state(
    readiness: WorkflowReadiness,
    active: str,
    key: str,
    failed: bool,
    language: str,
) -> WorkflowReadiness:
    t = _translator(language)
    if active != key:
        return readiness
    if failed:
        return WorkflowReadiness(
            key=readiness.key,
            status=STATUS_FAILED,
            label=t("readiness.failed.label"),
            detail=readiness.detail,
            next_action=t("readiness.failed.next"),
            missing=readiness.missing,
            artifacts=readiness.artifacts,
        )
    return WorkflowReadiness(
        key=readiness.key,
        status=STATUS_RUNNING,
        label=t("readiness.running.label"),
        detail=readiness.detail,
        next_action=t("readiness.running.next"),
        missing=readiness.missing,
        artifacts=readiness.artifacts,
    )


def _active_workflow(job_state: Mapping[str, object] | None) -> str:
    if not job_state or not bool(job_state.get("busy")) and str(job_state.get("status") or "") != STATUS_FAILED:
        return ""
    workflow = str(job_state.get("workflow") or "")
    if workflow in {"dictionary_build", "dictionary_prep", "dictionary"}:
        return "dictionary"
    if workflow == "class_printouts":
        return "class_printouts"
    if workflow in {"archive", "archive_rename_preview", "archive_rename_apply", "archive_duplicates_preview", "archive_duplicates_apply", "usage_signals", "live_lexicon"}:
        return "archive"
    if workflow in {"reports", "after_class_report"}:
        return "reports"
    return workflow


def _path(settings: Mapping[str, object], key: str, default: Path | str = "") -> Path:
    value = settings.get(key)
    text = str(value if value not in {None, ""} else default)
    return Path(text)


def _has_file_with_suffix(root: Path, suffixes: set[str]) -> bool:
    if not root.is_dir():
        return False
    return any(path.is_file() and path.suffix.lower() in suffixes for path in root.iterdir())


def _has_file_named(root: Path, filename: str) -> bool:
    if not root.is_dir():
        return False
    return any(path.is_file() and path.name == filename for path in root.rglob(filename))


def _translator(language: str):
    return lambda key: localized_text(language, key)
