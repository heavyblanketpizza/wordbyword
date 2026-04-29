from __future__ import annotations

import csv
import contextlib
import hashlib
import io
import json
import mimetypes
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fitz

from wordbyword.readers import normalize_text, read_text_file

SCHEMA_VERSION = "wordbyword.source_material_ir.v1"
DEFAULT_PARSED_SOURCE_ROOT = Path("class-source-materials") / "parsed"
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


@dataclass(frozen=True)
class SourceAsset:
    id: str
    type: str
    path: str
    page: int | None = None
    mime_type: str = ""
    bbox: list[float] = field(default_factory=list)
    role: str = ""
    source_ref: str = ""


@dataclass(frozen=True)
class SourceBlock:
    id: str
    type: str
    order: int
    text: str = ""
    page: int | None = None
    bbox: list[float] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    ocr_text: str = ""
    vision_description: str = ""
    needs_ocr: bool = False
    needs_vision: bool = False
    extraction_method: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceMaterialIR:
    schema_version: str
    source: dict[str, Any]
    blocks: list[SourceBlock]
    assets: list[SourceAsset]
    statistics: dict[str, Any]


@dataclass(frozen=True)
class SourceMaterialBundle:
    source_id: str
    output_dir: Path
    manifest_path: Path
    canonical_json_path: Path
    canonical_jsonl_path: Path
    ir: SourceMaterialIR


class SourceMaterialParseError(RuntimeError):
    """Raised when a source material cannot be parsed into the IR bundle."""


class _BundleWriter:
    def __init__(self, source_path: Path, output_dir: Path) -> None:
        self.source_path = source_path
        self.output_dir = output_dir
        self.assets: list[SourceAsset] = []
        self._asset_counts: dict[str, int] = {}

    def prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("manifest.json", "canonical_content.json", "canonical_blocks.jsonl"):
            path = self.output_dir / filename
            if path.is_file():
                path.unlink()
        for child in ("images", "tables", "drawings", "pages"):
            folder = self.output_dir / "assets" / child
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)

    def write_bytes_asset(
        self,
        *,
        asset_type: str,
        page: int | None,
        payload: bytes,
        suffix: str,
        mime_type: str,
        bbox: Iterable[float] = (),
        role: str = "",
        source_ref: str = "",
    ) -> SourceAsset:
        asset_id = self._next_asset_id(asset_type, page)
        folder = self._asset_folder(asset_type)
        filename = f"{asset_id}{suffix}"
        path = self.output_dir / "assets" / folder / filename
        path.write_bytes(payload)
        asset = SourceAsset(
            id=asset_id,
            type=asset_type,
            page=page,
            path=path.relative_to(self.output_dir).as_posix(),
            mime_type=mime_type,
            bbox=_rounded_bbox(bbox),
            role=role,
            source_ref=source_ref,
        )
        self.assets.append(asset)
        return asset

    def write_text_asset(
        self,
        *,
        asset_type: str,
        page: int | None,
        text: str,
        suffix: str,
        mime_type: str,
        bbox: Iterable[float] = (),
        role: str = "",
        source_ref: str = "",
    ) -> SourceAsset:
        asset_id = self._next_asset_id(asset_type, page)
        folder = self._asset_folder(asset_type)
        filename = f"{asset_id}{suffix}"
        path = self.output_dir / "assets" / folder / filename
        path.write_text(text, encoding="utf-8", newline="")
        asset = SourceAsset(
            id=asset_id,
            type=asset_type,
            page=page,
            path=path.relative_to(self.output_dir).as_posix(),
            mime_type=mime_type,
            bbox=_rounded_bbox(bbox),
            role=role,
            source_ref=source_ref,
        )
        self.assets.append(asset)
        return asset

    def copy_file_asset(
        self,
        *,
        asset_type: str,
        page: int | None,
        source_path: Path,
        mime_type: str,
        bbox: Iterable[float] = (),
        role: str = "",
        source_ref: str = "",
    ) -> SourceAsset:
        asset_id = self._next_asset_id(asset_type, page)
        folder = self._asset_folder(asset_type)
        suffix = source_path.suffix.lower() or mimetypes.guess_extension(mime_type) or ".bin"
        filename = f"{asset_id}{suffix}"
        destination = self.output_dir / "assets" / folder / filename
        shutil.copyfile(source_path, destination)
        asset = SourceAsset(
            id=asset_id,
            type=asset_type,
            page=page,
            path=destination.relative_to(self.output_dir).as_posix(),
            mime_type=mime_type,
            bbox=_rounded_bbox(bbox),
            role=role,
            source_ref=source_ref,
        )
        self.assets.append(asset)
        return asset

    def _next_asset_id(self, asset_type: str, page: int | None) -> str:
        key = f"{asset_type}:{page or 0}"
        count = self._asset_counts.get(key, 0) + 1
        self._asset_counts[key] = count
        page_part = f"p{page:04d}" if page is not None else "document"
        return f"{page_part}-{asset_type}-{count:03d}"

    def _asset_folder(self, asset_type: str) -> str:
        if asset_type == "table":
            return "tables"
        if asset_type == "drawing":
            return "drawings"
        if asset_type == "page":
            return "pages"
        return "images"


