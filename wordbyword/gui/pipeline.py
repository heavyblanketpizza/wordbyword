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
    input_label: str = ""
    input_path_key: str = ""
    output_label: str = ""
    output_path_key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "detail": self.detail,
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


def workflow_definitions() -> tuple[WorkflowDefinition, ...]:
    return (
        WorkflowDefinition(
            key="before_start",
            number="00",
            title="Before You Start",
            purpose="Put source files in the expected folders and make sure Ollama is available for model-backed steps.",
            primary_action_label="Review Setup",
            primary_workflow_id="",
            stages=(
                WorkflowStage(
                    "Dictionary sources",
                    "Put PDF, TXT, or MD source files in the dictionary source folder.",
                    output_label="Source folder",
                    output_path_key="dictionary_input_dir",
                ),
                WorkflowStage(
                    "Class source PDFs",
                    "Put classroom source materials in the class source-materials folder.",
                    output_label="Source folder",
                    output_path_key="class_source_dir",
                ),
                WorkflowStage(
                    "Ollama runtime",
                    "Model-backed cleanup, rewrite, rename, and report steps use the configured Ollama model.",
                    output_label="Model",
                    output_path_key="model",
                ),
            ),
        ),
        WorkflowDefinition(
            key="dictionary",
            number="01",
            title="Dictionary",
            purpose="Make a Korean learner-vocabulary dictionary from source documents.",
            primary_action_label="Build Final Dictionary",
            primary_workflow_id="dictionary_build",
            secondary_action_label="Prepare 00-09 Only",
            secondary_workflow_id="dictionary_prep",
            expected_output_key="dictionary_final",
            stages=(
                WorkflowStage(
                    "Source files",
                    "Read managed PDF, TXT, and MD files from the dictionary source folder.",
                    input_label="Input",
                    input_path_key="dictionary_input_dir",
                    output_label="Extracted text",
                    output_path_key="dictionary_output_dir",
                ),
                WorkflowStage(
                    "Stages 00-09",
                    "Extract pages, split sentences, tokenize Korean, score candidate terms, and collect example candidates.",
                    output_label="Prep artifacts",
                    output_path_key="dictionary_output_dir",
                ),
                WorkflowStage(
                    "Stages 10-13",
                    "Clean examples and dictionary candidates, link examples, and generate missing examples.",
                    output_label="Example-enriched vocabulary",
                    output_path_key="dictionary_output_dir",
                ),
                WorkflowStage(
                    "Stages 14-15",
                    "Add reference meanings and generate missing meanings to produce the final dictionary.",
                    input_label="Reference",
                    input_path_key="reference_dictionary_path",
                    output_label="Final dictionary",
                    output_path_key="dictionary_final",
                ),
            ),
        ),
        WorkflowDefinition(
            key="class_printouts",
            number="02",
            title="Class Printouts",
            purpose="Turn hard class readings into easier Korean DOCX handouts.",
            primary_action_label="Build Class Printouts",
            primary_workflow_id="class_printouts",
            expected_output_key="print_output_root",
            stages=(
                WorkflowStage(
                    "Source PDFs",
                    "Read class source materials from the class-source-materials folder.",
                    input_label="Input",
                    input_path_key="class_source_dir",
                    output_label="Parsed bundles",
                    output_path_key="source_output_root",
                ),
                WorkflowStage(
                    "OCR parse",
                    "Parse PDFs with PaddleOCR-VL and save markdown, chunks, quality reports, and assets.",
                    output_label="Parsed output",
                    output_path_key="source_output_root",
                ),
                WorkflowStage(
                    "Vocabulary-guided rewrite",
                    "Use the live lexicon when available, otherwise the final dictionary, to rewrite materials for class.",
                    input_label="Vocabulary",
                    input_path_key="vocabulary_source",
                    output_label="Rewrite output",
                    output_path_key="rewrite_output_root",
                ),
                WorkflowStage(
                    "DOCX handouts",
                    "Create editable Word handouts for class.",
                    output_label="Print output",
                    output_path_key="print_output_root",
                ),
            ),
        ),
        WorkflowDefinition(
            key="archive",
            number="03",
            title="Archive & Lexicon",
            purpose="Learn from used class handouts and maintain the live classroom vocabulary list.",
            primary_action_label="Build Usage Signals",
            primary_workflow_id="usage_signals",
            secondary_action_label="Update Live Lexicon",
            secondary_workflow_id="live_lexicon",
            expected_output_key="live_lexicon_path",
            stages=(
                WorkflowStage(
                    "Used handouts",
                    "Put used DOCX or PDF handouts in the class-session archive.",
                    input_label="Archive",
                    input_path_key="archive_root",
                    output_label="Managed archive",
                    output_path_key="archive_root",
                ),
                WorkflowStage(
                    "Safe names",
                    "Preview and apply managed filenames before counting vocabulary usage.",
                    output_label="Archive ledger",
                    output_path_key="archive_root",
                ),
                WorkflowStage(
                    "Duplicate check",
                    "Resolve duplicate printouts before building usage signals.",
                    output_label="Quarantine if needed",
                    output_path_key="archive_root",
                ),
                WorkflowStage(
                    "Usage signals",
                    "Count vocabulary from used handouts and write usage signal artifacts.",
                    output_label="Usage output",
                    output_path_key="usage_signal_output_root",
                ),
                WorkflowStage(
                    "Live lexicon",
                    "Update canonical, supplemental, and unmatched vocabulary outputs.",
                    output_label="Live lexicon",
                    output_path_key="live_lexicon_path",
                ),
            ),
        ),
        WorkflowDefinition(
            key="reports",
            number="04",
            title="Reports",
            purpose="Generate one adult-facing after-class reading report from an archived handout.",
            primary_action_label="Generate Report",
            primary_workflow_id="after_class_report",
            expected_output_key="after_class_report_output_root",
            stages=(
                WorkflowStage(
                    "Archived printout",
                    "Choose a managed archived handout.",
                    input_label="Input",
                    input_path_key="after_class_printout_path",
                    output_label="Report folder",
                    output_path_key="after_class_report_output_root",
                ),
                WorkflowStage(
                    "Vocabulary analysis",
                    "Analyze the handout and choose report vocabulary.",
                    output_label="Heuristic analysis",
                    output_path_key="after_class_report_output_root",
                ),
                WorkflowStage(
                    "Adult-facing report",
                    "Write an editable DOCX report for adults to use after class.",
                    output_label="Report output",
                    output_path_key="after_class_report_output_root",
                ),
            ),
        ),
    )


