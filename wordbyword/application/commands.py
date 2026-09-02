from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path

if TYPE_CHECKING:
    from wordbyword.application.workspace import RewriteVocabularySource
    from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings


class CommandId(StrEnum):
    MIGRATE_GRADE_WORKSPACES = "migrate_grade_workspaces"
    DICTIONARY_BUILD = "dictionary_build"
    DICTIONARY_PREP = "dictionary_prep"
    DICTIONARY_RECOVERY = "dictionary_recovery"
    PARSE_SOURCE_MATERIAL = "parse_source_material"
    PARSE_SOURCE_MATERIALS = "parse_source_materials"
    REWRITE_WHOLE_SOURCE_MATERIAL = "rewrite_whole_source_material"
    REWRITE_WHOLE_SOURCE_MATERIALS = "rewrite_whole_source_materials"
    REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL = "rewrite_vocabulary_guided_source_material"
    REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS = "rewrite_vocabulary_guided_source_materials"
    PRINT_CLASS_MATERIAL = "print_class_material"
    PRINT_CLASS_MATERIALS = "print_class_materials"
    CLASS_PRINTOUTS = "class_printouts"
    AFTER_CLASS_REPORT = "after_class_report"
    RESOLVE_ARCHIVE_DUPLICATES = "resolve_archive_duplicates"
    RESOLVE_DICTIONARY_SOURCE_DUPLICATES = "resolve_dictionary_source_duplicates"
    RENAME_CLASS_SESSION_ARCHIVE = "rename_class_session_archive"
    RENAME_DICTIONARY_SOURCE_ARCHIVE = "rename_dictionary_source_archive"
    USAGE_SIGNALS = "usage_signals"
    LIVE_LEXICON = "live_lexicon"
    SHOW_DISTRIBUTION = "show_distribution"
    LINK_CLEANED_EXAMPLES = "link_cleaned_examples"
    ADD_REFERENCE_MEANINGS = "add_reference_meanings"
    GENERATE_MISSING_MEANINGS = "generate_missing_meanings"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    status: str
    detail: str = ""
    artifacts: Mapping[str, str] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class CommandSpec:
    id: CommandId
    lock_label: str | None
    group: str


@dataclass(frozen=True)
class MigrateGradeWorkspacesRequest:
    command_id: CommandId
    apply: bool


@dataclass(frozen=True)
class DictionaryBuildRequest:
    command_id: CommandId
    target_checkpoint: str
    target_grade: str
    input_dir: Path
    output_dir: Path
    reference_dictionary_path: Path
    include_punctuation: bool
    include_numbers: bool
    model: str
    base_url: str | None
    force: bool
    provider: str = "ollama"
    enforce_managed_sources: bool = True


@dataclass(frozen=True)
class DictionaryRecoveryRequest:
    command_id: CommandId
    target_grade: str
    input_dir: Path
    output_dir: Path
    include_punctuation: bool
    include_numbers: bool
    clean_example_sentences: bool
    clean_dictionary_candidates: bool
    generate_missing_examples: bool
    retry_generated_examples: bool
    model: str
    base_url: str | None
    provider: str = "ollama"


@dataclass(frozen=True)
class SourceMaterialParseRequest:
    command_id: CommandId
    target_grade: str
    source_path: Path
    output_root: Path
    source_id: str | None
    parse_pdf: bool
    render_dpi: int
    paddleocr_vl_settings: PaddleOCRVLSettings | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SourceMaterialsParseRequest:
    command_id: CommandId
    target_grade: str
    source_dir: Path
    output_root: Path
    parse_pdf: bool
    render_dpi: int
    paddleocr_vl_settings: PaddleOCRVLSettings | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SourceMaterialRewriteRequest:
    command_id: CommandId
    target_grade: str
    parsed_path: Path
    output_root: Path
    model: str
    base_url: str | None
    vocabulary_source: RewriteVocabularySource | None = None
    provider: str = "ollama"


