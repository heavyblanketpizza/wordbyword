from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from wordbyword.after_class_reports import (
    AfterClassReportError,
    generate_after_class_reading_report,
)
from wordbyword.dictionary_build.example_cleanup import CleanupBatchProgress
from wordbyword.dictionary_build.example_generation import GeneratedExampleAttemptSummary
from wordbyword.dictionary_build.llm import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    ollama_client,
    unload_model_if_supported,
)
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.dictionary_build.pipeline import (
    PipelineUsageError,
    load_pipeline_result_from_output,
    run_pipeline as _run_pipeline_compat,
)
from wordbyword.dictionary_build.recovery import run_saved_dictionary_cleanup
from wordbyword.dictionary_build.resumable import (
    CheckpointStatus,
    UnresolvedGeneratedExampleFailures,
    build_resumable_dictionary,
    generated_example_failure_count as resumable_generated_example_failure_count,
)
from wordbyword.dictionary_build.source_archive import (
    DictionarySourceArchiveError,
    DictionarySourceDuplicateResolutionResult,
    apply_dictionary_source_duplicate_resolution_result,
    managed_dictionary_source_grade_from_stem,
    rename_dictionary_source_archive,
    resolve_dictionary_source_duplicates,
    validate_managed_dictionary_source_grades,
)
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    SourceMaterialParseError,
    parse_source_material,
    parse_source_materials,
)
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    SourceRewriteError,
    rewrite_vocabulary_guided_source_material as rewrite_vocabulary_guided_parsed_source_material,
    rewrite_vocabulary_guided_source_materials as rewrite_vocabulary_guided_parsed_source_materials,
    rewrite_whole_source_material as rewrite_whole_parsed_source_material,
    rewrite_whole_source_materials as rewrite_whole_parsed_source_materials,
)
from wordbyword.classroom_printout_studio.class_printing.printable import (
    PrintableMaterialError,
    print_class_material,
    print_class_materials,
)
from wordbyword.classroom_printout_studio import class_printout_workflow
from wordbyword.class_archive.archive import (
    ArchiveDuplicateResolutionResult,
    ClassSessionArchiveError,
    apply_archive_duplicate_resolution_result,
    resolve_archive_duplicates,
    rename_class_session_archive,
    validate_managed_class_archive_grades,
)
from wordbyword.class_archive.live_lexicon import (
    LiveLexiconUpdateError,
    update_live_lexicon,
)
from wordbyword.class_archive.usage_signals import (
    UsageSignalBuildError,
    build_usage_signals,
)
from wordbyword.cli.terminal_ui import (
    emit_artifact_drop,
    emit_after_class_report_progress,
    emit_after_class_report_summary,
    emit_boot_screen,
    emit_cleanup_progress,
    emit_completion_screen,
    emit_live_lexicon_progress as emit_live_lexicon_progress_ui,
    emit_live_lexicon_summary,
    emit_printable_progress,
    emit_printable_summary,
    emit_source_material_progress,
    emit_source_rewrite_progress,
    emit_source_rewrite_summary,
    emit_stage_update,
    emit_usage_signal_progress as emit_usage_signal_progress_ui,
    emit_usage_signal_summary,
    render_artifact_drop,
)
from wordbyword.cli.options import (
    PIPELINE_CLEANUP_FLAGS,
    SourceParseOptions,
    build_parser,
    validate_args,
)
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_event_name, artifact_filename, artifact_path
from wordbyword.dictionary_build.writers import refresh_saved_output_metadata, write_linked_dictionary_candidates_from_saved
from wordbyword.dictionary_build.saved_outputs import (
    write_generated_meaning_dictionary_candidates_from_saved,
    write_meaning_dictionary_candidates_from_saved,
)
from wordbyword.execution_guard import CommandAlreadyRunningError, acquire_workspace_command_guard
from wordbyword.grade_profiles import (
    GradeWorkspace,
    default_live_lexicon_path,
    target_profile_for_grade,
)
from wordbyword.grade_workspace_migration import migrate_grade_workspaces
from wordbyword.application import workspace as application_workspace
from wordbyword.application.commands import (
    AfterClassReportRequest,
    ArchiveDuplicatesRequest,
    ArchiveRenameRequest,
    ClassMaterialPrintRequest,
    ClassPrintoutsRequest,
    CommandId,
    CommandRequest,
    DictionarySourceDuplicatesRequest,
    DictionarySourceRenameRequest,
    DictionaryBuildRequest,
    DictionaryRecoveryRequest,
    DictionaryStageRequest,
    LiveLexiconRequest,
    MigrateGradeWorkspacesRequest,
    ShowDistributionRequest,
    SourceMaterialParseRequest,
    SourceMaterialRewriteRequest,
    SourceMaterialsParseRequest,
    UsageSignalsRequest,
    build_cli_request,
    command_id_from_cli_args,
    command_spec,
)
from wordbyword.application.workspace import (
    LazyOllamaCompletionClient,
    RewriteVocabularySource,
    current_python_has_paddleocr as application_current_python_has_paddleocr,
    discover_paddleocr_vl_sidecar_env as application_discover_paddleocr_vl_sidecar_env,
    paddleocr_vl_settings_from_args as application_paddleocr_vl_settings_from_args,
    paddleocr_vl_python_can_locate_paddleocr as application_paddleocr_vl_python_can_locate_paddleocr,
    paddleocr_vl_sidecar_python_path as application_paddleocr_vl_sidecar_python_path,
    paddleocr_vl_sidecar_search_roots as application_paddleocr_vl_sidecar_search_roots,
    source_parse_options_from_args as application_source_parse_options_from_args,
    validate_paddleocr_vl_sidecar as application_validate_paddleocr_vl_sidecar,
    wordbyword_project_root as application_wordbyword_project_root,
)

PADDLEOCR_VL_SIDECAR_ENV_VAR = "WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV"
PADDLEOCR_VL_SIDECAR_PYTHON_ENV_VAR = "WORDBYWORD_PADDLEOCR_VL_SIDECAR_PYTHON"
DEFAULT_PADDLEOCR_VL_SIDECAR_ENVS = (".venv-paddleocr-vl", ".venv-paddleocr")

# Compatibility surface for callers that patch/import wordbyword.cli.app.run_pipeline.
# The CLI command dispatcher must not call this.
run_pipeline = _run_pipeline_compat


def target_grade_from_args(args: argparse.Namespace) -> str:
    return application_workspace.target_grade_from_args(args)


def target_grade_was_explicit(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "target_grade_explicit", False))


def workspace_from_args(args: argparse.Namespace) -> GradeWorkspace:
    return application_workspace.workspace_from_args(args)


def option_was_explicit(args: argparse.Namespace, dest: str) -> bool:
    return application_workspace.option_was_explicit(args, dest)


