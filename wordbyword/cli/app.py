from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from wordbyword.application.commands import (
    AfterClassReportRequest,
    ArchiveDuplicatesRequest,
    ArchiveRenameRequest,
    ClassMaterialPrintRequest,
    ClassPrintoutsRequest,
    CommandId,
    CommandRequest,
    CommandResult,
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
    command_spec,
)
from wordbyword.application import use_cases as application_use_cases
from wordbyword.application.events import CommandEvent
from wordbyword.application.workspace import (
    ArtifactId,
    PaddleOCRVLSettings,
    PipelineUsageError,
    RewriteVocabularySource,
    artifact_event_name,
    artifact_filename,
    artifact_path,
    current_python_has_paddleocr,
    discover_paddleocr_vl_sidecar_env,
    file_sha256_if_present as workspace_file_sha256_if_present,
    resolved_dictionary_output_dir,
    rewrite_vocabulary_source,
    source_parse_options_from_args as workspace_source_parse_options_from_args,
    target_grade_from_args,
    validate_paddleocr_vl_sidecar,
    paddleocr_vl_settings_from_args as workspace_paddleocr_vl_settings_from_args,
)
from wordbyword.cli.options import PIPELINE_CLEANUP_FLAGS, SourceParseOptions, build_parser, validate_args
from wordbyword.cli.request_builder import (
    build_cli_request,
    command_id_from_cli_args,
    resolve_distribution_path as request_builder_resolve_distribution_path,
)
from wordbyword.cli.terminal_ui import (
    emit_after_class_report_progress,
    emit_cleanup_progress,
    emit_printable_progress,
    emit_source_material_progress,
    emit_source_rewrite_progress,
    emit_stage_update,
    render_artifact_drop,
)
from wordbyword.execution_guard import CommandAlreadyRunningError, acquire_workspace_command_guard


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    try:
        command_label = exclusive_command_label(args)
        if command_label is None:
            exit_code = run_immediate_command(args)
        else:
            try:
                with acquire_workspace_command_guard(command_label, workspace_root=Path.cwd()):
                    exit_code = run_immediate_command(args)
            except CommandAlreadyRunningError as error:
                sys.stderr.write(f"wordbyword: {error}. Try again after it finishes.\n")
                return 1
    except PipelineUsageError as error:
        parser.exit(2, f"wordbyword: error: {error}\n")

    if exit_code is not None:
        return exit_code
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


