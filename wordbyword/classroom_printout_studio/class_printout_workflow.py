from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wordbyword._json_io import read_json_object, write_json_atomic
from wordbyword.classroom_printout_studio.class_printing.printable import (
    PrintableProgress,
    print_class_material,
)
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    SourceRewriteProgress,
    rewrite_vocabulary_guided_source_material,
)
from wordbyword.classroom_printout_studio.source_parsing.materials import (
    SourceMaterialBundle,
    SourceMaterialProgress,
    discover_source_materials,
    parse_source_material,
    parser_settings_signature_for_source,
    sha256_file,
    source_id_for,
)
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.dictionary_build.llm import SupportsCompletion, ollama_client, unload_model_if_supported
from wordbyword.grade_profiles import DEFAULT_TARGET_GRADE, normalize_target_grade, target_profile_for_grade
from wordbyword.application.workspace import RewriteVocabularySource

CLASS_PRINTOUT_LEDGER_FILENAME = ".wordbyword-class-printout-ledger.json"
CLASS_PRINTOUT_LEDGER_SCHEMA_VERSION = "wordbyword.class_printout_workflow.v1"
CLASS_PRINTOUT_STATUS_PRINTED = "printed"
CLASS_PRINTOUT_STATUS_REWRITE_FAILED = "rewrite_failed"
CLASS_PRINTOUT_STATUS_PRINT_FAILED = "print_failed"

SourceMaterialProgressCallback = Callable[[SourceMaterialProgress], None]
SourceRewriteProgressCallback = Callable[[SourceRewriteProgress], None]
PrintableProgressCallback = Callable[[PrintableProgress], None]
CompletionClientFactory = Callable[..., SupportsCompletion]
ModelUnloadCallback = Callable[[SupportsCompletion], None]
ParseSourceMaterial = Callable[..., SourceMaterialBundle]
RewriteSourceMaterial = Callable[..., object]
PrintClassMaterial = Callable[..., object]


class ClassPrintoutWorkflowError(RuntimeError):
    """Raised when class-printout workflow state is invalid."""


@dataclass(frozen=True)
class BuildClassPrintoutSource:
    path: Path
    source_id: str
    sha256: str
    parser_settings_signature: str


@dataclass(frozen=True)
class ClassPrintoutWorkflowResult:
    status: str
    exit_code: int
    source_dir: Path
    parse_output_root: Path
    rewrite_output_root: Path
    print_output_root: Path
    vocabulary_source: RewriteVocabularySource
    ledger_path: Path
    discovered_sources: int
    skipped_sources: tuple[dict[str, object], ...] = ()
    parser_skipped_sources: tuple[dict[str, object], ...] = ()
    parsed_sources: int = 0
    rewritten: int = 0
    failed_rewrites: int = 0
    docx_written: int = 0
    failed_prints: int = 0
    print_outputs: tuple[object, ...] = ()


