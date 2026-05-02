from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import fitz
import pymupdf4llm
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from wordbyword._json_io import jsonable, write_json
from wordbyword.source_parsing.paddleocr_vl import (
    PaddleOCRVLInterruptedError,
    PaddleOCRVLPageResult,
    PaddleOCRVLProgress,
    PaddleOCRVLRuntimeError,
    PaddleOCRVLSettings,
    run_paddleocr_vl,
)

WORKSPACE_SCHEMA_VERSION = "wordbyword.source_material_workspace.v1"
PDF_PARSE_SCHEMA_VERSION = "wordbyword.heuristic_pdf_parse.v1"
PADDLEOCR_VL_PARSE_SCHEMA_VERSION = "wordbyword.paddleocr_vl_pdf_parse.v1"
PdfParserName = Literal["heuristic", "paddleocr-vl"]
SourceMaterialProgressPhase = Literal["start", "render", "load", "ocr", "write", "complete"]
DEFAULT_SOURCE_MATERIAL_ROOT = Path("class-source-materials")
DEFAULT_PARSED_SOURCE_ROOT = DEFAULT_SOURCE_MATERIAL_ROOT / "parsed"
SUPPORTED_SOURCE_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".xhtml",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".markdown",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
}
REMOVED_GENERATED_FILES = {
    "canonical_content.json",
    "canonical_blocks.json",
    "canonical_blocks.jsonl",
    "chunks.json",
    "content.md",
    "document_model.json",
    "parser_output.json",
    "quality_report.json",
}
REMOVED_ASSET_SUBFOLDERS = {
    "drawings",
    "images",
    "pages",
    "tables",
}
PDF_MARKDOWN_IMAGE_PATTERN = re.compile(r"(!\[[^\]]*]\()([^)]+)(\))")


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    filename: str
    extension: str
    mime_type: str
    sha256: str
    byte_size: int = Field(ge=0)
    prepared_at: str


class ParserMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["parsed", "not_run", "skipped"]
    name: str
    version: str = ""
    reason: str = ""
    parsed_at: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class FileManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: str = "assets/"
    content_markdown: str | None = None
    chunks_json: str | None = None
    document_model_json: str | None = None
    parser_output_json: str | None = None
    quality_report_json: str | None = None


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    source: SourceMetadata
    parser: ParserMetadata
    files: FileManifest


class AssetMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    kind: Literal["page_render", "extracted_image", "unknown"]
    page: int | None = None
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    mode: str = ""
    mime_type: str = ""
    byte_size: int = Field(ge=0)


class PageQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    width: float = Field(ge=0)
    height: float = Field(ge=0)
    rotation: int
    selectable_text_chars: int = Field(ge=0)
    markdown_chars: int = Field(ge=0)
    image_blocks: int = Field(ge=0)
    drawing_count: int = Field(ge=0)
    pymupdf_table_count: int = Field(ge=0)
    needs_ocr: bool
    page_render_asset: str


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    parser: ParserMetadata
    source: SourceMetadata
    page_count: int = Field(ge=0)
    markdown_chars: int = Field(ge=0)
    markdown_words: int = Field(ge=0)
    asset_count: int = Field(ge=0)
    page_render_count: int = Field(ge=0)
    extracted_image_count: int = Field(ge=0)
    needs_ocr_pages: list[int]
    warnings: list[str]
    pages: list[PageQuality]
    assets: list[AssetMetadata]


class PageChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    page: int
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    toc_items: list[Any] = Field(default_factory=list)
    page_boxes: list[Any] = Field(default_factory=list)


class ParsedPdfDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    source: SourceMetadata
    parser: ParserMetadata
    content_markdown_path: str
    chunks_path: str
    parser_output_path: str
    quality_report_path: str
    pages: list[PageQuality]
    assets: list[AssetMetadata]


@dataclass(frozen=True)
class SourceMaterialBundle:
    source_id: str
    source_path: Path
    output_dir: Path
    manifest_path: Path
    assets_dir: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class SourceMaterialBatchResult:
    source_dir: Path
    output_root: Path
    source_paths: list[Path]
    bundles: list[SourceMaterialBundle]


@dataclass(frozen=True)
class SourceMaterialProgress:
    phase: SourceMaterialProgressPhase
    source_path: Path
    parser: PdfParserName
    detail: str
    source_index: int = 1
    total_sources: int = 1
    completed_pages: int = 0
    total_pages: int = 0


