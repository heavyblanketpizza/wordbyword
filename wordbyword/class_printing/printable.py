from __future__ import annotations

import base64
import io
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wordbyword._json_io import read_json_list as read_json_list_file
from wordbyword._json_io import read_json_object as read_json_object_file
from wordbyword.dictionary_build.llm import (
    ChatMessage,
    CompletionResult,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    parse_json_object,
)
from wordbyword.dictionary_build.output_files import append_jsonl, initialize_jsonl_file, write_jsonl

PRINTABLE_SCHEMA_VERSION = "wordbyword.printable_material.v1"
DEFAULT_PRINT_OUTPUT_ROOT = Path("printable-for-class") / "grade1-ko-a4-ollama"
DEFAULT_TARGET_PROFILE = "grade1-ko"
DEFAULT_BODY_FONT_SIZE_PT = 13
MIN_BODY_FONT_SIZE_PT = 11
MAX_BODY_FONT_SIZE_PT = 15
MAX_SELECTED_IMAGES = 2
MAX_VISUAL_CANDIDATES = 24
PRINTABLE_PREVIEW_MAX_DIMENSION = 768
PRINTABLE_ASSET_MODE_VISION = "vision-candidate-images"
PRINTABLE_ASSET_MODE_TEXT_ONLY = "text-only-asset-catalog"
MARKDOWN_FENCE = "```"
KOREAN_PATTERN = re.compile(r"[가-힣]")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
PAGE_NUMBER_PATTERN = re.compile(r"page-(\d{4})")
HTML_IMAGE_PATTERN = re.compile(r"<img\b", re.IGNORECASE)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\([^)]+\)")
FORBIDDEN_ACTIVITY_HEADING_PATTERN = re.compile(
    r"(?:\b(?:question|questions|worksheet|activity|quiz|answers?)\b|문제|활동|퀴즈|정답|질문)",
    re.IGNORECASE,
)

FONT_CANDIDATES = (
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/Library/Fonts/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
)

PRINTABLE_SYSTEM_PROMPT = """You design printable Korean reading material for young students.
Use the rewritten class material as the only source.
Preserve the full rewritten reading content; do not summarize or shorten it to fit on one page.
The output may span multiple A4 pages. A4 is the paper size, not a one-page length target.
Write reading material only: no worksheet, quiz, comprehension questions, answers, or vocabulary activity sections.
Keep Korean natural, warm, and easy for 1학년 readers.
Choose at most two images only from the offered visual_candidate_asset_ids by asset_id.
Use selected_images[].asset_id exactly as written in visual_candidate_asset_ids.
Never use an asset path, image filename, or asset_id with an added file extension.
Do not invent image filenames or use external images.
Return only the requested JSON object."""


class PrintableMaterialError(RuntimeError):
    """Raised when printable class material cannot be generated safely."""


ProgressCallback = Callable[[str, str], None]


class PrintableImageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    placement: str = "after_title"
    caption: str = ""
    alt_text: str = ""


class PrintableSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    paragraphs: list[str] = Field(min_length=1)


class PrintableDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str
    subtitle: str = ""
    body_font_size_pt: int = Field(default=DEFAULT_BODY_FONT_SIZE_PT, ge=MIN_BODY_FONT_SIZE_PT, le=MAX_BODY_FONT_SIZE_PT)
    sections: list[PrintableSection] = Field(min_length=1)
    selected_images: list[PrintableImageSelection] = Field(default_factory=list, max_length=MAX_SELECTED_IMAGES)


@dataclass(frozen=True)
class PrintableAsset:
    asset_id: str
    path: str
    kind: str
    page: int | None
    width: int
    height: int
    mime_type: str
    byte_size: int
    caption: str = ""

    def prompt_row(self) -> dict[str, object]:
        row: dict[str, object] = {
            "asset_id": self.asset_id,
            "path": self.path,
            "kind": self.kind,
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
        }
        if self.caption:
            row["caption"] = self.caption
        return row

    def json_row(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "path": self.path,
            "kind": self.kind,
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "caption": self.caption,
        }


@dataclass(frozen=True)
class PrintableContext:
    source_id: str
    source_filename: str
    source_sha256: str
    rewritten_bundle_dir: Path
    content_markdown: str
    chunks: list[Mapping[str, object]]
    assets: tuple[PrintableAsset, ...]
    rewrite_manifest: Mapping[str, object]


@dataclass(frozen=True)
class PrintablePrompt:
    messages: tuple[ChatMessage, ...]
    user_prompt: str
    prompt_messages: tuple[dict[str, object], ...]
    attached_assets: tuple[PrintableAsset, ...]


@dataclass(frozen=True)
class PrintableMaterialOutputs:
    source_id: str
    output_dir: Path
    accepted: bool
    failure_reason: str
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


