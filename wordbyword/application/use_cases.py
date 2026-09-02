from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from wordbyword.after_class_reports import AfterClassReportError, generate_after_class_reading_report
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
from wordbyword.application.events import ProgressSink, emit, event
from wordbyword.class_archive.archive import (
    ClassSessionArchiveError,
    apply_archive_duplicate_resolution_result,
    rename_class_session_archive,
    resolve_archive_duplicates,
    validate_managed_class_archive_grades,
)
from wordbyword.class_archive.live_lexicon import LiveLexiconUpdateError, update_live_lexicon
from wordbyword.class_archive.usage_signals import UsageSignalBuildError, build_usage_signals
from wordbyword.classroom_printout_studio import class_printout_workflow
from wordbyword.classroom_printout_studio.class_printing.printable import (
    PrintableMaterialError,
    print_class_material,
    print_class_materials,
)
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    SourceRewriteError,
    rewrite_vocabulary_guided_source_material,
    rewrite_vocabulary_guided_source_materials,
    rewrite_whole_source_material,
    rewrite_whole_source_materials,
)
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    SourceMaterialParseError,
    parse_source_material,
    parse_source_materials,
)
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_event_name, artifact_filename, artifact_path
from wordbyword.dictionary_build.example_cleanup import CleanupBatchProgress
from wordbyword.dictionary_build.llm import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    build_provider_client,
    unload_model_if_supported,
)
from wordbyword.dictionary_build.output_files import read_jsonl
from wordbyword.dictionary_build.pipeline import PipelineUsageError, load_pipeline_result_from_output
from wordbyword.dictionary_build.recovery import run_saved_dictionary_cleanup
from wordbyword.dictionary_build.resumable import (
    UnresolvedGeneratedExampleFailures,
    build_resumable_dictionary,
    generated_example_failure_count as _generated_example_failure_count,
)
from wordbyword.dictionary_build.saved_outputs import (
    write_generated_meaning_dictionary_candidates_from_saved,
    write_meaning_dictionary_candidates_from_saved,
)
from wordbyword.dictionary_build.source_archive import (
    DictionarySourceArchiveError,
    apply_dictionary_source_duplicate_resolution_result,
    rename_dictionary_source_archive,
    resolve_dictionary_source_duplicates,
    validate_managed_dictionary_source_grades,
)
from wordbyword.dictionary_build.writers import refresh_saved_output_metadata, write_linked_dictionary_candidates_from_saved
from wordbyword.grade_profiles import target_profile_for_grade
from wordbyword.grade_workspace_migration import migrate_grade_workspaces


EXPECTED_ERRORS = (
    ValueError,
    PipelineUsageError,
    DictionarySourceArchiveError,
    SourceMaterialParseError,
    SourceRewriteError,
    PrintableMaterialError,
    AfterClassReportError,
    UsageSignalBuildError,
    LiveLexiconUpdateError,
    ClassSessionArchiveError,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    class_printout_workflow.ClassPrintoutWorkflowError,
)


class _LazyProviderClient:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str | None,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.client: SupportsCompletion | None = None

    def complete(self, *args: object, **kwargs: object) -> object:
        if self.client is None:
            self.client = build_provider_client(
                self.provider,
                model=self.model,
                base_url=self.base_url,
                timeout_seconds=self.timeout_seconds,
                max_output_tokens=self.max_output_tokens,
            )
        return self.client.complete(*args, **kwargs)  # type: ignore[arg-type]


def run_request(request: CommandRequest, emit_event: ProgressSink | None = None) -> CommandResult:
    workflow = workflow_for_request(request)
    emit(emit_event, event(workflow, "start", f"running {request.command_id.value}"))
    try:
        result = _run_request(request, emit_event)
    except KeyboardInterrupt:
        result = CommandResult(130, "failed", "command interrupted; partial workspace cleaned", error="interrupted")
    except EXPECTED_ERRORS as error:
        result = CommandResult(1, "failed", str(error), error=str(error))
    emit(
        emit_event,
        event(
            workflow,
            "complete",
            result.detail,
            status="complete" if result.exit_code == 0 else "failed",
            artifacts=result.artifacts,
            error=result.error,
        ),
    )
    return result