def build_class_printouts_workflow(
    *,
    source_dir: Path,
    parse_output_root: Path,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
    rewrite_output_root: Path,
    vocabulary_source: RewriteVocabularySource,
    print_output_root: Path,
    model: str,
    base_url: str | None,
    font_path: Path | None,
    body_font_size: int | None,
    force_class_printouts: bool,
    target_grade: str = DEFAULT_TARGET_GRADE,
    target_profile: str | None = None,
    parse_source_material_func: ParseSourceMaterial = parse_source_material,
    rewrite_source_material_func: RewriteSourceMaterial = rewrite_vocabulary_guided_source_material,
    print_class_material_func: PrintClassMaterial = print_class_material,
    completion_client_factory: CompletionClientFactory = ollama_client,
    model_unload_callback: ModelUnloadCallback = unload_model_if_supported,
    source_material_progress: SourceMaterialProgressCallback | None = None,
    source_rewrite_progress: SourceRewriteProgressCallback | None = None,
    printable_progress: PrintableProgressCallback | None = None,
) -> ClassPrintoutWorkflowResult:
    resolved_target_grade = normalize_target_grade(target_grade)
    resolved_target_profile = target_profile or target_profile_for_grade(resolved_target_grade)
    ledger_path = class_printout_ledger_path(parse_output_root)
    ledger = load_class_printout_ledger(ledger_path)
    discovered_sources = discover_class_printout_sources(
        source_dir=source_dir,
        parse_output_root=parse_output_root,
        parse_pdf=parse_pdf,
        render_dpi=render_dpi,
        paddleocr_vl_settings=paddleocr_vl_settings,
    )
    new_sources, skipped_sources = filter_new_class_printout_sources(
        discovered_sources,
        ledger=ledger,
        force=force_class_printouts,
        vocabulary_source=vocabulary_source,
        target_grade=resolved_target_grade,
        target_profile=resolved_target_profile,
    )

    if not discovered_sources:
        return ClassPrintoutWorkflowResult(
            status="no_sources",
            exit_code=1,
            source_dir=source_dir,
            parse_output_root=parse_output_root,
            rewrite_output_root=rewrite_output_root,
            print_output_root=print_output_root,
            vocabulary_source=vocabulary_source,
            ledger_path=ledger_path,
            discovered_sources=0,
        )

    if not new_sources:
        return ClassPrintoutWorkflowResult(
            status="skipped",
            exit_code=0,
            source_dir=source_dir,
            parse_output_root=parse_output_root,
            rewrite_output_root=rewrite_output_root,
            print_output_root=print_output_root,
            vocabulary_source=vocabulary_source,
            ledger_path=ledger_path,
            discovered_sources=len(discovered_sources),
            skipped_sources=tuple(skipped_sources),
        )

    parsed_bundles = parse_or_reuse_class_printout_sources(
        new_sources,
        parse_output_root=parse_output_root,
        parse_pdf=parse_pdf,
        render_dpi=render_dpi,
        paddleocr_vl_settings=paddleocr_vl_settings,
        target_grade=resolved_target_grade,
        reuse_parsed_cache=not force_class_printouts,
        parse_source_material_func=parse_source_material_func,
        progress=source_material_progress,
    )
    rewritable_bundles, parser_skipped_sources = class_printout_rewritable_bundles(parsed_bundles)
    source_by_id = {source.source_id: source for source in new_sources}
    parsed_by_id = {bundle.source_id: bundle for bundle in parsed_bundles}

    if not rewritable_bundles:
        return ClassPrintoutWorkflowResult(
            status="stopped_after_parsing",
            exit_code=1,
            source_dir=source_dir,
            parse_output_root=parse_output_root,
            rewrite_output_root=rewrite_output_root,
            print_output_root=print_output_root,
            vocabulary_source=vocabulary_source,
            ledger_path=ledger_path,
            discovered_sources=len(discovered_sources),
            skipped_sources=tuple(skipped_sources),
            parser_skipped_sources=tuple(parser_skipped_sources),
            parsed_sources=len(parsed_bundles),
        )

    rewrite_client = completion_client_factory(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=32768,
    )
    rewrite_outputs: list[object] = []
    try:
        for source_index, bundle in enumerate(rewritable_bundles, start=1):
            rewrite_outputs.append(
                rewrite_source_material_func(
                    bundle.output_dir,
                    dictionary_path=vocabulary_source.path,
                    vocabulary_source_kind=vocabulary_source.kind,
                    vocabulary_source_sha256=vocabulary_source.sha256,
                    output_root=rewrite_output_root,
                    client=rewrite_client,
                    model=model,
                    target_grade=resolved_target_grade,
                    target_profile=resolved_target_profile,
                    progress=source_rewrite_progress,
                    source_index=source_index,
                    total_sources=len(rewritable_bundles),
                )
            )
    finally:
        model_unload_callback(rewrite_client)

    successful_rewrites = [output for output in rewrite_outputs if int(getattr(output, "failed_page_count")) == 0]
    failed_rewrites = len(rewrite_outputs) - len(successful_rewrites)
    for rewrite_output in rewrite_outputs:
        if int(getattr(rewrite_output, "failed_page_count")) == 0:
            continue
        source = source_by_id.get(str(getattr(rewrite_output, "source_id")))
        if source is None:
            continue
        update_class_printout_ledger_entry(
            ledger,
            source=source,
            parsed_bundle=parsed_by_id.get(str(getattr(rewrite_output, "source_id"))),
            rewrite_output=rewrite_output,
            print_output=None,
            status=CLASS_PRINTOUT_STATUS_REWRITE_FAILED,
            rewrite_model=model,
            print_model=model,
            vocabulary_source=vocabulary_source,
            target_grade=resolved_target_grade,
            target_profile=resolved_target_profile,
        )
    if failed_rewrites:
        write_class_printout_ledger(ledger_path, ledger)

    if not successful_rewrites:
        return ClassPrintoutWorkflowResult(
            status="stopped_after_rewrite",
            exit_code=1,
            source_dir=source_dir,
            parse_output_root=parse_output_root,
            rewrite_output_root=rewrite_output_root,
            print_output_root=print_output_root,
            vocabulary_source=vocabulary_source,
            ledger_path=ledger_path,
            discovered_sources=len(discovered_sources),
            skipped_sources=tuple(skipped_sources),
            parser_skipped_sources=tuple(parser_skipped_sources),
            parsed_sources=len(parsed_bundles),
            rewritten=0,
            failed_rewrites=failed_rewrites,
        )

    print_client = completion_client_factory(
        model=model,
        base_url=base_url,
        timeout_seconds=1200.0,
        max_output_tokens=16384,
    )
    print_outputs: list[object] = []
    try:
        for source_index, rewrite_output in enumerate(successful_rewrites, start=1):
            print_outputs.append(
                print_class_material_func(
                    getattr(rewrite_output, "output_dir"),
                    output_root=print_output_root,
                    client=print_client,
                    model=model,
                    target_grade=resolved_target_grade,
                    target_profile=resolved_target_profile,
                    font_path=font_path,
                    body_font_size=body_font_size,
                    progress=printable_progress,
                    source_index=source_index,
                    total_sources=len(successful_rewrites),
                )
            )
    finally:
        model_unload_callback(print_client)

    written = sum(1 for output in print_outputs if bool(getattr(output, "accepted")))
    failed_prints = sum(1 for output in print_outputs if not bool(getattr(output, "accepted")))
    rewrite_by_id = {str(getattr(output, "source_id")): output for output in rewrite_outputs}
    for print_output in print_outputs:
        source = source_by_id.get(str(getattr(print_output, "source_id")))
        if source is None:
            continue
        update_class_printout_ledger_entry(
            ledger,
            source=source,
            parsed_bundle=parsed_by_id.get(str(getattr(print_output, "source_id"))),
            rewrite_output=rewrite_by_id.get(str(getattr(print_output, "source_id"))),
            print_output=print_output,
            status=CLASS_PRINTOUT_STATUS_PRINTED if bool(getattr(print_output, "accepted")) else CLASS_PRINTOUT_STATUS_PRINT_FAILED,
            rewrite_model=model,
            print_model=model,
            vocabulary_source=vocabulary_source,
            target_grade=resolved_target_grade,
            target_profile=resolved_target_profile,
        )
    write_class_printout_ledger(ledger_path, ledger)

    exit_code = 0 if failed_rewrites == 0 and failed_prints == 0 and written > 0 else 1
    return ClassPrintoutWorkflowResult(
        status="written" if exit_code == 0 else "failed",
        exit_code=exit_code,
        source_dir=source_dir,
        parse_output_root=parse_output_root,
        rewrite_output_root=rewrite_output_root,
        print_output_root=print_output_root,
        vocabulary_source=vocabulary_source,
        ledger_path=ledger_path,
        discovered_sources=len(discovered_sources),
        skipped_sources=tuple(skipped_sources),
        parser_skipped_sources=tuple(parser_skipped_sources),
        parsed_sources=len(parsed_bundles),
        rewritten=len(successful_rewrites),
        failed_rewrites=failed_rewrites,
        docx_written=written,
        failed_prints=failed_prints,
        print_outputs=tuple(print_outputs),
    )