@dataclass(frozen=True)
class PrintableMaterialBatchOutputs:
    rewritten_root: Path
    output_root: Path
    source_outputs: list[PrintableMaterialOutputs]
    skipped_sources: list[dict[str, object]]


def print_class_material(
    rewritten_bundle_dir: Path,
    *,
    output_root: Path = DEFAULT_PRINT_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    font_path: Path | None = None,
    body_font_size: int | None = None,
    progress: ProgressCallback | None = None,
) -> PrintableMaterialOutputs:
    context = build_printable_context(rewritten_bundle_dir)
    validate_body_font_size_override(body_font_size)
    output_dir = output_root / context.source_id
    prevent_input_overwrite(context.rewritten_bundle_dir, output_dir)

    _report(progress, context.source_id, f"preparing printable output at {output_dir}")
    prepare_output_dir(output_dir)
    copy_assets(context.rewritten_bundle_dir, output_dir)

    attempts_path = output_dir / "attempts.jsonl"
    failures_path = output_dir / "failures.jsonl"
    initialize_jsonl_file(attempts_path)
    initialize_jsonl_file(failures_path)

    prompt = build_printable_prompt(context, body_font_size=body_font_size)
    result: CompletionResult | None = None
    try:
        _report(progress, context.source_id, "planning printable reading material")
        if prompt.attached_assets:
            assert_client_supports_printable_images(client)
        result = client.complete(
            list(prompt.messages),
            temperature=0.2,
            max_output_tokens=print_plan_max_output_tokens(context),
            json_mode=True,
        )
        document = parse_and_validate_print_plan_response(
            result,
            context=context,
            visual_candidate_assets=prompt.attached_assets,
            body_font_size_override=body_font_size,
        )
        resolved_font_path, resolved_font_name = resolve_korean_font(font_path)
        write_printable_artifacts(
            output_dir,
            context=context,
            prompt=prompt,
            document=document,
            model=model,
            target_profile=target_profile,
            font_path=resolved_font_path,
            font_name=resolved_font_name,
        )
        attempt = printable_attempt_row(
            context=context,
            prompt=prompt,
            model=model,
            response=result.content,
            document=document,
            accepted=True,
            failure_reason="",
        )
        failure_rows: list[dict[str, object]] = []
    except (
        PrintableMaterialError,
        LLMConfigurationError,
        LLMRequestError,
        LLMResponseError,
        ValidationError,
        OSError,
        ValueError,
    ) as exc:
        attempt = printable_attempt_row(
            context=context,
            prompt=prompt,
            model=model,
            response=result.content if result is not None else "",
            document=None,
            accepted=False,
            failure_reason=str(exc),
        )
        failure_rows = [attempt]
        write_failed_printable_artifacts(
            output_dir,
            context=context,
            prompt=prompt,
            model=model,
            target_profile=target_profile,
            failure_reason=str(exc),
        )

    attempt_rows = [attempt]
    append_jsonl(attempts_path, attempt_rows)
    write_jsonl(failures_path, failure_rows)
    return PrintableMaterialOutputs(
        source_id=context.source_id,
        output_dir=output_dir,
        accepted=not failure_rows,
        failure_reason=str(attempt.get("failure_reason") or ""),
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
    )


def print_class_materials(
    rewritten_root: Path,
    *,
    output_root: Path = DEFAULT_PRINT_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    font_path: Path | None = None,
    body_font_size: int | None = None,
    progress: ProgressCallback | None = None,
) -> PrintableMaterialBatchOutputs:
    source_outputs: list[PrintableMaterialOutputs] = []
    skipped_sources: list[dict[str, object]] = []
    for bundle_dir in discover_rewritten_bundles(rewritten_root):
        skip_reason = skip_reason_for_rewritten_bundle(bundle_dir)
        if skip_reason:
            skipped_sources.append({"source_dir": str(bundle_dir), "reason": skip_reason})
            _report(progress, bundle_dir.name, f"skipping: {skip_reason}")
            continue
        source_outputs.append(
            print_class_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                target_profile=target_profile,
                font_path=font_path,
                body_font_size=body_font_size,
                progress=progress,
            )
        )
    return PrintableMaterialBatchOutputs(
        rewritten_root=rewritten_root,
        output_root=output_root,
        source_outputs=source_outputs,
        skipped_sources=skipped_sources,
    )