def workflow_definition_payload() -> list[dict[str, object]]:
    return [definition.to_dict() for definition in workflow_definitions()]


def readiness_payload(settings: Mapping[str, object], job_state: Mapping[str, object] | None = None) -> dict[str, dict[str, object]]:
    return {
        readiness.key: readiness.to_dict()
        for readiness in workflow_readiness(settings, job_state=job_state)
    }


def workflow_readiness(
    settings: Mapping[str, object],
    *,
    job_state: Mapping[str, object] | None = None,
) -> tuple[WorkflowReadiness, ...]:
    active = _active_workflow(job_state)
    status = str((job_state or {}).get("status") or "")
    failed = status == STATUS_FAILED
    return (
        _with_job_state(_before_start_readiness(settings), active, "before_start", failed),
        _with_job_state(_dictionary_readiness(settings), active, "dictionary", failed),
        _with_job_state(_class_printouts_readiness(settings), active, "class_printouts", failed),
        _with_job_state(_archive_readiness(settings), active, "archive", failed),
        _with_job_state(_reports_readiness(settings), active, "reports", failed),
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


def _before_start_readiness(settings: Mapping[str, object]) -> WorkflowReadiness:
    missing: list[str] = []
    dictionary_dir = _path(settings, "dictionary_input_dir")
    class_dir = _path(settings, "class_source_dir")
    if not dictionary_dir.exists():
        missing.append("Dictionary source folder")
    if not class_dir.exists():
        missing.append("Class source folder")
    if not str(settings.get("model") or "").strip():
        missing.append("Ollama model")
    if missing:
        return WorkflowReadiness(
            key="before_start",
            status=STATUS_MISSING_INPUT,
            label="Setup incomplete",
            detail="Create the expected folders and set the model before running model-backed workflows.",
            next_action="Fill in the missing setup fields.",
            missing=tuple(missing),
        )
    return WorkflowReadiness(
        key="before_start",
        status=STATUS_READY,
        label="Setup ready",
        detail="Default folders and model settings are present.",
        next_action="Add source files, then build the dictionary or class printouts.",
    )


def _dictionary_readiness(settings: Mapping[str, object]) -> WorkflowReadiness:
    input_dir = _path(settings, "dictionary_input_dir")
    output_dir = _path(settings, "dictionary_output_dir", DEFAULT_OUTPUT_DIR)
    final_dictionary = artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)
    artifacts = {"final_dictionary": str(final_dictionary), "output_dir": str(output_dir)}
    if final_dictionary.is_file():
        return WorkflowReadiness(
            key="dictionary",
            status=STATUS_DONE,
            label="Final dictionary found",
            detail="The final stage-15 dictionary exists.",
            next_action="Use it for vocabulary-guided class printouts.",
            artifacts=artifacts,
        )
    if not input_dir.is_dir():
        return WorkflowReadiness(
            key="dictionary",
            status=STATUS_MISSING_INPUT,
            label="Missing source folder",
            detail="The dictionary source folder does not exist.",
            next_action="Create the folder and add PDF, TXT, or MD source files.",
            missing=("Dictionary source folder",),
            artifacts=artifacts,
        )
    if not _has_file_with_suffix(input_dir, DICTIONARY_SOURCE_EXTENSIONS):
        return WorkflowReadiness(
            key="dictionary",
            status=STATUS_MISSING_INPUT,
            label="No source files",
            detail="No PDF, TXT, or MD files were found in the dictionary source folder.",
            next_action="Add dictionary source files.",
            missing=("Dictionary source files",),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="dictionary",
        status=STATUS_READY,
        label="Ready to build",
        detail="Dictionary source files are present.",
        next_action="Build the final dictionary.",
        artifacts=artifacts,
    )


