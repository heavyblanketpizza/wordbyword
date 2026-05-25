from __future__ import annotations

from collections.abc import Callable, Mapping

from wordbyword.after_class_reports import AfterClassReportError, generate_after_class_reading_report
from wordbyword.class_archive.archive import (
    ClassSessionArchiveError,
    rename_class_session_archive,
    resolve_archive_duplicates,
)
from wordbyword.class_archive.live_lexicon import LiveLexiconUpdateError, update_live_lexicon
from wordbyword.class_archive.usage_signals import UsageSignalBuildError, build_usage_signals
from wordbyword.classroom_printout_studio import class_printout_workflow
from wordbyword.classroom_printout_studio.class_printing.printable import PrintableMaterialError
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import SourceRewriteError
from wordbyword.classroom_printout_studio.source_parsing.materials import SourceMaterialParseError
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.application.commands import (
    AfterClassReportRequest,
    ArchiveDuplicatesRequest,
    ArchiveRenameRequest,
    ClassPrintoutsRequest,
    CommandRequest,
    DictionaryBuildRequest,
    LiveLexiconRequest,
    UsageSignalsRequest,
    build_gui_request,
)
from wordbyword.application.workspace import LazyCompletionClient, discover_paddleocr_vl_sidecar_env
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.llm import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    build_provider_client,
    unload_model_if_supported,
)
from wordbyword.dictionary_build.resumable import (
    UnresolvedGeneratedExampleFailures,
    build_resumable_dictionary,
)
from wordbyword.grade_profiles import target_profile_for_grade
from wordbyword.gui.job_events import (
    GuiJobEvent,
    after_class_report_event,
    artifact_event,
    checkpoint_event,
    cleanup_event,
    error_event,
    event,
    phase_detail_event,
    printable_event,
    result_event,
    source_material_event,
    source_rewrite_event,
    stage_event,
)

EmitEvent = Callable[[GuiJobEvent], None]


