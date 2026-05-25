from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from wordbyword.application.workspace import (
    RewriteVocabularySource,
    bool_value,
    int_value,
    llm_runtime_config_from_args,
    llm_runtime_config_from_values,
    optional_int_value,
    optional_path_value,
    optional_text_value,
    path_value,
    resolved_archive_root,
    reject_other_grade_paths,
    resolved_after_class_report_output_root,
    resolved_class_source_dir,
    resolved_canonical_dictionary_path,
    resolved_dictionary_input_dir,
    resolved_dictionary_output_dir,
    resolved_dictionary_source_root,
    resolved_live_lexicon_path,
    resolved_parsed_source_root,
    resolved_print_output_root,
    resolved_print_class_materials_root,
    resolved_reference_dictionary_path,
    resolved_supplemental_lexicon_path,
    resolved_unmatched_usage_output_path,
    resolved_usage_candidates_path,
    resolved_usage_signal_input_root,
    resolved_usage_signal_output_root,
    resolved_whole_rewrite_output_root,
    resolved_vocabulary_rewrite_output_root,
    rewrite_vocabulary_source,
    source_parse_options_from_args,
    target_grade_from_args,
    target_grade_from_value,
    text_value,
)
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path


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
    ARCHIVE_DUPLICATES_PREVIEW = "archive_duplicates_preview"
    ARCHIVE_DUPLICATES_APPLY = "archive_duplicates_apply"
    RESOLVE_DICTIONARY_SOURCE_DUPLICATES = "resolve_dictionary_source_duplicates"
    RENAME_CLASS_SESSION_ARCHIVE = "rename_class_session_archive"
    ARCHIVE_RENAME_PREVIEW = "archive_rename_preview"
    ARCHIVE_RENAME_APPLY = "archive_rename_apply"
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


@dataclass(frozen=True)
class CommandSpec:
    id: CommandId
    lock_label: str | None
    group: str


@dataclass(frozen=True)
class MigrateGradeWorkspacesRequest:
    command_id: CommandId
    apply: bool
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SourceMaterialsParseRequest:
    command_id: CommandId
    target_grade: str
    source_dir: Path
    output_root: Path
    parse_pdf: bool
    render_dpi: int
    paddleocr_vl_settings: PaddleOCRVLSettings | None = field(default=None, compare=False)
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ArchiveRenameRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool
    model: str
    base_url: str | None
    provider: str = "ollama"
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class DictionarySourceDuplicatesRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool
    raw_args: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class DictionarySourceRenameRequest:
    command_id: CommandId
    target_grade: str
    archive_root: Path
    apply: bool
    model: str
    base_url: str | None
    provider: str = "ollama"
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


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
    raw_args: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ShowDistributionRequest:
    command_id: CommandId
    jsonl_path: Path
    model: str
    raw_args: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class DictionaryStageRequest:
    command_id: CommandId
    output_dir: Path
    reference_dictionary_path: Path | None = None
    model: str = ""
    base_url: str | None = None
    provider: str = "ollama"
    raw_args: Any = field(default=None, compare=False, repr=False)


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


