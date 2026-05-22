from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
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
from wordbyword.dictionary_build.pipeline import PipelineUsageError, run_pipeline
from wordbyword.dictionary_build.resumable import (
    CheckpointStatus,
    UnresolvedGeneratedExampleFailures,
    build_resumable_dictionary,
    generated_example_failure_count as resumable_generated_example_failure_count,
)
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    SourceMaterialParseError,
    parse_source_material,
    parse_source_materials,
)
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
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
from wordbyword.class_archive.archive import (
    ClassSessionArchiveError,
    rename_class_session_archive,
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
    emit_printable_progress,
    emit_printable_summary,
    emit_source_material_progress,
    emit_source_rewrite_progress,
    emit_source_rewrite_summary,
    emit_stage_update,
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

PADDLEOCR_VL_SIDECAR_ENV_VAR = "WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV"
PADDLEOCR_VL_SIDECAR_PYTHON_ENV_VAR = "WORDBYWORD_PADDLEOCR_VL_SIDECAR_PYTHON"
DEFAULT_PADDLEOCR_VL_SIDECAR_ENVS = (".venv-paddleocr-vl", ".venv-paddleocr")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    try:
        immediate_exit_code = run_immediate_command(args)
    except PipelineUsageError as exc:
        parser.exit(2, f"wordbyword: error: {exc}\n")
    if immediate_exit_code is not None:
        return immediate_exit_code

    emit_boot_screen(
        input_dir=args.input,
        output_dir=args.output,
        clean_example_sentences=pipeline_cleanup_requested(args),
        sentence_cleanup_model=args.ollama_model,
    )

    try:
        result = run_pipeline(
            input_dir=args.input,
            output_dir=args.output,
            include_punctuation=args.include_punctuation,
            include_numbers=args.include_numbers,
            clean_example_sentences=args.clean_example_sentences,
            clean_dictionary_candidates=args.clean_dictionary_candidates,
            generate_missing_examples=args.generate_missing_examples,
            retry_generated_examples=args.retry_generated_examples,
            sentence_cleanup_model=args.ollama_model,
            sentence_cleanup_base_url=args.ollama_base_url,
            progress=emit_stage_update,
            cleanup_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="example",
                item_label="rows",
            ),
            dictionary_cleanup_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="dictionary",
                item_label="terms",
            ),
            generated_example_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="candidate example",
                item_label="terms",
            ),
            generated_example_retry_decider=generated_example_retry_decider(args),
            artifact_callback=pipeline_artifact_callback(model=args.ollama_model),
        )
    except PipelineUsageError as exc:
        parser.exit(2, f"wordbyword: error: {exc}\n")

    emit_completion_screen(
        result,
        input_dir=args.input,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=pipeline_cleanup_requested(args),
        sentence_cleanup_model=args.ollama_model,
    )
    return 0