SourceMaterialProgressCallback = Callable[[SourceMaterialProgress], None]


class SourceMaterialParseError(RuntimeError):
    """Raised when a source material workspace cannot be prepared or parsed."""


def parse_source_material(
    source_path: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
    source_id: str | None = None,
    parse_pdf: bool = True,
    pdf_parser: PdfParserName = "heuristic",
    render_pages: bool = True,
    render_dpi: int = 144,
    image_dpi: int = 180,
    paddleocr_vl_settings: PaddleOCRVLSettings | None = None,
    progress: SourceMaterialProgressCallback | None = None,
    source_index: int = 1,
    total_sources: int = 1,
) -> SourceMaterialBundle:
    source_path = source_path.expanduser()
    if not source_path.is_file():
        raise SourceMaterialParseError(f"Source material does not exist: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SOURCE_EXTENSIONS:
        raise SourceMaterialParseError(
            f"Unsupported source material format: {suffix or source_path.name}"
        )

    resolved_source_id = source_id or source_id_for(source_path)
    output_dir = output_root / resolved_source_id
    assets_dir = output_dir / "assets"
    _emit_source_material_progress(
        progress,
        phase="start",
        source_path=source_path,
        parser=pdf_parser,
        detail=f"preparing {source_path.name}",
        source_index=source_index,
        total_sources=total_sources,
    )
    _prepare_workspace(output_dir, assets_dir)

    source_metadata = _source_metadata(source_path=source_path, source_id=resolved_source_id)
    if suffix == ".pdf" and parse_pdf:
        parser_metadata = _parser_metadata(
            status="parsed",
            pdf_parser=pdf_parser,
            reason="",
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
        )
        if pdf_parser == "paddleocr-vl":
            parse_result = _parse_pdf_with_paddleocr_vl(
                source_path=source_path,
                output_dir=output_dir,
                assets_dir=assets_dir,
                source_metadata=source_metadata,
                parser_metadata=parser_metadata,
                render_dpi=render_dpi,
                settings=paddleocr_vl_settings or PaddleOCRVLSettings(),
                progress=progress,
                source_index=source_index,
                total_sources=total_sources,
            )
        else:
            parse_result = _parse_pdf(
                source_path=source_path,
                output_dir=output_dir,
                assets_dir=assets_dir,
                source_metadata=source_metadata,
                parser_metadata=parser_metadata,
                render_pages=render_pages,
                render_dpi=render_dpi,
                image_dpi=image_dpi,
            )
        manifest = _build_parsed_manifest(
            source_metadata=source_metadata,
            parser_metadata=parse_result.parser,
        )
    else:
        parser_metadata = _parser_metadata(
            status="skipped" if suffix != ".pdf" else "not_run",
            pdf_parser=pdf_parser,
            reason=(
                "Only PDF parsing is implemented in the current heuristic parser."
                if suffix != ".pdf"
                else "PDF parsing was disabled for this run."
            ),
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
            paddleocr_vl_settings=paddleocr_vl_settings,
        )
        manifest = _build_workspace_manifest(
            source_metadata=source_metadata,
            parser_metadata=parser_metadata,
        )

    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    _emit_source_material_progress(
        progress,
        phase="complete",
        source_path=source_path,
        parser=pdf_parser,
        detail=f"prepared {resolved_source_id}",
        source_index=source_index,
        total_sources=total_sources,
    )
    return SourceMaterialBundle(
        source_id=resolved_source_id,
        source_path=source_path,
        output_dir=output_dir,
        manifest_path=manifest_path,
        assets_dir=assets_dir,
        manifest=manifest,
    )


def parse_source_materials(
    source_dir: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
    parse_pdf: bool = True,
    pdf_parser: PdfParserName = "heuristic",
    render_pages: bool = True,
    render_dpi: int = 144,
    image_dpi: int = 180,
    paddleocr_vl_settings: PaddleOCRVLSettings | None = None,
    progress: SourceMaterialProgressCallback | None = None,
) -> SourceMaterialBatchResult:
    source_paths = discover_source_materials(source_dir, output_root=output_root)
    used_source_ids: set[str] = set()
    bundles: list[SourceMaterialBundle] = []
    for index, source_path in enumerate(source_paths, start=1):
        bundles.append(
            parse_source_material(
                source_path,
                output_root=output_root,
                source_id=_unique_source_id(source_path, source_dir, used_source_ids),
                parse_pdf=parse_pdf,
                pdf_parser=pdf_parser,
                render_pages=render_pages,
                render_dpi=render_dpi,
                image_dpi=image_dpi,
                paddleocr_vl_settings=paddleocr_vl_settings,
                progress=progress,
                source_index=index,
                total_sources=len(source_paths),
            )
        )
    return SourceMaterialBatchResult(
        source_dir=source_dir,
        output_root=output_root,
        source_paths=source_paths,
        bundles=bundles,
    )


def discover_source_materials(
    source_dir: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
) -> list[Path]:
    source_dir = source_dir.expanduser()
    if not source_dir.is_dir():
        raise SourceMaterialParseError(f"Source material directory does not exist: {source_dir}")

    resolved_output_root = output_root.expanduser().resolve()
    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_SOURCE_EXTENSIONS
        and not _is_relative_to(path.resolve(), resolved_output_root)
    )