def _run_request(request: CommandRequest, emit_event: ProgressSink | None) -> CommandResult:
    if isinstance(request, ShowDistributionRequest):
        return show_distribution(request)
    if isinstance(request, DictionaryStageRequest):
        return run_dictionary_stage(request, emit_event)
    if isinstance(request, MigrateGradeWorkspacesRequest):
        return migrate_grade_workspaces_command(request)
    if isinstance(request, DictionaryBuildRequest):
        return build_dictionary(request, emit_event)
    if isinstance(request, DictionaryRecoveryRequest):
        return recover_dictionary(request, emit_event)
    if isinstance(request, SourceMaterialParseRequest):
        return parse_one_source_material(request, emit_event)
    if isinstance(request, SourceMaterialsParseRequest):
        return parse_many_source_materials(request, emit_event)
    if isinstance(request, SourceMaterialRewriteRequest):
        return rewrite_source_material_request(request, emit_event)
    if isinstance(request, ClassMaterialPrintRequest):
        return print_class_material_request(request, emit_event)
    if isinstance(request, ClassPrintoutsRequest):
        return build_class_printouts(request, emit_event)
    if isinstance(request, AfterClassReportRequest):
        return generate_after_class_report(request, emit_event)
    if isinstance(request, ArchiveDuplicatesRequest):
        return resolve_archive_duplicate_request(request, emit_event)
    if isinstance(request, DictionarySourceDuplicatesRequest):
        return resolve_dictionary_source_duplicate_request(request)
    if isinstance(request, ArchiveRenameRequest):
        return rename_archive(request, emit_event)
    if isinstance(request, DictionarySourceRenameRequest):
        return rename_dictionary_source(request, emit_event)
    if isinstance(request, UsageSignalsRequest):
        return build_usage_signal_request(request, emit_event)
    if isinstance(request, LiveLexiconRequest):
        return update_live_lexicon_request(request, emit_event)
    raise ValueError(f"Unsupported command: {request.command_id}")


def workflow_for_request(request: CommandRequest) -> str:
    if isinstance(request, ClassPrintoutsRequest):
        return "class_printouts"
    if isinstance(request, AfterClassReportRequest):
        return "reports"
    if isinstance(request, SourceMaterialParseRequest | SourceMaterialsParseRequest | SourceMaterialRewriteRequest):
        return "source"
    return command_spec(request.command_id).group


def migrate_grade_workspaces_command(request: MigrateGradeWorkspacesRequest) -> CommandResult:
    result = migrate_grade_workspaces(apply=request.apply)
    action = "applied" if request.apply else "preview"
    return CommandResult(
        0,
        "complete",
        f"grade workspace migration {action}: {result.planned_count} planned, {result.applied_count} applied",
        artifacts={"manifest": str(result.manifest_path)},
    )


def build_dictionary(request: DictionaryBuildRequest, emit_event: ProgressSink | None) -> CommandResult:
    try:
        result = build_resumable_dictionary(
            target_grade=request.target_grade,
            input_dir=request.input_dir,
            output_dir=request.output_dir,
            reference_dictionary_path=request.reference_dictionary_path,
            include_punctuation=request.include_punctuation,
            include_numbers=request.include_numbers,
            model=request.model,
            base_url=request.base_url,
            provider=request.provider,
            force=request.force,
            progress=_phase_progress("dictionary", emit_event),
            cleanup_progress=_cleanup_progress("dictionary", emit_event, label="example cleanup"),
            dictionary_cleanup_progress=_cleanup_progress("dictionary", emit_event, label="dictionary cleanup"),
            generated_example_progress=_cleanup_progress("dictionary", emit_event, label="generated examples"),
            generated_meaning_progress=_cleanup_progress("dictionary", emit_event, label="generated meanings"),
            artifact_callback=_artifact_callback("dictionary", emit_event),
            checkpoint_status=lambda status: emit(
                emit_event,
                event(
                    "dictionary",
                    str(getattr(getattr(status, "action", ""), "value", "checkpoint") or "checkpoint"),
                    f"{getattr(status, 'key', '')} {getattr(status, 'label', '')}: {getattr(status, 'reason', '')}".strip(),
                ),
            ),
            target_checkpoint=request.target_checkpoint,
            enforce_managed_sources=request.enforce_managed_sources,
        )
    except UnresolvedGeneratedExampleFailures as error:
        detail = (
            f"stage 13 left {error.count:,} unresolved generated-example failure(s); "
            f"failures: {error.path}"
        )
        return CommandResult(1, "failed", detail, {"failures": str(error.path)}, detail)
    artifacts = {
        "output_dir": str(result.pipeline_result.output_dir),
    }
    final_path = artifact_path(request.output_dir, ArtifactId.FINAL_DICTIONARY)
    if request.target_checkpoint == "15":
        artifacts["final_dictionary"] = str(final_path)
    label = "full dictionary" if request.target_checkpoint == "15" else "dictionary prep"
    detail = f"{label} complete: {result.pipeline_result.vocabulary_terms:,} vocabulary term(s)"
    return CommandResult(0, "complete", detail, artifacts)


