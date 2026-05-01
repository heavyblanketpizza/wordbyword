from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import time
from base64 import b64encode
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol
from urllib import error, request

import fitz
import pymupdf4llm
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

WORKSPACE_SCHEMA_VERSION = "wordbyword.source_material_workspace.v1"
PDF_PARSE_SCHEMA_VERSION = "wordbyword.heuristic_pdf_parse.v1"
AGENTIC_OLLAMA_PARSE_SCHEMA_VERSION = "wordbyword.agentic_ollama_pdf_parse.v1"
AgenticOllamaResponseFormat = Literal["schema", "json", "none"]
AgenticCapturedAssetKind = Literal["table", "figure", "photo", "diagram", "chart", "equation", "other"]
DEFAULT_AGENTIC_OLLAMA_MODEL = "gemma4:31b"
DEFAULT_AGENTIC_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_AGENTIC_OLLAMA_RESPONSE_FORMAT: AgenticOllamaResponseFormat = "schema"
DEFAULT_AGENTIC_OLLAMA_WORKERS = 1
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
    "agentic_ollama_content.md",
    "agentic_ollama_assets.json",
    "agentic_ollama_pages.json",
    "agentic_ollama_report.json",
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

    status: Literal["parsed", "not_run", "skipped", "partial", "failed"]
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
    agentic_ollama_content_markdown: str | None = None
    agentic_ollama_assets_json: str | None = None
    agentic_ollama_pages_json: str | None = None
    agentic_ollama_report_json: str | None = None


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    source: SourceMetadata
    parser: ParserMetadata
    agentic_parsers: list[ParserMetadata] = Field(default_factory=list)
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


class AgenticOllamaTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    caption: str = ""
    markdown: str = ""
    notes: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


class AgenticOllamaFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    kind: Literal["figure", "photo", "diagram", "chart", "equation", "other"] = "figure"
    caption: str = ""
    visible_text: str = ""
    description: str = ""
    page_region: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


class AgenticOllamaParsedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    reading_order_markdown: str
    headings: list[str] = Field(default_factory=list)
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[AgenticOllamaTable] = Field(default_factory=list)
    figures: list[AgenticOllamaFigure] = Field(default_factory=list)
    footnotes_or_footer: list[str] = Field(default_factory=list)
    uncertain_text: list[str] = Field(default_factory=list)
    parser_notes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class AgenticOllamaCapturedAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    page: int
    kind: AgenticCapturedAssetKind
    source_image_path: str
    caption: str = ""
    markdown: str = ""
    visible_text: str = ""
    description: str = ""
    page_region: str = ""
    notes: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


class AgenticOllamaPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    image_path: str
    status: Literal["parsed", "failed"]
    markdown: str = ""
    parsed_json: dict[str, Any] | None = None
    raw_response: str = ""
    error: str = ""
    duration_seconds: float = Field(ge=0)


class AgenticOllamaReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    parser: ParserMetadata
    source: SourceMetadata
    page_count: int = Field(ge=0)
    attempted_pages: int = Field(ge=0)
    parsed_pages: int = Field(ge=0)
    failed_pages: int = Field(ge=0)
    skipped_pages: int = Field(ge=0)
    captured_asset_count: int = Field(ge=0)
    captured_table_count: int = Field(ge=0)
    captured_figure_count: int = Field(ge=0)
    warnings: list[str]
    pages: list[AgenticOllamaPageResult]


@dataclass(frozen=True)
class OllamaVisionResponse:
    content: str
    raw: dict[str, Any]


class SupportsOllamaVisionClient(Protocol):
    def parse_page(
        self,
        *,
        image_path: Path,
        page_number: int,
        page_count: int,
        prompt: str,
    ) -> OllamaVisionResponse: ...


@dataclass(frozen=True)
class AgenticOllamaProgress:
    phase: Literal["start", "page", "complete"]
    source_id: str
    source_filename: str
    model: str
    page_number: int
    attempted_pages: int
    document_pages: int
    completed_pages: int
    parsed_pages: int
    failed_pages: int
    skipped_pages: int
    current_image_path: str = ""


AgenticOllamaProgressCallback = Callable[[AgenticOllamaProgress], None]


class OllamaVisionClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str = DEFAULT_AGENTIC_OLLAMA_BASE_URL,
        timeout_seconds: float = 300.0,
        temperature: float = 0.0,
        max_output_tokens: int = 4096,
        response_format: AgenticOllamaResponseFormat = DEFAULT_AGENTIC_OLLAMA_RESPONSE_FORMAT,
        json_mode: bool | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.response_format = response_format
        if json_mode is not None:
            self.response_format = "json" if json_mode else "none"

    def parse_page(
        self,
        *,
        image_path: Path,
        page_number: int,
        page_count: int,
        prompt: str,
    ) -> OllamaVisionResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64encode(image_path.read_bytes()).decode("ascii")],
                }
            ],
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_output_tokens,
            },
        }
        if self.response_format == "schema":
            body["format"] = AgenticOllamaParsedPage.model_json_schema()
        elif self.response_format == "json":
            body["format"] = "json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SourceMaterialParseError(
                f"Ollama request failed for page {page_number}/{page_count} "
                f"with HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise SourceMaterialParseError(
                f"Ollama request failed for page {page_number}/{page_count}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise SourceMaterialParseError("Ollama returned invalid JSON.") from exc

        message = raw.get("message")
        if not isinstance(message, Mapping):
            raise SourceMaterialParseError("Ollama response did not include a message object.")
        content = message.get("content")
        return OllamaVisionResponse(
            content=content if isinstance(content, str) else "",
            raw=_jsonable(raw),
        )


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


class SourceMaterialParseError(RuntimeError):
    """Raised when a source material workspace cannot be prepared or parsed."""


def parse_source_material(
    source_path: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
    source_id: str | None = None,
    parse_pdf: bool = True,
    render_pages: bool = True,
    render_dpi: int = 144,
    image_dpi: int = 180,
    agentic_ollama_parse: bool = False,
    agentic_ollama_model: str = DEFAULT_AGENTIC_OLLAMA_MODEL,
    agentic_ollama_base_url: str = DEFAULT_AGENTIC_OLLAMA_BASE_URL,
    agentic_ollama_timeout_seconds: float = 300.0,
    agentic_ollama_max_pages: int | None = None,
    agentic_ollama_temperature: float = 0.0,
    agentic_ollama_max_output_tokens: int = 4096,
    agentic_ollama_response_format: AgenticOllamaResponseFormat = DEFAULT_AGENTIC_OLLAMA_RESPONSE_FORMAT,
    agentic_ollama_workers: int = DEFAULT_AGENTIC_OLLAMA_WORKERS,
    agentic_ollama_json_mode: bool = False,
    agentic_ollama_client: SupportsOllamaVisionClient | None = None,
    agentic_ollama_progress: AgenticOllamaProgressCallback | None = None,
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
    _prepare_workspace(output_dir, assets_dir)

    source_metadata = _source_metadata(source_path=source_path, source_id=resolved_source_id)
    if suffix == ".pdf" and parse_pdf:
        parser_metadata = _parser_metadata(
            status="parsed",
            reason="",
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
        )
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
            reason=(
                "Only PDF parsing is implemented in the current heuristic parser."
                if suffix != ".pdf"
                else "PDF parsing was disabled for this run."
            ),
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
        )
        manifest = _build_workspace_manifest(
            source_metadata=source_metadata,
            parser_metadata=parser_metadata,
        )

    if suffix == ".pdf" and agentic_ollama_parse:
        agentic_result = _parse_pdf_with_agentic_ollama(
            source_path=source_path,
            output_dir=output_dir,
            assets_dir=assets_dir,
            source_metadata=source_metadata,
            model=agentic_ollama_model,
            base_url=agentic_ollama_base_url,
            timeout_seconds=agentic_ollama_timeout_seconds,
            max_pages=agentic_ollama_max_pages,
            temperature=agentic_ollama_temperature,
            max_output_tokens=agentic_ollama_max_output_tokens,
            response_format=(
                "json" if agentic_ollama_json_mode else agentic_ollama_response_format
            ),
            workers=agentic_ollama_workers,
            render_dpi=render_dpi,
            client=agentic_ollama_client,
            progress=agentic_ollama_progress,
        )
        manifest = _with_agentic_ollama_manifest_entries(
            manifest,
            parser_metadata=agentic_result.parser,
        )

    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
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
    render_pages: bool = True,
    render_dpi: int = 144,
    image_dpi: int = 180,
    agentic_ollama_parse: bool = False,
    agentic_ollama_model: str = DEFAULT_AGENTIC_OLLAMA_MODEL,
    agentic_ollama_base_url: str = DEFAULT_AGENTIC_OLLAMA_BASE_URL,
    agentic_ollama_timeout_seconds: float = 300.0,
    agentic_ollama_max_pages: int | None = None,
    agentic_ollama_temperature: float = 0.0,
    agentic_ollama_max_output_tokens: int = 4096,
    agentic_ollama_response_format: AgenticOllamaResponseFormat = DEFAULT_AGENTIC_OLLAMA_RESPONSE_FORMAT,
    agentic_ollama_workers: int = DEFAULT_AGENTIC_OLLAMA_WORKERS,
    agentic_ollama_json_mode: bool = False,
    agentic_ollama_progress: AgenticOllamaProgressCallback | None = None,
) -> SourceMaterialBatchResult:
    source_paths = discover_source_materials(source_dir, output_root=output_root)
    used_source_ids: set[str] = set()
    bundles = [
        parse_source_material(
            source_path,
            output_root=output_root,
            source_id=_unique_source_id(source_path, source_dir, used_source_ids),
            parse_pdf=parse_pdf,
            render_pages=render_pages,
            render_dpi=render_dpi,
            image_dpi=image_dpi,
            agentic_ollama_parse=agentic_ollama_parse,
            agentic_ollama_model=agentic_ollama_model,
            agentic_ollama_base_url=agentic_ollama_base_url,
            agentic_ollama_timeout_seconds=agentic_ollama_timeout_seconds,
            agentic_ollama_max_pages=agentic_ollama_max_pages,
            agentic_ollama_temperature=agentic_ollama_temperature,
            agentic_ollama_max_output_tokens=agentic_ollama_max_output_tokens,
            agentic_ollama_response_format=agentic_ollama_response_format,
            agentic_ollama_workers=agentic_ollama_workers,
            agentic_ollama_json_mode=agentic_ollama_json_mode,
            agentic_ollama_progress=agentic_ollama_progress,
        )
        for source_path in source_paths
    ]
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
            metadata=_jsonable(chunk.get("metadata", {})),
            toc_items=_jsonable(chunk.get("toc_items", [])),
            page_boxes=_jsonable(chunk.get("page_boxes", [])),
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