def source_id_for(source_path: Path) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", source_path.stem.strip()).strip("-._")
    return cleaned.lower() or "source-material"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _emit_source_material_progress(
    progress: SourceMaterialProgressCallback | None,
    *,
    phase: SourceMaterialProgressPhase,
    source_path: Path,
    parser: PdfParserName,
    detail: str,
    source_index: int = 1,
    total_sources: int = 1,
    completed_pages: int = 0,
    total_pages: int = 0,
) -> None:
    if progress is None:
        return
    progress(
        SourceMaterialProgress(
            phase=phase,
            source_path=source_path,
            parser=parser,
            detail=detail,
            source_index=source_index,
            total_sources=total_sources,
            completed_pages=completed_pages,
            total_pages=total_pages,
        )
    )


def _paddleocr_progress_adapter(
    *,
    progress: SourceMaterialProgressCallback | None,
    source_path: Path,
    source_index: int,
    total_sources: int,
) -> Callable[[PaddleOCRVLProgress], None] | None:
    if progress is None:
        return None

    def report(paddle_progress: PaddleOCRVLProgress) -> None:
        _emit_source_material_progress(
            progress,
            phase=paddle_progress.phase,
            source_path=source_path,
            parser="paddleocr-vl",
            detail=paddle_progress.detail,
            source_index=source_index,
            total_sources=total_sources,
            completed_pages=paddle_progress.completed_pages,
            total_pages=paddle_progress.total_pages,
        )

    return report


def _parse_pdf(
    *,
    source_path: Path,
    output_dir: Path,
    assets_dir: Path,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
    render_pages: bool,
    render_dpi: int,
    image_dpi: int,
) -> ParsedPdfDocument:
    markdown_chunks = _extract_markdown_chunks(
        source_path=source_path,
        assets_dir=assets_dir,
        image_dpi=image_dpi,
    )
    content_markdown = _compose_markdown(markdown_chunks)
    content_markdown = _rewrite_markdown_asset_links(content_markdown, assets_dir=assets_dir)
    content_path = output_dir / "content.md"
    content_path.write_text(content_markdown, encoding="utf-8")

    chunks = [
        PageChunk(
            page=index,
            text=_rewrite_markdown_asset_links(str(chunk.get("text", "")), assets_dir=assets_dir),
            metadata=jsonable(chunk.get("metadata", {})),
            toc_items=jsonable(chunk.get("toc_items", [])),
            page_boxes=jsonable(chunk.get("page_boxes", [])),
        )
        for index, chunk in enumerate(markdown_chunks, start=1)
    ]
    chunks_path = output_dir / "chunks.json"
    _write_json(chunks_path, [chunk.model_dump(mode="json") for chunk in chunks])

    parser_output = _extract_parser_output_json(source_path=source_path)
    parser_output_path = output_dir / "parser_output.json"
    _write_json(parser_output_path, parser_output)

    pages, page_render_assets = _inspect_pdf_pages(
        source_path=source_path,
        assets_dir=assets_dir,
        chunks=chunks,
        render_pages=render_pages,
        render_dpi=render_dpi,
    )
    assets = _collect_assets(
        assets_dir=assets_dir,
        page_render_assets=page_render_assets,
    )
    quality_report = _quality_report(
        source_metadata=source_metadata,
        parser_metadata=parser_metadata,
        content_markdown=content_markdown,
        pages=pages,
        assets=assets,
    )
    quality_report_path = output_dir / "quality_report.json"
    _write_json(quality_report_path, quality_report.model_dump(mode="json"))

    parsed_document = ParsedPdfDocument(
        schema_version=PDF_PARSE_SCHEMA_VERSION,
        source=source_metadata,
        parser=parser_metadata,
        content_markdown_path="content.md",
        chunks_path="chunks.json",
        parser_output_path="parser_output.json",
        quality_report_path="quality_report.json",
        pages=pages,
        assets=assets,
    )
    document_model_path = output_dir / "document_model.json"
    _write_json(document_model_path, parsed_document.model_dump(mode="json"))
    return parsed_document