@dataclass(frozen=True)
class ClassMaterialPrintRequest:
    command_id: CommandId
    target_grade: str
    rewritten_path: Path
    output_root: Path
    model: str
    base_url: str | None
    font_path: Path | None
    body_font_size: int | None
    provider: str = "ollama"


@dataclass(frozen=True)
class ClassPrintoutsRequest:
    command_id: CommandId
    target_grade: str
    source_dir: Path
    parse_output_root: Path
    rewrite_output_root: Path
    print_output_root: Path
    dictionary_output_dir: Path
    live_lexicon_path: Path
    rewrite_dictionary_path: Path | None
    vocabulary_source: RewriteVocabularySource
    parse_pdf: bool
    render_dpi: int
    paddleocr_vl_settings: PaddleOCRVLSettings | None = field(compare=False)
    model: str
    base_url: str | None
    font_path: Path | None
    body_font_size: int | None
    force_class_printouts: bool
    provider: str = "ollama"


@dataclass(frozen=True)
class ArchiveRenameRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool
    model: str
    base_url: str | None
    provider: str = "ollama"


@dataclass(frozen=True)
class ArchiveDuplicatesRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool
    include_near_duplicates: bool
    model: str
    base_url: str | None
    provider: str = "ollama"


@dataclass(frozen=True)
class DictionarySourceDuplicatesRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool


@dataclass(frozen=True)
class DictionarySourceRenameRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool
    model: str
    base_url: str | None
    provider: str = "ollama"


@dataclass(frozen=True)
class UsageSignalsRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    output_root: Path
    include_punctuation: bool
    include_numbers: bool
    model: str
    base_url: str | None
    provider: str = "ollama"


@dataclass(frozen=True)
class LiveLexiconRequest:
    command_id: CommandId
    target_grade: str
    canonical_dictionary_path: Path
    usage_candidates_path: Path
    usage_summary_path: Path | None
    live_lexicon_path: Path
    supplemental_lexicon_path: Path
    unmatched_usage_output_path: Path


@dataclass(frozen=True)
class AfterClassReportRequest:
    command_id: CommandId
    target_grade: str
    printout_path: Path
    output_root: Path
    model: str
    base_url: str | None
    font_path: Path | None
    provider: str = "ollama"


@dataclass(frozen=True)
class ShowDistributionRequest:
    command_id: CommandId
    jsonl_path: Path
    model: str


@dataclass(frozen=True)
class DictionaryStageRequest:
    command_id: CommandId
    output_dir: Path
    reference_dictionary_path: Path | None = None
    model: str = ""
    base_url: str | None = None
    provider: str = "ollama"


CommandRequest = (
    MigrateGradeWorkspacesRequest
    | DictionaryBuildRequest
    | DictionaryRecoveryRequest
    | SourceMaterialParseRequest
    | SourceMaterialsParseRequest
    | SourceMaterialRewriteRequest
    | ClassMaterialPrintRequest
    | ClassPrintoutsRequest
    | ArchiveRenameRequest
    | ArchiveDuplicatesRequest
    | DictionarySourceDuplicatesRequest
    | DictionarySourceRenameRequest
    | UsageSignalsRequest
    | LiveLexiconRequest
    | AfterClassReportRequest
    | ShowDistributionRequest
    | DictionaryStageRequest
)