def parse_source_material(
    source_path: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
    source_id: str | None = None,
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
    writer = _BundleWriter(source_path=source_path, output_dir=output_dir)
    writer.prepare()

    blocks = _parse_blocks(source_path, writer)
    ir = SourceMaterialIR(
        schema_version=SCHEMA_VERSION,
        source={
            "id": resolved_source_id,
            "path": source_path.as_posix(),
            "filename": source_path.name,
            "extension": suffix,
            "mime_type": mimetypes.guess_type(source_path.name)[0] or "",
            "sha256": sha256_file(source_path),
            "byte_size": source_path.stat().st_size,
            "parsed_at": datetime.now(timezone.utc).isoformat(),
        },
        blocks=blocks,
        assets=writer.assets,
        statistics=_statistics(blocks, writer.assets),
    )
    _write_ir_bundle(output_dir, ir)
    return SourceMaterialBundle(
        source_id=resolved_source_id,
        output_dir=output_dir,
        manifest_path=output_dir / "manifest.json",
        canonical_json_path=output_dir / "canonical_content.json",
        canonical_jsonl_path=output_dir / "canonical_blocks.jsonl",
        ir=ir,
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


def _parse_blocks(source_path: Path, writer: _BundleWriter) -> list[SourceBlock]:
    suffix = source_path.suffix.lower()
    if suffix in {".pdf", ".epub"}:
        return _parse_pymupdf_document(source_path, writer)
    if suffix in {".txt", ".md", ".markdown", ".html", ".htm", ".xhtml"}:
        return _parse_plain_text_document(source_path)
    return _parse_image_document(source_path, writer)


def _parse_plain_text_document(source_path: Path) -> list[SourceBlock]:
    raw_text = read_text_file(source_path)
    text = normalize_text(_strip_basic_markup(raw_text, source_path.suffix.lower()))
    if not text:
        return []
    return [
        SourceBlock(
            id="b000001",
            type="document_text",
            order=1,
            text=text,
            extraction_method="plain_text",
        )
    ]


def _parse_image_document(source_path: Path, writer: _BundleWriter) -> list[SourceBlock]:
    mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    asset = writer.copy_file_asset(
        asset_type="image",
        page=1,
        source_path=source_path,
        mime_type=mime_type,
        role="source_image",
    )
    return [
        SourceBlock(
            id="b000001",
            type="image",
            order=1,
            page=1,
            assets=[asset.id],
            needs_ocr=True,
            needs_vision=True,
            extraction_method="source_image_copy",
            metadata={"asset_path": asset.path},
        )
    ]


def _parse_pymupdf_document(source_path: Path, writer: _BundleWriter) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    with fitz.open(source_path) as document:
        for page_index, page in enumerate(document, start=1):
            table_rects: list[fitz.Rect] = []
            for table_block in _table_blocks(page, page_index, writer):
                table_rects.append(fitz.Rect(table_block.bbox))
                blocks.append(table_block)

            for block in _page_content_blocks(page, page_index, writer, table_rects):
                blocks.append(block)

            drawing_block = _drawing_block(page, page_index, writer, table_rects)
            if drawing_block is not None:
                blocks.append(drawing_block)

    sorted_blocks = sorted(blocks, key=lambda block: (block.page or 0, _bbox_top(block.bbox), _bbox_left(block.bbox)))
    return [_with_order(block, order) for order, block in enumerate(sorted_blocks, start=1)]


def _table_blocks(
    page: fitz.Page,
    page_number: int,
    writer: _BundleWriter,
) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    find_tables = getattr(page, "find_tables", None)
    if find_tables is None:
        return blocks

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            table_finder = find_tables()
    except Exception:
        return blocks

    for table_index, table in enumerate(getattr(table_finder, "tables", []), start=1):
        try:
            rows = table.extract()
        except Exception:
            rows = []
        normalized_rows = [
            [normalize_text(str(cell or "")) for cell in row]
            for row in rows
            if any(str(cell or "").strip() for cell in row)
        ]
        if not normalized_rows:
            continue

        bbox = _rounded_bbox(table.bbox)
        csv_text = _csv_text(normalized_rows)
        csv_asset = writer.write_text_asset(
            asset_type="table",
            page=page_number,
            text=csv_text,
            suffix=".csv",
            mime_type="text/csv",
            bbox=bbox,
            role="table_cells",
        )
        image_asset = _write_page_clip_asset(
            page,
            writer,
            asset_type="table",
            page_number=page_number,
            clip=fitz.Rect(bbox),
            role="table_crop",
            source_ref=csv_asset.id,
        )
        asset_ids = [csv_asset.id]
        if image_asset is not None:
            asset_ids.append(image_asset.id)

        blocks.append(
            SourceBlock(
                id=f"b{page_number:04d}-table-{table_index:03d}",
                type="table",
                order=0,
                page=page_number,
                bbox=bbox,
                assets=asset_ids,
                rows=normalized_rows,
                text=_rows_to_text(normalized_rows),
                needs_vision=image_asset is not None,
                extraction_method="pymupdf_find_tables",
                metadata={"row_count": len(normalized_rows), "asset_path": csv_asset.path},
            )
        )

    return blocks


def _page_content_blocks(
    page: fitz.Page,
    page_number: int,
    writer: _BundleWriter,
    table_rects: list[fitz.Rect],
) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    raw = page.get_text("dict", sort=True)
    average_font_size = _average_font_size(raw.get("blocks", []))
    text_index = 0
    image_index = 0

    for raw_block in raw.get("blocks", []):
        bbox = _rounded_bbox(raw_block.get("bbox", ()))
        rect = fitz.Rect(bbox) if bbox else fitz.Rect()
        if _mostly_inside_any(rect, table_rects):
            continue

        block_type = int(raw_block.get("type", -1))
        if block_type == 0:
            text = _text_from_raw_block(raw_block)
            if not text:
                continue
            text_index += 1
            blocks.append(
                SourceBlock(
                    id=f"b{page_number:04d}-text-{text_index:03d}",
                    type=_text_block_type(raw_block, text, average_font_size),
                    order=0,
                    page=page_number,
                    bbox=bbox,
                    text=text,
                    extraction_method="pymupdf_text_block",
                    metadata={"max_font_size": _max_font_size(raw_block)},
                )
            )
        elif block_type == 1:
            asset = _image_asset_from_raw_block(raw_block, page_number, writer)
            if asset is None:
                continue
            image_index += 1
            blocks.append(
                SourceBlock(
                    id=f"b{page_number:04d}-image-{image_index:03d}",
                    type="image",
                    order=0,
                    page=page_number,
                    bbox=bbox,
                    assets=[asset.id],
                    needs_ocr=True,
                    needs_vision=True,
                    extraction_method="pymupdf_image_block",
                    metadata={"asset_path": asset.path},
                )
            )

    if not blocks and not normalize_text(page.get_text("text", sort=True)):
        page_asset = _write_page_clip_asset(
            page,
            writer,
            asset_type="page",
            page_number=page_number,
            clip=page.rect,
            role="full_page_ocr_fallback",
        )
        if page_asset is not None:
            blocks.append(
                SourceBlock(
                    id=f"b{page_number:04d}-page-001",
                    type="page_image",
                    order=0,
                    page=page_number,
                    bbox=_rounded_bbox(page.rect),
                    assets=[page_asset.id],
                    needs_ocr=True,
                    needs_vision=True,
                    extraction_method="pymupdf_page_render",
                    metadata={"asset_path": page_asset.path},
                )
            )

    return blocks


def _drawing_block(
    page: fitz.Page,
    page_number: int,
    writer: _BundleWriter,
    table_rects: list[fitz.Rect],
) -> SourceBlock | None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return None

    rects: list[fitz.Rect] = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        fitz_rect = fitz.Rect(rect)
        if fitz_rect.is_empty or fitz_rect.get_area() < 400:
            continue
        if _mostly_inside_any(fitz_rect, table_rects):
            continue
        rects.append(fitz_rect)

    if not rects:
        return None

    union = rects[0]
    for rect in rects[1:]:
        union |= rect
    if union.get_area() < page.rect.get_area() * 0.01:
        return None

    asset = _write_page_clip_asset(
        page,
        writer,
        asset_type="drawing",
        page_number=page_number,
        clip=union + (-4, -4, 4, 4),
        role="vector_drawing_crop",
    )
    if asset is None:
        return None

    return SourceBlock(
        id=f"b{page_number:04d}-drawing-001",
        type="drawing",
        order=0,
        page=page_number,
        bbox=asset.bbox,
        assets=[asset.id],
        needs_vision=True,
        extraction_method="pymupdf_vector_drawing_crop",
        metadata={"asset_path": asset.path, "drawing_count": len(rects)},
    )


def _image_asset_from_raw_block(
    raw_block: dict[str, Any],
    page_number: int,
    writer: _BundleWriter,
) -> SourceAsset | None:
    image_bytes = raw_block.get("image")
    if not isinstance(image_bytes, bytes):
        return None
    extension = str(raw_block.get("ext") or "png").lower().lstrip(".")
    suffix = f".{extension}"
    mime_type = mimetypes.guess_type(f"image.{extension}")[0] or f"image/{extension}"
    return writer.write_bytes_asset(
        asset_type="image",
        page=page_number,
        payload=image_bytes,
        suffix=suffix,
        mime_type=mime_type,
        bbox=raw_block.get("bbox", ()),
        role="embedded_image",
        source_ref=str(raw_block.get("xref") or ""),
    )


def _write_page_clip_asset(
    page: fitz.Page,
    writer: _BundleWriter,
    *,
    asset_type: str,
    page_number: int,
    clip: fitz.Rect,
    role: str,
    source_ref: str = "",
) -> SourceAsset | None:
    clip = fitz.Rect(clip) & page.rect
    if clip.is_empty:
        return None
    matrix = fitz.Matrix(2, 2)
    pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    payload = pixmap.tobytes("png")
    return writer.write_bytes_asset(
        asset_type=asset_type,
        page=page_number,
        payload=payload,
        suffix=".png",
        mime_type="image/png",
        bbox=clip,
        role=role,
        source_ref=source_ref,
    )


def _with_order(block: SourceBlock, order: int) -> SourceBlock:
    return SourceBlock(
        id=f"b{order:06d}",
        type=block.type,
        order=order,
        text=block.text,
        page=block.page,
        bbox=block.bbox,
        assets=block.assets,
        rows=block.rows,
        ocr_text=block.ocr_text,
        vision_description=block.vision_description,
        needs_ocr=block.needs_ocr,
        needs_vision=block.needs_vision,
        extraction_method=block.extraction_method,
        metadata={**block.metadata, "parser_block_id": block.id},
    )


def _write_ir_bundle(output_dir: Path, ir: SourceMaterialIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": ir.schema_version,
        "source": ir.source,
        "statistics": ir.statistics,
        "files": {
            "canonical_json": "canonical_content.json",
            "canonical_jsonl": "canonical_blocks.jsonl",
            "assets": "assets/",
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "canonical_content.json").write_text(
        json.dumps(_ir_to_dict(ir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "canonical_blocks.jsonl").open("w", encoding="utf-8") as handle:
        for block in ir.blocks:
            handle.write(json.dumps(asdict(block), ensure_ascii=False) + "\n")


def _ir_to_dict(ir: SourceMaterialIR) -> dict[str, Any]:
    return {
        "schema_version": ir.schema_version,
        "source": ir.source,
        "blocks": [asdict(block) for block in ir.blocks],
        "assets": [asdict(asset) for asset in ir.assets],
        "statistics": ir.statistics,
    }


def _statistics(blocks: list[SourceBlock], assets: list[SourceAsset]) -> dict[str, Any]:
    block_counts: dict[str, int] = {}
    asset_counts: dict[str, int] = {}
    for block in blocks:
        block_counts[block.type] = block_counts.get(block.type, 0) + 1
    for asset in assets:
        asset_counts[asset.type] = asset_counts.get(asset.type, 0) + 1

    return {
        "block_count": len(blocks),
        "asset_count": len(assets),
        "block_counts": block_counts,
        "asset_counts": asset_counts,
        "page_count": max((block.page or 0 for block in blocks), default=0),
        "character_count": sum(len(block.text) for block in blocks),
        "needs_ocr_count": sum(1 for block in blocks if block.needs_ocr),
        "needs_vision_count": sum(1 for block in blocks if block.needs_vision),
    }


def _text_from_raw_block(raw_block: dict[str, Any]) -> str:
    lines: list[str] = []
    for line in raw_block.get("lines", []):
        spans = line.get("spans", [])
        line_text = "".join(str(span.get("text", "")) for span in spans)
        if line_text.strip():
            lines.append(line_text)
    return normalize_text("\n".join(lines))


def _text_block_type(raw_block: dict[str, Any], text: str, average_font_size: float) -> str:
    max_font_size = _max_font_size(raw_block)
    if len(text) <= 140 and max_font_size >= max(average_font_size * 1.18, average_font_size + 1.5):
        return "heading"
    return "paragraph"


def _average_font_size(raw_blocks: list[dict[str, Any]]) -> float:
    sizes: list[float] = []
    for block in raw_blocks:
        if int(block.get("type", -1)) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = float(span.get("size", 0) or 0)
                if size > 0:
                    sizes.append(size)
    if not sizes:
        return 0.0
    return sum(sizes) / len(sizes)


def _max_font_size(raw_block: dict[str, Any]) -> float:
    maximum = 0.0
    for line in raw_block.get("lines", []):
        for span in line.get("spans", []):
            maximum = max(maximum, float(span.get("size", 0) or 0))
    return maximum


def _strip_basic_markup(raw_text: str, suffix: str) -> str:
    if suffix in {".html", ".htm", ".xhtml"}:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return text
    if suffix in {".md", ".markdown"}:
        text = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", raw_text)
        text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
        text = re.sub(r"^[#>*`~\-\s]+", " ", text, flags=re.MULTILINE)
        return text
    return raw_text


def _csv_text(rows: list[list[str]]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue()


def _rows_to_text(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in rows)


def _rounded_bbox(values: Iterable[float]) -> list[float]:
    try:
        return [round(float(value), 2) for value in values]
    except (TypeError, ValueError):
        return []


def _mostly_inside_any(rect: fitz.Rect, candidates: list[fitz.Rect]) -> bool:
    if rect.is_empty or not candidates:
        return False
    area = rect.get_area()
    if area <= 0:
        return False
    for candidate in candidates:
        intersection = rect & candidate
        if not intersection.is_empty and intersection.get_area() / area >= 0.85:
            return True
    return False


def _bbox_top(bbox: list[float]) -> float:
    if len(bbox) >= 2:
        return bbox[1]
    return 0.0


def _bbox_left(bbox: list[float]) -> float:
    if bbox:
        return bbox[0]
    return 0.0