def resolved_dictionary_input_dir(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_dictionary_input_dir(args)


def resolved_dictionary_output_dir(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_dictionary_output_dir(args)


def resolved_reference_dictionary_path(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_reference_dictionary_path(args)


def resolved_canonical_dictionary_path(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_canonical_dictionary_path(args)


def resolved_class_source_dir(args: argparse.Namespace, source_dir: Path) -> Path:
    return application_workspace.resolved_class_source_dir(args, source_dir)


def resolved_parsed_source_root(args: argparse.Namespace, parsed_root: Path) -> Path:
    return application_workspace.resolved_parsed_source_root(args, parsed_root)


def resolved_archive_root(args: argparse.Namespace, archive_root: Path) -> Path:
    return application_workspace.resolved_archive_root(args, archive_root)


def resolved_usage_signal_input_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_usage_signal_input_root(args)


def resolved_dictionary_source_root(args: argparse.Namespace, source_root: Path) -> Path:
    return application_workspace.resolved_dictionary_source_root(args, source_root)


def dictionary_source_grade_from_stem(stem: str) -> str:
    return managed_dictionary_source_grade_from_stem(stem)


def resolved_whole_rewrite_output_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_whole_rewrite_output_root(args)


def resolved_vocabulary_rewrite_output_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_vocabulary_rewrite_output_root(args)


def resolved_print_output_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_print_output_root(args)


def resolve_print_class_materials_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_print_class_materials_root(args)


def resolved_usage_signal_output_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_usage_signal_output_root(args)


def resolved_live_lexicon_path(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_live_lexicon_path(args)


def resolved_supplemental_lexicon_path(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_supplemental_lexicon_path(args)


def resolved_usage_candidates_path(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_usage_candidates_path(args)


def resolved_unmatched_usage_output_path(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_unmatched_usage_output_path(args)


def resolved_after_class_report_output_root(args: argparse.Namespace) -> Path:
    return application_workspace.resolved_after_class_report_output_root(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    try:
        command_label = exclusive_command_label(args)
        if command_label is None:
            immediate_exit_code = run_immediate_command(args)
        else:
            try:
                with acquire_workspace_command_guard(command_label, workspace_root=Path.cwd()):
                    immediate_exit_code = run_immediate_command(args)
            except CommandAlreadyRunningError as error:
                sys.stderr.write(f"wordbyword: {error}. Try again after it finishes.\n")
                return 1
    except PipelineUsageError as exc:
        parser.exit(2, f"wordbyword: error: {exc}\n")
    if immediate_exit_code is not None:
        return immediate_exit_code

    parser.exit(
        2,
        "wordbyword: error: choose an explicit command: --prep-dictionary for stages 00-09 "
        "or --build-dictionary for the full strict workflow.\n",
    )


def exclusive_command_label(args: argparse.Namespace) -> str | None:
    command_id = command_id_from_cli_args(args)
    if command_id is None:
        return None
    return command_spec(command_id).lock_label


def run_immediate_command(args: argparse.Namespace) -> int | None:
    request = build_cli_request(args)
    if request is None:
        return None
    return run_cli_request(request)


def run_cli_request(request: CommandRequest) -> int | None:
    if isinstance(request, ShowDistributionRequest):
        return show_distribution(jsonl_path=request.jsonl_path, model=request.model)

    if isinstance(request, DictionaryStageRequest):
        if request.command_id == CommandId.LINK_CLEANED_EXAMPLES:
            return link_cleaned_examples(output_dir=request.output_dir)
        if request.command_id == CommandId.ADD_REFERENCE_MEANINGS:
            return add_reference_meanings(
                output_dir=request.output_dir,
                reference_dictionary_path=request.reference_dictionary_path,
            )
        if request.command_id == CommandId.GENERATE_MISSING_MEANINGS:
            return generate_missing_meanings(
                output_dir=request.output_dir,
                model=request.model,
                base_url=request.base_url,
            )

    if isinstance(request, MigrateGradeWorkspacesRequest):
        return migrate_grade_workspaces_cli(apply=request.apply)

    if isinstance(request, DictionaryBuildRequest):
        if request.command_id == CommandId.DICTIONARY_PREP:
            return prep_dictionary_cli(request)
        return build_dictionary_cli(request)

    if isinstance(request, DictionaryRecoveryRequest):
        return dictionary_recovery_cli(request)

    if isinstance(request, SourceMaterialParseRequest):
        return parse_source_material_cli(
            source_path=request.source_path,
            output_root=request.output_root,
            source_id=request.source_id,
            target_grade=request.target_grade,
            parse_pdf=request.parse_pdf,
            render_dpi=request.render_dpi,
            paddleocr_vl_settings=request.paddleocr_vl_settings,
        )

    if isinstance(request, SourceMaterialsParseRequest):
        return parse_source_materials_cli(
            source_dir=request.source_dir,
            output_root=request.output_root,
            target_grade=request.target_grade,
            parse_pdf=request.parse_pdf,
            render_dpi=request.render_dpi,
            paddleocr_vl_settings=request.paddleocr_vl_settings,
        )

    if isinstance(request, SourceMaterialRewriteRequest):
        vocabulary_source = _request_vocabulary_source(request.vocabulary_source, request.raw_args)
        if request.command_id == CommandId.REWRITE_WHOLE_SOURCE_MATERIAL:
            return rewrite_whole_source_material_cli(
                parsed_bundle_dir=request.parsed_path,
                output_root=request.output_root,
                target_grade=request.target_grade,
                model=request.model,
                base_url=request.base_url,
            )
        if request.command_id == CommandId.REWRITE_WHOLE_SOURCE_MATERIALS:
            return rewrite_whole_source_materials_cli(
                parsed_root=request.parsed_path,
                output_root=request.output_root,
                target_grade=request.target_grade,
                model=request.model,
                base_url=request.base_url,
            )
        if vocabulary_source is None:
            raise ValueError(f"{request.command_id} requires a vocabulary source")
        if request.command_id == CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL:
            return rewrite_vocabulary_guided_source_material_cli(
                parsed_bundle_dir=request.parsed_path,
                output_root=request.output_root,
                vocabulary_source=vocabulary_source,
                target_grade=request.target_grade,
                model=request.model,
                base_url=request.base_url,
            )
        if request.command_id == CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS:
            return rewrite_vocabulary_guided_source_materials_cli(
                parsed_root=request.parsed_path,
                output_root=request.output_root,
                vocabulary_source=vocabulary_source,
                target_grade=request.target_grade,
                model=request.model,
                base_url=request.base_url,
            )

    if isinstance(request, ClassMaterialPrintRequest):
        if request.command_id == CommandId.PRINT_CLASS_MATERIAL:
            return print_class_material_cli(
                rewritten_bundle_dir=request.rewritten_path,
                output_root=request.output_root,
                target_grade=request.target_grade,
                model=request.model,
                base_url=request.base_url,
                font_path=request.font_path,
                body_font_size=request.body_font_size,
            )
        if request.command_id == CommandId.PRINT_CLASS_MATERIALS:
            return print_class_materials_cli(
                rewritten_root=request.rewritten_path,
                output_root=request.output_root,
                target_grade=request.target_grade,
                model=request.model,
                base_url=request.base_url,
                font_path=request.font_path,
                body_font_size=request.body_font_size,
            )

    if isinstance(request, ClassPrintoutsRequest):
        vocabulary_source = _request_vocabulary_source(request.vocabulary_source, request.raw_args)
        if vocabulary_source is None:
            raise ValueError(f"{request.command_id} requires a vocabulary source")
        return build_class_printouts_cli(
            source_dir=request.source_dir,
            parse_output_root=request.parse_output_root,
            parse_pdf=request.parse_pdf,
            render_dpi=request.render_dpi,
            paddleocr_vl_settings=request.paddleocr_vl_settings,
            rewrite_output_root=request.rewrite_output_root,
            vocabulary_source=vocabulary_source,
            print_output_root=request.print_output_root,
            target_grade=request.target_grade,
            model=request.model,
            base_url=request.base_url,
            font_path=request.font_path,
            body_font_size=request.body_font_size,
            force_class_printouts=request.force_class_printouts,
        )

    if isinstance(request, AfterClassReportRequest):
        return generate_after_class_reading_report_cli(
            printout_path=request.printout_path,
            output_root=request.output_root,
            model=request.model,
            base_url=request.base_url,
            font_path=request.font_path,
            target_grade=request.target_grade,
        )

    if isinstance(request, ArchiveDuplicatesRequest):
        return resolve_archive_duplicates_cli(
            archive_root=request.archive_root,
            yes=request.apply,
            include_near_duplicates=request.include_near_duplicates,
            model=request.model,
            base_url=request.base_url,
            target_grade=request.target_grade,
        )

    if isinstance(request, DictionarySourceDuplicatesRequest):
        return resolve_dictionary_source_duplicates_cli(
            archive_root=request.archive_root,
            yes=request.apply,
            target_grade=request.target_grade,
        )

    if isinstance(request, ArchiveRenameRequest):
        return rename_class_session_archive_cli(
            archive_root=request.archive_root,
            model=request.model,
            base_url=request.base_url,
            apply=request.apply,
            target_grade=request.target_grade,
        )

    if isinstance(request, DictionarySourceRenameRequest):
        return rename_dictionary_source_archive_cli(
            archive_root=request.archive_root,
            model=request.model,
            base_url=request.base_url,
            apply=request.apply,
            target_grade=request.target_grade,
        )

    if isinstance(request, UsageSignalsRequest):
        return build_usage_signals_cli(
            archive_root=request.archive_root,
            output_root=request.output_root,
            target_grade=request.target_grade,
            include_punctuation=request.include_punctuation,
            include_numbers=request.include_numbers,
            model=request.model,
            base_url=request.base_url,
        )

    if isinstance(request, LiveLexiconRequest):
        return update_live_lexicon_cli(
            canonical_dictionary_path=request.canonical_dictionary_path,
            usage_candidates_path=request.usage_candidates_path,
            usage_summary_path=request.usage_summary_path,
            live_lexicon_path=request.live_lexicon_path,
            supplemental_lexicon_path=request.supplemental_lexicon_path,
            unmatched_usage_output_path=request.unmatched_usage_output_path,
            target_grade=request.target_grade,
        )

    raise ValueError(f"Unsupported CLI command: {request.command_id}")


def _request_vocabulary_source(
    fallback: RewriteVocabularySource | None,
    raw_args: object,
) -> RewriteVocabularySource | None:
    if raw_args is not None:
        return rewrite_vocabulary_source_from_args(raw_args)
    return fallback


def _coerce_dictionary_build_request(args_or_request: argparse.Namespace | DictionaryBuildRequest, command_id: CommandId) -> DictionaryBuildRequest:
    if isinstance(args_or_request, DictionaryBuildRequest):
        return args_or_request
    request = build_cli_request(args_or_request)
    if not isinstance(request, DictionaryBuildRequest) or request.command_id != command_id:
        raise PipelineUsageError(f"expected {command_id.value} request")
    return request


def _coerce_dictionary_recovery_request(args_or_request: argparse.Namespace | DictionaryRecoveryRequest) -> DictionaryRecoveryRequest:
    if isinstance(args_or_request, DictionaryRecoveryRequest):
        return args_or_request
    request = build_cli_request(args_or_request)
    if not isinstance(request, DictionaryRecoveryRequest):
        raise PipelineUsageError("expected dictionary recovery request")
    return request


def build_dictionary_cli(args: argparse.Namespace | DictionaryBuildRequest) -> int:
    request = _coerce_dictionary_build_request(args, CommandId.DICTIONARY_BUILD)
    input_dir = request.input_dir
    output_dir = request.output_dir
    emit_boot_screen(
        input_dir=input_dir,
        output_dir=output_dir,
        clean_example_sentences=True,
        sentence_cleanup_model=request.model,
    )

    try:
        result = build_resumable_dictionary(
            target_grade=request.target_grade,
            input_dir=input_dir,
            output_dir=output_dir,
            reference_dictionary_path=request.reference_dictionary_path,
            include_punctuation=request.include_punctuation,
            include_numbers=request.include_numbers,
            model=request.model,
            base_url=request.base_url,
            force=request.force,
            progress=emit_stage_update,
            cleanup_progress=cleanup_progress_callback(
                model=request.model,
                label="example",
                item_label="rows",
            ),
            dictionary_cleanup_progress=cleanup_progress_callback(
                model=request.model,
                label="dictionary",
                item_label="terms",
            ),
            generated_example_progress=cleanup_progress_callback(
                model=request.model,
                label="candidate example",
                item_label="terms",
            ),
            generated_meaning_progress=cleanup_progress_callback(
                model=request.model,
                label="meaning",
                item_label="terms",
            ),
            artifact_callback=pipeline_artifact_callback(model=request.model),
            checkpoint_status=emit_dictionary_build_checkpoint_status,
            target_checkpoint="15",
        )
    except UnresolvedGeneratedExampleFailures as error:
        sys.stderr.write(
            "wordbyword: full dictionary build stopped because stage 13 left "
            f"{error.count:,} unresolved generated-example failure(s).\n"
            f"  failures: {error.path}\n"
            "  repair: run --retry-generated-examples until failures are clear, then run "
            "--add-reference-meanings and --generate-missing-meanings.\n"
        )
        return 1
    except (
        ValueError,
        PipelineUsageError,
        DictionarySourceArchiveError,
        LLMConfigurationError,
        LLMRequestError,
        LLMResponseError,
    ) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_completion_screen(
        result.pipeline_result,
        input_dir=input_dir,
        include_punctuation=request.include_punctuation,
        include_numbers=request.include_numbers,
        clean_example_sentences=True,
        sentence_cleanup_model=request.model,
    )
    return 0


def prep_dictionary_cli(args: argparse.Namespace | DictionaryBuildRequest) -> int:
    request = _coerce_dictionary_build_request(args, CommandId.DICTIONARY_PREP)
    input_dir = request.input_dir
    output_dir = request.output_dir
    emit_boot_screen(
        input_dir=input_dir,
        output_dir=output_dir,
        clean_example_sentences=False,
        sentence_cleanup_model=request.model,
    )

    try:
        result = build_resumable_dictionary(
            target_grade=request.target_grade,
            input_dir=input_dir,
            output_dir=output_dir,
            reference_dictionary_path=request.reference_dictionary_path,
            include_punctuation=request.include_punctuation,
            include_numbers=request.include_numbers,
            model=request.model,
            base_url=request.base_url,
            force=request.force,
            progress=emit_stage_update,
            artifact_callback=pipeline_artifact_callback(model=request.model),
            checkpoint_status=emit_dictionary_build_checkpoint_status,
            target_checkpoint="09",
        )
    except (
        ValueError,
        PipelineUsageError,
        DictionarySourceArchiveError,
        LLMConfigurationError,
        LLMRequestError,
        LLMResponseError,
    ) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_completion_screen(
        result.pipeline_result,
        input_dir=input_dir,
        include_punctuation=request.include_punctuation,
        include_numbers=request.include_numbers,
        clean_example_sentences=False,
        sentence_cleanup_model=request.model,
    )
    return 0


def dictionary_recovery_cli(args: argparse.Namespace | DictionaryRecoveryRequest) -> int:
    request = _coerce_dictionary_recovery_request(args)
    input_dir = request.input_dir
    output_dir = request.output_dir
    emit_boot_screen(
        input_dir=input_dir,
        output_dir=output_dir,
        clean_example_sentences=True,
        sentence_cleanup_model=request.model,
    )

    try:
        run_saved_dictionary_cleanup(
            output_dir=output_dir,
            clean_example_sentences=request.clean_example_sentences,
            clean_dictionary_candidates=request.clean_dictionary_candidates,
            generate_missing_examples=request.generate_missing_examples,
            retry_generated_examples=request.retry_generated_examples,
            sentence_cleanup_model=request.model,
            sentence_cleanup_base_url=request.base_url,
            progress=emit_stage_update,
            cleanup_progress=cleanup_progress_callback(
                model=request.model,
                label="example",
                item_label="rows",
            ),
            dictionary_cleanup_progress=cleanup_progress_callback(
                model=request.model,
                label="dictionary",
                item_label="terms",
            ),
            generated_example_progress=cleanup_progress_callback(
                model=request.model,
                label="candidate example",
                item_label="terms",
            ),
            generated_example_retry_decider=generated_example_retry_decider(request),
            artifact_callback=pipeline_artifact_callback(model=request.model),
        )
    except ValueError as error:
        raise PipelineUsageError(str(error)) from error
    except (LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_completion_screen(
        load_pipeline_result_from_output(output_dir),
        input_dir=input_dir,
        include_punctuation=request.include_punctuation,
        include_numbers=request.include_numbers,
        clean_example_sentences=True,
        sentence_cleanup_model=request.model,
    )
    return 0


def emit_dictionary_build_checkpoint_status(status: CheckpointStatus) -> None:
    sys.stdout.write(f"wordbyword: {status.action.value} {status.key} {status.label}: {status.reason}\n")
    sys.stdout.flush()


def generated_example_failure_count(output_dir: Path) -> int:
    return resumable_generated_example_failure_count(output_dir)


def rewrite_dictionary_path_from_args(args: argparse.Namespace) -> Path:
    return rewrite_vocabulary_source_from_args(args).path


def rewrite_vocabulary_source_from_args(args: argparse.Namespace) -> RewriteVocabularySource:
    return application_workspace.rewrite_vocabulary_source(
        explicit_path=args.rewrite_dictionary,
        target_grade=target_grade_from_args(args),
        dictionary_output_dir=resolved_dictionary_output_dir(args),
        live_lexicon_path_factory=default_live_lexicon_path,
        file_sha256=file_sha256_if_present,
    )


def file_sha256_if_present(path: Path) -> str:
    return application_workspace.file_sha256_if_present(path)


def source_parse_options_from_args(args: argparse.Namespace) -> SourceParseOptions:
    return application_source_parse_options_from_args(
        args,
        discover_sidecar_env=discover_paddleocr_vl_sidecar_env,
        validate_sidecar=validate_paddleocr_vl_sidecar,
        current_python_available=current_python_has_paddleocr,
    )


def pipeline_cleanup_requested(args: argparse.Namespace) -> bool:
    return any(bool(getattr(args, flag_name)) for flag_name in PIPELINE_CLEANUP_FLAGS)


def cleanup_progress_callback(
    *,
    model: str,
    label: str,
    item_label: str,
) -> Callable[[CleanupBatchProgress], None]:
    def report(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=model,
            label=label,
            item_label=item_label,
        )

    return report


def generated_example_retry_decider(
    args: argparse.Namespace | DictionaryRecoveryRequest,
) -> Callable[[GeneratedExampleAttemptSummary], bool]:
    output_dir = args.output_dir if isinstance(args, DictionaryRecoveryRequest) else resolved_dictionary_output_dir(args)
    retry_generated_examples = (
        args.retry_generated_examples if isinstance(args, DictionaryRecoveryRequest) else bool(args.retry_generated_examples)
    )
    model = args.model if isinstance(args, DictionaryRecoveryRequest) else args.ollama_model

    def decide(summary: GeneratedExampleAttemptSummary) -> bool:
        if retry_generated_examples:
            return False
        return prompt_generated_example_retry(
            summary=summary,
            output_dir=output_dir,
            model=model,
        )

    return decide


def pipeline_artifact_callback(
    *,
    model: str,
) -> Callable[[str, Sequence[Mapping[str, object]]], None]:
    def report(stage: str, entries: Sequence[Mapping[str, object]]) -> None:
        if stage == artifact_event_name(ArtifactId.EXAMPLE_SENTENCES):
            emit_artifact_drop(
                stage,
                file_label=artifact_filename(ArtifactId.EXAMPLE_SENTENCES),
                summary=(
                    ("relics", f"{len(entries):,} sentences"),
                    ("status", "signal locked"),
                ),
            )
            return
        if stage == artifact_event_name(ArtifactId.CLEANED_DICTIONARY_CANDIDATES):
            emit_artifact_drop(
                stage,
                entries=entries,
                file_label=artifact_filename(ArtifactId.CLEANED_DICTIONARY_CANDIDATES),
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("model", model),
                    ("status", "cache locked"),
                ),
            )
            return
        if stage == artifact_event_name(ArtifactId.LINKED_DICTIONARY_CANDIDATES):
            replaced_count = count_truthy(entries, "example_replaced_with_cleaned_sentence")
            emit_artifact_drop(
                stage,
                file_label=artifact_filename(ArtifactId.LINKED_DICTIONARY_CANDIDATES),
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("replaced", f"{replaced_count:,} examples"),
                    ("rewrite", f"{len(entries) - replaced_count:,} pending"),
                ),
            )
            return
        if stage == artifact_event_name(ArtifactId.GENERATED_EXAMPLE_DICTIONARY):
            generated_count = count_truthy(entries, "example_generated_with_ollama")
            emit_artifact_drop(
                stage,
                file_label=artifact_filename(ArtifactId.GENERATED_EXAMPLE_DICTIONARY),
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("generated", f"{generated_count:,} examples"),
                    ("preserved", f"{len(entries) - generated_count:,} existing"),
                    ("model", model),
                ),
            )
            return
        if stage == artifact_event_name(ArtifactId.MEANING_DICTIONARY_CANDIDATES):
            matched_count = count_nonempty(entries, "meaning")
            emit_artifact_drop(
                stage,
                file_label=artifact_filename(ArtifactId.MEANING_DICTIONARY_CANDIDATES),
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("matched", f"{matched_count:,} meanings"),
                    ("missing", f"{len(entries) - matched_count:,} meanings"),
                ),
            )
            return
        if stage == artifact_event_name(ArtifactId.FINAL_DICTIONARY):
            generated_count = count_truthy(entries, "meaning_generated_with_ollama")
            emit_artifact_drop(
                stage,
                file_label=artifact_filename(ArtifactId.FINAL_DICTIONARY),
                summary=(
                    ("relics", f"{len(entries):,} headwords"),
                    ("generated", f"{generated_count:,} meanings"),
                    ("preserved", f"{len(entries) - generated_count:,} meanings"),
                    ("model", model),
                ),
            )

    return report


def count_truthy(entries: Sequence[Mapping[str, object]], key: str) -> int:
    return sum(1 for entry in entries if bool(entry.get(key)))


def count_nonempty(entries: Sequence[Mapping[str, object]], key: str) -> int:
    return sum(1 for entry in entries if bool(str(entry.get(key, "")).strip()))


DICTIONARY_DISTRIBUTION_ARTIFACT_IDS = (
    ArtifactId.DICTIONARY_CANDIDATES,
    ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
    ArtifactId.LINKED_DICTIONARY_CANDIDATES,
    ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ArtifactId.MEANING_DICTIONARY_CANDIDATES,
    ArtifactId.FINAL_DICTIONARY,
)

DEFAULT_DISTRIBUTION_ARTIFACT_IDS = tuple(reversed(DICTIONARY_DISTRIBUTION_ARTIFACT_IDS))


def resolve_distribution_path(supplied: str, output_dir: Path) -> Path:
    if supplied:
        return Path(supplied)
    for artifact_id in DEFAULT_DISTRIBUTION_ARTIFACT_IDS:
        path = artifact_path(output_dir, artifact_id)
        if path.is_file():
            return path
    return artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)


def distribution_stage_for_path(jsonl_path: Path) -> str:
    for artifact_id in DICTIONARY_DISTRIBUTION_ARTIFACT_IDS:
        if jsonl_path.name == artifact_filename(artifact_id):
            return artifact_event_name(artifact_id)
    return artifact_event_name(ArtifactId.FINAL_DICTIONARY)


def load_dictionary_candidates(jsonl_path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {jsonl_path}: {error.msg}"
                ) from error
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Line {line_number} of {jsonl_path} is not a JSON object."
                )
            entries.append(entry)
    return entries


def migrate_grade_workspaces_cli(*, apply: bool) -> int:
    result = migrate_grade_workspaces(apply=apply)
    action = "applied" if apply else "preview"
    sys.stdout.write(
        f"grade workspace migration {action}\n"
        f"  manifest: {result.manifest_path}\n"
        f"  planned: {result.planned_count}\n"
        f"  applied: {result.applied_count}\n"
        f"  skipped: {result.skipped_count}\n"
    )
    for move in result.moves:
        status = "applied" if move.applied else "skipped" if move.skipped else "planned"
        sys.stdout.write(
            f"  - {status} {move.kind} {move.source_path} -> {move.target_path} "
            f"({move.target_grade}; {move.reason})\n"
        )
    sys.stdout.flush()
    return 0


def emit_rendered_artifact_drop(
    stage: str,
    *,
    file_label: str,
    summary: Sequence[tuple[str, str]],
    entries: Sequence[Mapping[str, object]] | None = None,
) -> None:
    rendered = render_artifact_drop(
        stage,
        entries=entries,
        file_label=file_label,
        summary=summary,
        color=sys.stdout.isatty() and "NO_COLOR" not in os.environ,
        width=shutil.get_terminal_size(fallback=(100, 24)).columns,
    )
    sys.stdout.write(rendered)
    sys.stdout.write("\n")
    sys.stdout.flush()


def show_distribution(*, jsonl_path: Path, model: str) -> int:
    if not jsonl_path.is_file():
        sys.stderr.write(
            f"wordbyword: cannot read distribution source: {jsonl_path}\n"
            f"  hint: run with --generate-missing-meanings to write {artifact_filename(ArtifactId.FINAL_DICTIONARY)}, "
            f"or pass an explicit path: --show-distribution path/to/{artifact_filename(ArtifactId.CLEANED_DICTIONARY_CANDIDATES)}\n"
        )
        return 1

    try:
        entries = load_dictionary_candidates(jsonl_path)
    except ValueError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_rendered_artifact_drop(
        distribution_stage_for_path(jsonl_path),
        entries=entries,
        file_label=str(jsonl_path),
        summary=(
            ("relics", f"{len(entries):,} headwords"),
            ("model", model),
            ("status", "replay // no llm calls"),
        ),
    )
    return 0


def parse_source_material_cli(
    *,
    source_path: Path,
    output_root: Path,
    source_id: str | None,
    target_grade: str,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    try:
        bundle = parse_source_material(
            source_path,
            output_root=output_root,
            source_id=source_id,
            target_grade=target_grade,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            progress=emit_source_material_progress,
        )
    except KeyboardInterrupt:
        sys.stderr.write("wordbyword: source-material parsing interrupted; partial workspace cleaned.\n")
        return 130
    except SourceMaterialParseError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    files = bundle.manifest.get("files", {})
    sys.stdout.write(
        "source material parsed\n"
        f"  source_id: {bundle.source_id}\n"
        f"  output_dir: {bundle.output_dir}\n"
        f"  manifest: {bundle.manifest_path}\n"
        f"  assets: {bundle.assets_dir}\n"
        f"  parser_status: {bundle.manifest['parser']['status']}\n"
    )
    for label in ("content_markdown", "quality_report_json", "document_model_json", "chunks_json"):
        if label in files:
            sys.stdout.write(f"  {label}: {bundle.output_dir / str(files[label])}\n")
    sys.stdout.flush()
    return 0


def parse_source_materials_cli(
    *,
    source_dir: Path,
    output_root: Path,
    target_grade: str,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    try:
        result = parse_source_materials(
            source_dir,
            output_root=output_root,
            target_grade=target_grade,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            progress=emit_source_material_progress,
        )
    except KeyboardInterrupt:
        sys.stderr.write("wordbyword: source-material parsing interrupted; partial workspace cleaned.\n")
        return 130
    except SourceMaterialParseError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "source materials parsed\n"
        f"  source_dir: {result.source_dir}\n"
        f"  output_root: {result.output_root}\n"
        f"  sources: {len(result.source_paths)}\n"
    )
    for bundle in result.bundles:
        files = bundle.manifest.get("files", {})
        content = f", content: {files['content_markdown']}" if "content_markdown" in files else ""
        sys.stdout.write(
            f"  - {bundle.source_id}: {bundle.output_dir} "
            f"(assets: {bundle.assets_dir.name}/, parser: {bundle.manifest['parser']['status']}{content})\n"
        )
    sys.stdout.flush()
    return 0


def rewrite_output_summary_row(output: object) -> dict[str, object]:
    failed_pages = int(getattr(output, "failed_page_count"))
    row: dict[str, object] = {
        "source_id": str(getattr(output, "source_id")),
        "output_dir": str(getattr(output, "output_dir")),
        "status": "written" if failed_pages == 0 else "failed",
        "rewritten_pages": int(getattr(output, "rewritten_page_count")),
        "failed_pages": failed_pages,
    }
    selected_vocabulary = getattr(output, "selected_vocabulary", None)
    if selected_vocabulary is not None:
        row["selected_vocabulary"] = len(selected_vocabulary)
    return row


def printable_output_summary_row(output: object) -> dict[str, object]:
    accepted = bool(getattr(output, "accepted"))
    output_dir = getattr(output, "output_dir")
    attempt_rows = getattr(output, "attempt_rows", []) or []
    attempt = attempt_rows[-1] if attempt_rows and isinstance(attempt_rows[-1], Mapping) else {}
    selected_asset_ids = attempt.get("selected_asset_ids", [])
    selected_image_count = len(selected_asset_ids) if isinstance(selected_asset_ids, Sequence) and not isinstance(selected_asset_ids, str) else 0
    body_font_size_pt = attempt.get("body_font_size_pt")
    row: dict[str, object] = {
        "source_id": str(getattr(output, "source_id")),
        "output_dir": str(output_dir),
        "status": "written" if accepted else "failed",
        "selected_image_count": selected_image_count,
        "failure_reason": str(getattr(output, "failure_reason", "") or ""),
    }
    if body_font_size_pt is not None:
        row["body_font_size_pt"] = body_font_size_pt
    if accepted:
        row["docx"] = str(Path(output_dir) / "material.docx")
    return row


def after_class_report_summary_row(output: object) -> dict[str, object]:
    accepted = bool(getattr(output, "accepted"))
    row: dict[str, object] = {
        "source_id": str(getattr(output, "source_id")),
        "output_dir": str(getattr(output, "output_dir")),
        "status": "written" if accepted else "failed",
        "selected_vocabulary_count": int(getattr(output, "selected_vocabulary_count", 0)),
        "failure_reason": str(getattr(output, "failure_reason", "") or ""),
    }
    docx_path = getattr(output, "docx_path", None)
    if accepted and docx_path is not None:
        row["docx"] = str(docx_path)
    return row


def rewrite_whole_source_material_cli(
    *,
    parsed_bundle_dir: Path,
    output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_whole_parsed_source_material(
                parsed_bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if emit_source_rewrite_summary(
        title="whole document",
        summary=(
            ("status", "written" if outputs.failed_page_count == 0 else "failed"),
            ("source_id", outputs.source_id),
            ("output", str(outputs.output_dir)),
            ("rewritten", f"{outputs.rewritten_page_count:,} pages"),
            ("failed", f"{outputs.failed_page_count:,} pages"),
            ("content", str(outputs.output_dir / "content.md")),
        ),
        source_rows=(rewrite_output_summary_row(outputs),),
    ):
        return 0 if outputs.failed_page_count == 0 else 1

    sys.stdout.write(
        "source material rewritten as whole document\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  rewritten_pages: {outputs.rewritten_page_count}\n"
        f"  failed_pages: {outputs.failed_page_count}\n"
        f"  content: {outputs.output_dir / 'content.md'}\n"
    )
    sys.stdout.flush()
    return 0 if outputs.failed_page_count == 0 else 1


def rewrite_whole_source_materials_cli(
    *,
    parsed_root: Path,
    output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_whole_parsed_source_materials(
                parsed_root,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
    rewritten_pages = sum(output.rewritten_page_count for output in outputs.source_outputs)
    if emit_source_rewrite_summary(
        title="whole document batch",
        summary=(
            ("status", "written" if failed_pages == 0 and outputs.source_outputs else "failed"),
            ("parsed", str(outputs.parsed_root)),
            ("output", str(outputs.output_root)),
            ("sources", f"{len(outputs.source_outputs):,}"),
            ("skipped", f"{len(outputs.skipped_sources):,}"),
            ("rewritten", f"{rewritten_pages:,} pages"),
            ("failed", f"{failed_pages:,} pages"),
        ),
        source_rows=tuple(rewrite_output_summary_row(output) for output in outputs.source_outputs),
        skipped_rows=tuple(outputs.skipped_sources),
    ):
        return 0 if failed_pages == 0 and outputs.source_outputs else 1

    sys.stdout.write(
        "source materials rewritten as whole documents\n"
        f"  parsed_root: {outputs.parsed_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  rewritten_pages: {rewritten_pages}\n"
        f"  failed_pages: {failed_pages}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"(rewritten: {output.rewritten_page_count}, failed: {output.failed_page_count})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed_pages == 0 and outputs.source_outputs else 1


def rewrite_vocabulary_guided_source_material_cli(
    *,
    parsed_bundle_dir: Path,
    output_root: Path,
    vocabulary_source: RewriteVocabularySource,
    target_grade: str,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_vocabulary_guided_parsed_source_material(
                parsed_bundle_dir,
                dictionary_path=vocabulary_source.path,
                vocabulary_source_kind=vocabulary_source.kind,
                vocabulary_source_sha256=vocabulary_source.sha256,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if emit_source_rewrite_summary(
        title="vocabulary guided",
        summary=(
            ("status", "written" if outputs.failed_page_count == 0 else "failed"),
            ("source_id", outputs.source_id),
            ("output", str(outputs.output_dir)),
            ("vocabulary", f"{vocabulary_source.kind}: {vocabulary_source.path}"),
            ("selected", f"{len(outputs.selected_vocabulary):,} vocabulary"),
            ("rewritten", f"{outputs.rewritten_page_count:,} pages"),
            ("failed", f"{outputs.failed_page_count:,} pages"),
            ("content", str(outputs.output_dir / "content.md")),
        ),
        source_rows=(rewrite_output_summary_row(outputs),),
    ):
        return 0 if outputs.failed_page_count == 0 else 1

    sys.stdout.write(
        "source material rewritten with vocabulary guidance\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  vocabulary_source: {vocabulary_source.kind} {vocabulary_source.path}\n"
        f"  selected_vocabulary: {len(outputs.selected_vocabulary)}\n"
        f"  rewritten_pages: {outputs.rewritten_page_count}\n"
        f"  failed_pages: {outputs.failed_page_count}\n"
        f"  content: {outputs.output_dir / 'content.md'}\n"
    )
    sys.stdout.flush()
    return 0 if outputs.failed_page_count == 0 else 1


def rewrite_vocabulary_guided_source_materials_cli(
    *,
    parsed_root: Path,
    output_root: Path,
    vocabulary_source: RewriteVocabularySource,
    target_grade: str,
    model: str,
    base_url: str | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = rewrite_vocabulary_guided_parsed_source_materials(
                parsed_root,
                dictionary_path=vocabulary_source.path,
                vocabulary_source_kind=vocabulary_source.kind,
                vocabulary_source_sha256=vocabulary_source.sha256,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                progress=emit_source_rewrite_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
    rewritten_pages = sum(output.rewritten_page_count for output in outputs.source_outputs)
    selected_vocabulary_count = sum(len(output.selected_vocabulary) for output in outputs.source_outputs)
    if emit_source_rewrite_summary(
        title="vocabulary guided batch",
        summary=(
            ("status", "written" if failed_pages == 0 and outputs.source_outputs else "failed"),
            ("parsed", str(outputs.parsed_root)),
            ("output", str(outputs.output_root)),
            ("vocabulary", f"{vocabulary_source.kind}: {vocabulary_source.path}"),
            ("sources", f"{len(outputs.source_outputs):,}"),
            ("skipped", f"{len(outputs.skipped_sources):,}"),
            ("selected", f"{selected_vocabulary_count:,} vocabulary"),
            ("rewritten", f"{rewritten_pages:,} pages"),
            ("failed", f"{failed_pages:,} pages"),
        ),
        source_rows=tuple(rewrite_output_summary_row(output) for output in outputs.source_outputs),
        skipped_rows=tuple(outputs.skipped_sources),
    ):
        return 0 if failed_pages == 0 and outputs.source_outputs else 1

    sys.stdout.write(
        "source materials rewritten with vocabulary guidance\n"
        f"  parsed_root: {outputs.parsed_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  vocabulary_source: {vocabulary_source.kind} {vocabulary_source.path}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  selected_vocabulary: {selected_vocabulary_count}\n"
        f"  rewritten_pages: {rewritten_pages}\n"
        f"  failed_pages: {failed_pages}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"(selected: {len(output.selected_vocabulary)}, rewritten: {output.rewritten_page_count}, "
            f"failed: {output.failed_page_count})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed_pages == 0 and outputs.source_outputs else 1


def print_class_material_cli(
    *,
    rewritten_bundle_dir: Path,
    output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=16384,
    )
    try:
        try:
            outputs = print_class_material(
                rewritten_bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                font_path=font_path,
                body_font_size=body_font_size,
                progress=emit_printable_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (PrintableMaterialError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    summary_row = printable_output_summary_row(outputs)
    summary = [
        ("status", "written" if outputs.accepted else "failed"),
        ("source_id", outputs.source_id),
        ("output", str(outputs.output_dir)),
    ]
    if outputs.accepted:
        summary.append(("docx", str(outputs.output_dir / "material.docx")))
    else:
        summary.append(("failure", outputs.failure_reason))
    if "selected_image_count" in summary_row:
        summary.append(("selected", f"{summary_row['selected_image_count']} images"))
    if "body_font_size_pt" in summary_row:
        summary.append(("body font", f"{summary_row['body_font_size_pt']}pt"))
    if emit_printable_summary(
        title="DOCX material",
        summary=tuple(summary),
        source_rows=(summary_row,),
    ):
        return 0 if outputs.accepted else 1

    sys.stdout.write(
        "printable class material written\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  status: {'written' if outputs.accepted else 'failed'}\n"
    )
    if outputs.accepted:
        sys.stdout.write(
            f"  docx: {outputs.output_dir / 'material.docx'}\n"
        )
    else:
        sys.stdout.write(f"  failure: {outputs.failure_reason}\n")
    sys.stdout.flush()
    return 0 if outputs.accepted else 1


def print_class_materials_cli(
    *,
    rewritten_root: Path,
    output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=16384,
    )
    try:
        try:
            outputs = print_class_materials(
                rewritten_root,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                font_path=font_path,
                body_font_size=body_font_size,
                progress=emit_printable_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (PrintableMaterialError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    failed = sum(1 for output in outputs.source_outputs if not output.accepted)
    written = sum(1 for output in outputs.source_outputs if output.accepted)
    if emit_printable_summary(
        title="DOCX materials",
        summary=(
            ("status", "written" if failed == 0 and written > 0 else "failed"),
            ("rewritten", str(outputs.rewritten_root)),
            ("output", str(outputs.output_root)),
            ("sources", f"{len(outputs.source_outputs):,}"),
            ("skipped", f"{len(outputs.skipped_sources):,}"),
            ("written", f"{written:,}"),
            ("failed", f"{failed:,}"),
        ),
        source_rows=tuple(printable_output_summary_row(output) for output in outputs.source_outputs),
        skipped_rows=tuple(outputs.skipped_sources),
    ):
        return 0 if failed == 0 and written > 0 else 1

    sys.stdout.write(
        "printable class materials written\n"
        f"  rewritten_root: {outputs.rewritten_root}\n"
        f"  output_root: {outputs.output_root}\n"
        f"  sources: {len(outputs.source_outputs)}\n"
        f"  skipped_sources: {len(outputs.skipped_sources)}\n"
        f"  written: {written}\n"
        f"  failed: {failed}\n"
    )
    for output in outputs.source_outputs:
        sys.stdout.write(
            f"  - {output.source_id}: {output.output_dir} "
            f"({'written' if output.accepted else 'failed'})\n"
        )
    for skipped in outputs.skipped_sources:
        sys.stdout.write(f"  - skipped {skipped['source_dir']}: {skipped['reason']}\n")
    sys.stdout.flush()
    return 0 if failed == 0 and written > 0 else 1


def build_class_printouts_cli(
    *,
    source_dir: Path,
    parse_output_root: Path,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
    rewrite_output_root: Path,
    vocabulary_source: RewriteVocabularySource,
    print_output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
    force_class_printouts: bool,
) -> int:
    try:
        result = class_printout_workflow.build_class_printouts_workflow(
            source_dir=source_dir,
            parse_output_root=parse_output_root,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            rewrite_output_root=rewrite_output_root,
            vocabulary_source=vocabulary_source,
            print_output_root=print_output_root,
            target_grade=target_grade,
            target_profile=target_profile_for_grade(target_grade),
            model=model,
            base_url=base_url,
            font_path=font_path,
            body_font_size=body_font_size,
            force_class_printouts=force_class_printouts,
            parse_source_material_func=parse_source_material,
            rewrite_source_material_func=rewrite_vocabulary_guided_parsed_source_material,
            print_class_material_func=print_class_material,
            completion_client_factory=ollama_client,
            model_unload_callback=warn_if_model_unload_fails,
            source_material_progress=emit_source_material_progress,
            source_rewrite_progress=emit_source_rewrite_progress,
            printable_progress=emit_printable_progress,
        )
    except KeyboardInterrupt:
        sys.stderr.write("wordbyword: class printout build interrupted during parsing; partial workspace cleaned.\n")
        return 130
    except (SourceMaterialParseError, class_printout_workflow.ClassPrintoutWorkflowError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1
    except (SourceRewriteError, PrintableMaterialError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if result.status == "no_sources":
        sys.stderr.write(f"wordbyword: no class source materials found in {source_dir}\n")
        return 1

    if result.status == "skipped":
        if emit_printable_summary(
            title="workflow complete",
            summary=(
                ("status", "skipped"),
                ("source_dir", str(source_dir)),
                ("parsed", str(parse_output_root)),
                ("rewrite", str(rewrite_output_root)),
                ("print", str(print_output_root)),
                ("vocabulary", f"{vocabulary_source.kind}: {vocabulary_source.path}"),
                ("discovered sources", f"{result.discovered_sources:,}"),
                ("skipped", f"{len(result.skipped_sources):,}"),
                ("docx written", "0"),
            ),
        ):
            return 0
        sys.stdout.write(
            "class printout workflow complete\n"
            f"  source_dir: {source_dir}\n"
            f"  parsed_root: {parse_output_root}\n"
            f"  rewrite_root: {rewrite_output_root}\n"
            f"  print_root: {print_output_root}\n"
            f"  vocabulary_source: {vocabulary_source.kind} {vocabulary_source.path}\n"
            f"  discovered_sources: {result.discovered_sources}\n"
            f"  skipped_sources: {len(result.skipped_sources)}\n"
            "  parsed_sources: 0\n"
            "  rewritten: 0\n"
            "  failed_rewrites: 0\n"
            "  docx_written: 0\n"
            "  failed_prints: 0\n"
        )
        sys.stdout.flush()
        return 0

    if result.status == "stopped_after_parsing":
        all_skipped_sources = [*result.skipped_sources, *result.parser_skipped_sources]
        if emit_printable_summary(
            title="workflow stopped",
            summary=(
                ("status", "stopped after parsing"),
                ("source_dir", str(source_dir)),
                ("prepared", f"{result.parsed_sources:,}"),
                ("skipped", f"{len(all_skipped_sources):,}"),
                ("vocabulary", f"{vocabulary_source.kind}: {vocabulary_source.path}"),
                ("rewritable", "0"),
            ),
        ):
            return 1

        sys.stdout.write(
            "class printout workflow stopped after parsing\n"
            f"  source_dir: {source_dir}\n"
            f"  prepared_sources: {result.parsed_sources}\n"
            f"  skipped_sources: {len(all_skipped_sources)}\n"
            f"  vocabulary_source: {vocabulary_source.kind} {vocabulary_source.path}\n"
            "  rewritable: 0\n"
        )
        sys.stdout.flush()
        return 1

    if result.status == "stopped_after_rewrite":
        if emit_printable_summary(
            title="workflow stopped",
            summary=(
                ("status", "stopped after rewrite"),
                ("source_dir", str(source_dir)),
                ("parsed", f"{result.parsed_sources:,}"),
                ("skipped", f"{len(result.skipped_sources) + len(result.parser_skipped_sources):,}"),
                ("vocabulary", f"{vocabulary_source.kind}: {vocabulary_source.path}"),
                ("rewritten", "0"),
                ("failed rewrites", f"{result.failed_rewrites:,}"),
            ),
        ):
            return 1

        sys.stdout.write(
            "class printout workflow stopped after rewrite\n"
            f"  source_dir: {source_dir}\n"
            f"  parsed_sources: {result.parsed_sources}\n"
            f"  skipped_sources: {len(result.skipped_sources) + len(result.parser_skipped_sources)}\n"
            f"  vocabulary_source: {vocabulary_source.kind} {vocabulary_source.path}\n"
            f"  rewritten: 0\n"
            f"  failed_rewrites: {result.failed_rewrites}\n"
        )
        sys.stdout.flush()
        return 1

    if emit_printable_summary(
        title="workflow complete",
        summary=(
            ("status", "written" if result.exit_code == 0 else "failed"),
            ("source_dir", str(source_dir)),
            ("parsed", str(parse_output_root)),
            ("rewrite", str(rewrite_output_root)),
            ("print", str(print_output_root)),
            ("vocabulary", f"{vocabulary_source.kind}: {vocabulary_source.path}"),
            ("ledger", str(result.ledger_path)),
            ("parsed sources", f"{result.parsed_sources:,}"),
            ("skipped sources", f"{len(result.skipped_sources) + len(result.parser_skipped_sources):,}"),
            ("rewritten", f"{result.rewritten:,}"),
            ("failed rewrites", f"{result.failed_rewrites:,}"),
            ("docx written", f"{result.docx_written:,}"),
            ("failed prints", f"{result.failed_prints:,}"),
        ),
        source_rows=tuple(printable_output_summary_row(output) for output in result.print_outputs),
    ):
        return result.exit_code

    sys.stdout.write(
        "class printout workflow complete\n"
        f"  source_dir: {source_dir}\n"
        f"  parsed_root: {parse_output_root}\n"
        f"  rewrite_root: {rewrite_output_root}\n"
        f"  print_root: {print_output_root}\n"
        f"  vocabulary_source: {vocabulary_source.kind} {vocabulary_source.path}\n"
        f"  ledger: {result.ledger_path}\n"
        f"  parsed_sources: {result.parsed_sources}\n"
        f"  skipped_sources: {len(result.skipped_sources) + len(result.parser_skipped_sources)}\n"
        f"  rewritten: {result.rewritten}\n"
        f"  failed_rewrites: {result.failed_rewrites}\n"
        f"  docx_written: {result.docx_written}\n"
        f"  failed_prints: {result.failed_prints}\n"
    )
    for output in result.print_outputs:
        status = "written" if bool(getattr(output, "accepted")) else "failed"
        sys.stdout.write(f"  - {output.source_id}: {output.output_dir} ({status})\n")
    sys.stdout.flush()
    return result.exit_code


def generate_after_class_reading_report_cli(
    *,
    printout_path: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    target_grade: str,
) -> int:
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        try:
            outputs = generate_after_class_reading_report(
                printout_path,
                output_root=output_root,
                client=client,
                model=model,
                target_grade=target_grade,
                font_path=font_path,
                progress=emit_after_class_report_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (AfterClassReportError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    summary_row = after_class_report_summary_row(outputs)
    summary = [
        ("status", "written" if outputs.accepted else "failed"),
        ("source_id", outputs.source_id),
        ("output", str(outputs.output_dir)),
        ("heuristic analysis", str(outputs.heuristic_analysis_dir)),
        ("selected vocabulary", f"{outputs.selected_vocabulary_count}"),
    ]
    if outputs.accepted and outputs.docx_path is not None:
        summary.append(("docx", str(outputs.docx_path)))
    else:
        summary.append(("failure", outputs.failure_reason))
    if emit_after_class_report_summary(
        title="reading report",
        summary=tuple(summary),
        source_rows=(summary_row,),
    ):
        return 0 if outputs.accepted else 1

    sys.stdout.write(
        "after-class reading report generated\n"
        f"  source_id: {outputs.source_id}\n"
        f"  output_dir: {outputs.output_dir}\n"
        f"  heuristic_analysis_dir: {outputs.heuristic_analysis_dir}\n"
        f"  selected_vocabulary: {outputs.selected_vocabulary_count}\n"
        f"  status: {'written' if outputs.accepted else 'failed'}\n"
    )
    if outputs.accepted and outputs.docx_path is not None:
        sys.stdout.write(f"  docx: {outputs.docx_path}\n")
    else:
        sys.stdout.write(f"  failure: {outputs.failure_reason}\n")
    sys.stdout.flush()
    return 0 if outputs.accepted else 1


def build_usage_signals_cli(
    *,
    archive_root: Path,
    output_root: Path,
    target_grade: str,
    include_punctuation: bool,
    include_numbers: bool,
    model: str,
    base_url: str | None,
) -> int:
    near_duplicate_client = LazyOllamaCompletionClient(
        model=model,
        base_url=base_url,
        timeout_seconds=300.0,
        max_output_tokens=1024,
        client_factory=ollama_client,
    )
    try:
        try:
            result = build_usage_signals(
                archive_root=archive_root,
                output_root=output_root,
                target_grade=target_grade,
                include_punctuation=include_punctuation,
                include_numbers=include_numbers,
                near_duplicate_client=near_duplicate_client,
                near_duplicate_model=model,
                progress=emit_usage_signal_progress,
            )
        finally:
            if near_duplicate_client.client is not None:
                warn_if_model_unload_fails(near_duplicate_client.client)
    except UsageSignalBuildError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if emit_usage_signal_summary(result):
        return 0

    sys.stdout.write(
        "usage signals built\n"
        f"  archive_root: {result.archive_root}\n"
        f"  output_root: {result.output_root}\n"
        f"  documents: {result.documents}\n"
        f"  text_units: {result.extracted_pages}\n"
        f"  sentences: {result.sentences}\n"
        f"  token_occurrences: {result.token_occurrences}\n"
        f"  vocabulary_terms: {result.vocabulary_terms}\n"
        f"  surface_words: {result.surface_words}\n"
    )
    sys.stdout.flush()
    return 0


def emit_usage_signal_progress(phase: str, detail: str) -> None:
    if emit_usage_signal_progress_ui(phase, detail):
        return
    sys.stdout.write(f"usage signals [{phase}] {detail}\n")
    sys.stdout.flush()


def update_live_lexicon_cli(
    *,
    target_grade: str,
    canonical_dictionary_path: Path,
    usage_candidates_path: Path,
    usage_summary_path: Path | None,
    live_lexicon_path: Path,
    supplemental_lexicon_path: Path,
    unmatched_usage_output_path: Path,
) -> int:
    try:
        result = update_live_lexicon(
            target_grade=target_grade,
            canonical_dictionary_path=canonical_dictionary_path,
            usage_candidates_path=usage_candidates_path,
            usage_summary_path=usage_summary_path,
            live_lexicon_path=live_lexicon_path,
            supplemental_lexicon_path=supplemental_lexicon_path,
            unmatched_usage_output_path=unmatched_usage_output_path,
            progress=emit_live_lexicon_progress,
        )
    except LiveLexiconUpdateError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if emit_live_lexicon_summary(result):
        return 0

    sys.stdout.write(
        "live lexicon updated\n"
        f"  canonical_dictionary: {result.canonical_dictionary_path}\n"
        f"  canonical_source: {result.canonical_source}\n"
        f"  live_lexicon: {result.live_lexicon_path}\n"
        f"  supplemental_lexicon: {result.supplemental_lexicon_path}\n"
        f"  usage_candidates: {result.usage_candidates_path}\n"
        f"  usage_summary: {result.usage_summary_path}\n"
        f"  unmatched_usage_output: {result.unmatched_usage_output_path}\n"
        f"  canonical_rows: {result.canonical_rows}\n"
        f"  live_lexicon_rows: {result.live_lexicon_rows}\n"
        f"  usage_candidate_rows: {result.usage_candidate_rows}\n"
        f"  total_class_printouts: {result.total_class_printouts}\n"
        f"  matched: {result.matched_count}\n"
        f"  unmatched: {result.unmatched_count}\n"
        f"  supplemental_rows: {result.supplemental_rows}\n"
        f"  supplemental_active_rows: {result.supplemental_active_rows}\n"
        f"  supplemental_added: {result.supplemental_added_count}\n"
        f"  supplemental_updated: {result.supplemental_updated_count}\n"
        f"  supplemental_inactive: {result.supplemental_inactive_count}\n"
        f"  supplemental_promoted: {result.supplemental_promoted_count}\n"
    )
    sys.stdout.flush()
    return 0


def emit_live_lexicon_progress(phase: str, detail: str) -> None:
    emit_live_lexicon_progress_ui(phase, detail)


def resolve_dictionary_source_duplicates_cli(
    *,
    archive_root: Path,
    yes: bool,
    target_grade: str,
) -> int:
    try:
        validate_managed_dictionary_source_grades(archive_root, target_grade=target_grade)
    except DictionarySourceArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1
    try:
        preview = resolve_dictionary_source_duplicates(archive_root, apply=False)
    except DictionarySourceArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if preview.duplicate_group_count == 0:
        sys.stdout.write(
            "no duplicate dictionary source archive files found\n"
            f"  archive_root: {preview.archive_root}\n"
        )
        sys.stdout.flush()
        return 0

    write_dictionary_source_duplicate_resolution_summary(preview, applied=False)
    if not yes:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "wordbyword: dictionary source duplicate resolution requires an interactive terminal. "
                "Rerun in a terminal, or pass --yes to apply the recommended quarantine plan.\n"
            )
            return 1
        response = input("Apply this dictionary source duplicate quarantine plan? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            sys.stderr.write("wordbyword: dictionary source duplicate resolution cancelled; no files were moved.\n")
            return 1

    try:
        result = apply_dictionary_source_duplicate_resolution_result(preview)
    except DictionarySourceArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    write_dictionary_source_duplicate_resolution_summary(result, applied=True)
    return 0


def write_dictionary_source_duplicate_resolution_summary(
    result: DictionarySourceDuplicateResolutionResult,
    *,
    applied: bool,
) -> None:
    sys.stdout.write(
        f"{'dictionary source duplicates resolved' if applied else 'dictionary source duplicate resolution plan'}\n"
        f"  archive_root: {result.archive_root}\n"
        f"  duplicate_groups: {result.duplicate_group_count}\n"
        f"  quarantine_root: {result.quarantine_root}\n"
        f"  manifest: {result.manifest_path}\n"
    )
    if result.batch_root is not None:
        sys.stdout.write(f"  batch: {result.batch_root}\n")
    for index, resolution in enumerate(result.resolutions, start=1):
        sys.stdout.write(f"  group {index}:\n")
        for evidence in resolution.group.evidences:
            sys.stdout.write(f"    reason: {evidence.reason} {evidence.detail}\n")
        sys.stdout.write(f"    keep: {resolution.keep_path.name}\n")
        for source, destination in zip(
            resolution.quarantine_paths,
            resolution.quarantine_destination_paths,
            strict=True,
        ):
            action = "quarantined" if applied else "quarantine"
            sys.stdout.write(f"    {action}: {source.name} -> {destination.name}\n")
    sys.stdout.flush()


def resolve_archive_duplicates_cli(
    *,
    archive_root: Path,
    yes: bool,
    include_near_duplicates: bool,
    model: str,
    base_url: str | None,
    target_grade: str,
) -> int:
    try:
        validate_managed_class_archive_grades(archive_root, target_grade=target_grade)
    except ClassSessionArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1
    near_duplicate_client: LazyOllamaCompletionClient | None = None
    if include_near_duplicates:
        near_duplicate_client = LazyOllamaCompletionClient(
            model=model,
            base_url=base_url,
            timeout_seconds=300.0,
            max_output_tokens=1024,
            client_factory=ollama_client,
        )
    try:
        try:
            preview = resolve_archive_duplicates(
                archive_root,
                apply=False,
                include_near_duplicates=include_near_duplicates,
                near_duplicate_client=near_duplicate_client,
                near_duplicate_model=model if include_near_duplicates else None,
            )
        finally:
            if near_duplicate_client is not None and near_duplicate_client.client is not None:
                warn_if_model_unload_fails(near_duplicate_client.client)
    except ClassSessionArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if preview.duplicate_group_count == 0:
        sys.stdout.write(
            "no duplicate class-session archive files found\n"
            f"  archive_root: {preview.archive_root}\n"
        )
        write_archive_near_duplicate_review_summary(preview)
        sys.stdout.flush()
        return 0

    write_archive_duplicate_resolution_summary(preview, applied=False)
    if not yes:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "wordbyword: duplicate resolution requires an interactive terminal. "
                "Rerun with --resolve-archive-duplicates in a terminal, or pass --yes to apply the recommended quarantine plan.\n"
            )
            return 1
        response = input("Apply this duplicate quarantine plan? [y/N] ").strip().lower()
        if response not in {"y", "yes"}:
            sys.stderr.write("wordbyword: duplicate resolution cancelled; no files were moved.\n")
            return 1

    try:
        result = apply_archive_duplicate_resolution_result(preview)
    except ClassSessionArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    write_archive_duplicate_resolution_summary(result, applied=True)
    return 0


def write_archive_duplicate_resolution_summary(
    result: ArchiveDuplicateResolutionResult,
    *,
    applied: bool,
) -> None:
    sys.stdout.write(
        f"{'archive duplicates resolved' if applied else 'archive duplicate resolution plan'}\n"
        f"  archive_root: {result.archive_root}\n"
        f"  duplicate_groups: {result.duplicate_group_count}\n"
        f"  quarantine_root: {result.quarantine_root}\n"
        f"  manifest: {result.manifest_path}\n"
    )
    if result.batch_root is not None:
        sys.stdout.write(f"  batch: {result.batch_root}\n")
    for index, resolution in enumerate(result.resolutions, start=1):
        sys.stdout.write(f"  group {index}:\n")
        for evidence in resolution.group.evidences:
            sys.stdout.write(f"    reason: {evidence.reason} {evidence.detail}\n")
        sys.stdout.write(f"    keep: {resolution.keep_path.name}\n")
        for source, destination in zip(
            resolution.quarantine_paths,
            resolution.quarantine_destination_paths,
            strict=True,
        ):
            action = "quarantined" if applied else "quarantine"
            sys.stdout.write(f"    {action}: {source.name} -> {destination.name}\n")
    write_archive_near_duplicate_review_summary(result)
    sys.stdout.flush()


def write_archive_near_duplicate_review_summary(result: ArchiveDuplicateResolutionResult) -> None:
    if not result.near_duplicate_reviews:
        return
    sys.stdout.write(f"  near_duplicate_reviews: {len(result.near_duplicate_reviews)}\n")
    for index, review in enumerate(result.near_duplicate_reviews, start=1):
        action = "actionable" if review.actionable else "reported"
        filenames = ", ".join(path.name for path in review.candidate.paths)
        sys.stdout.write(
            f"    review {index}: {action} {review.verdict} "
            f"confidence:{review.confidence:.3f} files:{filenames}\n"
        )


def rename_class_session_archive_cli(
    *,
    archive_root: Path,
    model: str,
    base_url: str | None,
    apply: bool,
    target_grade: str,
) -> int:
    try:
        validate_managed_class_archive_grades(archive_root, target_grade=target_grade)
    except ClassSessionArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        try:
            result = rename_class_session_archive(
                archive_root,
                client=client,
                model=model,
                target_grade=target_grade,
                apply=apply,
                progress=emit_archive_rename_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (ClassSessionArchiveError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "class-session archive rename complete\n"
        f"  archive_root: {result.archive_root}\n"
        f"  mode: {'apply' if apply else 'dry-run'}\n"
        f"  files: {len(result.plans)}\n"
        f"  planned: {result.planned_count}\n"
        f"  renamed: {result.renamed_count}\n"
        f"  skipped: {result.skipped_count}\n"
        f"  ledger: {result.ledger_path}\n"
    )
    for plan in result.plans:
        confidence = f"{plan.metadata.confidence:.2f}"
        sys.stdout.write(
            f"  - {plan.original_name} -> {plan.target_name} "
            f"({plan.status}, confidence: {confidence})\n"
        )
    sys.stdout.flush()
    return 0


def emit_archive_rename_progress(detail: str) -> None:
    sys.stdout.write(f"archive rename {detail}\n")
    sys.stdout.flush()


def rename_dictionary_source_archive_cli(
    *,
    archive_root: Path,
    model: str,
    base_url: str | None,
    apply: bool,
    target_grade: str,
) -> int:
    try:
        validate_managed_dictionary_source_grades(archive_root, target_grade=target_grade)
    except DictionarySourceArchiveError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1
    client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        try:
            result = rename_dictionary_source_archive(
                archive_root,
                client=client,
                model=model,
                target_grade=target_grade,
                apply=apply,
                progress=emit_dictionary_source_rename_progress,
            )
        finally:
            warn_if_model_unload_fails(client)
    except (DictionarySourceArchiveError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "dictionary source archive rename complete\n"
        f"  archive_root: {result.archive_root}\n"
        f"  mode: {'apply' if apply else 'dry-run'}\n"
        f"  files: {len(result.plans)}\n"
        f"  planned: {result.planned_count}\n"
        f"  renamed: {result.renamed_count}\n"
        f"  registered: {result.registered_count}\n"
        f"  skipped: {result.skipped_count}\n"
        f"  blocked: {result.blocked_count}\n"
        f"  ledger: {result.ledger_path}\n"
    )
    for plan in result.plans:
        confidence = f"{plan.metadata.confidence:.2f}"
        sys.stdout.write(
            f"  - {plan.original_path} -> {plan.target_path_relative} "
            f"({plan.status}, confidence: {confidence})\n"
        )
    sys.stdout.flush()
    return 0 if not apply or result.blocked_count == 0 else 1


def emit_dictionary_source_rename_progress(detail: str) -> None:
    sys.stdout.write(f"dictionary source archive rename {detail}\n")
    sys.stdout.flush()


def warn_if_model_unload_fails(client: SupportsCompletion) -> None:
    try:
        unload_model_if_supported(client)
    except Exception as error:  # pragma: no cover - defensive cleanup path
        sys.stderr.write(f"wordbyword: warning: could not unload Ollama model: {error}\n")
        sys.stderr.flush()


def paddleocr_vl_settings_from_args(args: argparse.Namespace) -> PaddleOCRVLSettings | None:
    return application_paddleocr_vl_settings_from_args(
        args,
        discover_sidecar_env=discover_paddleocr_vl_sidecar_env,
        validate_sidecar=validate_paddleocr_vl_sidecar,
        current_python_available=current_python_has_paddleocr,
    )


def discover_paddleocr_vl_sidecar_env(*, roots: Sequence[Path] | None = None) -> Path | None:
    return application_discover_paddleocr_vl_sidecar_env(roots=roots)


def paddleocr_vl_sidecar_search_roots() -> tuple[Path, ...]:
    return application_paddleocr_vl_sidecar_search_roots()


def wordbyword_project_root() -> Path:
    return application_wordbyword_project_root()


def current_python_has_paddleocr() -> bool:
    return application_current_python_has_paddleocr()


def validate_paddleocr_vl_sidecar(*, sidecar_env: str | None, sidecar_python: str | None) -> None:
    application_validate_paddleocr_vl_sidecar(sidecar_env=sidecar_env, sidecar_python=sidecar_python)


def paddleocr_vl_sidecar_python_path(*, sidecar_env: str | None, sidecar_python: str | None) -> Path | None:
    return application_paddleocr_vl_sidecar_python_path(sidecar_env=sidecar_env, sidecar_python=sidecar_python)


def paddleocr_vl_python_can_locate_paddleocr(python_path: Path) -> bool:
    return application_paddleocr_vl_python_can_locate_paddleocr(python_path)


def prompt_generated_example_retry(
    *,
    summary: GeneratedExampleAttemptSummary,
    output_dir: Path,
    model: str,
) -> bool:
    if summary.failed_count <= 0 or not sys.stdin.isatty():
        return False

    attempts_path = artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_ATTEMPTS)
    failures_path = artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_FAILURES)
    sys.stdout.write(
        "\n"
        f"wordbyword: candidate-example attempt {summary.attempt_number} with {model} left "
        f"{summary.failed_count:,} failed row(s).\n"
        f"  attempts: {attempts_path}\n"
        f"  failures: {failures_path}\n"
    )
    sys.stdout.flush()

    while True:
        try:
            response = input("Retry the failed subset with Ollama? [Y/n] ").strip().lower()
        except EOFError:
            return False
        if response in {"", "y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        sys.stdout.write("Please answer Y or n.\n")
        sys.stdout.flush()


def link_cleaned_examples(*, output_dir: Path) -> int:
    try:
        outputs = write_linked_dictionary_candidates_from_saved(output_dir, require_inputs=True)
    except ValueError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    assert outputs is not None
    refresh_saved_output_metadata(output_dir, linked_dictionary_outputs=outputs)

    emit_rendered_artifact_drop(
        artifact_event_name(ArtifactId.LINKED_DICTIONARY_CANDIDATES),
        file_label=str(artifact_path(output_dir, ArtifactId.LINKED_DICTIONARY_CANDIDATES)),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("replaced", f"{outputs.linked_count:,} examples"),
            ("rewrite", f"{outputs.unlinked_count:,} pending"),
            ("status", "replay // no llm calls"),
        ),
    )
    return 0


def add_reference_meanings(*, output_dir: Path, reference_dictionary_path: Path) -> int:
    try:
        outputs = write_meaning_dictionary_candidates_from_saved(
            output_dir,
            reference_dictionary_path=reference_dictionary_path,
        )
    except ValueError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_rendered_artifact_drop(
        artifact_event_name(ArtifactId.MEANING_DICTIONARY_CANDIDATES),
        file_label=str(artifact_path(output_dir, ArtifactId.MEANING_DICTIONARY_CANDIDATES)),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("matched", f"{outputs.matched_count:,} meanings"),
            ("missing", f"{outputs.unmatched_count:,} meanings"),
            ("status", "replay // no llm calls"),
        ),
    )
    return 0


def generate_missing_meanings(
    *,
    output_dir: Path,
    model: str,
    base_url: str | None = None,
    generated_meaning_client: SupportsCompletion | None = None,
) -> int:
    owns_client = generated_meaning_client is None

    def generated_meaning_progress(progress: CleanupBatchProgress) -> None:
        emit_cleanup_progress(
            progress,
            model=model,
            label="meaning",
            item_label="terms",
        )

    try:
        client = generated_meaning_client or ollama_client(
            model=model,
            base_url=base_url,
            timeout_seconds=300.0,
            max_output_tokens=4096,
        )
        try:
            outputs = write_generated_meaning_dictionary_candidates_from_saved(
                output_dir,
                generated_meaning_client=client,
                generated_meaning_progress=generated_meaning_progress,
            )
        finally:
            if owns_client:
                warn_if_model_unload_fails(client)
    except (ValueError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_rendered_artifact_drop(
        artifact_event_name(ArtifactId.FINAL_DICTIONARY),
        file_label=str(artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)),
        summary=(
            ("relics", f"{len(outputs.rows):,} headwords"),
            ("generated", f"{outputs.generated_count:,} meanings"),
            ("preserved", f"{outputs.preserved_count:,} meanings"),
            ("missing", f"{outputs.missing_count:,} meanings"),
            ("model", model),
        ),
    )
    return 0