def build_printable_context(rewritten_bundle_dir: Path) -> PrintableContext:
    manifest_path = rewritten_bundle_dir / "manifest.json"
    content_path = rewritten_bundle_dir / "content.md"
    chunks_path = rewritten_bundle_dir / "chunks.json"
    if not manifest_path.is_file():
        raise PrintableMaterialError(f"Missing rewritten source manifest: {manifest_path}")
    if not content_path.is_file():
        raise PrintableMaterialError(f"Missing rewritten content markdown: {content_path}")
    if not chunks_path.is_file():
        raise PrintableMaterialError(f"Missing rewritten chunks file: {chunks_path}")

    manifest = read_json_object(manifest_path)
    chunks = read_json_list(chunks_path)
    source = mapping_value(manifest, "source")
    source_id = str(source.get("id") or rewritten_bundle_dir.name).strip() or rewritten_bundle_dir.name
    content_markdown = content_path.read_text(encoding="utf-8")
    if not content_markdown.strip():
        raise PrintableMaterialError(f"Rewritten content markdown is empty: {content_path}")

    return PrintableContext(
        source_id=source_id,
        source_filename=str(source.get("filename") or ""),
        source_sha256=str(source.get("sha256") or ""),
        rewritten_bundle_dir=rewritten_bundle_dir,
        content_markdown=content_markdown,
        chunks=[chunk for chunk in chunks if isinstance(chunk, Mapping)],
        assets=tuple(discover_printable_assets(rewritten_bundle_dir, chunks)),
        rewrite_manifest=manifest,
    )


def build_printable_prompt(
    context: PrintableContext,
    *,
    body_font_size: int | None = None,
) -> PrintablePrompt:
    required_shape = {
        "source_id": context.source_id,
        "title": "...",
        "subtitle": "...",
        "body_font_size_pt": body_font_size or DEFAULT_BODY_FONT_SIZE_PT,
        "sections": [
            {
                "heading": "...",
                "paragraphs": ["..."],
            }
        ],
        "selected_images": [
            {
                "asset_id": "...",
                "placement": "after_title",
                "caption": "...",
                "alt_text": "...",
            }
        ],
    }
    asset_catalog = [asset.prompt_row() for asset in context.assets]
    visual_candidate_payloads = printable_visual_candidate_payloads(context)
    visual_candidate_asset_ids = [asset.asset_id for asset, _payload in visual_candidate_payloads]
    font_instruction = (
        f"Use body_font_size_pt exactly {body_font_size}."
        if body_font_size is not None
        else f"Choose body_font_size_pt from {MIN_BODY_FONT_SIZE_PT} to {MAX_BODY_FONT_SIZE_PT}; prefer {DEFAULT_BODY_FONT_SIZE_PT}."
    )
    user_prompt = "\n".join(
        [
            "Printable-material request:",
            f"source_id: {context.source_id}",
            f"source_filename: {context.source_filename}",
            "Output is for A4 classroom handouts for young Korean students.",
            "A4 is the paper size. Do not treat this as a one-page limit.",
            "Preserve the full rewritten reading content across as many A4 pages as needed.",
            "Do not summarize, shorten, or omit reading content just to make the handout shorter.",
            font_instruction,
            "Use only reading sections. Do not include questions, quizzes, answers, worksheet tasks, or vocabulary exercises.",
            f"Choose at most {MAX_SELECTED_IMAGES} image(s) that support the rewritten reading.",
            "Only choose images that are listed in visual_candidate_asset_ids and attached above for visual inspection.",
            "Use selected_images=[] if no attached image fits the reading material.",
            "For selected_images[].asset_id, copy one exact string from visual_candidate_asset_ids.",
            "Do not use asset path values like assets/foo.jpg, filenames like foo.jpg, or add .jpg/.png to an asset_id.",
            "Prefer extracted_image assets over page_render assets when both are available.",
            "Do not copy raw image Markdown or HTML from the rewritten content into section paragraphs.",
            "",
            "visual_candidate_asset_ids:",
            json.dumps(visual_candidate_asset_ids, ensure_ascii=False),
            "",
            "asset_catalog:",
            json.dumps(asset_catalog, ensure_ascii=False),
            "",
            "rewritten_content_markdown:",
            context.content_markdown,
            "",
            "Return JSON exactly in this shape:",
            json.dumps(required_shape, ensure_ascii=False),
        ]
    )
    system_message = ChatMessage(role="system", content=PRINTABLE_SYSTEM_PROMPT)
    user_message = ChatMessage(role="user", content=user_prompt)
    messages: list[ChatMessage] = [system_message]
    prompt_messages: list[dict[str, object]] = [printable_prompt_message_row(system_message)]
    attached_assets: list[PrintableAsset] = []
    for asset, payload in visual_candidate_payloads:
        message = ChatMessage(
            role="user",
            content=printable_visual_candidate_prompt(asset),
            images=(payload,),
        )
        messages.append(message)
        prompt_messages.append(printable_prompt_message_row(message, attached_assets=(asset,)))
        attached_assets.append(asset)
    messages.append(user_message)
    prompt_messages.append(printable_prompt_message_row(user_message))
    return PrintablePrompt(
        messages=tuple(messages),
        user_prompt=user_prompt,
        prompt_messages=tuple(prompt_messages),
        attached_assets=tuple(attached_assets),
    )