def _parse_pdf_with_agentic_ollama(
    *,
    source_path: Path,
    output_dir: Path,
    assets_dir: Path,
    source_metadata: SourceMetadata,
    model: str,
    base_url: str,
    timeout_seconds: float,
    max_pages: int | None,
    temperature: float,
    max_output_tokens: int,
    response_format: AgenticOllamaResponseFormat,
    workers: int,
    render_dpi: int,
    client: SupportsOllamaVisionClient | None = None,
    progress: AgenticOllamaProgressCallback | None = None,
) -> AgenticOllamaReport:
    page_images = _ensure_page_render_assets(
        source_path=source_path,
        assets_dir=assets_dir,
        render_dpi=render_dpi,
    )
    attempted_images = page_images[:max_pages] if max_pages and max_pages > 0 else page_images
    resolved_client = client or OllamaVisionClient(
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_format=response_format,
    )
    page_count = len(page_images)
    page_tasks = list(enumerate(attempted_images, start=1))
    worker_count = max(1, workers)
    attempted_count = len(page_tasks)
    skipped_count = page_count - attempted_count
    _emit_agentic_ollama_progress(
        progress,
        phase="start",
        source_metadata=source_metadata,
        model=model,
        page_number=0,
        attempted_pages=attempted_count,
        document_pages=page_count,
        completed_pages=0,
        parsed_pages=0,
        failed_pages=0,
        skipped_pages=skipped_count,
        current_image_path="",
    )

    def parse_task(task: tuple[int, Path]) -> AgenticOllamaPageResult:
        page_number, image_path = task
        return _agentic_ollama_parse_page(
            client=resolved_client,
            image_path=image_path,
            page_number=page_number,
            page_count=page_count,
        )

    page_results: list[AgenticOllamaPageResult] = []
    completed_pages = 0
    parsed_so_far = 0
    failed_so_far = 0
    if worker_count == 1 or len(page_tasks) <= 1:
        for task in page_tasks:
            page_result = parse_task(task)
            page_results.append(page_result)
            completed_pages += 1
            parsed_so_far += 1 if page_result.status == "parsed" else 0
            failed_so_far += 1 if page_result.status == "failed" else 0
            _emit_agentic_ollama_progress(
                progress,
                phase="complete" if completed_pages >= attempted_count else "page",
                source_metadata=source_metadata,
                model=model,
                page_number=page_result.page,
                attempted_pages=attempted_count,
                document_pages=page_count,
                completed_pages=completed_pages,
                parsed_pages=parsed_so_far,
                failed_pages=failed_so_far,
                skipped_pages=skipped_count,
                current_image_path=page_result.image_path,
            )
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(parse_task, task) for task in page_tasks]
            for future in as_completed(futures):
                page_result = future.result()
                page_results.append(page_result)
                completed_pages += 1
                parsed_so_far += 1 if page_result.status == "parsed" else 0
                failed_so_far += 1 if page_result.status == "failed" else 0
                _emit_agentic_ollama_progress(
                    progress,
                    phase="complete" if completed_pages >= attempted_count else "page",
                    source_metadata=source_metadata,
                    model=model,
                    page_number=page_result.page,
                    attempted_pages=attempted_count,
                    document_pages=page_count,
                    completed_pages=completed_pages,
                    parsed_pages=parsed_so_far,
                    failed_pages=failed_so_far,
                    skipped_pages=skipped_count,
                    current_image_path=page_result.image_path,
                )
    page_results.sort(key=lambda page: page.page)
    if not page_tasks:
        _emit_agentic_ollama_progress(
            progress,
            phase="complete",
            source_metadata=source_metadata,
            model=model,
            page_number=0,
            attempted_pages=0,
            document_pages=page_count,
            completed_pages=0,
            parsed_pages=0,
            failed_pages=0,
            skipped_pages=skipped_count,
            current_image_path="",
        )

    captured_assets = _agentic_ollama_captured_assets(page_results)
    parsed_count = sum(1 for page in page_results if page.status == "parsed")
    failed_count = sum(1 for page in page_results if page.status == "failed")
    status: Literal["parsed", "partial", "failed"] = "parsed"
    if parsed_count == 0 and page_results:
        status = "failed"
    elif failed_count or skipped_count:
        status = "partial"

    parser_metadata = ParserMetadata(
        status=status,
        name="ollama-vision-page-parser",
        version="",
        reason="" if status != "failed" else "All attempted pages failed.",
        parsed_at=datetime.now(timezone.utc).isoformat(),
        settings={
            "model": model,
            "base_url": base_url,
            "page_image_source": "assets/page-*.png",
            "render_dpi": render_dpi,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "response_format": response_format,
            "parallel_workers": worker_count,
            "max_pages": max_pages or 0,
            "prompt_contract": "Return one validated JSON object matching AgenticOllamaParsedPage.",
            "captured_assets": "agentic_ollama_assets.json",
        },
    )
    warnings: list[str] = []
    if failed_count:
        warnings.append(f"{failed_count} page(s) failed during Ollama vision parsing.")
    if skipped_count:
        warnings.append(f"{skipped_count} page(s) were skipped by the max-pages limit.")

    report = AgenticOllamaReport(
        schema_version=AGENTIC_OLLAMA_PARSE_SCHEMA_VERSION,
        parser=parser_metadata,
        source=source_metadata,
        page_count=page_count,
        attempted_pages=len(page_results),
        parsed_pages=parsed_count,
        failed_pages=failed_count,
        skipped_pages=skipped_count,
        captured_asset_count=len(captured_assets),
        captured_table_count=sum(1 for asset in captured_assets if asset.kind == "table"),
        captured_figure_count=sum(1 for asset in captured_assets if asset.kind != "table"),
        warnings=warnings,
        pages=page_results,
    )
    _write_agentic_ollama_outputs(
        output_dir=output_dir,
        report=report,
        captured_assets=captured_assets,
    )
    return report