def _class_printouts_readiness(settings: Mapping[str, object]) -> WorkflowReadiness:
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
            label="Printouts found",
            detail="At least one DOCX handout exists in the print output folder.",
            next_action="Archive used handouts after class.",
            artifacts=artifacts,
        )
    missing: list[str] = []
    if not source_dir.is_dir() or not _has_file_with_suffix(source_dir, SUPPORTED_SOURCE_EXTENSIONS):
        missing.append("Class source materials")
    if not live_lexicon.is_file() and not final_dictionary.is_file():
        missing.append("Live lexicon or final dictionary")
    if missing:
        return WorkflowReadiness(
            key="class_printouts",
            status=STATUS_MISSING_INPUT,
            label="Missing inputs",
            detail="Class printouts need source materials and vocabulary guidance.",
            next_action="Add class source PDFs and build a dictionary or live lexicon.",
            missing=tuple(missing),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="class_printouts",
        status=STATUS_READY,
        label="Ready to build",
        detail="Class source materials and vocabulary guidance are available.",
        next_action="Build class printouts.",
        artifacts=artifacts,
    )


def _archive_readiness(settings: Mapping[str, object]) -> WorkflowReadiness:
    archive_root = _path(settings, "archive_root")
    usage_output_root = _path(settings, "usage_signal_output_root")
    live_lexicon = _path(settings, "live_lexicon_path")
    artifacts = {"usage_output": str(usage_output_root), "live_lexicon": str(live_lexicon)}
    if live_lexicon.is_file():
        return WorkflowReadiness(
            key="archive",
            status=STATUS_DONE,
            label="Live lexicon found",
            detail="The live lexicon exists and can guide future class printouts.",
            next_action="Generate reports or continue updating after more classes.",
            artifacts=artifacts,
        )
    if not archive_root.is_dir() or not _has_file_with_suffix(archive_root, HANDOUT_EXTENSIONS):
        return WorkflowReadiness(
            key="archive",
            status=STATUS_MISSING_INPUT,
            label="No archived handouts",
            detail="No used DOCX or PDF handouts were found in the archive folder.",
            next_action="Put used class handouts in the class-session archive.",
            missing=("Archived handouts",),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="archive",
        status=STATUS_READY,
        label="Ready to learn",
        detail="Archived handouts are available for usage-signal analysis.",
        next_action="Preview safe names, resolve duplicates, then build usage signals.",
        artifacts=artifacts,
    )


def _reports_readiness(settings: Mapping[str, object]) -> WorkflowReadiness:
    printout = _path(settings, "after_class_printout_path")
    output_root = _path(settings, "after_class_report_output_root")
    artifacts = {"report_output": str(output_root)}
    if output_root.is_dir() and _has_file_named(output_root, "after_class_report.docx"):
        return WorkflowReadiness(
            key="reports",
            status=STATUS_DONE,
            label="Report found",
            detail="At least one after-class report exists in the report output folder.",
            next_action="Generate another report when a new archived printout is ready.",
            artifacts=artifacts,
        )
    if not printout.is_file():
        return WorkflowReadiness(
            key="reports",
            status=STATUS_MISSING_INPUT,
            label="Choose a printout",
            detail="A managed archived printout is required before a report can be generated.",
            next_action="Select an archived DOCX or PDF handout.",
            missing=("Archived printout",),
            artifacts=artifacts,
        )
    return WorkflowReadiness(
        key="reports",
        status=STATUS_READY,
        label="Ready to report",
        detail="An archived printout is selected.",
        next_action="Generate the after-class reading report.",
        artifacts=artifacts,
    )


def _with_job_state(
    readiness: WorkflowReadiness,
    active: str,
    key: str,
    failed: bool,
) -> WorkflowReadiness:
    if active != key:
        return readiness
    if failed:
        return WorkflowReadiness(
            key=readiness.key,
            status=STATUS_FAILED,
            label="Last run failed",
            detail=readiness.detail,
            next_action="Review the run drawer and fix the reported issue.",
            missing=readiness.missing,
            artifacts=readiness.artifacts,
        )
    return WorkflowReadiness(
        key=readiness.key,
        status=STATUS_RUNNING,
        label="Running",
        detail=readiness.detail,
        next_action="Watch the run drawer for progress.",
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