def _parse_pdf_with_paddleocr_vl(
    *,
    source_path: Path,
    output_dir: Path,
    assets_dir: Path,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
    render_dpi: int,
    settings: PaddleOCRVLSettings,
    progress: SourceMaterialProgressCallback | None = None,
    source_index: int = 1,
    total_sources: int = 1,
) -> ParsedPdfDocument:
    try:
        return _parse_pdf_with_paddleocr_vl_impl(
            source_path=source_path,
            output_dir=output_dir,
            assets_dir=assets_dir,
            source_metadata=source_metadata,
            parser_metadata=parser_metadata,
            render_dpi=render_dpi,
            settings=settings,
            progress=progress,
            source_index=source_index,
            total_sources=total_sources,
        )
    except PaddleOCRVLInterruptedError as error:
        _cleanup_interrupted_workspace(output_dir)
        raise KeyboardInterrupt from error
    except KeyboardInterrupt:
        _cleanup_interrupted_workspace(output_dir)
        raise


def _parse_pdf_with_paddleocr_vl_impl(
    *,
    source_path: Path,
    output_dir: Path,
    assets_dir: Path,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
    render_dpi: int,
    settings: PaddleOCRVLSettings,
    progress: SourceMaterialProgressCallback | None = None,
    source_index: int = 1,
    total_sources: int = 1,
) -> ParsedPdfDocument:
    page_render_assets_by_page = _render_pdf_page_images(
        source_path=source_path,
        assets_dir=assets_dir,
        dpi=render_dpi,
        progress=progress,
        parser="paddleocr-vl",
        source_index=source_index,
        total_sources=total_sources,
    )
    page_image_paths = [
        assets_dir / Path(page_render_assets_by_page[page]).name
        for page in sorted(page_render_assets_by_page)
    ]
    incremental_pages: list[PaddleOCRVLPageResult] = []

    def save_incremental_page(page_result: PaddleOCRVLPageResult) -> None:
        incremental_pages.append(page_result)
        _write_paddleocr_vl_merged_outputs(
            output_dir=output_dir,
            source_metadata=source_metadata,
            parser_metadata=parser_metadata,
            settings=settings,
            page_render_assets_by_page=page_render_assets_by_page,
            page_results=incremental_pages,
            complete=False,
        )

    try:
        paddle_result = run_paddleocr_vl(
            page_image_paths=page_image_paths,
            assets_dir=assets_dir,
            settings=settings,
            progress=_paddleocr_progress_adapter(
                progress=progress,
                source_path=source_path,
                source_index=source_index,
                total_sources=total_sources,
            ),
            page_result_callback=save_incremental_page,
        )
    except PaddleOCRVLInterruptedError:
        raise
    except PaddleOCRVLRuntimeError as error:
        raise SourceMaterialParseError(str(error)) from error

    _emit_source_material_progress(
        progress,
        phase="write",
        source_path=source_path,
        parser="paddleocr-vl",
        detail="writing PaddleOCR-VL parse bundle",
        source_index=source_index,
        total_sources=total_sources,
        completed_pages=len(paddle_result.pages),
        total_pages=len(page_image_paths),
    )
    parser_metadata = parser_metadata.model_copy(
        update={"version": paddle_result.parser_version}
    )
    content_markdown, chunks = _write_paddleocr_vl_merged_outputs(
        output_dir=output_dir,
        source_metadata=source_metadata,
        parser_metadata=parser_metadata,
        settings=paddle_result.settings,
        page_render_assets_by_page=page_render_assets_by_page,
        page_results=paddle_result.pages,
        complete=True,
    )

    pages, page_render_assets = _inspect_pdf_pages(
        source_path=source_path,
        assets_dir=assets_dir,
        chunks=chunks,
        render_pages=False,
        render_dpi=render_dpi,
        rendered_page_assets=page_render_assets_by_page,
    )
    assets = _collect_assets(
        assets_dir=assets_dir,
        page_render_assets=page_render_assets,
    )
    quality_report = _quality_report(
        source_metadata=source_metadata,
        parser_metadata=parser_metadata,
        content_markdown=content_markdown,
        pages=pages,
        assets=assets,
        schema_version=PADDLEOCR_VL_PARSE_SCHEMA_VERSION,
    )
    quality_report_path = output_dir / "quality_report.json"
    _write_json(quality_report_path, quality_report.model_dump(mode="json"))

    parsed_document = ParsedPdfDocument(
        schema_version=PADDLEOCR_VL_PARSE_SCHEMA_VERSION,
        source=source_metadata,
        parser=parser_metadata,
        content_markdown_path="content.md",
        chunks_path="chunks.json",
        parser_output_path="parser_output.json",
        quality_report_path="quality_report.json",
        pages=pages,
        assets=assets,
    )
    document_model_path = output_dir / "document_model.json"
    _write_json(document_model_path, parsed_document.model_dump(mode="json"))
    _emit_source_material_progress(
        progress,
        phase="write",
        source_path=source_path,
        parser="paddleocr-vl",
        detail="wrote PaddleOCR-VL parse bundle",
        source_index=source_index,
        total_sources=total_sources,
        completed_pages=len(paddle_result.pages),
        total_pages=len(page_image_paths),
    )
    return parsed_document