def parse_and_validate_print_plan_response(
    result: CompletionResult,
    *,
    context: PrintableContext,
    visual_candidate_assets: Sequence[PrintableAsset] | None = None,
    body_font_size_override: int | None = None,
) -> PrintableDocument:
    if MARKDOWN_FENCE in result.content:
        raise PrintableMaterialError("Printable plan response included a markdown fence.")
    parsed = parse_json_object(result.content)
    try:
        document = PrintableDocument.model_validate(parsed)
    except ValidationError as exc:
        raise PrintableMaterialError(f"Printable plan response failed schema validation: {exc}") from exc
    if document.source_id != context.source_id:
        raise PrintableMaterialError(f"Printable plan omitted requested source_id '{context.source_id}'.")

    document = normalize_printable_document(document, body_font_size_override=body_font_size_override)
    validate_printable_document(
        document,
        context=context,
        visual_candidate_assets=visual_candidate_assets,
    )
    return document


def assert_client_supports_printable_images(client: SupportsCompletion) -> None:
    preflight = getattr(client, "assert_supports_images", None)
    if not callable(preflight):
        raise PrintableMaterialError(
            "Printable image selection requires a completion client that can verify image support."
        )
    try:
        preflight()
    except LLMConfigurationError:
        raise
    except (LLMRequestError, LLMResponseError) as exc:
        raise PrintableMaterialError(f"Could not verify Ollama image support for printable image selection: {exc}") from exc


def normalize_printable_document(
    document: PrintableDocument,
    *,
    body_font_size_override: int | None,
) -> PrintableDocument:
    sections = [
        PrintableSection(
            heading=section.heading.strip(),
            paragraphs=[paragraph.strip() for paragraph in section.paragraphs if paragraph.strip()],
        )
        for section in document.sections
    ]
    selected_images = [
        PrintableImageSelection(
            asset_id=image.asset_id.strip(),
            placement=(image.placement or "after_title").strip() or "after_title",
            caption=image.caption.strip(),
            alt_text=image.alt_text.strip(),
        )
        for image in document.selected_images
        if image.asset_id.strip()
    ]
    body_font_size_pt = body_font_size_override if body_font_size_override is not None else document.body_font_size_pt
    return PrintableDocument(
        source_id=document.source_id.strip(),
        title=document.title.strip(),
        subtitle=document.subtitle.strip(),
        body_font_size_pt=body_font_size_pt,
        sections=sections,
        selected_images=selected_images,
    )


def validate_printable_document(
    document: PrintableDocument,
    *,
    context: PrintableContext,
    visual_candidate_assets: Sequence[PrintableAsset] | None = None,
) -> None:
    if not document.title:
        raise PrintableMaterialError("Printable plan title is empty.")
    if not document.sections:
        raise PrintableMaterialError("Printable plan did not include reading sections.")
    combined_reading = "\n".join(
        paragraph for section in document.sections for paragraph in section.paragraphs
    )
    if not KOREAN_PATTERN.search(combined_reading):
        raise PrintableMaterialError("Printable plan reading sections did not contain Korean text.")
    for section in document.sections:
        if not section.heading:
            raise PrintableMaterialError("Printable plan included a section with an empty heading.")
        if FORBIDDEN_ACTIVITY_HEADING_PATTERN.search(section.heading):
            raise PrintableMaterialError(f"Printable plan included a worksheet-like section heading: {section.heading}")
        for text in [section.heading, *section.paragraphs]:
            if MARKDOWN_FENCE in text:
                raise PrintableMaterialError("Printable plan section text included a markdown fence.")
            if HTML_IMAGE_PATTERN.search(text) or MARKDOWN_IMAGE_PATTERN.search(text):
                raise PrintableMaterialError("Printable plan section text included raw image markup.")

    assets_by_id = {asset.asset_id: asset for asset in context.assets}
    if visual_candidate_assets is None:
        visual_candidate_assets = tuple(asset for asset, _payload in printable_visual_candidate_payloads(context))
    visual_candidate_ids = {asset.asset_id for asset in visual_candidate_assets}
    seen_image_ids: set[str] = set()
    for image in document.selected_images:
        if image.asset_id in seen_image_ids:
            raise PrintableMaterialError(f"Printable plan repeated selected image asset_id '{image.asset_id}'.")
        if image.asset_id not in assets_by_id:
            raise PrintableMaterialError(f"Printable plan selected unknown asset_id '{image.asset_id}'.")
        if image.asset_id not in visual_candidate_ids:
            raise PrintableMaterialError(
                f"Printable plan selected asset_id '{image.asset_id}' that was not attached as a visual candidate."
            )
        asset = assets_by_id[image.asset_id]
        if not (context.rewritten_bundle_dir / asset.path).is_file():
            raise PrintableMaterialError(f"Printable plan selected missing asset file '{asset.path}'.")
        seen_image_ids.add(image.asset_id)