def class_printout_ledger_path(parse_output_root: Path) -> Path:
    return parse_output_root / CLASS_PRINTOUT_LEDGER_FILENAME


def class_printout_ledger_entry_key(
    *,
    target_grade: str,
    source_sha256: str,
    parser_settings_signature: str,
    vocabulary_source_sha256: str,
) -> str:
    return "|".join(
        (
            target_grade,
            source_sha256,
            parser_settings_signature,
            vocabulary_source_sha256 or "missing-vocabulary-sha256",
        )
    )


def load_class_printout_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {"schema_version": CLASS_PRINTOUT_LEDGER_SCHEMA_VERSION, "entries": {}}
    payload = read_json_object(ledger_path, error_type=ClassPrintoutWorkflowError)
    if payload.get("schema_version") != CLASS_PRINTOUT_LEDGER_SCHEMA_VERSION:
        raise ClassPrintoutWorkflowError(
            f"Class printout ledger schema is {payload.get('schema_version')!r}, "
            f"expected {CLASS_PRINTOUT_LEDGER_SCHEMA_VERSION!r}: {ledger_path}"
        )
    if not isinstance(payload.get("entries"), dict):
        raise ClassPrintoutWorkflowError(f"Class printout ledger has invalid entries object: {ledger_path}")
    return payload