def _write_paddleocr_vl_merged_outputs(
    *,
    output_dir: Path,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
    settings: PaddleOCRVLSettings,
    page_render_assets_by_page: dict[int, str],
    page_results: list[PaddleOCRVLPageResult],
    complete: bool,
) -> tuple[str, list[PageChunk]]:
    sorted_page_results = sorted(page_results, key=lambda page: page.page)
    content_sections = []
    for page in sorted_page_results:
        text = page.markdown.strip() or "_No text extracted on this page._"
        content_sections.append(f"<!-- page:{page.page} -->\n\n{text}")
    content_markdown = "\n\n---\n\n".join(content_sections).strip() + "\n"
    content_path = output_dir / "content.md"
    content_path.write_text(content_markdown, encoding="utf-8")

    chunks = [
        PageChunk(
            page=page.page,
            text=page.markdown,
            metadata={
                "parser": "paddleocr-vl",
                "input_image_path": page.input_image_path,
                "markdown_asset_paths": page.markdown_asset_paths,
            },
        )
        for page in sorted_page_results
    ]
    chunks_path = output_dir / "chunks.json"
    _write_json(chunks_path, [chunk.model_dump(mode="json") for chunk in chunks])

    parser_output = {
        "schema_version": PADDLEOCR_VL_PARSE_SCHEMA_VERSION,
        "complete": complete,
        "completed_pages": len(sorted_page_results),
        "page_count": len(page_render_assets_by_page),
        "source": source_metadata.model_dump(mode="json"),
        "parser": parser_metadata.model_dump(mode="json"),
        "settings": {
            key: value
            for key, value in asdict(settings).items()
            if value is not None
        },
        "page_images": {
            str(page): asset_path
            for page, asset_path in sorted(page_render_assets_by_page.items())
        },
        "page_results": [
            {
                "page": page.page,
                "input_image_path": page.input_image_path,
                "markdown": page.markdown,
                "markdown_asset_paths": page.markdown_asset_paths,
                "raw_json": page.raw_json,
            }
            for page in sorted_page_results
        ],
    }
    parser_output_path = output_dir / "parser_output.json"
    _write_json(parser_output_path, parser_output)
    return content_markdown, chunks