def write_printable_artifacts(
    output_dir: Path,
    *,
    context: PrintableContext,
    prompt: PrintablePrompt,
    document: PrintableDocument,
    model: str,
    target_profile: str,
    font_path: Path,
    font_name: str,
) -> None:
    (output_dir / "print_plan.json").write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "content.md").write_text(
        render_printable_markdown(document, context=context),
        encoding="utf-8",
    )
    render_docx(
        output_dir / "material.docx",
        document=document,
        context=context,
        font_name=font_name,
    )
    write_quality_and_manifest(
        output_dir,
        context=context,
        prompt=prompt,
        document=document,
        model=model,
        target_profile=target_profile,
        accepted=True,
        failure_reason="",
        font_path=font_path,
        font_name=font_name,
    )


def write_failed_printable_artifacts(
    output_dir: Path,
    *,
    context: PrintableContext,
    prompt: PrintablePrompt,
    model: str,
    target_profile: str,
    failure_reason: str,
) -> None:
    write_quality_and_manifest(
        output_dir,
        context=context,
        prompt=prompt,
        document=None,
        model=model,
        target_profile=target_profile,
        accepted=False,
        failure_reason=failure_reason,
        font_path=None,
        font_name="",
    )


def render_printable_markdown(document: PrintableDocument, *, context: PrintableContext) -> str:
    assets_by_id = {asset.asset_id: asset for asset in context.assets}
    lines = [f"# {document.title}"]
    if document.subtitle:
        lines.extend(["", document.subtitle])
    for image in document.selected_images:
        asset = assets_by_id[image.asset_id]
        alt_text = image.alt_text or image.caption or asset.caption or asset.asset_id
        lines.extend(["", f"![{alt_text}]({asset.path})"])
        caption = image.caption or asset.caption
        if caption:
            lines.append(f"*{caption}*")
    for section in document.sections:
        lines.extend(["", f"## {section.heading}"])
        for paragraph in section.paragraphs:
            lines.extend(["", paragraph])
    return "\n".join(lines).strip() + "\n"