def run_workflow(workflow: str, values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    return run_gui_request(build_gui_request(workflow, values), emit)


def run_gui_request(request: CommandRequest, emit: EmitEvent) -> GuiJobEvent:
    if isinstance(request, DictionaryBuildRequest):
        return run_dictionary(request, emit)
    if isinstance(request, ClassPrintoutsRequest):
        return run_class_printouts(request, emit)
    if isinstance(request, ArchiveRenameRequest):
        return run_archive_rename(request, emit)
    if isinstance(request, ArchiveDuplicatesRequest):
        return run_archive_duplicates(request, emit)
    if isinstance(request, UsageSignalsRequest):
        return run_usage_signals(request, emit)
    if isinstance(request, LiveLexiconRequest):
        return run_live_lexicon(request, emit)
    if isinstance(request, AfterClassReportRequest):
        return run_after_class_report(request, emit)
    raise ValueError(f"Command is not available in the GUI adapter: {request.command_id}")


def run_dictionary(request: DictionaryBuildRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "dictionary"
    emit(event(workflow, "start", f"building dictionary checkpoint {request.target_checkpoint}"))
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
            progress=lambda phase, detail: emit(stage_event(workflow, phase, detail)),
            cleanup_progress=lambda progress: emit(cleanup_event(workflow, progress, label="example cleanup")),
            dictionary_cleanup_progress=lambda progress: emit(cleanup_event(workflow, progress, label="dictionary cleanup")),
            generated_example_progress=lambda progress: emit(cleanup_event(workflow, progress, label="generated examples")),
            generated_meaning_progress=lambda progress: emit(cleanup_event(workflow, progress, label="generated meanings")),
            artifact_callback=lambda stage, entries: emit(artifact_event(workflow, stage, entries)),
            checkpoint_status=lambda status: emit(checkpoint_event(workflow, status)),
            target_checkpoint=request.target_checkpoint,
        )
    except UnresolvedGeneratedExampleFailures as error:
        failures = str(error.path)
        raise RuntimeError(
            f"Stage 13 left {error.count:,} unresolved generated-example failure(s). "
            f"Failures: {failures}. Repair in CLI, then run meaning stages."
        ) from error
    artifacts = {
        "output_dir": str(result.pipeline_result.output_dir),
        "final_dictionary": str(artifact_path(request.output_dir, ArtifactId.FINAL_DICTIONARY)),
    }
    detail = (
        f"{'Full dictionary' if request.target_checkpoint == '15' else 'Dictionary prep'} complete: "
        f"{result.pipeline_result.vocabulary_terms:,} vocabulary term(s)"
    )
    return result_event(workflow, detail, artifacts)


def run_class_printouts(request: ClassPrintoutsRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "class_printouts"
    emit(event(workflow, "start", "building class printouts"))
    result = class_printout_workflow.build_class_printouts_workflow(
        source_dir=request.source_dir,
        parse_output_root=request.parse_output_root,
        parse_pdf=request.parse_pdf,
        render_dpi=request.render_dpi,
        paddleocr_vl_settings=request.paddleocr_vl_settings or _paddleocr_settings(request.parse_pdf),
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
        model_unload_callback=lambda client: _safe_unload(client, emit, workflow),
        source_material_progress=lambda progress: emit(source_material_event(workflow, progress)),
        source_rewrite_progress=lambda progress: emit(source_rewrite_event(workflow, progress)),
        printable_progress=lambda progress: emit(printable_event(workflow, progress)),
    )
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
    if result.exit_code != 0:
        return event(workflow, "complete", detail, status="failed", artifacts=artifacts, error=detail)
    return result_event(workflow, detail, artifacts)


def run_archive_rename(request: ArchiveRenameRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "archive"
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
            apply=request.apply,
            progress=lambda detail: emit(phase_detail_event(workflow, "rename", detail)),
        )
    finally:
        _safe_unload(client, emit, workflow)
    artifacts = {"archive_root": str(result.archive_root), "ledger": str(result.ledger_path)}
    detail = f"rename {'applied' if request.apply else 'preview'}: {result.planned_count} planned, {result.renamed_count} renamed"
    return result_event(workflow, detail, artifacts)


def run_archive_duplicates(request: ArchiveDuplicatesRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "archive"
    lazy_client: LazyCompletionClient | None = None
    if request.include_near_duplicates:
        lazy_client = LazyCompletionClient(
            provider=request.provider,
            model=request.model,
            base_url=request.base_url,
            timeout_seconds=300.0,
            max_output_tokens=1024,
        )
    try:
        emit(phase_detail_event(workflow, "scan", "scanning archive duplicates"))
        result = resolve_archive_duplicates(
            request.archive_root,
            apply=request.apply,
            include_near_duplicates=request.include_near_duplicates,
            near_duplicate_client=lazy_client,
            near_duplicate_model=request.model if request.include_near_duplicates else None,
        )
    finally:
        if lazy_client is not None and lazy_client.client is not None:
            _safe_unload(lazy_client.client, emit, workflow)
    artifacts = {"archive_root": str(result.archive_root), "quarantine_root": str(result.quarantine_root)}
    detail = (
        f"duplicates {'applied' if result.applied else 'preview'}: "
        f"{result.duplicate_group_count} duplicate group(s)"
    )
    return result_event(workflow, detail, artifacts)


def run_usage_signals(request: UsageSignalsRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "archive"
    lazy_client = LazyCompletionClient(
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
            progress=lambda phase, detail: emit(phase_detail_event(workflow, phase, detail)),
            artifact_callback=lambda stage, entries: emit(artifact_event(workflow, stage, entries)),
        )
    finally:
        if lazy_client.client is not None:
            _safe_unload(lazy_client.client, emit, workflow)
    artifacts = {"output_root": str(result.output_root)}
    detail = f"usage signals built: {result.documents:,} document(s), {result.vocabulary_terms:,} vocabulary term(s)"
    return result_event(workflow, detail, artifacts)


def run_live_lexicon(request: LiveLexiconRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "archive"
    result = update_live_lexicon(
        target_grade=request.target_grade,
        canonical_dictionary_path=request.canonical_dictionary_path,
        usage_candidates_path=request.usage_candidates_path,
        usage_summary_path=request.usage_summary_path,
        live_lexicon_path=request.live_lexicon_path,
        supplemental_lexicon_path=request.supplemental_lexicon_path,
        unmatched_usage_output_path=request.unmatched_usage_output_path,
        progress=lambda phase, detail: emit(phase_detail_event(workflow, phase, detail)),
    )
    artifacts = {
        "live_lexicon": str(result.live_lexicon_path),
        "supplemental_lexicon": str(result.supplemental_lexicon_path),
        "unmatched_usage": str(result.unmatched_usage_output_path),
    }
    detail = f"live lexicon updated: {result.matched_count:,} matched, {result.unmatched_count:,} unmatched"
    return result_event(workflow, detail, artifacts)


def run_after_class_report(request: AfterClassReportRequest, emit: EmitEvent) -> GuiJobEvent:
    workflow = "reports"
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
            progress=lambda progress: emit(after_class_report_event(workflow, progress)),
        )
    finally:
        _safe_unload(client, emit, workflow)
    artifacts = {
        "output_dir": str(outputs.output_dir),
        "heuristic_analysis": str(outputs.heuristic_analysis_dir),
    }
    if outputs.docx_path is not None:
        artifacts["docx"] = str(outputs.docx_path)
    detail = f"report {'written' if outputs.accepted else 'failed'}: {outputs.selected_vocabulary_count} vocabulary item(s)"
    if not outputs.accepted:
        return event(
            workflow,
            "complete",
            detail,
            status="failed",
            artifacts=artifacts,
            error=outputs.failure_reason or detail,
        )
    return result_event(workflow, detail, artifacts)


def run_with_error_event(workflow: str, values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    try:
        return run_workflow(workflow, values, emit)
    except (
        ValueError,
        RuntimeError,
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
    ) as error:
        return error_event(workflow, "workflow failed", error)


def _paddleocr_settings(parse_pdf: bool) -> PaddleOCRVLSettings | None:
    if not parse_pdf:
        return None
    sidecar_env = discover_paddleocr_vl_sidecar_env()
    if sidecar_env is None:
        return PaddleOCRVLSettings()
    return PaddleOCRVLSettings(sidecar_env=sidecar_env.as_posix())


def _safe_unload(client: SupportsCompletion, emit: EmitEvent, workflow: str) -> None:
    try:
        unload_model_if_supported(client)
    except Exception as error:  # pragma: no cover - defensive cleanup path
        emit(phase_detail_event(workflow, "warning", f"could not unload model: {error}"))