def run_immediate_command(args: argparse.Namespace) -> int | None:
    if args.build_dictionary:
        return build_dictionary_cli(args)
    if args.parse_source_material is not None:
        options = source_parse_options_from_args(args)
        return parse_source_material_cli(
            source_path=args.parse_source_material,
            output_root=options.output_root,
            source_id=args.source_id,
            parse_pdf=options.parse_pdf,
            render_dpi=options.render_dpi,
            paddleocr_vl_settings=options.paddleocr_vl_settings,
        )
    if args.parse_source_materials is not None:
        options = source_parse_options_from_args(args)
        return parse_source_materials_cli(
            source_dir=args.parse_source_materials,
            output_root=options.output_root,
            parse_pdf=options.parse_pdf,
            render_dpi=options.render_dpi,
            paddleocr_vl_settings=options.paddleocr_vl_settings,
        )
    if args.rewrite_whole_source_material is not None:
        return rewrite_whole_source_material_cli(
            parsed_bundle_dir=args.rewrite_whole_source_material,
            output_root=args.rewrite_output_root or DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_whole_source_materials is not None:
        return rewrite_whole_source_materials_cli(
            parsed_root=args.rewrite_whole_source_materials,
            output_root=args.rewrite_output_root or DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_vocabulary_guided_source_material is not None:
        return rewrite_vocabulary_guided_source_material_cli(
            parsed_bundle_dir=args.rewrite_vocabulary_guided_source_material,
            output_root=args.rewrite_output_root or DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
            dictionary_path=rewrite_dictionary_path_from_args(args),
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.rewrite_vocabulary_guided_source_materials is not None:
        return rewrite_vocabulary_guided_source_materials_cli(
            parsed_root=args.rewrite_vocabulary_guided_source_materials,
            output_root=args.rewrite_output_root or DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
            dictionary_path=rewrite_dictionary_path_from_args(args),
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    if args.print_class_material is not None:
        return print_class_material_cli(
            rewritten_bundle_dir=args.print_class_material,
            output_root=args.print_output_root,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            font_path=args.print_font_path,
            body_font_size=args.print_body_font_size,
        )
    if args.print_class_materials is not None:
        return print_class_materials_cli(
            rewritten_root=args.print_class_materials,
            output_root=args.print_output_root,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            font_path=args.print_font_path,
            body_font_size=args.print_body_font_size,
        )
    if args.build_class_printouts is not None:
        options = source_parse_options_from_args(args)
        return build_class_printouts_cli(
            source_dir=args.build_class_printouts,
            parse_output_root=options.output_root,
            parse_pdf=options.parse_pdf,
            render_dpi=options.render_dpi,
            paddleocr_vl_settings=options.paddleocr_vl_settings,
            rewrite_output_root=args.rewrite_output_root or DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
            dictionary_path=rewrite_dictionary_path_from_args(args),
            print_output_root=args.print_output_root,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            font_path=args.print_font_path,
            body_font_size=args.print_body_font_size,
        )
    if args.generate_after_class_reading_report is not None:
        return generate_after_class_reading_report_cli(
            printout_path=args.generate_after_class_reading_report,
            output_root=args.after_class_report_output_root,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            font_path=args.after_class_report_font_path,
        )
    if args.rename_class_session_archive is not None:
        return rename_class_session_archive_cli(
            archive_root=args.rename_class_session_archive,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            apply=args.apply_archive_renames,
        )
    if args.build_usage_signals:
        return build_usage_signals_cli(
            archive_root=args.usage_signal_input_root,
            output_root=args.usage_signal_output_root,
            include_punctuation=args.include_punctuation,
            include_numbers=args.include_numbers,
        )
    if args.update_live_lexicon:
        return update_live_lexicon_cli(
            canonical_dictionary_path=args.canonical_dictionary,
            usage_candidates_path=args.usage_candidates,
            usage_summary_path=args.usage_summary,
            live_lexicon_path=args.live_lexicon,
            unmatched_usage_output_path=args.unmatched_usage_output,
        )
    if args.show_distribution is not None:
        return show_distribution(
            jsonl_path=resolve_distribution_path(args.show_distribution, args.output),
            model=args.ollama_model,
        )
    if args.link_cleaned_examples:
        return link_cleaned_examples(output_dir=args.output)
    if args.add_reference_meanings:
        return add_reference_meanings(
            output_dir=args.output,
            reference_dictionary_path=args.reference_dictionary,
        )
    if args.generate_missing_meanings:
        return generate_missing_meanings(
            output_dir=args.output,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
        )
    return None


def build_dictionary_cli(args: argparse.Namespace) -> int:
    emit_boot_screen(
        input_dir=args.input,
        output_dir=args.output,
        clean_example_sentences=True,
        sentence_cleanup_model=args.ollama_model,
    )

    try:
        result = build_resumable_dictionary(
            input_dir=args.input,
            output_dir=args.output,
            reference_dictionary_path=args.reference_dictionary,
            include_punctuation=args.include_punctuation,
            include_numbers=args.include_numbers,
            model=args.ollama_model,
            base_url=args.ollama_base_url,
            force=args.force,
            progress=emit_stage_update,
            cleanup_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="example",
                item_label="rows",
            ),
            dictionary_cleanup_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="dictionary",
                item_label="terms",
            ),
            generated_example_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="candidate example",
                item_label="terms",
            ),
            generated_meaning_progress=cleanup_progress_callback(
                model=args.ollama_model,
                label="meaning",
                item_label="terms",
            ),
            artifact_callback=pipeline_artifact_callback(model=args.ollama_model),
            checkpoint_status=emit_dictionary_build_checkpoint_status,
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
    except (ValueError, PipelineUsageError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    emit_completion_screen(
        result.pipeline_result,
        input_dir=args.input,
        include_punctuation=args.include_punctuation,
        include_numbers=args.include_numbers,
        clean_example_sentences=True,
        sentence_cleanup_model=args.ollama_model,
    )
    return 0


def emit_dictionary_build_checkpoint_status(status: CheckpointStatus) -> None:
    sys.stdout.write(f"wordbyword: {status.action.value} {status.key} {status.label}: {status.reason}\n")
    sys.stdout.flush()


def generated_example_failure_count(output_dir: Path) -> int:
    return resumable_generated_example_failure_count(output_dir)


def rewrite_dictionary_path_from_args(args: argparse.Namespace) -> Path:
    return args.rewrite_dictionary or artifact_path(args.output, ArtifactId.FINAL_DICTIONARY)


def source_parse_options_from_args(args: argparse.Namespace) -> SourceParseOptions:
    return SourceParseOptions(
        output_root=args.source_output_root,
        parse_pdf=not args.skip_pdf_parse,
        render_dpi=args.pdf_render_dpi,
        paddleocr_vl_settings=None if args.skip_pdf_parse else paddleocr_vl_settings_from_args(args),
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
    args: argparse.Namespace,
) -> Callable[[GeneratedExampleAttemptSummary], bool]:
    def decide(summary: GeneratedExampleAttemptSummary) -> bool:
        if args.retry_generated_examples:
            return False
        return prompt_generated_example_retry(
            summary=summary,
            output_dir=args.output,
            model=args.ollama_model,
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
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    try:
        bundle = parse_source_material(
            source_path,
            output_root=output_root,
            source_id=source_id,
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
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> int:
    try:
        result = parse_source_materials(
            source_dir,
            output_root=output_root,
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
    dictionary_path: Path,
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
                dictionary_path=dictionary_path,
                output_root=output_root,
                client=client,
                model=model,
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
            ("dictionary", str(dictionary_path)),
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
        f"  dictionary: {dictionary_path}\n"
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
    dictionary_path: Path,
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
                dictionary_path=dictionary_path,
                output_root=output_root,
                client=client,
                model=model,
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
            ("dictionary", str(dictionary_path)),
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
        f"  dictionary: {dictionary_path}\n"
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
    dictionary_path: Path,
    print_output_root: Path,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
) -> int:
    try:
        parsed = parse_source_materials(
            source_dir,
            output_root=parse_output_root,
            parse_pdf=parse_pdf,
            render_dpi=render_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
            progress=emit_source_material_progress,
        )
    except KeyboardInterrupt:
        sys.stderr.write("wordbyword: class printout build interrupted during parsing; partial workspace cleaned.\n")
        return 130
    except SourceMaterialParseError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    if not parsed.bundles:
        sys.stderr.write(f"wordbyword: no class source materials found in {source_dir}\n")
        return 1

    rewrite_client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    rewrite_outputs = []
    try:
        try:
            for source_index, bundle in enumerate(parsed.bundles, start=1):
                rewrite_outputs.append(
                    rewrite_vocabulary_guided_parsed_source_material(
                        bundle.output_dir,
                        dictionary_path=dictionary_path,
                        output_root=rewrite_output_root,
                        client=rewrite_client,
                        model=model,
                        progress=emit_source_rewrite_progress,
                        source_index=source_index,
                        total_sources=len(parsed.bundles),
                    )
                )
        finally:
            warn_if_model_unload_fails(rewrite_client)
    except (SourceRewriteError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    successful_rewrites = [output for output in rewrite_outputs if output.failed_page_count == 0]
    failed_rewrites = len(rewrite_outputs) - len(successful_rewrites)
    if not successful_rewrites:
        if emit_printable_summary(
            title="workflow stopped",
            summary=(
                ("status", "stopped after rewrite"),
                ("source_dir", str(parsed.source_dir)),
                ("parsed", f"{len(parsed.bundles):,}"),
                ("rewritten", "0"),
                ("failed rewrites", f"{failed_rewrites:,}"),
            ),
        ):
            return 1

        sys.stdout.write(
            "class printout workflow stopped after rewrite\n"
            f"  source_dir: {parsed.source_dir}\n"
            f"  parsed_sources: {len(parsed.bundles)}\n"
            f"  rewritten: 0\n"
            f"  failed_rewrites: {failed_rewrites}\n"
        )
        sys.stdout.flush()
        return 1

    print_client = ollama_client(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=16384,
    )
    print_outputs = []
    try:
        try:
            for source_index, rewrite_output in enumerate(successful_rewrites, start=1):
                print_outputs.append(
                    print_class_material(
                        rewrite_output.output_dir,
                        output_root=print_output_root,
                        client=print_client,
                        model=model,
                        font_path=font_path,
                        body_font_size=body_font_size,
                        progress=emit_printable_progress,
                        source_index=source_index,
                        total_sources=len(successful_rewrites),
                    )
                )
        finally:
            warn_if_model_unload_fails(print_client)
    except (PrintableMaterialError, LLMConfigurationError, LLMRequestError, LLMResponseError) as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    written = sum(1 for output in print_outputs if output.accepted)
    failed_prints = sum(1 for output in print_outputs if not output.accepted)
    if emit_printable_summary(
        title="workflow complete",
        summary=(
            ("status", "written" if failed_rewrites == 0 and failed_prints == 0 and written > 0 else "failed"),
            ("source_dir", str(parsed.source_dir)),
            ("parsed", str(parsed.output_root)),
            ("rewrite", str(rewrite_output_root)),
            ("print", str(print_output_root)),
            ("parsed sources", f"{len(parsed.bundles):,}"),
            ("rewritten", f"{len(successful_rewrites):,}"),
            ("failed rewrites", f"{failed_rewrites:,}"),
            ("docx written", f"{written:,}"),
            ("failed prints", f"{failed_prints:,}"),
        ),
        source_rows=tuple(printable_output_summary_row(output) for output in print_outputs),
    ):
        return 0 if failed_rewrites == 0 and failed_prints == 0 and written > 0 else 1

    sys.stdout.write(
        "class printout workflow complete\n"
        f"  source_dir: {parsed.source_dir}\n"
        f"  parsed_root: {parsed.output_root}\n"
        f"  rewrite_root: {rewrite_output_root}\n"
        f"  print_root: {print_output_root}\n"
        f"  parsed_sources: {len(parsed.bundles)}\n"
        f"  rewritten: {len(successful_rewrites)}\n"
        f"  failed_rewrites: {failed_rewrites}\n"
        f"  docx_written: {written}\n"
        f"  failed_prints: {failed_prints}\n"
    )
    for output in print_outputs:
        status = "written" if output.accepted else "failed"
        sys.stdout.write(f"  - {output.source_id}: {output.output_dir} ({status})\n")
    sys.stdout.flush()
    return 0 if failed_rewrites == 0 and failed_prints == 0 and written > 0 else 1


def generate_after_class_reading_report_cli(
    *,
    printout_path: Path,
    output_root: Path,
    model: str,
    base_url: str | None,
    font_path: Path | None,
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
    include_punctuation: bool,
    include_numbers: bool,
) -> int:
    try:
        result = build_usage_signals(
            archive_root=archive_root,
            output_root=output_root,
            include_punctuation=include_punctuation,
            include_numbers=include_numbers,
            progress=emit_usage_signal_progress,
        )
    except UsageSignalBuildError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

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
    sys.stdout.write(f"usage signals [{phase}] {detail}\n")
    sys.stdout.flush()


def update_live_lexicon_cli(
    *,
    canonical_dictionary_path: Path,
    usage_candidates_path: Path,
    usage_summary_path: Path | None,
    live_lexicon_path: Path,
    unmatched_usage_output_path: Path,
) -> int:
    try:
        result = update_live_lexicon(
            canonical_dictionary_path=canonical_dictionary_path,
            usage_candidates_path=usage_candidates_path,
            usage_summary_path=usage_summary_path,
            live_lexicon_path=live_lexicon_path,
            unmatched_usage_output_path=unmatched_usage_output_path,
        )
    except LiveLexiconUpdateError as error:
        sys.stderr.write(f"wordbyword: {error}\n")
        return 1

    sys.stdout.write(
        "live lexicon updated\n"
        f"  canonical_dictionary: {result.canonical_dictionary_path}\n"
        f"  live_lexicon: {result.live_lexicon_path}\n"
        f"  usage_candidates: {result.usage_candidates_path}\n"
        f"  usage_summary: {result.usage_summary_path}\n"
        f"  unmatched_usage_output: {result.unmatched_usage_output_path}\n"
        f"  canonical_rows: {result.canonical_rows}\n"
        f"  live_lexicon_rows: {result.live_lexicon_rows}\n"
        f"  usage_candidate_rows: {result.usage_candidate_rows}\n"
        f"  total_class_printouts: {result.total_class_printouts}\n"
        f"  matched: {result.matched_count}\n"
        f"  unmatched: {result.unmatched_count}\n"
    )
    sys.stdout.flush()
    return 0


def rename_class_session_archive_cli(
    *,
    archive_root: Path,
    model: str,
    base_url: str | None,
    apply: bool,
) -> int:
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


def warn_if_model_unload_fails(client: SupportsCompletion) -> None:
    try:
        unload_model_if_supported(client)
    except Exception as error:  # pragma: no cover - defensive cleanup path
        sys.stderr.write(f"wordbyword: warning: could not unload Ollama model: {error}\n")
        sys.stderr.flush()


def paddleocr_vl_settings_from_args(args: argparse.Namespace) -> PaddleOCRVLSettings | None:
    sidecar_env = (
        args.paddleocr_vl_sidecar_env.as_posix()
        if args.paddleocr_vl_sidecar_env is not None
        else None
    )
    sidecar_python = (
        args.paddleocr_vl_sidecar_python.as_posix()
        if args.paddleocr_vl_sidecar_python is not None
        else None
    )
    if sidecar_env is None and sidecar_python is None:
        sidecar_python = os.environ.get(PADDLEOCR_VL_SIDECAR_PYTHON_ENV_VAR) or None
        sidecar_env = os.environ.get(PADDLEOCR_VL_SIDECAR_ENV_VAR) or None
    if sidecar_env is None and sidecar_python is None:
        discovered_sidecar_env = discover_paddleocr_vl_sidecar_env()
        sidecar_env = discovered_sidecar_env.as_posix() if discovered_sidecar_env is not None else None

    if sidecar_env is not None or sidecar_python is not None:
        validate_paddleocr_vl_sidecar(sidecar_env=sidecar_env, sidecar_python=sidecar_python)
    elif not current_python_has_paddleocr():
        raise PipelineUsageError(
            "PaddleOCR-VL parser is selected, but PaddleOCR-VL is not available. "
            "Create .venv-paddleocr-vl, set WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV, "
            "or pass --paddleocr-vl-sidecar-env /path/to/env. Use --skip-pdf-parse "
            "only when you intentionally want workspace preparation without PDF parsing."
        )

    return PaddleOCRVLSettings(
        device=args.paddleocr_vl_device,
        engine=args.paddleocr_vl_engine,
        vl_rec_backend=args.paddleocr_vl_rec_backend,
        vl_rec_server_url=args.paddleocr_vl_rec_server_url,
        vl_rec_api_model_name=args.paddleocr_vl_rec_api_model_name,
        vl_rec_api_key=args.paddleocr_vl_rec_api_key,
        use_doc_orientation_classify=args.paddleocr_vl_doc_orientation or None,
        use_doc_unwarping=args.paddleocr_vl_doc_unwarping or None,
        use_layout_detection=False if args.paddleocr_vl_no_layout_detection else None,
        use_chart_recognition=args.paddleocr_vl_chart_recognition or None,
        use_seal_recognition=args.paddleocr_vl_seal_recognition or None,
        use_ocr_for_image_block=False if args.paddleocr_vl_no_ocr_for_image_block else None,
        max_new_tokens=args.paddleocr_vl_max_new_tokens,
        sidecar_env=sidecar_env,
        sidecar_python=sidecar_python,
        sidecar_timeout_seconds=args.paddleocr_vl_sidecar_timeout_seconds,
    )


def discover_paddleocr_vl_sidecar_env(*, roots: Sequence[Path] | None = None) -> Path | None:
    for root in roots or paddleocr_vl_sidecar_search_roots():
        for env_name in DEFAULT_PADDLEOCR_VL_SIDECAR_ENVS:
            candidate = root / env_name
            if candidate.is_dir():
                python_path = paddleocr_vl_sidecar_python_path(sidecar_env=candidate.as_posix(), sidecar_python=None)
                if python_path is not None and paddleocr_vl_python_can_locate_paddleocr(python_path):
                    return candidate
    return None


def paddleocr_vl_sidecar_search_roots() -> tuple[Path, ...]:
    roots = [Path.cwd(), wordbyword_project_root()]
    unique_roots: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique_roots:
            unique_roots.append(resolved)
    return tuple(unique_roots)


def wordbyword_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def current_python_has_paddleocr() -> bool:
    return importlib.util.find_spec("paddleocr") is not None


def validate_paddleocr_vl_sidecar(*, sidecar_env: str | None, sidecar_python: str | None) -> None:
    python_path = paddleocr_vl_sidecar_python_path(sidecar_env=sidecar_env, sidecar_python=sidecar_python)
    label = sidecar_python or sidecar_env or "unknown sidecar"
    if python_path is None or not paddleocr_vl_python_can_locate_paddleocr(python_path):
        raise PipelineUsageError(
            "PaddleOCR-VL sidecar cannot import paddleocr: "
            f"{label}. Install PaddleOCR-VL there or choose a valid sidecar with "
            "--paddleocr-vl-sidecar-env /path/to/env."
        )


def paddleocr_vl_sidecar_python_path(*, sidecar_env: str | None, sidecar_python: str | None) -> Path | None:
    if sidecar_python:
        return Path(sidecar_python).expanduser()
    if not sidecar_env:
        return None
    sidecar_env_path = Path(sidecar_env).expanduser()
    for candidate in (
        sidecar_env_path / "bin" / "python",
        sidecar_env_path / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def paddleocr_vl_python_can_locate_paddleocr(python_path: Path) -> bool:
    if not python_path.is_file():
        return False
    try:
        result = subprocess.run(
            [
                python_path.as_posix(),
                "-c",
                "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('paddleocr') else 1)",
            ],
            capture_output=True,
            check=False,
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


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