def render_docx(
    path: Path,
    *,
    document: PrintableDocument,
    context: PrintableContext,
    font_name: str,
) -> None:
    assets_by_id = {asset.asset_id: asset for asset in context.assets}
    doc = Document()
    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(15)
    section.bottom_margin = Mm(15)
    section.left_margin = Mm(15)
    section.right_margin = Mm(15)

    configure_docx_styles(doc, font_name=font_name, body_font_size=document.body_font_size_pt)
    add_docx_paragraph(doc, document.title, font_name=font_name, size=20, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    if document.subtitle:
        add_docx_paragraph(doc, document.subtitle, font_name=font_name, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
    for image in document.selected_images:
        if normalized_placement(image.placement) == "after_title":
            add_docx_image(doc, assets_by_id[image.asset_id], image, context=context, font_name=font_name)
    for index, item in enumerate(document.sections):
        add_docx_paragraph(doc, item.heading, font_name=font_name, size=15, bold=True)
        for paragraph in item.paragraphs:
            add_docx_paragraph(doc, paragraph, font_name=font_name, size=document.body_font_size_pt)
        if index == 0:
            for image in document.selected_images:
                if normalized_placement(image.placement) == "after_first_section":
                    add_docx_image(doc, assets_by_id[image.asset_id], image, context=context, font_name=font_name)
    for image in document.selected_images:
        if normalized_placement(image.placement) == "end":
            add_docx_image(doc, assets_by_id[image.asset_id], image, context=context, font_name=font_name)
    doc.save(path)


def configure_docx_styles(doc: Document, *, font_name: str, body_font_size: int) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = font_name
    normal.font.size = Pt(body_font_size)
    set_docx_east_asia_font(normal, font_name)


def set_docx_east_asia_font(style: Any, font_name: str) -> None:
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = rpr._add_rFonts()  # type: ignore[attr-defined]
    rfonts.set(qn("w:eastAsia"), font_name)


def add_docx_paragraph(
    doc: Document,
    text: str,
    *,
    font_name: str,
    size: int | float,
    bold: bool = False,
    align: int | None = None,
) -> None:
    paragraph = doc.add_paragraph()
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.name = font_name
    run.font.size = Pt(size)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def add_docx_image(
    doc: Document,
    asset: PrintableAsset,
    selection: PrintableImageSelection,
    *,
    context: PrintableContext,
    font_name: str,
) -> None:
    image_path = context.rewritten_bundle_dir / asset.path
    with Image.open(image_path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        return
    max_width_mm = 125.0
    max_height_mm = 70.0
    width_mm = min(max_width_mm, max_height_mm * (width / height))
    doc.add_picture(str(image_path), width=Mm(width_mm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption = selection.caption or asset.caption
    if caption:
        add_docx_paragraph(doc, caption, font_name=font_name, size=9, align=WD_ALIGN_PARAGRAPH.CENTER)


def printable_visual_candidate_payloads(context: PrintableContext) -> tuple[tuple[PrintableAsset, str], ...]:
    payloads: list[tuple[PrintableAsset, str]] = []
    for asset in printable_visual_candidates(context):
        payload = printable_preview_payload(context, asset)
        if payload:
            payloads.append((asset, payload))
    return tuple(payloads)


def printable_visual_candidates(context: PrintableContext) -> tuple[PrintableAsset, ...]:
    usable_assets = [asset for asset in context.assets if is_usable_printable_image_asset(context, asset)]
    non_page_assets = [asset for asset in usable_assets if not is_page_render_asset(asset)]
    candidates = non_page_assets if non_page_assets else usable_assets
    return tuple(sorted(candidates, key=printable_visual_candidate_sort_key)[:MAX_VISUAL_CANDIDATES])


def is_usable_printable_image_asset(context: PrintableContext, asset: PrintableAsset) -> bool:
    path = context.rewritten_bundle_dir / asset.path
    return (
        path.is_file()
        and path.suffix.lower() in IMAGE_EXTENSIONS
        and asset.width > 0
        and asset.height > 0
    )


def is_page_render_asset(asset: PrintableAsset) -> bool:
    if asset.kind.strip().lower() == "page_render":
        return True
    return PAGE_NUMBER_PATTERN.fullmatch(Path(asset.path).stem) is not None


def printable_visual_candidate_sort_key(asset: PrintableAsset) -> tuple[int, int, str, str]:
    kind = asset.kind.strip().lower()
    if kind == "extracted_image":
        kind_rank = 0
    elif is_page_render_asset(asset):
        kind_rank = 2
    else:
        kind_rank = 1
    return (kind_rank, asset.page or 999999, asset.asset_id, asset.path)


def printable_preview_payload(context: PrintableContext, asset: PrintableAsset) -> str:
    image_path = context.rewritten_bundle_dir / asset.path
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
            image.thumbnail(
                (PRINTABLE_PREVIEW_MAX_DIMENSION, PRINTABLE_PREVIEW_MAX_DIMENSION),
                image_resampling_filter(),
            )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)
    except OSError:
        return ""
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def image_resampling_filter() -> int:
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return int(resampling.LANCZOS)
    return int(getattr(Image, "LANCZOS", Image.BICUBIC))


def printable_visual_candidate_prompt(asset: PrintableAsset) -> str:
    return "\n".join(
        [
            "Printable visual candidate:",
            f"asset_id: {asset.asset_id}",
            f"path: {asset.path}",
            f"kind: {asset.kind}",
            f"page: {asset.page if asset.page is not None else ''}",
            f"dimensions: {asset.width}x{asset.height}",
            f"mime_type: {asset.mime_type}",
            f"caption: {asset.caption}",
        ]
    )


def printable_prompt_message_row(
    message: ChatMessage,
    *,
    attached_assets: Sequence[PrintableAsset] = (),
) -> dict[str, object]:
    return {
        "role": message.role,
        "content": message.content,
        "attached_asset_ids": [asset.asset_id for asset in attached_assets],
    }


def printable_asset_mode(attached_assets: Sequence[PrintableAsset]) -> str:
    return PRINTABLE_ASSET_MODE_VISION if attached_assets else PRINTABLE_ASSET_MODE_TEXT_ONLY


def discover_printable_assets(rewritten_bundle_dir: Path, chunks: Sequence[Any]) -> list[PrintableAsset]:
    assets_by_path: dict[str, PrintableAsset] = {}
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        for raw_asset in chunk.get("assets", []) if isinstance(chunk.get("assets"), list) else []:
            if not isinstance(raw_asset, Mapping):
                continue
            asset = printable_asset_from_row(raw_asset, rewritten_bundle_dir=rewritten_bundle_dir)
            if asset is not None:
                assets_by_path[asset.path] = asset

    assets_dir = rewritten_bundle_dir / "assets"
    if assets_dir.is_dir():
        for path in sorted(assets_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            relative_path = path.relative_to(rewritten_bundle_dir).as_posix()
            if relative_path in assets_by_path:
                continue
            asset = printable_asset_from_file(path, relative_path=relative_path)
            if asset is not None:
                assets_by_path[asset.path] = asset
    return sorted(assets_by_path.values(), key=lambda asset: (asset.page or 999999, asset.kind, asset.asset_id))


def printable_asset_from_row(
    row: Mapping[str, object],
    *,
    rewritten_bundle_dir: Path,
) -> PrintableAsset | None:
    path = str(row.get("path") or "").strip()
    if not path:
        return None
    absolute_path = rewritten_bundle_dir / path
    if not absolute_path.is_file() or absolute_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    width = int(row.get("width") or 0)
    height = int(row.get("height") or 0)
    if width <= 0 or height <= 0:
        inspected = image_size(absolute_path)
        width, height = inspected
    return PrintableAsset(
        asset_id=str(row.get("asset_id") or row.get("id") or Path(path).stem).strip(),
        path=path,
        kind=str(row.get("kind") or "unknown").strip() or "unknown",
        page=int_or_none(row.get("page")) or page_from_path(path),
        width=max(width, 0),
        height=max(height, 0),
        mime_type=str(row.get("mime_type") or mime_type_from_suffix(path)),
        byte_size=int(row.get("byte_size") or absolute_path.stat().st_size),
        caption=str(row.get("caption") or "").strip(),
    )


def printable_asset_from_file(path: Path, *, relative_path: str) -> PrintableAsset | None:
    width, height = image_size(path)
    if width <= 0 or height <= 0:
        return None
    return PrintableAsset(
        asset_id=path.stem,
        path=relative_path,
        kind="existing_image",
        page=page_from_path(relative_path),
        width=width,
        height=height,
        mime_type=mime_type_from_suffix(relative_path),
        byte_size=path.stat().st_size,
    )


def image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except OSError:
        return (0, 0)


def mime_type_from_suffix(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    if suffix in {".tif", ".tiff"}:
        return "image/tiff"
    if suffix == ".bmp":
        return "image/bmp"
    return ""


def normalized_placement(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"end", "after_reading", "after_sections", "bottom"}:
        return "end"
    if normalized in {"after_first_section", "middle", "near_relevant_text", "after_section"}:
        return "after_first_section"
    return "after_title"


def printable_attempt_row(
    *,
    context: PrintableContext,
    prompt: PrintablePrompt,
    model: str,
    response: str,
    document: PrintableDocument | None,
    accepted: bool,
    failure_reason: str,
) -> dict[str, object]:
    return {
        "source_id": context.source_id,
        "model": model,
        "system_prompt": PRINTABLE_SYSTEM_PROMPT,
        "user_prompt": prompt.user_prompt,
        "prompt_messages": list(prompt.prompt_messages),
        "response": response,
        "accepted": accepted,
        "failure_reason": failure_reason,
        "selected_asset_ids": [image.asset_id for image in document.selected_images] if document else [],
        "body_font_size_pt": document.body_font_size_pt if document else None,
        "asset_mode": printable_asset_mode(prompt.attached_assets),
        "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
        "visual_candidate_count": len(prompt.attached_assets),
    }


def write_quality_and_manifest(
    output_dir: Path,
    *,
    context: PrintableContext,
    prompt: PrintablePrompt,
    document: PrintableDocument | None,
    model: str,
    target_profile: str,
    accepted: bool,
    failure_reason: str,
    font_path: Path | None,
    font_name: str,
) -> None:
    quality = build_quality_report(
        context=context,
        prompt=prompt,
        document=document,
        model=model,
        target_profile=target_profile,
        accepted=accepted,
        failure_reason=failure_reason,
        font_path=font_path,
        font_name=font_name,
    )
    manifest = build_printable_manifest(
        context=context,
        prompt=prompt,
        document=document,
        model=model,
        target_profile=target_profile,
        accepted=accepted,
        failure_reason=failure_reason,
        font_path=font_path,
        font_name=font_name,
    )
    (output_dir / "quality_report.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_quality_report(
    *,
    context: PrintableContext,
    prompt: PrintablePrompt,
    document: PrintableDocument | None,
    model: str,
    target_profile: str,
    accepted: bool,
    failure_reason: str,
    font_path: Path | None,
    font_name: str,
) -> dict[str, object]:
    return {
        "schema_version": PRINTABLE_SCHEMA_VERSION,
        "source_id": context.source_id,
        "target_profile": target_profile,
        "model": model,
        "status": "written" if accepted else "failed",
        "failure_reason": failure_reason,
        "section_count": len(document.sections) if document else 0,
        "selected_image_count": len(document.selected_images) if document else 0,
        "body_font_size_pt": document.body_font_size_pt if document else None,
        "font_path": str(font_path) if font_path is not None else "",
        "font_name": font_name,
        "output_formats": ["docx"] if accepted else [],
        "requires_human_revision": accepted,
        "revision_target": "material.docx" if accepted else "",
        "asset_catalog_count": len(context.assets),
        "asset_mode": printable_asset_mode(prompt.attached_assets),
        "attached_image_count": len(prompt.attached_assets),
        "visual_candidate_count": len(prompt.attached_assets),
    }


def build_printable_manifest(
    *,
    context: PrintableContext,
    prompt: PrintablePrompt,
    document: PrintableDocument | None,
    model: str,
    target_profile: str,
    accepted: bool,
    failure_reason: str,
    font_path: Path | None,
    font_name: str,
) -> dict[str, object]:
    files: dict[str, object] = {
        "assets": "assets/",
        "attempts_jsonl": "attempts.jsonl",
        "failures_jsonl": "failures.jsonl",
        "quality_report_json": "quality_report.json",
    }
    if accepted:
        files.update(
            {
                "print_plan_json": "print_plan.json",
                "content_markdown": "content.md",
                "docx": "material.docx",
            }
        )
    return {
        "schema_version": PRINTABLE_SCHEMA_VERSION,
        "source": {
            "id": context.source_id,
            "filename": context.source_filename,
            "sha256": context.source_sha256,
        },
        "source_rewrite": {
            "bundle_dir": str(context.rewritten_bundle_dir),
            "schema_version": str(context.rewrite_manifest.get("schema_version") or ""),
            "rewrite": dict(context.rewrite_manifest.get("rewrite", {}))
            if isinstance(context.rewrite_manifest.get("rewrite"), Mapping)
            else {},
        },
        "printable": {
            "target_profile": target_profile,
            "model": model,
            "status": "written" if accepted else "failed",
            "failure_reason": failure_reason,
            "body_font_size_pt": document.body_font_size_pt if document else None,
            "selected_image_count": len(document.selected_images) if document else 0,
            "font_path": str(font_path) if font_path is not None else "",
            "font_name": font_name,
            "asset_mode": printable_asset_mode(prompt.attached_assets),
            "attached_image_count": len(prompt.attached_assets),
            "visual_candidate_count": len(prompt.attached_assets),
            "requires_human_revision": accepted,
            "revision_target": "material.docx" if accepted else "",
            "rendered_at": datetime.now(timezone.utc).isoformat(),
        },
        "files": files,
    }


def resolve_korean_font(font_path: Path | None) -> tuple[Path, str]:
    candidates = [font_path.expanduser()] if font_path is not None else list(FONT_CANDIDATES)
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            font = fitz.Font(fontfile=str(candidate))
        except Exception:
            continue
        return candidate, font.name
    if font_path is not None:
        raise PrintableMaterialError(f"Could not load print font: {font_path}")
    raise PrintableMaterialError(
        "Could not find a Korean-capable print font. Pass --print-font-path with a local Korean font file."
    )


def validate_body_font_size_override(body_font_size: int | None) -> None:
    if body_font_size is None:
        return
    if body_font_size < MIN_BODY_FONT_SIZE_PT or body_font_size > MAX_BODY_FONT_SIZE_PT:
        raise PrintableMaterialError(
            f"print body font size must be between {MIN_BODY_FONT_SIZE_PT} and {MAX_BODY_FONT_SIZE_PT}: {body_font_size}"
        )


def print_plan_max_output_tokens(context: PrintableContext) -> int:
    return max(8192, min(32768, len(context.content_markdown) * 2 + 4096))


def discover_rewritten_bundles(rewritten_root: Path) -> list[Path]:
    if not rewritten_root.is_dir():
        raise PrintableMaterialError(f"Rewritten source root does not exist: {rewritten_root}")
    return sorted(path for path in rewritten_root.iterdir() if path.is_dir())


def skip_reason_for_rewritten_bundle(bundle_dir: Path) -> str:
    for filename in ("manifest.json", "content.md", "chunks.json"):
        if not (bundle_dir / filename).is_file():
            return f"missing {filename}"
    return ""


def prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def prevent_input_overwrite(rewritten_bundle_dir: Path, output_dir: Path) -> None:
    if output_dir.resolve() == rewritten_bundle_dir.resolve():
        raise PrintableMaterialError(
            f"Printable output would overwrite the rewritten input bundle: {rewritten_bundle_dir}"
        )


def copy_assets(rewritten_bundle_dir: Path, output_dir: Path) -> None:
    source_assets = rewritten_bundle_dir / "assets"
    target_assets = output_dir / "assets"
    if source_assets.is_dir():
        shutil.copytree(source_assets, target_assets)
    else:
        target_assets.mkdir(parents=True, exist_ok=True)


def page_from_path(path: str) -> int | None:
    match = PAGE_NUMBER_PATTERN.search(path)
    if match is None:
        return None
    return int(match.group(1))


def read_json_object(path: Path) -> dict[str, Any]:
    return read_json_object_file(path, error_type=PrintableMaterialError)


def read_json_list(path: Path) -> list[Any]:
    return read_json_list_file(path, error_type=PrintableMaterialError)


def mapping_value(row: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = row.get(key)
    if not isinstance(value, Mapping):
        raise PrintableMaterialError(f"Expected '{key}' object in rewritten source metadata.")
    return value


def int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _report(progress: ProgressCallback | None, source_id: str, detail: str) -> None:
    if progress is not None:
        progress(source_id, detail)