def run_cli_request(request: CommandRequest) -> int:
    confirmed_duplicate_request = False
    if isinstance(request, ArchiveDuplicatesRequest | DictionarySourceDuplicatesRequest) and not request.apply and sys.stdin.isatty():
        answer = input("Apply duplicate resolution? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            sys.stderr.write("wordbyword: duplicate resolution cancelled.\n")
            sys.stderr.flush()
            return 1
        request = replace(request, apply=True)
        confirmed_duplicate_request = True
    result = application_use_cases.run_request(request, emit_event=emit_cli_event)
    if (
        isinstance(request, ArchiveDuplicatesRequest | DictionarySourceDuplicatesRequest)
        and not request.apply
        and not confirmed_duplicate_request
        and _duplicate_resolution_has_candidates(result)
    ):
        sys.stderr.write("wordbyword: duplicate resolution found candidates; rerun with --yes to apply.\n")
        sys.stderr.flush()
        return 1
    if result.exit_code != 0 and result.error:
        sys.stderr.write(f"wordbyword: {result.error}\n")
        sys.stderr.flush()
    elif result.detail and not isinstance(request, ShowDistributionRequest):
        _render_result_header(request)
        sys.stdout.write(f"{result.detail}\n")
        _render_result_artifacts(result)
        sys.stdout.flush()
    if result.exit_code == 0 and isinstance(request, ShowDistributionRequest):
        _render_distribution(request.jsonl_path, request.model)
    return result.exit_code


def _duplicate_resolution_has_candidates(result: CommandResult) -> bool:
    try:
        duplicate_groups = int(result.artifacts.get("duplicate_groups", "0"))
    except ValueError:
        duplicate_groups = 0
    return result.exit_code == 0 and duplicate_groups > 0


def _render_result_header(request: CommandRequest) -> None:
    if not sys.stdout.isatty():
        return
    headers = {
        CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL: "SOURCE REWRITE // VOCABULARY GUIDED",
        CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS: "SOURCE REWRITE // VOCABULARY GUIDED",
        CommandId.REWRITE_WHOLE_SOURCE_MATERIAL: "SOURCE REWRITE // WHOLE SOURCE",
        CommandId.REWRITE_WHOLE_SOURCE_MATERIALS: "SOURCE REWRITE // WHOLE SOURCES",
        CommandId.PRINT_CLASS_MATERIAL: "PRINTABLE CLASS // DOCX MATERIAL",
        CommandId.PRINT_CLASS_MATERIALS: "PRINTABLE CLASS // DOCX MATERIALS",
        CommandId.CLASS_PRINTOUTS: "PRINTABLE CLASS // WORKFLOW COMPLETE",
        CommandId.AFTER_CLASS_REPORT: "AFTER-CLASS REPORT // READING REPORT",
    }
    header = headers.get(request.command_id)
    if header:
        sys.stdout.write(f"{header}\n")


def _render_result_artifacts(result: CommandResult) -> None:
    for key, value in result.artifacts.items():
        sys.stdout.write(f"{key}: {value}\n")


def emit_cli_event(command_event: CommandEvent) -> None:
    if command_event.status == "running" and command_event.phase not in {"start", "artifact"}:
        if sys.stdout.isatty():
            if _emit_structured_cli_progress(command_event):
                return
            emit_stage_update(command_event.phase, f"{command_event.workflow} :: {command_event.detail}")
            return
        phase = command_event.phase.upper() if sys.stdout.isatty() else command_event.phase
        sys.stdout.write(f"wordbyword: {command_event.workflow}/{phase}: {command_event.detail}\n")
        sys.stdout.flush()


def _emit_structured_cli_progress(command_event: CommandEvent) -> bool:
    payload = command_event.payload
    kind = str(payload.get("kind", ""))
    if kind == "source_material_progress":
        emit_source_material_progress(
            SimpleNamespace(
                phase=str(payload.get("phase", command_event.phase)),
                source_path=Path(str(payload.get("source_path", ""))),
                parser=str(payload.get("parser", "")),
                detail=str(payload.get("detail", command_event.detail)),
                source_index=_payload_int(payload, "source_index"),
                total_sources=_payload_int(payload, "total_sources", default=1),
                completed_pages=_payload_int(payload, "completed_pages"),
                total_pages=_payload_int(payload, "total_pages"),
            )
        )
        return True
    if kind == "source_rewrite_progress":
        emit_source_rewrite_progress(
            SimpleNamespace(
                phase=str(payload.get("phase", command_event.phase)),
                source_id=str(payload.get("source_id", "")),
                detail=str(payload.get("detail", command_event.detail)),
                source_index=_payload_int(payload, "source_index", default=1),
                total_sources=_payload_int(payload, "total_sources", default=1),
                rewrite_mode=str(payload.get("rewrite_mode", "")),
                output_dir=_payload_optional_path(payload, "output_dir"),
                rewritten_pages=_payload_int(payload, "rewritten_pages"),
                failed_pages=_payload_int(payload, "failed_pages"),
                selected_vocabulary=_payload_int(payload, "selected_vocabulary"),
            )
        )
        return True
    if kind == "printable_progress":
        emit_printable_progress(
            SimpleNamespace(
                phase=str(payload.get("phase", command_event.phase)),
                source_id=str(payload.get("source_id", "")),
                detail=str(payload.get("detail", command_event.detail)),
                source_index=_payload_int(payload, "source_index", default=1),
                total_sources=_payload_int(payload, "total_sources", default=1),
                output_dir=_payload_optional_path(payload, "output_dir"),
                accepted=payload.get("accepted", None),
                selected_image_count=_payload_int(payload, "selected_image_count"),
                body_font_size_pt=_payload_optional_int(payload, "body_font_size_pt"),
                failure_reason=str(payload.get("failure_reason", "")),
            )
        )
        return True
    if kind == "after_class_report_progress":
        emit_after_class_report_progress(
            SimpleNamespace(
                phase=str(payload.get("phase", command_event.phase)),
                source_id=str(payload.get("source_id", "")),
                detail=str(payload.get("detail", command_event.detail)),
                output_dir=_payload_optional_path(payload, "output_dir"),
                heuristic_analysis_dir=_payload_optional_path(payload, "heuristic_analysis_dir"),
                accepted=payload.get("accepted", None),
                selected_vocabulary_count=_payload_int(payload, "selected_vocabulary_count"),
                docx_path=_payload_optional_path(payload, "docx_path"),
                failure_reason=str(payload.get("failure_reason", "")),
            )
        )
        return True
    if kind == "cleanup_progress":
        emit_cleanup_progress(
            SimpleNamespace(
                phase=str(payload.get("phase", command_event.phase)),
                batch_index=_payload_int(payload, "batch_index"),
                total_batches=_payload_int(payload, "total_batches"),
                completed_batches=_payload_int(payload, "completed_batches"),
                completed_rows=_payload_int(payload, "completed_rows"),
                total_rows=_payload_int(payload, "total_rows"),
                current_batch_size=_payload_int(payload, "current_batch_size"),
            ),
            label=str(payload.get("label", "cleanup")),
        )
        return True
    return False


def _payload_int(payload: Mapping[str, object], key: str, *, default: int = 0) -> int:
    try:
        return int(payload.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _payload_optional_int(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_optional_path(payload: Mapping[str, object], key: str) -> Path | None:
    value = str(payload.get(key, "") or "")
    return Path(value) if value else None


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
    request = ShowDistributionRequest(command_id=CommandId.SHOW_DISTRIBUTION, jsonl_path=jsonl_path, model=model)
    result = application_use_cases.run_request(request)
    if result.exit_code != 0:
        sys.stderr.write(
            f"wordbyword: cannot read distribution source: {jsonl_path}\n"
            f"  hint: run with --generate-missing-meanings to write {artifact_filename(ArtifactId.FINAL_DICTIONARY)}, "
            f"or pass an explicit path: --show-distribution path/to/{artifact_filename(ArtifactId.CLEANED_DICTIONARY_CANDIDATES)}\n"
        )
        return result.exit_code
    _render_distribution(jsonl_path, model)
    return 0


def _render_distribution(jsonl_path: Path, model: str) -> None:
    stage, summary, entries = application_use_cases.dictionary_distribution_summary(jsonl_path, model)
    emit_rendered_artifact_drop(stage, entries=entries, file_label=str(jsonl_path), summary=summary)


def load_dictionary_candidates(jsonl_path: Path) -> list[dict[str, object]]:
    return application_use_cases.load_dictionary_candidates(jsonl_path)


def resolve_distribution_path(value: str | Path, output_dir: Path) -> Path:
    return request_builder_resolve_distribution_path(value, output_dir)


def paddleocr_vl_settings_from_args(args: argparse.Namespace) -> PaddleOCRVLSettings | None:
    return workspace_paddleocr_vl_settings_from_args(
        args,
        discover_sidecar_env=discover_paddleocr_vl_sidecar_env,
        validate_sidecar=validate_paddleocr_vl_sidecar,
        current_python_available=current_python_has_paddleocr,
    )


def source_parse_options_from_args(args: argparse.Namespace) -> SourceParseOptions:
    return workspace_source_parse_options_from_args(
        args,
        discover_sidecar_env=discover_paddleocr_vl_sidecar_env,
        validate_sidecar=validate_paddleocr_vl_sidecar,
        current_python_available=current_python_has_paddleocr,
    )


def build_dictionary_cli(args: argparse.Namespace | DictionaryBuildRequest) -> int:
    request = args if isinstance(args, DictionaryBuildRequest) else build_cli_request(args)
    if not isinstance(request, DictionaryBuildRequest):
        raise PipelineUsageError("expected dictionary build request")
    return run_cli_request(request)


def prep_dictionary_cli(args: argparse.Namespace | DictionaryBuildRequest) -> int:
    request = args if isinstance(args, DictionaryBuildRequest) else build_cli_request(args)
    if not isinstance(request, DictionaryBuildRequest):
        raise PipelineUsageError("expected dictionary prep request")
    return run_cli_request(request)


def dictionary_recovery_cli(args: argparse.Namespace | DictionaryRecoveryRequest) -> int:
    request = args if isinstance(args, DictionaryRecoveryRequest) else build_cli_request(args)
    if not isinstance(request, DictionaryRecoveryRequest):
        raise PipelineUsageError("expected dictionary recovery request")
    return run_cli_request(request)


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
    return run_cli_request(
        SourceMaterialParseRequest(
            command_id=CommandId.PARSE_SOURCE_MATERIAL,
            target_grade=target_grade,
            source_path=source_path,
            output_root=output_root,
            source_id=source_id,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
        )
    )


def parse_source_materials_cli(
    *,
    source_dir: Path,
    output_root: Path,
    target_grade: str,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    return run_cli_request(
        SourceMaterialsParseRequest(
            command_id=CommandId.PARSE_SOURCE_MATERIALS,
            target_grade=target_grade,
            source_dir=source_dir,
            output_root=output_root,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
        )
    )


def rewrite_whole_source_material_cli(
    *, parsed_bundle_dir: Path, output_root: Path, target_grade: str, model: str, base_url: str | None, provider: str = "ollama"
) -> int:
    return run_cli_request(
        SourceMaterialRewriteRequest(
            command_id=CommandId.REWRITE_WHOLE_SOURCE_MATERIAL,
            target_grade=target_grade,
            parsed_path=parsed_bundle_dir,
            output_root=output_root,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


def rewrite_whole_source_materials_cli(
    *, parsed_root: Path, output_root: Path, target_grade: str, model: str, base_url: str | None, provider: str = "ollama"
) -> int:
    return run_cli_request(
        SourceMaterialRewriteRequest(
            command_id=CommandId.REWRITE_WHOLE_SOURCE_MATERIALS,
            target_grade=target_grade,
            parsed_path=parsed_root,
            output_root=output_root,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


def rewrite_vocabulary_guided_source_material_cli(
    *,
    parsed_bundle_dir: Path,
    output_root: Path,
    vocabulary_source: RewriteVocabularySource,
    target_grade: str,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
) -> int:
    return run_cli_request(
        SourceMaterialRewriteRequest(
            command_id=CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL,
            target_grade=target_grade,
            parsed_path=parsed_bundle_dir,
            output_root=output_root,
            model=model,
            base_url=base_url,
            provider=provider,
            vocabulary_source=vocabulary_source,
        )
    )


def rewrite_vocabulary_guided_source_materials_cli(
    *,
    parsed_root: Path,
    output_root: Path,
    vocabulary_source: RewriteVocabularySource,
    target_grade: str,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
) -> int:
    return run_cli_request(
        SourceMaterialRewriteRequest(
            command_id=CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS,
            target_grade=target_grade,
            parsed_path=parsed_root,
            output_root=output_root,
            model=model,
            base_url=base_url,
            provider=provider,
            vocabulary_source=vocabulary_source,
        )
    )


def print_class_material_cli(
    *,
    rewritten_bundle_dir: Path,
    output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    return run_cli_request(
        ClassMaterialPrintRequest(
            command_id=CommandId.PRINT_CLASS_MATERIAL,
            target_grade=target_grade,
            rewritten_path=rewritten_bundle_dir,
            output_root=output_root,
            model=model,
            base_url=base_url,
            font_path=font_path,
            body_font_size=body_font_size,
            provider=provider,
        )
    )


def print_class_materials_cli(
    *,
    rewritten_root: Path,
    output_root: Path,
    target_grade: str,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    return run_cli_request(
        ClassMaterialPrintRequest(
            command_id=CommandId.PRINT_CLASS_MATERIALS,
            target_grade=target_grade,
            rewritten_path=rewritten_root,
            output_root=output_root,
            model=model,
            base_url=base_url,
            font_path=font_path,
            body_font_size=body_font_size,
            provider=provider,
        )
    )


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
    provider: str = "ollama",
    font_path: Path | None,
    body_font_size: int | None,
    force_class_printouts: bool,
) -> int:
    return run_cli_request(
        ClassPrintoutsRequest(
            command_id=CommandId.CLASS_PRINTOUTS,
            target_grade=target_grade,
            source_dir=source_dir,
            parse_output_root=parse_output_root,
            rewrite_output_root=rewrite_output_root,
            print_output_root=print_output_root,
            dictionary_output_dir=Path(),
            live_lexicon_path=Path(),
            rewrite_dictionary_path=None,
            vocabulary_source=vocabulary_source,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            model=model,
            base_url=base_url,
            font_path=font_path,
            body_font_size=body_font_size,
            force_class_printouts=force_class_printouts,
            provider=provider,
        )
    )


def generate_after_class_reading_report_cli(
    *,
    printout_path: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
    font_path: Path | None,
    target_grade: str,
) -> int:
    return run_cli_request(
        AfterClassReportRequest(
            command_id=CommandId.AFTER_CLASS_REPORT,
            target_grade=target_grade,
            printout_path=printout_path,
            output_root=output_root,
            model=model,
            base_url=base_url,
            font_path=font_path,
            provider=provider,
        )
    )


def build_usage_signals_cli(
    *,
    archive_root: Path,
    output_root: Path,
    target_grade: str,
    include_punctuation: bool,
    include_numbers: bool,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
) -> int:
    return run_cli_request(
        UsageSignalsRequest(
            command_id=CommandId.USAGE_SIGNALS,
            target_grade=target_grade,
            archive_root=archive_root,
            output_root=output_root,
            include_punctuation=include_punctuation,
            include_numbers=include_numbers,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


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
    return run_cli_request(
        LiveLexiconRequest(
            command_id=CommandId.LIVE_LEXICON,
            target_grade=target_grade,
            canonical_dictionary_path=canonical_dictionary_path,
            usage_candidates_path=usage_candidates_path,
            usage_summary_path=usage_summary_path,
            live_lexicon_path=live_lexicon_path,
            supplemental_lexicon_path=supplemental_lexicon_path,
            unmatched_usage_output_path=unmatched_usage_output_path,
        )
    )


def resolve_dictionary_source_duplicates_cli(*, archive_root: Path, yes: bool, target_grade: str) -> int:
    return run_cli_request(
        DictionarySourceDuplicatesRequest(
            command_id=CommandId.RESOLVE_DICTIONARY_SOURCE_DUPLICATES,
            target_grade=target_grade,
            archive_root=archive_root,
            apply=yes,
        )
    )


def resolve_archive_duplicates_cli(
    *,
    archive_root: Path,
    yes: bool,
    include_near_duplicates: bool,
    model: str,
    base_url: str | None,
    provider: str = "ollama",
    target_grade: str,
) -> int:
    return run_cli_request(
        ArchiveDuplicatesRequest(
            command_id=CommandId.RESOLVE_ARCHIVE_DUPLICATES,
            target_grade=target_grade,
            archive_root=archive_root,
            apply=yes,
            include_near_duplicates=include_near_duplicates,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


def rename_class_session_archive_cli(
    *, archive_root: Path, model: str, base_url: str | None, provider: str = "ollama", apply: bool, target_grade: str
) -> int:
    return run_cli_request(
        ArchiveRenameRequest(
            command_id=CommandId.RENAME_CLASS_SESSION_ARCHIVE,
            target_grade=target_grade,
            archive_root=archive_root,
            apply=apply,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


def rename_dictionary_source_archive_cli(
    *, archive_root: Path, model: str, base_url: str | None, provider: str = "ollama", apply: bool, target_grade: str
) -> int:
    return run_cli_request(
        DictionarySourceRenameRequest(
            command_id=CommandId.RENAME_DICTIONARY_SOURCE_ARCHIVE,
            target_grade=target_grade,
            archive_root=archive_root,
            apply=apply,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


def link_cleaned_examples(*, output_dir: Path) -> int:
    return run_cli_request(DictionaryStageRequest(command_id=CommandId.LINK_CLEANED_EXAMPLES, output_dir=output_dir))


def add_reference_meanings(*, output_dir: Path, reference_dictionary_path: Path) -> int:
    return run_cli_request(
        DictionaryStageRequest(
            command_id=CommandId.ADD_REFERENCE_MEANINGS,
            output_dir=output_dir,
            reference_dictionary_path=reference_dictionary_path,
        )
    )


def generate_missing_meanings(
    *,
    output_dir: Path,
    model: str,
    base_url: str | None = None,
    provider: str = "ollama",
    generated_meaning_client: object | None = None,
) -> int:
    if generated_meaning_client is not None:
        raise ValueError("generated_meaning_client injection moved to application use cases")
    return run_cli_request(
        DictionaryStageRequest(
            command_id=CommandId.GENERATE_MISSING_MEANINGS,
            output_dir=output_dir,
            model=model,
            base_url=base_url,
            provider=provider,
        )
    )


def migrate_grade_workspaces_cli(*, apply: bool) -> int:
    return run_cli_request(MigrateGradeWorkspacesRequest(command_id=CommandId.MIGRATE_GRADE_WORKSPACES, apply=apply))


def pipeline_cleanup_requested(args: argparse.Namespace) -> bool:
    return any(bool(getattr(args, flag_name)) for flag_name in PIPELINE_CLEANUP_FLAGS)


def generated_example_failure_count(output_dir: Path) -> int:
    return application_use_cases.generated_example_failure_count(output_dir)


def rewrite_dictionary_path_from_args(args: argparse.Namespace) -> Path:
    return rewrite_vocabulary_source_from_args(args).path


def rewrite_vocabulary_source_from_args(args: argparse.Namespace) -> RewriteVocabularySource:
    return rewrite_vocabulary_source(
        explicit_path=args.rewrite_dictionary,
        target_grade=target_grade_from_args(args),
        dictionary_output_dir=resolved_dictionary_output_dir(args),
    )


def file_sha256_if_present(path: Path) -> str:
    return workspace_file_sha256_if_present(path)


def prompt_generated_example_retry(*, summary: object, output_dir: Path, model: str) -> bool:
    failed_count = int(getattr(summary, "failed_count", 0))
    if failed_count <= 0 or not sys.stdin.isatty():
        return False
    attempts_path = artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_ATTEMPTS)
    failures_path = artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_FAILURES)
    sys.stdout.write(
        "\n"
        f"wordbyword: candidate-example attempt {getattr(summary, 'attempt_number', 0)} with {model} left "
        f"{failed_count:,} failed row(s).\n"
        f"  attempts: {attempts_path}\n"
        f"  failures: {failures_path}\n"
    )
    sys.stdout.flush()
    answer = input("Retry failed generated examples? [Y/n] ").strip().lower()
    return answer in {"", "y", "yes"}


def distribution_stage_for_path(jsonl_path: Path) -> str:
    for artifact_id in (
        ArtifactId.DICTIONARY_CANDIDATES,
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ):
        if jsonl_path.name == artifact_filename(artifact_id):
            return artifact_event_name(artifact_id)
    return artifact_event_name(ArtifactId.FINAL_DICTIONARY)