def _extract_markdown_chunks(*, source_path: Path, assets_dir: Path, image_dpi: int) -> list[dict[str, Any]]:
    raw_chunks = pymupdf4llm.to_markdown(
        source_path.as_posix(),
        write_images=True,
        image_path=assets_dir.as_posix(),
        image_format="png",
        image_size_limit=0.03,
        page_chunks=True,
        page_separators=False,
        show_progress=False,
        dpi=image_dpi,
        use_ocr=False,
    )
    if not isinstance(raw_chunks, list):
        raise SourceMaterialParseError("PyMuPDF4LLM did not return page chunks.")
    return [dict(chunk) for chunk in raw_chunks]


def _extract_parser_output_json(*, source_path: Path) -> dict[str, Any]:
    raw_output = pymupdf4llm.to_json(
        source_path.as_posix(),
        write_images=False,
        embed_images=False,
        show_progress=False,
        use_ocr=False,
    )
    if isinstance(raw_output, str):
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as error:
            raise SourceMaterialParseError(f"PyMuPDF4LLM JSON output is invalid: {error.msg}") from error
    else:
        parsed = raw_output
    if not isinstance(parsed, dict):
        raise SourceMaterialParseError("PyMuPDF4LLM JSON output is not a JSON object.")
    return jsonable(parsed)