def _agentic_ollama_parse_page(
    *,
    client: SupportsOllamaVisionClient,
    image_path: Path,
    page_number: int,
    page_count: int,
) -> AgenticOllamaPageResult:
    prompt = _agentic_ollama_page_prompt(page_number=page_number, page_count=page_count)
    started = time.monotonic()
    raw_response = ""
    try:
        response = client.parse_page(
            image_path=image_path,
            page_number=page_number,
            page_count=page_count,
            prompt=prompt,
        )
        raw_response = response.content
        duration = round(time.monotonic() - started, 3)
        parsed_page = _validated_agentic_page(response.content, expected_page_number=page_number)
        parsed_json = parsed_page.model_dump(mode="json")
        markdown = _markdown_from_agentic_response(response.content, parsed_json)
        return AgenticOllamaPageResult(
            page=page_number,
            image_path=f"assets/{image_path.name}",
            status="parsed",
            markdown=markdown,
            parsed_json=parsed_json,
            raw_response=raw_response,
            duration_seconds=duration,
        )
    except Exception as exc:
        duration = round(time.monotonic() - started, 3)
        return AgenticOllamaPageResult(
            page=page_number,
            image_path=f"assets/{image_path.name}",
            status="failed",
            raw_response=raw_response,
            error=str(exc),
            duration_seconds=duration,
        )