PIPELINE_CLEANUP_FLAGS = (
    "clean_example_sentences",
    "clean_dictionary_candidates",
    "generate_missing_examples",
    "retry_generated_examples",
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
    CommandId.ARCHIVE_DUPLICATES_PREVIEW: CommandSpec(
        CommandId.ARCHIVE_DUPLICATES_PREVIEW, "archive duplicate resolution", "archive"
    ),
    CommandId.ARCHIVE_DUPLICATES_APPLY: CommandSpec(
        CommandId.ARCHIVE_DUPLICATES_APPLY, "archive duplicate resolution", "archive"
    ),
    CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES: CommandSpec(
        CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES,
        "dictionary source duplicate resolution",
        "dictionary",
    ),
    CommandId.RENAME_CLASS_SESSION_ARCHIVE: CommandSpec(
        CommandId.RENAME_CLASS_SESSION_ARCHIVE, "class session archive rename", "archive"
    ),
    CommandId.ARCHIVE_RENAME_PREVIEW: CommandSpec(
        CommandId.ARCHIVE_RENAME_PREVIEW, "class session archive rename", "archive"
    ),
    CommandId.ARCHIVE_RENAME_APPLY: CommandSpec(
        CommandId.ARCHIVE_RENAME_APPLY, "class session archive rename", "archive"
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

_GUI_WORKFLOW_IDS: dict[str, CommandId] = {
    "dictionary_build": CommandId.DICTIONARY_BUILD,
    "dictionary_prep": CommandId.DICTIONARY_PREP,
    "class_printouts": CommandId.CLASS_PRINTOUTS,
    "archive_rename_preview": CommandId.ARCHIVE_RENAME_PREVIEW,
    "archive_rename_apply": CommandId.ARCHIVE_RENAME_APPLY,
    "archive_duplicates_preview": CommandId.ARCHIVE_DUPLICATES_PREVIEW,
    "archive_duplicates_apply": CommandId.ARCHIVE_DUPLICATES_APPLY,
    "usage_signals": CommandId.USAGE_SIGNALS,
    "live_lexicon": CommandId.LIVE_LEXICON,
    "after_class_report": CommandId.AFTER_CLASS_REPORT,
}

DICTIONARY_DISTRIBUTION_ARTIFACT_IDS = (
    ArtifactId.DICTIONARY_CANDIDATES,
    ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
    ArtifactId.LINKED_DICTIONARY_CANDIDATES,
    ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ArtifactId.MEANING_DICTIONARY_CANDIDATES,
    ArtifactId.FINAL_DICTIONARY,
)

DEFAULT_DISTRIBUTION_ARTIFACT_IDS = tuple(reversed(DICTIONARY_DISTRIBUTION_ARTIFACT_IDS))


def registered_command_ids() -> tuple[CommandId, ...]:
    return tuple(_SPECS)


def command_spec(command_id: CommandId | str) -> CommandSpec:
    return _SPECS[CommandId(command_id)]


def gui_workflow_command_ids() -> Mapping[str, CommandId]:
    return dict(_GUI_WORKFLOW_IDS)


def resolve_distribution_path(supplied: str, output_dir: Path) -> Path:
    if supplied:
        return Path(supplied)
    for artifact_id in DEFAULT_DISTRIBUTION_ARTIFACT_IDS:
        path = artifact_path(output_dir, artifact_id)
        if path.is_file():
            return path
    return artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)


def command_id_from_cli_args(args: object) -> CommandId | None:
    if getattr(args, "show_distribution", None) is not None:
        return CommandId.SHOW_DISTRIBUTION
    if getattr(args, "migrate_grade_workspaces", False):
        return CommandId.MIGRATE_GRADE_WORKSPACES
    if getattr(args, "build_dictionary", False):
        return CommandId.DICTIONARY_BUILD
    if getattr(args, "prep_dictionary", False):
        return CommandId.DICTIONARY_PREP
    if any(bool(getattr(args, flag_name, False)) for flag_name in PIPELINE_CLEANUP_FLAGS):
        return CommandId.DICTIONARY_RECOVERY
    if getattr(args, "parse_source_material", None) is not None:
        return CommandId.PARSE_SOURCE_MATERIAL
    if getattr(args, "parse_source_materials", None) is not None:
        return CommandId.PARSE_SOURCE_MATERIALS
    if getattr(args, "rewrite_whole_source_material", None) is not None:
        return CommandId.REWRITE_WHOLE_SOURCE_MATERIAL
    if getattr(args, "rewrite_whole_source_materials", None) is not None:
        return CommandId.REWRITE_WHOLE_SOURCE_MATERIALS
    if getattr(args, "rewrite_vocabulary_guided_source_material", None) is not None:
        return CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL
    if getattr(args, "rewrite_vocabulary_guided_source_materials", None) is not None:
        return CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS
    if getattr(args, "print_class_material", None) is not None:
        return CommandId.PRINT_CLASS_MATERIAL
    if getattr(args, "print_class_materials", None) is not None:
        return CommandId.PRINT_CLASS_MATERIALS
    if getattr(args, "build_class_printouts", None) is not None:
        return CommandId.CLASS_PRINTOUTS
    if getattr(args, "generate_after_class_reading_report", None) is not None:
        return CommandId.AFTER_CLASS_REPORT
    if getattr(args, "resolve_archive_duplicates", None) is not None:
        return CommandId.RESOLVE_ARCHIVE_DUPLICATES
    if getattr(args, "resolve_dictionary_source_duplicates", None) is not None:
        return CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES
    if getattr(args, "rename_class_session_archive", None) is not None:
        return CommandId.RENAME_CLASS_SESSION_ARCHIVE
    if getattr(args, "rename_dictionary_source_archive", None) is not None:
        return CommandId.RENAME_DICTIONARY_SOURCE_ARCHIVE
    if getattr(args, "build_usage_signals", False):
        return CommandId.USAGE_SIGNALS
    if getattr(args, "update_live_lexicon", False):
        return CommandId.LIVE_LEXICON
    if getattr(args, "link_cleaned_examples", False):
        return CommandId.LINK_CLEANED_EXAMPLES
    if getattr(args, "add_reference_meanings", False):
        return CommandId.ADD_REFERENCE_MEANINGS
    if getattr(args, "generate_missing_meanings", False):
        return CommandId.GENERATE_MISSING_MEANINGS
    return None


def build_cli_request(args: object) -> CommandRequest | None:
    command_id = command_id_from_cli_args(args)
    if command_id is None:
        return None
    llm = llm_runtime_config_from_args(args)
    if command_id == CommandId.MIGRATE_GRADE_WORKSPACES:
        return MigrateGradeWorkspacesRequest(
            command_id=command_id,
            apply=bool(getattr(args, "apply_grade_workspace_migration", False)),
            raw_args=args,
        )
    if command_id in {CommandId.DICTIONARY_BUILD, CommandId.DICTIONARY_PREP}:
        return DictionaryBuildRequest(
            command_id=command_id,
            target_checkpoint="15" if command_id == CommandId.DICTIONARY_BUILD else "09",
            target_grade=target_grade_from_args(args),
            input_dir=resolved_dictionary_input_dir(args),
            output_dir=resolved_dictionary_output_dir(args),
            reference_dictionary_path=resolved_reference_dictionary_path(args),
            include_punctuation=bool(getattr(args, "include_punctuation", False)),
            include_numbers=bool(getattr(args, "include_numbers", False)),
            model=llm.model,
            base_url=llm.base_url,
            force=bool(getattr(args, "force", False)),
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.DICTIONARY_RECOVERY:
        return DictionaryRecoveryRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            input_dir=resolved_dictionary_input_dir(args),
            output_dir=resolved_dictionary_output_dir(args),
            include_punctuation=bool(getattr(args, "include_punctuation", False)),
            include_numbers=bool(getattr(args, "include_numbers", False)),
            clean_example_sentences=bool(getattr(args, "clean_example_sentences", False)),
            clean_dictionary_candidates=bool(getattr(args, "clean_dictionary_candidates", False)),
            generate_missing_examples=bool(getattr(args, "generate_missing_examples", False)),
            retry_generated_examples=bool(getattr(args, "retry_generated_examples", False)),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.PARSE_SOURCE_MATERIAL:
        parse_options = source_parse_options_from_args(args)
        return SourceMaterialParseRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            source_path=getattr(args, "parse_source_material"),
            output_root=parse_options.output_root,
            source_id=getattr(args, "source_id", None),
            parse_pdf=parse_options.parse_pdf,
            render_dpi=parse_options.render_dpi,
            paddleocr_vl_settings=parse_options.paddleocr_vl_settings,
            raw_args=args,
        )
    if command_id == CommandId.PARSE_SOURCE_MATERIALS:
        parse_options = source_parse_options_from_args(args)
        return SourceMaterialsParseRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            source_dir=resolved_class_source_dir(args, getattr(args, "parse_source_materials")),
            output_root=parse_options.output_root,
            parse_pdf=parse_options.parse_pdf,
            render_dpi=parse_options.render_dpi,
            paddleocr_vl_settings=parse_options.paddleocr_vl_settings,
            raw_args=args,
        )
    if command_id in {CommandId.REWRITE_WHOLE_SOURCE_MATERIAL, CommandId.REWRITE_WHOLE_SOURCE_MATERIALS}:
        parsed_path = getattr(
            args,
            "rewrite_whole_source_material"
            if command_id == CommandId.REWRITE_WHOLE_SOURCE_MATERIAL
            else "rewrite_whole_source_materials",
        )
        if command_id == CommandId.REWRITE_WHOLE_SOURCE_MATERIALS:
            parsed_path = resolved_parsed_source_root(args, parsed_path)
        return SourceMaterialRewriteRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            parsed_path=parsed_path,
            output_root=resolved_whole_rewrite_output_root(args),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    if command_id in {
        CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL,
        CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS,
    }:
        target_grade = target_grade_from_args(args)
        dictionary_output_dir = resolved_dictionary_output_dir(args)
        explicit_rewrite_dictionary = getattr(args, "rewrite_dictionary", None)
        parsed_path = getattr(
            args,
            "rewrite_vocabulary_guided_source_material"
            if command_id == CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL
            else "rewrite_vocabulary_guided_source_materials",
        )
        if command_id == CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS:
            parsed_path = resolved_parsed_source_root(args, parsed_path)
        return SourceMaterialRewriteRequest(
            command_id=command_id,
            target_grade=target_grade,
            parsed_path=parsed_path,
            output_root=resolved_vocabulary_rewrite_output_root(args),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            vocabulary_source=rewrite_vocabulary_source(
                explicit_path=explicit_rewrite_dictionary,
                target_grade=target_grade,
                dictionary_output_dir=dictionary_output_dir,
            ),
            raw_args=args,
        )
    if command_id in {CommandId.PRINT_CLASS_MATERIAL, CommandId.PRINT_CLASS_MATERIALS}:
        rewritten_path = getattr(
            args,
            "print_class_material" if command_id == CommandId.PRINT_CLASS_MATERIAL else "print_class_materials",
        )
        if command_id == CommandId.PRINT_CLASS_MATERIALS:
            rewritten_path = resolved_print_class_materials_root(args)
        return ClassMaterialPrintRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            rewritten_path=rewritten_path,
            output_root=resolved_print_output_root(args),
            model=llm.model,
            base_url=llm.base_url,
            font_path=getattr(args, "print_font_path", None),
            body_font_size=getattr(args, "print_body_font_size", None),
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.CLASS_PRINTOUTS:
        target_grade = target_grade_from_args(args)
        dictionary_output_dir = resolved_dictionary_output_dir(args)
        explicit_rewrite_dictionary = getattr(args, "rewrite_dictionary", None)
        parse_options = source_parse_options_from_args(args)
        return ClassPrintoutsRequest(
            command_id=command_id,
            target_grade=target_grade,
            source_dir=resolved_class_source_dir(args, getattr(args, "build_class_printouts")),
            parse_output_root=parse_options.output_root,
            rewrite_output_root=resolved_vocabulary_rewrite_output_root(args),
            print_output_root=resolved_print_output_root(args),
            dictionary_output_dir=dictionary_output_dir,
            live_lexicon_path=resolved_live_lexicon_path(args),
            rewrite_dictionary_path=explicit_rewrite_dictionary,
            vocabulary_source=rewrite_vocabulary_source(
                explicit_path=explicit_rewrite_dictionary,
                target_grade=target_grade,
                dictionary_output_dir=dictionary_output_dir,
            ),
            parse_pdf=parse_options.parse_pdf,
            render_dpi=parse_options.render_dpi,
            paddleocr_vl_settings=parse_options.paddleocr_vl_settings,
            model=llm.model,
            base_url=llm.base_url,
            font_path=getattr(args, "print_font_path", None),
            body_font_size=getattr(args, "print_body_font_size", None),
            force_class_printouts=bool(getattr(args, "force_class_printouts", False)),
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.RENAME_CLASS_SESSION_ARCHIVE:
        return ArchiveRenameRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            archive_root=resolved_archive_root(args, getattr(args, "rename_class_session_archive")),
            apply=bool(getattr(args, "apply_archive_renames", False)),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.RESOLVE_ARCHIVE_DUPLICATES:
        return ArchiveDuplicatesRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            archive_root=resolved_archive_root(args, getattr(args, "resolve_archive_duplicates")),
            apply=bool(getattr(args, "yes", False)),
            include_near_duplicates=bool(getattr(args, "include_near_duplicates", False)),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES:
        return DictionarySourceDuplicatesRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            archive_root=resolved_dictionary_source_root(args, getattr(args, "resolve_dictionary_source_duplicates")),
            apply=bool(getattr(args, "yes", False)),
            raw_args=args,
        )
    if command_id == CommandId.RENAME_DICTIONARY_SOURCE_ARCHIVE:
        return DictionarySourceRenameRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            archive_root=resolved_dictionary_source_root(args, getattr(args, "rename_dictionary_source_archive")),
            apply=bool(getattr(args, "apply_dictionary_source_renames", False)),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.USAGE_SIGNALS:
        return UsageSignalsRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            archive_root=resolved_usage_signal_input_root(args),
            output_root=resolved_usage_signal_output_root(args),
            include_punctuation=bool(getattr(args, "include_punctuation", False)),
            include_numbers=bool(getattr(args, "include_numbers", False)),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    if command_id == CommandId.LIVE_LEXICON:
        return LiveLexiconRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            canonical_dictionary_path=resolved_canonical_dictionary_path(args),
            usage_candidates_path=resolved_usage_candidates_path(args),
            usage_summary_path=getattr(args, "usage_summary", None),
            live_lexicon_path=resolved_live_lexicon_path(args),
            supplemental_lexicon_path=resolved_supplemental_lexicon_path(args),
            unmatched_usage_output_path=resolved_unmatched_usage_output_path(args),
            raw_args=args,
        )
    if command_id == CommandId.AFTER_CLASS_REPORT:
        return AfterClassReportRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            printout_path=getattr(args, "generate_after_class_reading_report"),
            output_root=resolved_after_class_report_output_root(args),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            font_path=getattr(args, "after_class_report_font_path", None),
            raw_args=args,
        )
    if command_id == CommandId.SHOW_DISTRIBUTION:
        return ShowDistributionRequest(
            command_id=command_id,
            jsonl_path=resolve_distribution_path(
                getattr(args, "show_distribution"),
                resolved_dictionary_output_dir(args),
            ),
            model=llm.model,
            raw_args=args,
        )
    if command_id in {
        CommandId.LINK_CLEANED_EXAMPLES,
        CommandId.ADD_REFERENCE_MEANINGS,
        CommandId.GENERATE_MISSING_MEANINGS,
    }:
        return DictionaryStageRequest(
            command_id=command_id,
            output_dir=resolved_dictionary_output_dir(args),
            reference_dictionary_path=getattr(args, "reference_dictionary", None),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            raw_args=args,
        )
    raise ValueError(f"Unsupported CLI command: {command_id}")