def write_class_printout_ledger(ledger_path: Path, ledger: dict[str, Any]) -> None:
    ledger["schema_version"] = CLASS_PRINTOUT_LEDGER_SCHEMA_VERSION
    ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(ledger_path, ledger)


def discover_class_printout_sources(
    *,
    source_dir: Path,
    parse_output_root: Path,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
) -> list[BuildClassPrintoutSource]:
    source_paths = discover_source_materials(source_dir, output_root=parse_output_root)
    used_source_ids: set[str] = set()
    sources: list[BuildClassPrintoutSource] = []
    for source_path in source_paths:
        source_id = unique_class_printout_source_id(source_path, source_dir, used_source_ids)
        sources.append(
            BuildClassPrintoutSource(
                path=source_path,
                source_id=source_id,
                sha256=sha256_file(source_path),
                parser_settings_signature=parser_settings_signature_for_source(
                    source_path,
                    parse_pdf=parse_pdf,
                    render_dpi=render_dpi,
                    paddleocr_vl_settings=paddleocr_vl_settings,
                ),
            )
        )
    return sources


def unique_class_printout_source_id(source_path: Path, source_dir: Path, used_source_ids: set[str]) -> str:
    source_id = source_id_for(source_path)
    if source_id not in used_source_ids:
        used_source_ids.add(source_id)
        return source_id
    try:
        relative = source_path.relative_to(source_dir)
    except ValueError:
        relative = Path(source_path.name)
    source_id = source_id_for(Path("-".join(relative.with_suffix("").parts)))
    if source_id not in used_source_ids:
        used_source_ids.add(source_id)
        return source_id
    source_id = f"{source_id}-{sha256_file(source_path)[:8]}"
    used_source_ids.add(source_id)
    return source_id


def valid_printed_class_printout_entry(
    entry: object,
    *,
    source_sha256: str,
    target_grade: str,
    target_profile: str,
) -> bool:
    if not isinstance(entry, Mapping):
        return False
    if entry.get("status") != CLASS_PRINTOUT_STATUS_PRINTED:
        return False
    source = entry.get("source")
    if not isinstance(source, Mapping) or source.get("sha256") != source_sha256:
        return False
    printable = entry.get("printable")
    if not isinstance(printable, Mapping):
        return False
    manifest_path = Path(str(printable.get("manifest_path") or ""))
    docx_path = Path(str(printable.get("docx_path") or ""))
    if not manifest_path.is_file() or not docx_path.is_file():
        return False
    try:
        manifest = read_json_object(manifest_path)
    except (OSError, ValueError):
        return False
    manifest_source = manifest.get("source")
    manifest_printable = manifest.get("printable")
    if not isinstance(manifest_source, Mapping) or manifest_source.get("sha256") != source_sha256:
        return False
    if not isinstance(manifest_printable, Mapping) or manifest_printable.get("status") != "written":
        return False
    if str(manifest_printable.get("target_grade") or "") != target_grade:
        return False
    if str(manifest_printable.get("target_profile") or "") != target_profile:
        return False
    return True