def _compose_markdown(chunks: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for page_number, chunk in enumerate(chunks, start=1):
        text = str(chunk.get("text", "")).strip()
        if not text:
            text = "_No text extracted on this page._"
        sections.append(f"<!-- page:{page_number} -->\n\n{text}")
    return "\n\n---\n\n".join(sections).strip() + "\n"


def _rewrite_markdown_asset_links(markdown: str, *, assets_dir: Path) -> str:
    assets_dir_resolved = assets_dir.resolve()

    def replace(match: re.Match[str]) -> str:
        target = match.group(2)
        if target.startswith(("http://", "https://", "data:")):
            return match.group(0)
        if target.startswith("assets/"):
            return match.group(0)
        target_path = Path(target)
        try:
            relative = target_path.resolve().relative_to(assets_dir_resolved)
        except ValueError:
            return match.group(0)
        return f"{match.group(1)}assets/{relative.as_posix()}{match.group(3)}"

    return PDF_MARKDOWN_IMAGE_PATTERN.sub(replace, markdown)


def _inspect_pdf_pages(
    *,
    source_path: Path,
    assets_dir: Path,
    chunks: list[PageChunk],
    render_pages: bool,
    render_dpi: int,
    rendered_page_assets: dict[int, str] | None = None,
) -> tuple[list[PageQuality], set[str]]:
    pages: list[PageQuality] = []
    page_render_assets: set[str] = set()
    chunk_by_page = {chunk.page: chunk for chunk in chunks}
    with fitz.open(source_path) as document:
        for index, page in enumerate(document, start=1):
            raw_text = page.get_text("text", sort=True)
            image_blocks = _image_block_count(page)
            drawing_count = _drawing_count(page)
            table_count = _table_count(page)
            render_asset = (rendered_page_assets or {}).get(index, "")
            if render_asset:
                page_render_assets.add(render_asset)
            elif render_pages:
                render_asset = _render_page(page=page, page_number=index, assets_dir=assets_dir, dpi=render_dpi)
                page_render_assets.add(render_asset)
            chunk = chunk_by_page.get(index)
            markdown_chars = len(chunk.text) if chunk is not None else 0
            pages.append(
                PageQuality(
                    page=index,
                    width=round(float(page.rect.width), 2),
                    height=round(float(page.rect.height), 2),
                    rotation=int(page.rotation),
                    selectable_text_chars=len(raw_text.strip()),
                    markdown_chars=markdown_chars,
                    image_blocks=image_blocks,
                    drawing_count=drawing_count,
                    pymupdf_table_count=table_count,
                    needs_ocr=len(raw_text.strip()) < 20 and markdown_chars < 20,
                    page_render_asset=render_asset,
                )
            )
    return pages, page_render_assets


def _render_pdf_page_images(
    *,
    source_path: Path,
    assets_dir: Path,
    dpi: int,
    progress: SourceMaterialProgressCallback | None = None,
    parser: PdfParserName = "heuristic",
    source_index: int = 1,
    total_sources: int = 1,
) -> dict[int, str]:
    page_assets: dict[int, str] = {}
    with fitz.open(source_path) as document:
        total_pages = document.page_count
        _emit_source_material_progress(
            progress,
            phase="render",
            source_path=source_path,
            parser=parser,
            detail=f"rendering {total_pages:,} page image(s)",
            source_index=source_index,
            total_sources=total_sources,
            completed_pages=0,
            total_pages=total_pages,
        )
        for index, page in enumerate(document, start=1):
            page_assets[index] = _render_page(
                page=page,
                page_number=index,
                assets_dir=assets_dir,
                dpi=dpi,
            )
            _emit_source_material_progress(
                progress,
                phase="render",
                source_path=source_path,
                parser=parser,
                detail=f"rendered page {index:,}/{total_pages:,}",
                source_index=source_index,
                total_sources=total_sources,
                completed_pages=index,
                total_pages=total_pages,
            )
    return page_assets


def _render_page(*, page: fitz.Page, page_number: int, assets_dir: Path, dpi: int) -> str:
    filename = f"page-{page_number:04d}.png"
    path = assets_dir / filename
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    pixmap.save(path)
    return f"assets/{filename}"


def _image_block_count(page: fitz.Page) -> int:
    try:
        raw = page.get_text("dict", sort=True)
    except Exception:
        return 0
    return sum(1 for block in raw.get("blocks", []) if int(block.get("type", -1)) == 1)


def _drawing_count(page: fitz.Page) -> int:
    try:
        return len(page.get_drawings())
    except Exception:
        return 0


def _table_count(page: fitz.Page) -> int:
    find_tables = getattr(page, "find_tables", None)
    if find_tables is None:
        return 0
    try:
        return len(find_tables().tables)
    except Exception:
        return 0


def _collect_assets(*, assets_dir: Path, page_render_assets: set[str]) -> list[AssetMetadata]:
    assets: list[AssetMetadata] = []
    for path in sorted(asset for asset in assets_dir.iterdir() if asset.is_file()):
        relative_path = f"assets/{path.name}"
        width, height, mode = _image_dimensions(path)
        assets.append(
            AssetMetadata(
                id=path.stem,
                path=relative_path,
                kind="page_render" if relative_path in page_render_assets else _asset_kind(path),
                page=_page_number_from_asset(path.name),
                width=width,
                height=height,
                mode=mode,
                mime_type=mimetypes.guess_type(path.name)[0] or "",
                byte_size=path.stat().st_size,
            )
        )
    return assets


def _image_dimensions(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height), str(image.mode)
    except Exception:
        return 0, 0, ""


def _asset_kind(path: Path) -> Literal["page_render", "extracted_image", "unknown"]:
    if path.name.startswith("page-"):
        return "page_render"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
        return "extracted_image"
    return "unknown"


def _page_number_from_asset(filename: str) -> int | None:
    page_render = re.match(r"page-(\d{4})\.", filename)
    if page_render is not None:
        return int(page_render.group(1))
    extracted = re.search(r"\.pdf-(\d{4})-", filename)
    if extracted is not None:
        return int(extracted.group(1))
    return None


def _quality_report(
    *,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
    content_markdown: str,
    pages: list[PageQuality],
    assets: list[AssetMetadata],
    schema_version: str = PDF_PARSE_SCHEMA_VERSION,
) -> QualityReport:
    warnings: list[str] = []
    needs_ocr_pages = [page.page for page in pages if page.needs_ocr]
    if needs_ocr_pages:
        warnings.append(
            "Some pages have little or no selectable text and should be passed to an OCR or vision parser."
        )
    if not content_markdown.strip():
        warnings.append("No Markdown content was extracted.")
    if any(page.pymupdf_table_count > 0 for page in pages):
        warnings.append(
            "PyMuPDF detected table-like regions; inspect Markdown tables and consider pdfplumber debugging if quality is poor."
        )

    return QualityReport(
        schema_version=schema_version,
        parser=parser_metadata,
        source=source_metadata,
        page_count=len(pages),
        markdown_chars=len(content_markdown),
        markdown_words=len(re.findall(r"\S+", content_markdown)),
        asset_count=len(assets),
        page_render_count=sum(1 for asset in assets if asset.kind == "page_render"),
        extracted_image_count=sum(1 for asset in assets if asset.kind == "extracted_image"),
        needs_ocr_pages=needs_ocr_pages,
        warnings=warnings,
        pages=pages,
        assets=assets,
    )


def _prepare_workspace(output_dir: Path, assets_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in REMOVED_GENERATED_FILES:
        path = output_dir / filename
        if path.is_file():
            path.unlink()

    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    for folder_name in REMOVED_ASSET_SUBFOLDERS:
        path = assets_dir / folder_name
        if path.is_dir():
            shutil.rmtree(path)


def _cleanup_interrupted_workspace(output_dir: Path) -> None:
    if output_dir.is_dir():
        shutil.rmtree(output_dir)


def _source_metadata(*, source_path: Path, source_id: str) -> SourceMetadata:
    suffix = source_path.suffix.lower()
    return SourceMetadata(
        id=source_id,
        path=source_path.as_posix(),
        filename=source_path.name,
        extension=suffix,
        mime_type=mimetypes.guess_type(source_path.name)[0] or "",
        sha256=sha256_file(source_path),
        byte_size=source_path.stat().st_size,
        prepared_at=datetime.now(timezone.utc).isoformat(),
    )


def _parser_metadata(
    *,
    status: Literal["parsed", "not_run", "skipped"],
    pdf_parser: PdfParserName,
    reason: str,
    render_pages: bool,
    render_dpi: int,
    image_dpi: int,
    paddleocr_vl_settings: PaddleOCRVLSettings | None = None,
) -> ParserMetadata:
    if status == "parsed" and pdf_parser == "paddleocr-vl":
        settings = paddleocr_vl_settings or PaddleOCRVLSettings()
        return ParserMetadata(
            status=status,
            name="paddleocr-vl-1.5",
            version="",
            reason=reason,
            parsed_at=datetime.now(timezone.utc).isoformat(),
            settings={
                "primary_output": "content.md",
                "page_image_inputs": True,
                "render_dpi": render_dpi,
                "image_workflow": "PDF pages are rendered to PNG before PaddleOCR-VL inference.",
                "pipeline_version": "v1.5",
                "asset_layout": "flat assets/ folder per source material",
                **{key: value for key, value in asdict(settings).items() if value is not None},
            },
        )
    return ParserMetadata(
        status=status,
        name="pymupdf4llm+pymupdf+pillow" if status == "parsed" else "",
        version=str(getattr(pymupdf4llm, "__version__", getattr(pymupdf4llm, "VERSION", "")))
        if status == "parsed"
        else "",
        reason=reason,
        parsed_at=datetime.now(timezone.utc).isoformat() if status == "parsed" else None,
        settings={
            "primary_output": "content.md",
            "markdown_page_chunks": True,
            "write_extracted_images": status == "parsed",
            "image_dpi": image_dpi,
            "render_pages": render_pages,
            "render_dpi": render_dpi,
            "ocr": "disabled; low-text pages are flagged in quality_report.json",
            "asset_layout": "flat assets/ folder per source material",
        },
    )


def _build_workspace_manifest(
    *,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
) -> dict[str, Any]:
    manifest = SourceManifest(
        schema_version=WORKSPACE_SCHEMA_VERSION,
        source=source_metadata,
        parser=parser_metadata,
        files=FileManifest(),
    )
    return manifest.model_dump(mode="json", exclude_none=True)


def _build_parsed_manifest(
    *,
    source_metadata: SourceMetadata,
    parser_metadata: ParserMetadata,
) -> dict[str, Any]:
    manifest = SourceManifest(
        schema_version=WORKSPACE_SCHEMA_VERSION,
        source=source_metadata,
        parser=parser_metadata,
        files=FileManifest(
            content_markdown="content.md",
            chunks_json="chunks.json",
            document_model_json="document_model.json",
            parser_output_json="parser_output.json",
            quality_report_json="quality_report.json",
        ),
    )
    return manifest.model_dump(mode="json", exclude_none=True)


def _write_json(path: Path, payload: Any) -> None:
    write_json(path, payload)


def _unique_source_id(source_path: Path, source_dir: Path, used_source_ids: set[str]) -> str:
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

    digest = hashlib.sha256(source_path.as_posix().encode("utf-8")).hexdigest()[:8]
    source_id = f"{source_id}-{digest}"
    used_source_ids.add(source_id)
    return source_id


def _is_relative_to(path: Path, possible_parent: Path) -> bool:
    try:
        path.relative_to(possible_parent)
    except ValueError:
        return False
    return True
