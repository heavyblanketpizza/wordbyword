from __future__ import annotations

from pathlib import Path

from wordbyword.application.commands import (
    AfterClassReportRequest,
    ArchiveDuplicatesRequest,
    ArchiveRenameRequest,
    ClassMaterialPrintRequest,
    ClassPrintoutsRequest,
    CommandId,
    CommandRequest,
    DictionaryBuildRequest,
    DictionaryRecoveryRequest,
    DictionarySourceDuplicatesRequest,
    DictionarySourceRenameRequest,
    DictionaryStageRequest,
    LiveLexiconRequest,
    MigrateGradeWorkspacesRequest,
    ShowDistributionRequest,
    SourceMaterialParseRequest,
    SourceMaterialRewriteRequest,
    SourceMaterialsParseRequest,
    UsageSignalsRequest,
)
from wordbyword.application.workspace import (
    ArtifactId,
    PaddleOCRVLSettings,
    artifact_path,
    current_python_has_paddleocr,
    discover_paddleocr_vl_sidecar_env,
    llm_runtime_config_from_args,
    paddleocr_vl_settings_from_args as workspace_paddleocr_vl_settings_from_args,
    resolved_after_class_report_output_root,
    resolved_archive_root,
    resolved_canonical_dictionary_path,
    resolved_class_source_dir,
    resolved_dictionary_input_dir,
    resolved_dictionary_output_dir,
    resolved_dictionary_source_root,
    resolved_live_lexicon_path,
    resolved_parsed_source_root,
    resolved_print_class_materials_root,
    resolved_print_output_root,
    resolved_reference_dictionary_path,
    resolved_supplemental_lexicon_path,
    resolved_unmatched_usage_output_path,
    resolved_usage_candidates_path,
    resolved_usage_signal_input_root,
    resolved_usage_signal_output_root,
    resolved_vocabulary_rewrite_output_root,
    resolved_whole_rewrite_output_root,
    rewrite_vocabulary_source,
    source_parse_options_from_args as workspace_source_parse_options_from_args,
    target_grade_from_args,
    validate_paddleocr_vl_sidecar,
)
from wordbyword.cli.options import PIPELINE_CLEANUP_FLAGS, SourceParseOptions


DICTIONARY_DISTRIBUTION_ARTIFACT_IDS = (
    ArtifactId.DICTIONARY_CANDIDATES,
    ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
    ArtifactId.LINKED_DICTIONARY_CANDIDATES,
    ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ArtifactId.MEANING_DICTIONARY_CANDIDATES,
    ArtifactId.FINAL_DICTIONARY,
)

DEFAULT_DISTRIBUTION_ARTIFACT_IDS = tuple(reversed(DICTIONARY_DISTRIBUTION_ARTIFACT_IDS))


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
        )
    if command_id == CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES:
        return DictionarySourceDuplicatesRequest(
            command_id=command_id,
            target_grade=target_grade_from_args(args),
            archive_root=resolved_dictionary_source_root(args, getattr(args, "resolve_dictionary_source_duplicates")),
            apply=bool(getattr(args, "yes", False)),
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
        )
    if command_id == CommandId.SHOW_DISTRIBUTION:
        return ShowDistributionRequest(
            command_id=command_id,
            jsonl_path=resolve_distribution_path(
                getattr(args, "show_distribution"),
                resolved_dictionary_output_dir(args),
            ),
            model=llm.model,
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
        )
    raise ValueError(f"Unsupported CLI command: {command_id}")


def resolve_distribution_path(supplied: str, output_dir: Path) -> Path:
    if supplied:
        return Path(supplied)
    for artifact_id in DEFAULT_DISTRIBUTION_ARTIFACT_IDS:
        path = artifact_path(output_dir, artifact_id)
        if path.is_file():
            return path
    return artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)


def source_parse_options_from_args(args: object) -> SourceParseOptions:
    return workspace_source_parse_options_from_args(
        args,
        discover_sidecar_env=discover_paddleocr_vl_sidecar_env,
        validate_sidecar=validate_paddleocr_vl_sidecar,
        current_python_available=current_python_has_paddleocr,
    )


def paddleocr_vl_settings_from_args(args: object) -> PaddleOCRVLSettings | None:
    return workspace_paddleocr_vl_settings_from_args(
        args,
        discover_sidecar_env=discover_paddleocr_vl_sidecar_env,
        validate_sidecar=validate_paddleocr_vl_sidecar,
        current_python_available=current_python_has_paddleocr,
    )