def _emit_agentic_ollama_progress(
    progress: AgenticOllamaProgressCallback | None,
    *,
    phase: Literal["start", "page", "complete"],
    source_metadata: SourceMetadata,
    model: str,
    page_number: int,
    attempted_pages: int,
    document_pages: int,
    completed_pages: int,
    parsed_pages: int,
    failed_pages: int,
    skipped_pages: int,
    current_image_path: str,
) -> None:
    if progress is None:
        return
    progress(
        AgenticOllamaProgress(
            phase=phase,
            source_id=source_metadata.id,
            source_filename=source_metadata.filename,
            model=model,
            page_number=page_number,
            attempted_pages=attempted_pages,
            document_pages=document_pages,
            completed_pages=completed_pages,
            parsed_pages=parsed_pages,
            failed_pages=failed_pages,
            skipped_pages=skipped_pages,
            current_image_path=current_image_path,
        )
    )


def _agentic_ollama_page_prompt(*, page_number: int, page_count: int) -> str:
    schema = json.dumps(AgenticOllamaParsedPage.model_json_schema(), ensure_ascii=False)
    return (
        "You are a meticulous document parsing agent. Parse the attached image of "
        f"PDF page {page_number} of {page_count}.\n\n"
        "Return exactly one JSON object matching this JSON Schema, with no markdown fence "
        f"and no commentary:\n{schema}\n\n"
        "Rules:\n"
        "1. Preserve visible wording. Do not summarize or simplify.\n"
        "2. Preserve headings, captions, page text, formulas, labels, and table content.\n"
        "3. If a region is unreadable, record that in uncertain_text instead of guessing.\n"
        "4. Use Markdown tables when table structure is visible.\n"
        "5. Put visual non-table assets in figures with caption, visible_text, description, "
        "page_region, kind, and confidence.\n"
        "6. Use stable ids like page-0001-table-01 or page-0001-figure-01 for tables and figures.\n"
        "7. Use empty strings or empty arrays for fields that are not visible.\n"
        "8. Keep the output valid JSON."
    )


def _validated_agentic_page(content: str, *, expected_page_number: int) -> AgenticOllamaParsedPage:
    parsed_json = _extract_json_object(content)
    if parsed_json is None:
        raise SourceMaterialParseError("Ollama response did not contain one JSON object.")
    parsed_page = AgenticOllamaParsedPage.model_validate(parsed_json)
    if parsed_page.page_number == expected_page_number:
        return parsed_page
    parser_notes = [
        *parsed_page.parser_notes,
        (
            f"Model returned page_number {parsed_page.page_number}; "
            f"normalized to {expected_page_number}."
        ),
    ]
    return parsed_page.model_copy(
        update={"page_number": expected_page_number, "parser_notes": parser_notes}
    )


def _agentic_ollama_captured_assets(
    pages: list[AgenticOllamaPageResult],
) -> list[AgenticOllamaCapturedAsset]:
    assets: list[AgenticOllamaCapturedAsset] = []
    for page in pages:
        if page.status != "parsed" or page.parsed_json is None:
            continue
        tables = page.parsed_json.get("tables", [])
        if isinstance(tables, list):
            for index, raw_table in enumerate(tables, start=1):
                if not isinstance(raw_table, Mapping):
                    continue
                assets.append(
                    AgenticOllamaCapturedAsset(
                        id=_asset_id(raw_table.get("id"), page.page, "table", index),
                        page=page.page,
                        kind="table",
                        source_image_path=page.image_path,
                        caption=_string_value(raw_table.get("caption")),
                        markdown=_string_value(raw_table.get("markdown")),
                        notes=_string_value(raw_table.get("notes")),
                        confidence=_confidence_value(raw_table.get("confidence")),
                    )
                )
        figures = page.parsed_json.get("figures", [])
        if isinstance(figures, list):
            for index, raw_figure in enumerate(figures, start=1):
                if not isinstance(raw_figure, Mapping):
                    continue
                kind = _asset_kind_value(raw_figure.get("kind"))
                assets.append(
                    AgenticOllamaCapturedAsset(
                        id=_asset_id(raw_figure.get("id"), page.page, kind, index),
                        page=page.page,
                        kind=kind,
                        source_image_path=page.image_path,
                        caption=_string_value(raw_figure.get("caption")),
                        visible_text=_string_value(raw_figure.get("visible_text")),
                        description=_string_value(raw_figure.get("description")),
                        page_region=_string_value(raw_figure.get("page_region")),
                        confidence=_confidence_value(raw_figure.get("confidence")),
                    )
                )
    return assets