_SPECS: dict[CommandId, CommandSpec] = {
    CommandId.MIGRATE_GRADE_WORKSPACES: CommandSpec(
        CommandId.MIGRATE_GRADE_WORKSPACES, "grade workspace migration", "workspace"
    ),
    CommandId.DICTIONARY_BUILD: CommandSpec(
        CommandId.DICTIONARY_BUILD, "dictionary build", "dictionary"
    ),
    CommandId.DICTIONARY_PREP: CommandSpec(
        CommandId.DICTIONARY_PREP, "dictionary prep", "dictionary"
    ),
    CommandId.DICTIONARY_RECOVERY: CommandSpec(
        CommandId.DICTIONARY_RECOVERY, "dictionary recovery", "dictionary"
    ),
    CommandId.PARSE_SOURCE_MATERIAL: CommandSpec(
        CommandId.PARSE_SOURCE_MATERIAL, "source material parse", "source"
    ),
    CommandId.PARSE_SOURCE_MATERIALS: CommandSpec(
        CommandId.PARSE_SOURCE_MATERIALS, "source materials parse", "source"
    ),
    CommandId.REWRITE_WHOLE_SOURCE_MATERIAL: CommandSpec(
        CommandId.REWRITE_WHOLE_SOURCE_MATERIAL, "whole source material rewrite", "source"
    ),
    CommandId.REWRITE_WHOLE_SOURCE_MATERIALS: CommandSpec(
        CommandId.REWRITE_WHOLE_SOURCE_MATERIALS, "whole source materials rewrite", "source"
    ),
    CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL: CommandSpec(
        CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL,
        "vocabulary-guided source material rewrite",
        "source",
    ),
    CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS: CommandSpec(
        CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS,
        "vocabulary-guided source materials rewrite",
        "source",
    ),
    CommandId.PRINT_CLASS_MATERIAL: CommandSpec(
        CommandId.PRINT_CLASS_MATERIAL, "class material print", "class"
    ),
    CommandId.PRINT_CLASS_MATERIALS: CommandSpec(
        CommandId.PRINT_CLASS_MATERIALS, "class materials print", "class"
    ),
    CommandId.CLASS_PRINTOUTS: CommandSpec(
        CommandId.CLASS_PRINTOUTS, "class printout build", "class"
    ),
    CommandId.AFTER_CLASS_REPORT: CommandSpec(
        CommandId.AFTER_CLASS_REPORT, "after-class reading report", "reports"
    ),
    CommandId.RESOLVE_ARCHIVE_DUPLICATES: CommandSpec(
        CommandId.RESOLVE_ARCHIVE_DUPLICATES, "archive duplicate resolution", "archive"
    ),
    CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES: CommandSpec(
        CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES,
        "dictionary source duplicate resolution",
        "dictionary",
    ),
    CommandId.RENAME_CLASS_SESSION_ARCHIVE: CommandSpec(
        CommandId.RENAME_CLASS_SESSION_ARCHIVE, "class session archive rename", "archive"
    ),
    CommandId.RENAME_DICTIONARY_SOURCE_ARCHIVE: CommandSpec(
        CommandId.RENAME_DICTIONARY_SOURCE_ARCHIVE,
        "dictionary source archive rename",
        "dictionary",
    ),
    CommandId.USAGE_SIGNALS: CommandSpec(
        CommandId.USAGE_SIGNALS, "usage signal build", "archive"
    ),
    CommandId.LIVE_LEXICON: CommandSpec(
        CommandId.LIVE_LEXICON, "live lexicon update", "archive"
    ),
    CommandId.SHOW_DISTRIBUTION: CommandSpec(
        CommandId.SHOW_DISTRIBUTION, None, "dictionary"
    ),
    CommandId.LINK_CLEANED_EXAMPLES: CommandSpec(
        CommandId.LINK_CLEANED_EXAMPLES, "cleaned example linking", "dictionary"
    ),
    CommandId.ADD_REFERENCE_MEANINGS: CommandSpec(
        CommandId.ADD_REFERENCE_MEANINGS, "reference meaning assignment", "dictionary"
    ),
    CommandId.GENERATE_MISSING_MEANINGS: CommandSpec(
        CommandId.GENERATE_MISSING_MEANINGS, "missing meaning generation", "dictionary"
    ),
}

def registered_command_ids() -> tuple[CommandId, ...]:
    return tuple(_SPECS)


def command_spec(command_id: CommandId | str) -> CommandSpec:
    return _SPECS[CommandId(command_id)]


def request_output_artifact_path(request: CommandRequest, artifact_id: ArtifactId) -> Path:
    output_dir = getattr(request, "output_dir", None)
    if not isinstance(output_dir, Path):
        raise ValueError(f"{request.command_id} does not expose an output_dir")
    return artifact_path(output_dir, artifact_id)
