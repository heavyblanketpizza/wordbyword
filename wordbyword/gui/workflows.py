from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

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
from wordbyword.classroom_printout_studio.source_parsing.materials import SourceMaterialParseError, sha256_file
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.cli.app import discover_paddleocr_vl_sidecar_env
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_path
from wordbyword.dictionary_build.llm import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    ollama_client,
    unload_model_if_supported,
)
from wordbyword.dictionary_build.resumable import (
    UnresolvedGeneratedExampleFailures,
    build_resumable_dictionary,
)
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


class LazyOllamaCompletionClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str | None,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.client: SupportsCompletion | None = None

    def complete(self, *args: object, **kwargs: object) -> object:
        if self.client is None:
            self.client = ollama_client(
                model=self.model,
                base_url=self.base_url,
                timeout_seconds=self.timeout_seconds,
                max_output_tokens=self.max_output_tokens,
            )
        return self.client.complete(*args, **kwargs)  # type: ignore[arg-type]


def run_workflow(workflow: str, values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    if workflow == "dictionary_build":
        return run_dictionary(values, emit, target_checkpoint="15")
    if workflow == "dictionary_prep":
        return run_dictionary(values, emit, target_checkpoint="09")
    if workflow == "class_printouts":
        return run_class_printouts(values, emit)
    if workflow == "archive_rename_preview":
        return run_archive_rename(values, emit, apply=False)
    if workflow == "archive_rename_apply":
        return run_archive_rename(values, emit, apply=True)
    if workflow == "archive_duplicates_preview":
        return run_archive_duplicates(values, emit, apply=False)
    if workflow == "archive_duplicates_apply":
        return run_archive_duplicates(values, emit, apply=True)
    if workflow == "usage_signals":
        return run_usage_signals(values, emit)
    if workflow == "live_lexicon":
        return run_live_lexicon(values, emit)
    if workflow == "after_class_report":
        return run_after_class_report(values, emit)
    raise ValueError(f"Unknown GUI workflow: {workflow}")


def run_dictionary(values: Mapping[str, object], emit: EmitEvent, *, target_checkpoint: str) -> GuiJobEvent:
    workflow = "dictionary"
    input_dir = _path(values, "dictionary_input_dir")
    output_dir = _path(values, "dictionary_output_dir")
    reference_dictionary_path = _path(values, "reference_dictionary_path")
    model = _text(values, "model")
    base_url = _optional_text(values, "base_url")
    emit(event(workflow, "start", f"building dictionary checkpoint {target_checkpoint}"))
    try:
        result = build_resumable_dictionary(
            input_dir=input_dir,
            output_dir=output_dir,
            reference_dictionary_path=reference_dictionary_path,
            include_punctuation=_bool(values, "include_punctuation"),
            include_numbers=_bool(values, "include_numbers"),
            model=model,
            base_url=base_url,
            force=_bool(values, "dictionary_force"),
            progress=lambda phase, detail: emit(stage_event(workflow, phase, detail)),
            cleanup_progress=lambda progress: emit(cleanup_event(workflow, progress, label="example cleanup")),
            dictionary_cleanup_progress=lambda progress: emit(cleanup_event(workflow, progress, label="dictionary cleanup")),
            generated_example_progress=lambda progress: emit(cleanup_event(workflow, progress, label="generated examples")),
            generated_meaning_progress=lambda progress: emit(cleanup_event(workflow, progress, label="generated meanings")),
            artifact_callback=lambda stage, entries: emit(artifact_event(workflow, stage, entries)),
            checkpoint_status=lambda status: emit(checkpoint_event(workflow, status)),
            target_checkpoint=target_checkpoint,
        )
    except UnresolvedGeneratedExampleFailures as error:
        failures = str(error.path)
        raise RuntimeError(
            f"Stage 13 left {error.count:,} unresolved generated-example failure(s). "
            f"Failures: {failures}. Repair in CLI, then run meaning stages."
        ) from error
    artifacts = {
        "output_dir": str(result.pipeline_result.output_dir),
        "final_dictionary": str(artifact_path(output_dir, ArtifactId.FINAL_DICTIONARY)),
    }
    detail = (
        f"{'Full dictionary' if target_checkpoint == '15' else 'Dictionary prep'} complete: "
        f"{result.pipeline_result.vocabulary_terms:,} vocabulary term(s)"
    )
    return result_event(workflow, detail, artifacts)


def run_class_printouts(values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    workflow = "class_printouts"
    emit(event(workflow, "start", "building class printouts"))
    parse_pdf = not _bool(values, "skip_pdf_parse")
    result = class_printout_workflow.build_class_printouts_workflow(
        source_dir=_path(values, "class_source_dir"),
        parse_output_root=_path(values, "source_output_root"),
        parse_pdf=parse_pdf,
        render_dpi=_int(values, "pdf_render_dpi", 144),
        paddleocr_vl_settings=_paddleocr_settings(parse_pdf),
        rewrite_output_root=_path(values, "rewrite_output_root"),
        vocabulary_source=_rewrite_vocabulary_source(values),
        print_output_root=_path(values, "print_output_root"),
        model=_text(values, "model"),
        base_url=_optional_text(values, "base_url"),
        font_path=_optional_path(values, "print_font_path"),
        body_font_size=_optional_int(values, "print_body_font_size"),
        force_class_printouts=_bool(values, "force_class_printouts"),
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


def run_archive_rename(values: Mapping[str, object], emit: EmitEvent, *, apply: bool) -> GuiJobEvent:
    workflow = "archive"
    model = _text(values, "model")
    client = ollama_client(
        model=model,
        base_url=_optional_text(values, "base_url"),
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        result = rename_class_session_archive(
            _path(values, "archive_root"),
            client=client,
            model=model,
            apply=apply,
            progress=lambda detail: emit(phase_detail_event(workflow, "rename", detail)),
        )
    finally:
        _safe_unload(client, emit, workflow)
    artifacts = {"archive_root": str(result.archive_root), "ledger": str(result.ledger_path)}
    detail = f"rename {'applied' if apply else 'preview'}: {result.planned_count} planned, {result.renamed_count} renamed"
    return result_event(workflow, detail, artifacts)


def run_archive_duplicates(values: Mapping[str, object], emit: EmitEvent, *, apply: bool) -> GuiJobEvent:
    workflow = "archive"
    include_near_duplicates = _bool(values, "include_near_duplicates")
    lazy_client: LazyOllamaCompletionClient | None = None
    if include_near_duplicates:
        lazy_client = LazyOllamaCompletionClient(
            model=_text(values, "model"),
            base_url=_optional_text(values, "base_url"),
            timeout_seconds=300.0,
            max_output_tokens=1024,
        )
    try:
        emit(phase_detail_event(workflow, "scan", "scanning archive duplicates"))
        result = resolve_archive_duplicates(
            _path(values, "archive_root"),
            apply=apply,
            include_near_duplicates=include_near_duplicates,
            near_duplicate_client=lazy_client,
            near_duplicate_model=_text(values, "model") if include_near_duplicates else None,
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


def run_usage_signals(values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    workflow = "archive"
    lazy_client = LazyOllamaCompletionClient(
        model=_text(values, "model"),
        base_url=_optional_text(values, "base_url"),
        timeout_seconds=300.0,
        max_output_tokens=1024,
    )
    try:
        result = build_usage_signals(
            archive_root=_path(values, "archive_root"),
            output_root=_path(values, "usage_signal_output_root"),
            include_punctuation=_bool(values, "include_punctuation"),
            include_numbers=_bool(values, "include_numbers"),
            near_duplicate_client=lazy_client,
            near_duplicate_model=_text(values, "model"),
            progress=lambda phase, detail: emit(phase_detail_event(workflow, phase, detail)),
            artifact_callback=lambda stage, entries: emit(artifact_event(workflow, stage, entries)),
        )
    finally:
        if lazy_client.client is not None:
            _safe_unload(lazy_client.client, emit, workflow)
    artifacts = {"output_root": str(result.output_root)}
    detail = f"usage signals built: {result.documents:,} document(s), {result.vocabulary_terms:,} vocabulary term(s)"
    return result_event(workflow, detail, artifacts)


def run_live_lexicon(values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    workflow = "archive"
    result = update_live_lexicon(
        canonical_dictionary_path=_path(values, "canonical_dictionary_path"),
        usage_candidates_path=_path(values, "usage_candidates_path"),
        usage_summary_path=_optional_path(values, "usage_summary_path"),
        live_lexicon_path=_path(values, "live_lexicon_path"),
        supplemental_lexicon_path=_path(values, "supplemental_lexicon_path"),
        unmatched_usage_output_path=_path(values, "unmatched_usage_output_path"),
        progress=lambda phase, detail: emit(phase_detail_event(workflow, phase, detail)),
    )
    artifacts = {
        "live_lexicon": str(result.live_lexicon_path),
        "supplemental_lexicon": str(result.supplemental_lexicon_path),
        "unmatched_usage": str(result.unmatched_usage_output_path),
    }
    detail = f"live lexicon updated: {result.matched_count:,} matched, {result.unmatched_count:,} unmatched"
    return result_event(workflow, detail, artifacts)


def run_after_class_report(values: Mapping[str, object], emit: EmitEvent) -> GuiJobEvent:
    workflow = "reports"
    model = _text(values, "model")
    client = ollama_client(
        model=model,
        base_url=_optional_text(values, "base_url"),
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    try:
        outputs = generate_after_class_reading_report(
            _path(values, "after_class_printout_path"),
            output_root=_path(values, "after_class_report_output_root"),
            client=client,
            model=model,
            font_path=_optional_path(values, "after_class_report_font_path"),
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


def _rewrite_vocabulary_source(values: Mapping[str, object]) -> class_printout_workflow.RewriteVocabularySource:
    explicit = _optional_path(values, "rewrite_dictionary_path")
    if explicit is not None:
        return class_printout_workflow.RewriteVocabularySource(
            path=explicit,
            kind="explicit",
            sha256=_sha256_if_present(explicit),
        )
    live_lexicon_path = _path(values, "live_lexicon_path")
    if live_lexicon_path.is_file():
        return class_printout_workflow.RewriteVocabularySource(
            path=live_lexicon_path,
            kind="live_lexicon",
            sha256=_sha256_if_present(live_lexicon_path),
        )
    dictionary_path = artifact_path(_path(values, "dictionary_output_dir"), ArtifactId.FINAL_DICTIONARY)
    return class_printout_workflow.RewriteVocabularySource(
        path=dictionary_path,
        kind="dictionary_seed",
        sha256=_sha256_if_present(dictionary_path),
    )


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
        emit(phase_detail_event(workflow, "warning", f"could not unload Ollama model: {error}"))


def _sha256_if_present(path: Path) -> str:
    return sha256_file(path) if path.is_file() else ""


def _path(values: Mapping[str, object], key: str) -> Path:
    text = _text(values, key)
    return Path(text)


def _optional_path(values: Mapping[str, object], key: str) -> Path | None:
    text = _optional_text(values, key)
    return None if text is None else Path(text)


def _text(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    return "" if value is None else str(value)


def _optional_text(values: Mapping[str, object], key: str) -> str | None:
    text = _text(values, key).strip()
    return text or None


def _bool(values: Mapping[str, object], key: str) -> bool:
    value = values.get(key)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int(values: Mapping[str, object], key: str, default: int) -> int:
    try:
        return int(values.get(key, default))
    except (TypeError, ValueError):
        return default


def _optional_int(values: Mapping[str, object], key: str) -> int | None:
    value = _optional_text(values, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