def _asset_id(value: Any, page_number: int, kind: str, index: int) -> str:
    text = _string_value(value).strip()
    if text:
        return text
    return f"page-{page_number:04d}-{kind}-{index:02d}"


def _asset_kind_value(value: Any) -> AgenticCapturedAssetKind:
    text = _string_value(value).strip().lower()
    if text in {"photo", "diagram", "chart", "equation", "other"}:
        return text
    return "figure"


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_string_value(item) for item in value if _string_value(item))
    return str(value)


def _confidence_value(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(confidence, 0.0), 1.0)


def _write_agentic_ollama_outputs(
    *,
    output_dir: Path,
    report: AgenticOllamaReport,
    captured_assets: list[AgenticOllamaCapturedAsset],
) -> None:
    pages_payload = [page.model_dump(mode="json") for page in report.pages]
    assets_payload = [asset.model_dump(mode="json") for asset in captured_assets]
    _write_json(output_dir / "agentic_ollama_assets.json", assets_payload)
    _write_json(output_dir / "agentic_ollama_pages.json", pages_payload)
    _write_json(output_dir / "agentic_ollama_report.json", report.model_dump(mode="json"))
    markdown = _compose_agentic_markdown(report.pages)
    (output_dir / "agentic_ollama_content.md").write_text(markdown, encoding="utf-8")


def _compose_agentic_markdown(pages: list[AgenticOllamaPageResult]) -> str:
    sections: list[str] = []
    for page in pages:
        if page.status == "parsed":
            content = page.markdown.strip() or page.raw_response.strip()
        else:
            content = f"_Agentic Ollama parse failed: {page.error}_"
        sections.append(f"<!-- page:{page.page} -->\n\n{content}")
    return "\n\n---\n\n".join(sections).strip() + "\n"


def _ensure_page_render_assets(*, source_path: Path, assets_dir: Path, render_dpi: int) -> list[Path]:
    page_images: list[Path] = []
    with fitz.open(source_path) as document:
        for index, page in enumerate(document, start=1):
            image_path = assets_dir / f"page-{index:04d}.png"
            if not image_path.is_file():
                _render_page(page=page, page_number=index, assets_dir=assets_dir, dpi=render_dpi)
            page_images.append(image_path)
    return page_images


def _extract_json_object(content: str) -> dict[str, Any] | None:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _markdown_from_agentic_response(content: str, parsed_json: dict[str, Any] | None) -> str:
    if parsed_json is not None:
        markdown = parsed_json.get("reading_order_markdown")
        if isinstance(markdown, str) and markdown.strip():
            return markdown.strip()
    return content.strip()


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
    return _jsonable(parsed)


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
            render_asset = ""
            if render_pages:
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
        schema_version=PDF_PARSE_SCHEMA_VERSION,
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
    status: Literal["parsed", "not_run", "skipped", "partial", "failed"],
    reason: str,
    render_pages: bool,
    render_dpi: int,
    image_dpi: int,
) -> ParserMetadata:
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


def _with_agentic_ollama_manifest_entries(
    manifest: dict[str, Any],
    *,
    parser_metadata: ParserMetadata,
) -> dict[str, Any]:
    updated = dict(manifest)
    files = dict(updated.get("files", {}))
    files.update(
        {
            "agentic_ollama_content_markdown": "agentic_ollama_content.md",
            "agentic_ollama_assets_json": "agentic_ollama_assets.json",
            "agentic_ollama_pages_json": "agentic_ollama_pages.json",
            "agentic_ollama_report_json": "agentic_ollama_report.json",
        }
    )
    updated["files"] = files
    agentic_parsers = list(updated.get("agentic_parsers", []))
    agentic_parsers.append(parser_metadata.model_dump(mode="json"))
    updated["agentic_parsers"] = agentic_parsers
    return updated


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


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