def recover_dictionary(request: DictionaryRecoveryRequest, emit_event: ProgressSink | None) -> CommandResult:
    run_saved_dictionary_cleanup(
        output_dir=request.output_dir,
        clean_example_sentences=request.clean_example_sentences,
        clean_dictionary_candidates=request.clean_dictionary_candidates,
        generate_missing_examples=request.generate_missing_examples,
        retry_generated_examples=request.retry_generated_examples,
        sentence_cleanup_model=request.model,
        sentence_cleanup_base_url=request.base_url,
        sentence_cleanup_provider=request.provider,
        progress=_phase_progress("dictionary", emit_event),
        cleanup_progress=_cleanup_progress("dictionary", emit_event, label="example cleanup"),
        dictionary_cleanup_progress=_cleanup_progress("dictionary", emit_event, label="dictionary cleanup"),
        generated_example_progress=_cleanup_progress("dictionary", emit_event, label="generated examples"),
        artifact_callback=_artifact_callback("dictionary", emit_event),
    )
    pipeline_result = load_pipeline_result_from_output(request.output_dir)
    return CommandResult(
        0,
        "complete",
        f"dictionary recovery complete: {pipeline_result.vocabulary_terms:,} vocabulary term(s)",
        {"output_dir": str(request.output_dir)},
    )


def show_distribution(request: ShowDistributionRequest) -> CommandResult:
    if not request.jsonl_path.is_file():
        detail = (
            f"cannot read distribution source: {request.jsonl_path}; "
            f"run --generate-missing-meanings or pass an explicit path"
        )
        return CommandResult(1, "failed", detail, error=detail)
    entries = load_dictionary_candidates(request.jsonl_path)
    return CommandResult(
        0,
        "complete",
        f"distribution loaded: {len(entries):,} headword(s)",
        {"jsonl": str(request.jsonl_path), "row_count": str(len(entries))},
    )


def run_dictionary_stage(request: DictionaryStageRequest, emit_event: ProgressSink | None) -> CommandResult:
    if request.command_id == CommandId.LINK_CLEANED_EXAMPLES:
        outputs = write_linked_dictionary_candidates_from_saved(request.output_dir, require_inputs=True)
        assert outputs is not None
        refresh_saved_output_metadata(request.output_dir, linked_dictionary_outputs=outputs)
        return CommandResult(
            0,
            "complete",
            f"linked examples: {outputs.linked_count:,} replaced, {outputs.unlinked_count:,} pending",
            {"output": str(artifact_path(request.output_dir, ArtifactId.LINKED_DICTIONARY_CANDIDATES))},
        )
    if request.command_id == CommandId.ADD_REFERENCE_MEANINGS:
        if request.reference_dictionary_path is None:
            raise ValueError("reference dictionary path is required")
        outputs = write_meaning_dictionary_candidates_from_saved(
            request.output_dir,
            reference_dictionary_path=request.reference_dictionary_path,
        )
        return CommandResult(
            0,
            "complete",
            f"reference meanings added: {outputs.matched_count:,} matched, {outputs.unmatched_count:,} missing",
            {"output": str(artifact_path(request.output_dir, ArtifactId.MEANING_DICTIONARY_CANDIDATES))},
        )
    if request.command_id == CommandId.GENERATE_MISSING_MEANINGS:
        client = build_provider_client(
            request.provider,
            model=request.model,
            base_url=request.base_url,
            timeout_seconds=300.0,
            max_output_tokens=4096,
        )
        try:
            outputs = write_generated_meaning_dictionary_candidates_from_saved(
                request.output_dir,
                generated_meaning_client=client,
                generated_meaning_progress=_cleanup_progress("dictionary", emit_event, label="generated meanings"),
            )
        finally:
            _safe_unload(client, emit_event, workflow="dictionary")
        return CommandResult(
            0,
            "complete",
            f"missing meanings generated: {outputs.generated_count:,} generated, {outputs.missing_count:,} missing",
            {"output": str(artifact_path(request.output_dir, ArtifactId.FINAL_DICTIONARY))},
        )
    raise ValueError(f"Unsupported dictionary stage command: {request.command_id}")


def parse_one_source_material(request: SourceMaterialParseRequest, emit_event: ProgressSink | None) -> CommandResult:
    bundle = parse_source_material(
        request.source_path,
        output_root=request.output_root,
        source_id=request.source_id,
        target_grade=request.target_grade,
        parse_pdf=request.parse_pdf,
        render_dpi=request.render_dpi,
        paddleocr_vl_settings=request.paddleocr_vl_settings,
        progress=_source_material_progress("source", emit_event),
    )
    return CommandResult(
        0,
        "complete",
        f"source material parsed: {bundle.source_id}",
        {"output_dir": str(bundle.output_dir), "manifest": str(bundle.manifest_path)},
    )


def parse_many_source_materials(request: SourceMaterialsParseRequest, emit_event: ProgressSink | None) -> CommandResult:
    result = parse_source_materials(
        request.source_dir,
        output_root=request.output_root,
        target_grade=request.target_grade,
        parse_pdf=request.parse_pdf,
        render_dpi=request.render_dpi,
        paddleocr_vl_settings=request.paddleocr_vl_settings,
        progress=_source_material_progress("source", emit_event),
    )
    return CommandResult(
        0,
        "complete",
        f"source materials parsed: {len(result.bundles):,} bundle(s)",
        {"source_dir": str(result.source_dir), "output_root": str(result.output_root)},
    )