def filter_new_class_printout_sources(
    sources: Sequence[BuildClassPrintoutSource],
    *,
    ledger: Mapping[str, Any],
    force: bool,
    vocabulary_source: RewriteVocabularySource,
    target_grade: str,
    target_profile: str,
) -> tuple[list[BuildClassPrintoutSource], list[dict[str, object]]]:
    entries = ledger.get("entries", {})
    if not isinstance(entries, Mapping):
        raise ClassPrintoutWorkflowError("Class printout ledger has invalid entries object.")
    new_sources: list[BuildClassPrintoutSource] = []
    skipped: list[dict[str, object]] = []
    for source in sources:
        entry_key = class_printout_ledger_entry_key(
            target_grade=target_grade,
            source_sha256=source.sha256,
            parser_settings_signature=source.parser_settings_signature,
            vocabulary_source_sha256=vocabulary_source.sha256,
        )
        if not force and valid_printed_class_printout_entry(
            entries.get(entry_key),
            source_sha256=source.sha256,
            target_grade=target_grade,
            target_profile=target_profile,
        ):
            skipped.append(
                {
                    "source_id": source.source_id,
                    "source_path": str(source.path),
                    "source_sha256": source.sha256,
                    "reason": "already printed successfully",
                }
            )
            continue
        new_sources.append(source)
    fail_on_duplicate_new_source_bytes(new_sources)
    return new_sources, skipped


def fail_on_duplicate_new_source_bytes(sources: Sequence[BuildClassPrintoutSource]) -> None:
    grouped: dict[str, list[BuildClassPrintoutSource]] = {}
    for source in sources:
        grouped.setdefault(source.sha256, []).append(source)
    duplicates = {digest: group for digest, group in grouped.items() if len(group) > 1}
    if not duplicates:
        return
    lines = ["duplicate class source materials detected; remove or move duplicate source files before building printouts:"]
    for digest, group in sorted(duplicates.items()):
        lines.append(f"  sha256:{digest}")
        for source in sorted(group, key=lambda item: item.path.as_posix()):
            lines.append(f"    - {source.path}")
    raise ClassPrintoutWorkflowError("\n".join(lines))


def reusable_parsed_bundle(
    source: BuildClassPrintoutSource,
    *,
    parse_output_root: Path,
    target_grade: str,
) -> SourceMaterialBundle | None:
    output_dir = parse_output_root / source.source_id
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = read_json_object(manifest_path)
    except (OSError, ValueError):
        return None
    manifest_source = manifest.get("source")
    manifest_parser = manifest.get("parser")
    files = manifest.get("files")
    if str(manifest.get("target_grade") or "") != target_grade:
        return None
    if not isinstance(manifest_source, Mapping) or manifest_source.get("sha256") != source.sha256:
        return None
    if not isinstance(manifest_parser, Mapping):
        return None
    parser_settings = manifest_parser.get("settings")
    if not isinstance(parser_settings, Mapping):
        return None
    if parser_settings.get("settings_signature") != source.parser_settings_signature:
        return None
    if not isinstance(files, Mapping):
        return None
    for file_key in ("content_markdown", "chunks_json"):
        relative_path = files.get(file_key)
        if relative_path and not (output_dir / str(relative_path)).is_file():
            return None
    return SourceMaterialBundle(
        source_id=source.source_id,
        source_path=source.path,
        output_dir=output_dir,
        manifest_path=manifest_path,
        assets_dir=output_dir / "assets",
        manifest=manifest,
    )