def build_gui_request(workflow: str, values: Mapping[str, object]) -> CommandRequest:
    try:
        command_id = _GUI_WORKFLOW_IDS[workflow]
    except KeyError as error:
        raise ValueError(f"Unknown GUI workflow: {workflow}") from error
    llm = llm_runtime_config_from_values(values)
    if command_id in {CommandId.DICTIONARY_BUILD, CommandId.DICTIONARY_PREP}:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "dictionary_input_dir", "dictionary_output_dir")
        return DictionaryBuildRequest(
            command_id=command_id,
            target_checkpoint="15" if command_id == CommandId.DICTIONARY_BUILD else "09",
            target_grade=target_grade,
            input_dir=path_value(values, "dictionary_input_dir"),
            output_dir=path_value(values, "dictionary_output_dir"),
            reference_dictionary_path=path_value(values, "reference_dictionary_path"),
            include_punctuation=bool_value(values, "include_punctuation"),
            include_numbers=bool_value(values, "include_numbers"),
            model=llm.model,
            base_url=llm.base_url,
            force=bool_value(values, "dictionary_force"),
            provider=llm.provider,
        )
    if command_id == CommandId.CLASS_PRINTOUTS:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(
            values,
            target_grade,
            "class_source_dir",
            "source_output_root",
            "rewrite_output_root",
            "print_output_root",
            "live_lexicon_path",
            "dictionary_output_dir",
        )
        dictionary_output_dir = path_value(values, "dictionary_output_dir")
        explicit = optional_path_value(values, "rewrite_dictionary_path")
        return ClassPrintoutsRequest(
            command_id=command_id,
            target_grade=target_grade,
            source_dir=path_value(values, "class_source_dir"),
            parse_output_root=path_value(values, "source_output_root"),
            rewrite_output_root=path_value(values, "rewrite_output_root"),
            print_output_root=path_value(values, "print_output_root"),
            dictionary_output_dir=dictionary_output_dir,
            live_lexicon_path=path_value(values, "live_lexicon_path"),
            rewrite_dictionary_path=explicit,
            vocabulary_source=rewrite_vocabulary_source(
                explicit_path=explicit,
                target_grade=target_grade,
                dictionary_output_dir=dictionary_output_dir,
            ),
            parse_pdf=not bool_value(values, "skip_pdf_parse"),
            render_dpi=int_value(values, "pdf_render_dpi", 144),
            paddleocr_vl_settings=None,
            model=llm.model,
            base_url=llm.base_url,
            font_path=optional_path_value(values, "print_font_path"),
            body_font_size=optional_int_value(values, "print_body_font_size"),
            force_class_printouts=bool_value(values, "force_class_printouts"),
            provider=llm.provider,
        )
    if command_id in {CommandId.ARCHIVE_RENAME_PREVIEW, CommandId.ARCHIVE_RENAME_APPLY}:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "archive_root")
        return ArchiveRenameRequest(
            command_id=command_id,
            target_grade=target_grade,
            archive_root=path_value(values, "archive_root"),
            apply=command_id == CommandId.ARCHIVE_RENAME_APPLY,
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
        )
    if command_id in {CommandId.ARCHIVE_DUPLICATES_PREVIEW, CommandId.ARCHIVE_DUPLICATES_APPLY}:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "archive_root")
        return ArchiveDuplicatesRequest(
            command_id=command_id,
            target_grade=target_grade,
            archive_root=path_value(values, "archive_root"),
            apply=command_id == CommandId.ARCHIVE_DUPLICATES_APPLY,
            include_near_duplicates=bool_value(values, "include_near_duplicates"),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
        )
    if command_id == CommandId.USAGE_SIGNALS:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "archive_root", "usage_signal_output_root")
        return UsageSignalsRequest(
            command_id=command_id,
            target_grade=target_grade,
            archive_root=path_value(values, "archive_root"),
            output_root=path_value(values, "usage_signal_output_root"),
            include_punctuation=bool_value(values, "include_punctuation"),
            include_numbers=bool_value(values, "include_numbers"),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
        )
    if command_id == CommandId.LIVE_LEXICON:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(
            values,
            target_grade,
            "canonical_dictionary_path",
            "usage_candidates_path",
            "live_lexicon_path",
            "supplemental_lexicon_path",
            "unmatched_usage_output_path",
        )
        return LiveLexiconRequest(
            command_id=command_id,
            target_grade=target_grade,
            canonical_dictionary_path=path_value(values, "canonical_dictionary_path"),
            usage_candidates_path=path_value(values, "usage_candidates_path"),
            usage_summary_path=optional_path_value(values, "usage_summary_path"),
            live_lexicon_path=path_value(values, "live_lexicon_path"),
            supplemental_lexicon_path=path_value(values, "supplemental_lexicon_path"),
            unmatched_usage_output_path=path_value(values, "unmatched_usage_output_path"),
        )
    if command_id == CommandId.AFTER_CLASS_REPORT:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "after_class_report_output_root")
        return AfterClassReportRequest(
            command_id=command_id,
            target_grade=target_grade,
            printout_path=path_value(values, "after_class_printout_path"),
            output_root=path_value(values, "after_class_report_output_root"),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            font_path=optional_path_value(values, "after_class_report_font_path"),
        )
    raise ValueError(f"Unsupported GUI command: {command_id}")


def run_cli_request(request: CommandRequest) -> int | None:
    warnings.warn(
        "wordbyword.application.commands.run_cli_request is deprecated; "
        "use wordbyword.cli.app.run_cli_request instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from wordbyword.cli.app import run_cli_request as execute_cli_request

    return execute_cli_request(request)


def run_gui_request(
    request: CommandRequest,
    emit: Any,
) -> Any:
    warnings.warn(
        "wordbyword.application.commands.run_gui_request is deprecated; "
        "use wordbyword.gui.workflows.run_gui_request instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from wordbyword.gui.workflows import run_gui_request as execute_gui_request

    return execute_gui_request(request, emit)


def request_output_artifact_path(request: CommandRequest, artifact_id: ArtifactId) -> Path:
    output_dir = getattr(request, "output_dir", None)
    if not isinstance(output_dir, Path):
        raise ValueError(f"{request.command_id} does not expose an output_dir")
    return artifact_path(output_dir, artifact_id)