def rewrite_source_material_request(request: SourceMaterialRewriteRequest, emit_event: ProgressSink | None) -> CommandResult:
    client = build_provider_client(
        request.provider,
        model=request.model,
        base_url=request.base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        if request.command_id == CommandId.REWRITE_WHOLE_SOURCE_MATERIAL:
            outputs = rewrite_whole_source_material(
                request.parsed_path,
                output_root=request.output_root,
                client=client,
                model=request.model,
                target_grade=request.target_grade,
                progress=_source_rewrite_progress("source", emit_event),
            )
            return _rewrite_result(outputs)
        if request.command_id == CommandId.REWRITE_WHOLE_SOURCE_MATERIALS:
            outputs = rewrite_whole_source_materials(
                request.parsed_path,
                output_root=request.output_root,
                client=client,
                model=request.model,
                target_grade=request.target_grade,
                progress=_source_rewrite_progress("source", emit_event),
            )
            failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
            exit_code = 0 if failed_pages == 0 and outputs.source_outputs else 1
            return CommandResult(
                exit_code,
                "complete" if exit_code == 0 else "failed",
                f"source materials rewritten: {len(outputs.source_outputs):,} source(s), {failed_pages:,} failed page(s)",
                {"output_root": str(outputs.output_root)},
                "" if exit_code == 0 else "rewrite failed",
            )
        if request.vocabulary_source is None:
            raise ValueError(f"{request.command_id} requires a vocabulary source")
        if request.command_id == CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIAL:
            outputs = rewrite_vocabulary_guided_source_material(
                request.parsed_path,
                dictionary_path=request.vocabulary_source.path,
                vocabulary_source_kind=request.vocabulary_source.kind,
                vocabulary_source_sha256=request.vocabulary_source.sha256,
                output_root=request.output_root,
                client=client,
                model=request.model,
                target_grade=request.target_grade,
                progress=_source_rewrite_progress("source", emit_event),
            )
            return _rewrite_result(outputs)
        if request.command_id == CommandId.REWRITE_VOCABULARY_GUIDED_SOURCE_MATERIALS:
            outputs = rewrite_vocabulary_guided_source_materials(
                request.parsed_path,
                dictionary_path=request.vocabulary_source.path,
                vocabulary_source_kind=request.vocabulary_source.kind,
                vocabulary_source_sha256=request.vocabulary_source.sha256,
                output_root=request.output_root,
                client=client,
                model=request.model,
                target_grade=request.target_grade,
                progress=_source_rewrite_progress("source", emit_event),
            )
            failed_pages = sum(output.failed_page_count for output in outputs.source_outputs)
            exit_code = 0 if failed_pages == 0 and outputs.source_outputs else 1
            return CommandResult(
                exit_code,
                "complete" if exit_code == 0 else "failed",
                f"source materials rewritten: {len(outputs.source_outputs):,} source(s), {failed_pages:,} failed page(s)",
                {"output_root": str(outputs.output_root)},
                "" if exit_code == 0 else "rewrite failed",
            )
    finally:
        _safe_unload(client, emit_event, workflow="source")
    raise ValueError(f"Unsupported source rewrite command: {request.command_id}")


def print_class_material_request(request: ClassMaterialPrintRequest, emit_event: ProgressSink | None) -> CommandResult:
    client = build_provider_client(
        request.provider,
        model=request.model,
        base_url=request.base_url,
        timeout_seconds=1200.0,
        max_output_tokens=16384,
    )
    try:
        if request.command_id == CommandId.PRINT_CLASS_MATERIAL:
            outputs = print_class_material(
                request.rewritten_path,
                output_root=request.output_root,
                client=client,
                model=request.model,
                target_grade=request.target_grade,
                font_path=request.font_path,
                body_font_size=request.body_font_size,
                progress=_printable_progress("class", emit_event),
            )
            return _print_result(outputs)
        if request.command_id == CommandId.PRINT_CLASS_MATERIALS:
            outputs = print_class_materials(
                request.rewritten_path,
                output_root=request.output_root,
                client=client,
                model=request.model,
                target_grade=request.target_grade,
                font_path=request.font_path,
                body_font_size=request.body_font_size,
                progress=_printable_progress("class", emit_event),
            )
            failed = sum(1 for output in outputs.source_outputs if not output.accepted)
            written = sum(1 for output in outputs.source_outputs if output.accepted)
            exit_code = 0 if failed == 0 and written > 0 else 1
            return CommandResult(
                exit_code,
                "complete" if exit_code == 0 else "failed",
                f"class materials printed: written: {written:,}, failed: {failed:,}",
                {"output_root": str(outputs.output_root)},
                "" if exit_code == 0 else "print failed",
            )
    finally:
        _safe_unload(client, emit_event, workflow="class")
    raise ValueError(f"Unsupported class print command: {request.command_id}")


def build_class_printouts(request: ClassPrintoutsRequest, emit_event: ProgressSink | None) -> CommandResult:
    result = class_printout_workflow.build_class_printouts_workflow(
        source_dir=request.source_dir,
        parse_output_root=request.parse_output_root,
        parse_pdf=request.parse_pdf,
        render_dpi=request.render_dpi,
        paddleocr_vl_settings=request.paddleocr_vl_settings,
        rewrite_output_root=request.rewrite_output_root,
        vocabulary_source=request.vocabulary_source,
        print_output_root=request.print_output_root,
        target_grade=request.target_grade,
        target_profile=target_profile_for_grade(request.target_grade),
        provider=request.provider,
        model=request.model,
        base_url=request.base_url,
        font_path=request.font_path,
        body_font_size=request.body_font_size,
        force_class_printouts=request.force_class_printouts,
        parse_source_material_func=parse_source_material,
        rewrite_source_material_func=rewrite_vocabulary_guided_source_material,
        print_class_material_func=print_class_material,
        completion_client_factory=build_provider_client,
        model_unload_callback=lambda client: _safe_unload(client, emit_event, workflow="class_printouts"),
        source_material_progress=_source_material_progress("class_printouts", emit_event),
        source_rewrite_progress=_source_rewrite_progress("class_printouts", emit_event),
        printable_progress=_printable_progress("class_printouts", emit_event),
    )
    if result.status == "no_sources":
        detail = f"no class source materials found in {request.source_dir}"
        return CommandResult(1, "failed", detail, error=detail)
    artifacts = {
        "parsed": str(result.parse_output_root),
        "rewritten": str(result.rewrite_output_root),
        "printouts": str(result.print_output_root),
        "ledger": str(result.ledger_path),
    }
    detail = (
        f"{result.status}: {result.docx_written:,} DOCX written, "
        f"{result.failed_rewrites:,} rewrite failure(s), {result.failed_prints:,} print failure(s)"
    )
    return CommandResult(result.exit_code, "complete" if result.exit_code == 0 else "failed", detail, artifacts)


def generate_after_class_report(request: AfterClassReportRequest, emit_event: ProgressSink | None) -> CommandResult:
    client = build_provider_client(
        request.provider,
        model=request.model,
        base_url=request.base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        outputs = generate_after_class_reading_report(
            request.printout_path,
            output_root=request.output_root,
            client=client,
            model=request.model,
            target_grade=request.target_grade,
            font_path=request.font_path,
            progress=_after_class_report_progress("reports", emit_event),
        )
    finally:
        _safe_unload(client, emit_event, workflow="reports")
    artifacts = {"output_dir": str(outputs.output_dir), "heuristic_analysis": str(outputs.heuristic_analysis_dir)}
    if outputs.docx_path is not None:
        artifacts["docx"] = str(outputs.docx_path)
    artifacts["selected_vocabulary"] = str(outputs.selected_vocabulary_count)
    detail = (
        f"after-class reading report generated: {outputs.selected_vocabulary_count} vocabulary item(s)"
        if outputs.accepted
        else f"after-class reading report failed: {outputs.selected_vocabulary_count} vocabulary item(s)"
    )
    return CommandResult(0 if outputs.accepted else 1, "complete" if outputs.accepted else "failed", detail, artifacts)


def resolve_archive_duplicate_request(request: ArchiveDuplicatesRequest, emit_event: ProgressSink | None) -> CommandResult:
    validate_managed_class_archive_grades(request.archive_root, target_grade=request.target_grade)
    lazy_client: _LazyProviderClient | None = None
    if request.include_near_duplicates:
        lazy_client = _LazyProviderClient(
            provider=request.provider,
            model=request.model,
            base_url=request.base_url,
            timeout_seconds=300.0,
            max_output_tokens=1024,
        )
    try:
        preview = resolve_archive_duplicates(
            request.archive_root,
            apply=False,
            include_near_duplicates=request.include_near_duplicates,
            near_duplicate_client=lazy_client,
            near_duplicate_model=request.model if request.include_near_duplicates else None,
        )
    finally:
        if lazy_client is not None and lazy_client.client is not None:
            _safe_unload(lazy_client.client, emit_event, workflow="archive")
    result = apply_archive_duplicate_resolution_result(preview) if request.apply else preview
    action = "applied" if result.applied else "preview"
    return CommandResult(
        0,
        "complete",
        f"archive duplicates resolved: {action}: {result.duplicate_group_count} duplicate group(s)",
        {
            "archive_root": str(result.archive_root),
            "quarantine_root": str(result.quarantine_root),
            "duplicate_groups": str(result.duplicate_group_count),
            "near_duplicate_reviews": str(len(result.near_duplicate_reviews)),
        },
    )


def resolve_dictionary_source_duplicate_request(request: DictionarySourceDuplicatesRequest) -> CommandResult:
    validate_managed_dictionary_source_grades(request.archive_root, target_grade=request.target_grade)
    preview = resolve_dictionary_source_duplicates(request.archive_root, apply=False)
    result = apply_dictionary_source_duplicate_resolution_result(preview) if request.apply else preview
    action = "applied" if result.applied else "preview"
    return CommandResult(
        0,
        "complete",
        f"dictionary source duplicate resolution {action}: {result.duplicate_group_count} duplicate group(s)",
        {
            "archive_root": str(result.archive_root),
            "quarantine_root": str(result.quarantine_root),
            "duplicate_groups": str(result.duplicate_group_count),
        },
    )


def rename_archive(request: ArchiveRenameRequest, emit_event: ProgressSink | None) -> CommandResult:
    validate_managed_class_archive_grades(request.archive_root, target_grade=request.target_grade)
    client = build_provider_client(
        request.provider,
        model=request.model,
        base_url=request.base_url,
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        result = rename_class_session_archive(
            request.archive_root,
            client=client,
            model=request.model,
            target_grade=request.target_grade,
            apply=request.apply,
            progress=lambda detail: emit(emit_event, event("archive", "rename", detail)),
        )
    finally:
        _safe_unload(client, emit_event, workflow="archive")
    return CommandResult(
        0,
        "complete",
        f"class-session archive rename complete: {result.planned_count} planned, {result.renamed_count} renamed",
        {"archive_root": str(result.archive_root), "ledger": str(result.ledger_path)},
    )


def rename_dictionary_source(request: DictionarySourceRenameRequest, emit_event: ProgressSink | None) -> CommandResult:
    validate_managed_dictionary_source_grades(request.archive_root, target_grade=request.target_grade)
    client = build_provider_client(
        request.provider,
        model=request.model,
        base_url=request.base_url,
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        result = rename_dictionary_source_archive(
            request.archive_root,
            client=client,
            model=request.model,
            target_grade=request.target_grade,
            apply=request.apply,
            progress=lambda detail: emit(emit_event, event("dictionary", "rename", detail)),
        )
    finally:
        _safe_unload(client, emit_event, workflow="dictionary")
    exit_code = 0 if not request.apply or result.blocked_count == 0 else 1
    return CommandResult(
        exit_code,
        "complete" if exit_code == 0 else "failed",
        f"dictionary source rename {'applied' if request.apply else 'preview'}: {result.planned_count} planned, {result.renamed_count} renamed",
        {"archive_root": str(result.archive_root), "ledger": str(result.ledger_path)},
    )


def build_usage_signal_request(request: UsageSignalsRequest, emit_event: ProgressSink | None) -> CommandResult:
    lazy_client = _LazyProviderClient(
        provider=request.provider,
        model=request.model,
        base_url=request.base_url,
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        result = build_usage_signals(
            archive_root=request.archive_root,
            output_root=request.output_root,
            target_grade=request.target_grade,
            include_punctuation=request.include_punctuation,
            include_numbers=request.include_numbers,
            near_duplicate_client=lazy_client,
            near_duplicate_model=request.model,
            progress=_phase_progress("archive", emit_event),
            artifact_callback=_artifact_callback("archive", emit_event),
        )
    finally:
        if lazy_client.client is not None:
            _safe_unload(lazy_client.client, emit_event, workflow="archive")
    return CommandResult(
        0,
        "complete",
        f"usage signals built: {result.documents:,} document(s), {result.vocabulary_terms:,} vocabulary term(s)",
        {"output_root": str(result.output_root)},
    )


def update_live_lexicon_request(request: LiveLexiconRequest, emit_event: ProgressSink | None) -> CommandResult:
    result = update_live_lexicon(
        target_grade=request.target_grade,
        canonical_dictionary_path=request.canonical_dictionary_path,
        usage_candidates_path=request.usage_candidates_path,
        usage_summary_path=request.usage_summary_path,
        live_lexicon_path=request.live_lexicon_path,
        supplemental_lexicon_path=request.supplemental_lexicon_path,
        unmatched_usage_output_path=request.unmatched_usage_output_path,
        progress=_phase_progress("archive", emit_event),
    )
    return CommandResult(
        0,
        "complete",
        f"live lexicon updated: {result.matched_count:,} matched, {result.unmatched_count:,} unmatched",
        {
            "live_lexicon": str(result.live_lexicon_path),
            "supplemental_lexicon": str(result.supplemental_lexicon_path),
            "unmatched_usage": str(result.unmatched_usage_output_path),
        },
    )


def load_dictionary_candidates(jsonl_path: Path) -> list[dict[str, object]]:
    rows = read_jsonl(jsonl_path)
    return [dict(row) for row in rows]


def generated_example_failure_count(output_dir: Path) -> int:
    return _generated_example_failure_count(output_dir)


def dictionary_distribution_summary(jsonl_path: Path, model: str) -> tuple[str, Sequence[tuple[str, str]], list[dict[str, object]]]:
    entries = load_dictionary_candidates(jsonl_path)
    return (
        distribution_stage_for_path(jsonl_path),
        (
            ("relics", f"{len(entries):,} headwords"),
            ("model", model),
            ("status", "replay // no llm calls"),
        ),
        entries,
    )


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


def _phase_progress(workflow: str, emit_event: ProgressSink | None):
    def progress(phase: str, detail: str) -> None:
        emit(emit_event, event(workflow, phase, detail))

    return progress


def _cleanup_progress(workflow: str, emit_event: ProgressSink | None, *, label: str):
    def progress(value: CleanupBatchProgress) -> None:
        phase = str(getattr(value, "phase", "cleanup"))
        batch_index = int(getattr(value, "batch_index", 0))
        completed_batches = int(getattr(value, "completed_batches", 0))
        total_batches = int(getattr(value, "total_batches", 0))
        completed_rows = int(getattr(value, "completed_rows", 0))
        total_rows = int(getattr(value, "total_rows", 0))
        detail = f"{label}: batch {batch_index}/{total_batches}, rows {completed_rows}/{total_rows}"
        emit(
            emit_event,
            event(
                workflow,
                phase,
                detail,
                current=completed_rows,
                total=total_rows,
                payload={
                    "kind": "cleanup_progress",
                    "label": label,
                    "phase": phase,
                    "batch_index": batch_index,
                    "total_batches": total_batches,
                    "completed_batches": completed_batches,
                    "completed_rows": completed_rows,
                    "total_rows": total_rows,
                    "current_batch_size": int(getattr(value, "current_batch_size", 0)),
                },
            ),
        )

    return progress


def _artifact_callback(workflow: str, emit_event: ProgressSink | None):
    def callback(stage: str, entries: Sequence[Mapping[str, object]]) -> None:
        emit(emit_event, event(workflow, "artifact", f"{stage}: {len(entries):,} row(s)"))

    return callback


def _source_material_progress(workflow: str, emit_event: ProgressSink | None):
    def progress(value: object) -> None:
        source_path = Path(str(getattr(value, "source_path", "")))
        raw_detail = str(getattr(value, "detail", ""))
        detail = raw_detail
        if source_path.name:
            detail = f"{source_path.name}: {detail}"
        phase = str(getattr(value, "phase", "parse"))
        total_pages = int(getattr(value, "total_pages", 0))
        completed_pages = int(getattr(value, "completed_pages", 0))
        source_index = int(getattr(value, "source_index", 0))
        total_sources = int(getattr(value, "total_sources", 0))
        emit(
            emit_event,
            event(
                workflow,
                phase,
                detail,
                current=completed_pages if total_pages else source_index,
                total=total_pages if total_pages else total_sources,
                payload={
                    "kind": "source_material_progress",
                    "phase": phase,
                    "source_path": str(source_path),
                    "parser": str(getattr(value, "parser", "")),
                    "detail": raw_detail,
                    "source_index": source_index,
                    "total_sources": total_sources,
                    "completed_pages": completed_pages,
                    "total_pages": total_pages,
                },
            ),
        )

    return progress


def _source_rewrite_progress(workflow: str, emit_event: ProgressSink | None):
    def progress(value: object) -> None:
        raw_detail = str(getattr(value, "detail", ""))
        detail = raw_detail
        source_id = str(getattr(value, "source_id", ""))
        if source_id:
            detail = f"{source_id}: {detail}"
        phase = str(getattr(value, "phase", "rewrite"))
        output_dir = getattr(value, "output_dir", None)
        emit(
            emit_event,
            event(
                workflow,
                phase,
                detail,
                current=int(getattr(value, "source_index", 0)),
                total=int(getattr(value, "total_sources", 0)),
                artifacts=_path_artifacts(output_dir=output_dir),
                payload={
                    "kind": "source_rewrite_progress",
                    "phase": phase,
                    "source_id": source_id,
                    "detail": raw_detail,
                    "source_index": int(getattr(value, "source_index", 0)),
                    "total_sources": int(getattr(value, "total_sources", 0)),
                    "rewrite_mode": str(getattr(value, "rewrite_mode", "")),
                    "output_dir": "" if output_dir is None else str(output_dir),
                    "rewritten_pages": int(getattr(value, "rewritten_pages", 0)),
                    "failed_pages": int(getattr(value, "failed_pages", 0)),
                    "selected_vocabulary": int(getattr(value, "selected_vocabulary", 0)),
                },
            ),
        )

    return progress


def _printable_progress(workflow: str, emit_event: ProgressSink | None):
    def progress(value: object) -> None:
        raw_detail = str(getattr(value, "detail", ""))
        detail = raw_detail
        source_id = str(getattr(value, "source_id", ""))
        if source_id:
            detail = f"{source_id}: {detail}"
        phase = str(getattr(value, "phase", "print"))
        output_dir = getattr(value, "output_dir", None)
        emit(
            emit_event,
            event(
                workflow,
                phase,
                detail,
                current=int(getattr(value, "source_index", 0)),
                total=int(getattr(value, "total_sources", 0)),
                artifacts=_path_artifacts(output_dir=output_dir),
                payload={
                    "kind": "printable_progress",
                    "phase": phase,
                    "source_id": source_id,
                    "detail": raw_detail,
                    "source_index": int(getattr(value, "source_index", 0)),
                    "total_sources": int(getattr(value, "total_sources", 0)),
                    "output_dir": "" if output_dir is None else str(output_dir),
                    "accepted": getattr(value, "accepted", None),
                    "selected_image_count": int(getattr(value, "selected_image_count", 0)),
                    "body_font_size_pt": getattr(value, "body_font_size_pt", None),
                    "failure_reason": str(getattr(value, "failure_reason", "")),
                },
            ),
        )

    return progress


def _after_class_report_progress(workflow: str, emit_event: ProgressSink | None):
    def progress(value: object) -> None:
        raw_detail = str(getattr(value, "detail", ""))
        detail = raw_detail
        source_id = str(getattr(value, "source_id", ""))
        if source_id:
            detail = f"{source_id}: {detail}"
        phase = str(getattr(value, "phase", "report"))
        output_dir = getattr(value, "output_dir", None)
        heuristic_analysis_dir = getattr(value, "heuristic_analysis_dir", None)
        docx_path = getattr(value, "docx_path", None)
        emit(
            emit_event,
            event(
                workflow,
                phase,
                detail,
                artifacts=_path_artifacts(
                    output_dir=output_dir,
                    heuristic_analysis_dir=heuristic_analysis_dir,
                    docx_path=docx_path,
                ),
                payload={
                    "kind": "after_class_report_progress",
                    "phase": phase,
                    "source_id": source_id,
                    "detail": raw_detail,
                    "output_dir": "" if output_dir is None else str(output_dir),
                    "heuristic_analysis_dir": "" if heuristic_analysis_dir is None else str(heuristic_analysis_dir),
                    "accepted": getattr(value, "accepted", None),
                    "selected_vocabulary_count": int(getattr(value, "selected_vocabulary_count", 0)),
                    "docx_path": "" if docx_path is None else str(docx_path),
                    "failure_reason": str(getattr(value, "failure_reason", "")),
                },
            ),
        )

    return progress


def _rewrite_result(outputs: object) -> CommandResult:
    failed_pages = int(getattr(outputs, "failed_page_count"))
    exit_code = 0 if failed_pages == 0 else 1
    return CommandResult(
        exit_code,
        "complete" if exit_code == 0 else "failed",
        f"source material rewritten: {getattr(outputs, 'source_id')}, {failed_pages:,} failed page(s)",
        {"output_dir": str(getattr(outputs, "output_dir"))},
        "" if exit_code == 0 else "rewrite failed",
    )


def _print_result(outputs: object) -> CommandResult:
    accepted = bool(getattr(outputs, "accepted"))
    artifacts = {"output_dir": str(getattr(outputs, "output_dir"))}
    if accepted:
        artifacts["docx"] = str(Path(str(getattr(outputs, "output_dir"))) / "material.docx")
    detail = (
        f"printable class material written: {getattr(outputs, 'source_id')}"
        if accepted
        else f"printable class material failed: {getattr(outputs, 'source_id')}"
    )
    return CommandResult(0 if accepted else 1, "complete" if accepted else "failed", detail, artifacts)


def _path_artifacts(**paths: object) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key, path in paths.items():
        if path is None:
            continue
        text = str(path)
        if text:
            artifacts[key] = text
    return artifacts


def _safe_unload(client: SupportsCompletion, emit_event: ProgressSink | None, *, workflow: str) -> None:
    try:
        unload_model_if_supported(client)
    except Exception as error:  # pragma: no cover - defensive cleanup path
        emit(emit_event, event(workflow, "warning", f"could not unload model: {error}"))


def result_to_json(result: CommandResult) -> str:
    return json.dumps(
        {
            "exit_code": result.exit_code,
            "status": result.status,
            "detail": result.detail,
            "artifacts": dict(result.artifacts),
            "error": result.error,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