def parse_or_reuse_class_printout_sources(
    sources: Sequence[BuildClassPrintoutSource],
    *,
    parse_output_root: Path,
    parse_pdf: bool,
    render_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None,
    target_grade: str,
    reuse_parsed_cache: bool,
    parse_source_material_func: ParseSourceMaterial,
    progress: SourceMaterialProgressCallback | None,
) -> list[SourceMaterialBundle]:
    bundles: list[SourceMaterialBundle] = []
    for index, source in enumerate(sources, start=1):
        reusable = (
            reusable_parsed_bundle(source, parse_output_root=parse_output_root, target_grade=target_grade)
            if reuse_parsed_cache
            else None
        )
        if reusable is not None:
            bundles.append(reusable)
            continue
        bundles.append(
            parse_source_material_func(
                source.path,
                output_root=parse_output_root,
                source_id=source.source_id,
                parse_pdf=parse_pdf,
                render_dpi=render_dpi,
                paddleocr_vl_settings=paddleocr_vl_settings,
                target_grade=target_grade,
                progress=progress,
                source_index=index,
                total_sources=len(sources),
            )
        )
    return bundles


def class_printout_rewritable_bundles(
    bundles: Sequence[SourceMaterialBundle],
) -> tuple[list[SourceMaterialBundle], list[dict[str, object]]]:
    rewritable: list[SourceMaterialBundle] = []
    skipped: list[dict[str, object]] = []
    for bundle in bundles:
        parser = bundle.manifest.get("parser")
        files = bundle.manifest.get("files")
        status = str(parser.get("status") or "") if isinstance(parser, Mapping) else ""
        if (
            status == "parsed"
            and isinstance(files, Mapping)
            and (bundle.output_dir / str(files.get("content_markdown") or "content.md")).is_file()
            and (bundle.output_dir / str(files.get("chunks_json") or "chunks.json")).is_file()
        ):
            rewritable.append(bundle)
            continue
        skipped.append(
            {
                "source_id": bundle.source_id,
                "source_path": str(bundle.source_path),
                "source_dir": str(bundle.output_dir),
                "reason": f"parser status is {status or 'unknown'}",
            }
        )
    return rewritable, skipped


def update_class_printout_ledger_entry(
    ledger: dict[str, Any],
    *,
    source: BuildClassPrintoutSource,
    parsed_bundle: SourceMaterialBundle | None,
    rewrite_output: object | None,
    print_output: object | None,
    status: str,
    rewrite_model: str,
    print_model: str,
    vocabulary_source: RewriteVocabularySource,
    target_grade: str,
    target_profile: str,
) -> None:
    entries = ledger.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ClassPrintoutWorkflowError("Class printout ledger has invalid entries object.")
    print_output_dir = Path(getattr(print_output, "output_dir", "")) if print_output is not None else None
    printable_manifest = print_output_dir / "manifest.json" if print_output_dir is not None else None
    printable_docx = print_output_dir / "material.docx" if print_output_dir is not None else None
    entry_key = class_printout_ledger_entry_key(
        target_grade=target_grade,
        source_sha256=source.sha256,
        parser_settings_signature=source.parser_settings_signature,
        vocabulary_source_sha256=vocabulary_source.sha256,
    )
    entries[entry_key] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "target_grade": target_grade,
        "source": {
            "path": str(source.path),
            "name": source.path.name,
            "source_id": source.source_id,
            "sha256": source.sha256,
        },
        "parser": {
            "settings_signature": source.parser_settings_signature,
            "bundle_dir": str(parsed_bundle.output_dir) if parsed_bundle is not None else "",
            "manifest_path": str(parsed_bundle.manifest_path) if parsed_bundle is not None else "",
        },
        "rewrite": {
            "target_grade": target_grade,
            "target_profile": target_profile,
            "model": rewrite_model,
            "bundle_dir": str(getattr(rewrite_output, "output_dir", "")) if rewrite_output is not None else "",
            "vocabulary_source": {
                "kind": vocabulary_source.kind,
                "path": str(vocabulary_source.path),
                "sha256": vocabulary_source.sha256,
            },
        },
        "printable": {
            "target_grade": target_grade,
            "target_profile": target_profile,
            "model": print_model,
            "bundle_dir": str(print_output_dir or ""),
            "manifest_path": str(printable_manifest or ""),
            "docx_path": str(printable_docx or ""),
            "accepted": bool(getattr(print_output, "accepted", False)) if print_output is not None else False,
        },
    }
