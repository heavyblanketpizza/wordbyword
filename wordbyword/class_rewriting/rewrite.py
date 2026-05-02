from __future__ import annotations

import base64
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

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

SOURCE_REWRITE_SCHEMA_VERSION = "wordbyword.source_rewrite.v1"
DEFAULT_TARGET_PROFILE = "grade1-ko"
DEFAULT_REWRITE_OUTPUT_ROOT = Path("class-source-materials") / "rewritten" / "grade1-ko-ollama"
DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT = (
    Path("class-source-materials") / "rewritten" / "grade1-ko-ollama-whole-document"
)
IMAGE_SRC_PATTERN = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
CONTENT_PAGE_MARKER_PATTERN = re.compile(r"<!--\s*page:(\d+)\s*-->")
PAGE_NUMBER_PATTERN = re.compile(r"page-(\d{4})")
KOREAN_PATTERN = re.compile(r"[가-힣]")
MARKDOWN_FENCE = "```"

GRADE_ONE_REWRITE_SYSTEM_PROMPT = """You rewrite mature class-source materials into Korean for 1학년 students, about seven years old.
Use short, natural Korean sentences.
Keep the meaning faithful to the source.
Use the whole document and figures for context.
Do not add facts that are not present in the source.
Do not use any built vocabulary, dictionary artifact, vocabulary list, or external glossary.
Preserve the original page's image and asset markdown/html blocks exactly.
Return only the requested JSON object."""


class SourceRewriteError(RuntimeError):
    """Raised when parsed source material cannot be rewritten safely."""


class SupportsImagePreflight(Protocol):
    def assert_supports_images(self) -> None: ...


ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class RewriteAsset:
    asset_id: str
    path: str
    kind: str
    page: int | None
    width: int
    height: int
    mime_type: str
    byte_size: int
    caption: str = ""

    def prompt_row(self, *, image_index: int | None = None) -> str:
        prefix = f"{image_index}. " if image_index is not None else "- "
        caption = f", caption={self.caption}" if self.caption else ""
        return (
            f"{prefix}asset_id={self.asset_id}, path={self.path}, page={self.page or ''}, "
            f"kind={self.kind}, size={self.width}x{self.height}, mime_type={self.mime_type}{caption}"
        )

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
class RewritePage:
    page: int
    source_text: str
    context_markdown: str
    assets: tuple[RewriteAsset, ...]


@dataclass(frozen=True)
class ContextPacket:
    source_id: str
    source_sha256: str
    source_filename: str
    parser_name: str
    parser_version: str
    content_markdown: str
    pages: tuple[RewritePage, ...]
    assets: tuple[RewriteAsset, ...]


@dataclass(frozen=True)
class SourceRewriteOutputs:
    source_id: str
    output_dir: Path
    rewritten_page_count: int
    failed_page_count: int
    page_rows: list[dict[str, object]]
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


@dataclass(frozen=True)
class SourceRewriteBatchOutputs:
    parsed_root: Path
    output_root: Path
    source_outputs: list[SourceRewriteOutputs]
    skipped_sources: list[dict[str, object]]


@dataclass(frozen=True)
class SourceWholeRewriteOutputs:
    source_id: str
    output_dir: Path
    rewritten_page_count: int
    failed_page_count: int
    page_rows: list[dict[str, object]]
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


@dataclass(frozen=True)
class SourceWholeRewriteBatchOutputs:
    parsed_root: Path
    output_root: Path
    source_outputs: list[SourceWholeRewriteOutputs]
    skipped_sources: list[dict[str, object]]


def rewrite_source_material(
    parsed_bundle_dir: Path,
    *,
    output_root: Path = DEFAULT_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
) -> SourceRewriteOutputs:
    context = build_context_packet(parsed_bundle_dir)
    if not context.pages:
        raise SourceRewriteError(f"No page chunks found in {parsed_bundle_dir}.")

    output_dir = output_root / context.source_id
    _report(progress, context.source_id, f"preparing rewrite output at {output_dir}")
    prepare_output_dir(output_dir)
    copy_assets(parsed_bundle_dir, output_dir)

    attached_assets = attachable_assets(parsed_bundle_dir, context.assets)
    if require_images and attached_assets:
        assert_client_supports_images(client)
    image_payloads = image_payloads_for_assets(parsed_bundle_dir, attached_assets)

    context_packet_path = output_dir / "context_packet.md"
    context_packet_path.write_text(context.content_markdown, encoding="utf-8")

    attempts_path = output_dir / "attempts.jsonl"
    failures_path = output_dir / "failures.jsonl"
    initialize_jsonl_file(attempts_path)
    initialize_jsonl_file(failures_path)

    page_rows: list[dict[str, object]] = []
    attempt_rows: list[dict[str, object]] = []
    failure_rows: list[dict[str, object]] = []
    rewritten_by_page: dict[int, str] = {}

    for page in context.pages:
        _report(progress, context.source_id, f"rewriting page {page.page}")
        attempt = rewrite_page_with_full_context(
            client,
            context=context,
            page=page,
            attached_assets=attached_assets,
            image_payloads=image_payloads,
            model=model,
        )
        attempt_rows.append(attempt)
        append_jsonl(attempts_path, [attempt])
        if bool(attempt["accepted"]):
            rewritten_markdown = str(attempt["rewritten_markdown"])
            rewritten_by_page[page.page] = rewritten_markdown
        else:
            failure_rows.append(attempt)
            write_jsonl(failures_path, failure_rows)

        page_rows = materialize_page_rows(context.pages, rewritten_by_page, failure_rows)
        write_rewrite_artifacts(
            output_dir,
            context=context,
            page_rows=page_rows,
            attempt_rows=attempt_rows,
            failure_rows=failure_rows,
            model=model,
            target_profile=target_profile,
            attached_assets=attached_assets,
        )

    return SourceRewriteOutputs(
        source_id=context.source_id,
        output_dir=output_dir,
        rewritten_page_count=len(rewritten_by_page),
        failed_page_count=len(failure_rows),
        page_rows=page_rows,
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
    )


def rewrite_source_materials(
    parsed_root: Path,
    *,
    output_root: Path = DEFAULT_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
) -> SourceRewriteBatchOutputs:
    source_outputs: list[SourceRewriteOutputs] = []
    skipped_sources: list[dict[str, object]] = []
    for bundle_dir in discover_parsed_bundles(parsed_root):
        skip_reason = skip_reason_for_bundle(bundle_dir)
        if skip_reason:
            skipped_sources.append({"source_dir": str(bundle_dir), "reason": skip_reason})
            _report(progress, bundle_dir.name, f"skipping: {skip_reason}")
            continue
        source_outputs.append(
            rewrite_source_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                target_profile=target_profile,
                require_images=require_images,
                progress=progress,
            )
        )
    return SourceRewriteBatchOutputs(
        parsed_root=parsed_root,
        output_root=output_root,
        source_outputs=source_outputs,
        skipped_sources=skipped_sources,
    )


def rewrite_whole_source_material(
    parsed_bundle_dir: Path,
    *,
    output_root: Path = DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
) -> SourceWholeRewriteOutputs:
    context = build_context_packet(parsed_bundle_dir)
    if not context.pages:
        raise SourceRewriteError(f"No page chunks found in {parsed_bundle_dir}.")

    output_dir = output_root / context.source_id
    _report(progress, context.source_id, f"preparing whole-document rewrite output at {output_dir}")
    prepare_output_dir(output_dir)
    copy_assets(parsed_bundle_dir, output_dir)
    (output_dir / "context_packet.md").write_text(context.content_markdown, encoding="utf-8")

    attempts_path = output_dir / "attempts.jsonl"
    failures_path = output_dir / "failures.jsonl"
    initialize_jsonl_file(attempts_path)
    initialize_jsonl_file(failures_path)

    attached_assets = attachable_assets(parsed_bundle_dir, context.assets)
    image_payloads: tuple[str, ...] = ()
    preflight_error = ""
    try:
        if require_images and attached_assets:
            assert_client_supports_images(client)
        image_payloads = image_payloads_for_assets(parsed_bundle_dir, attached_assets)
    except (LLMConfigurationError, LLMRequestError, LLMResponseError, SourceRewriteError) as exc:
        preflight_error = str(exc)

    _report(progress, context.source_id, "rewriting whole document")
    attempt = (
        failed_whole_document_attempt(
            context=context,
            model=model,
            attached_assets=attached_assets,
            failure_reason=preflight_error,
        )
        if preflight_error
        else rewrite_whole_document_with_full_context(
            client,
            context=context,
            attached_assets=attached_assets,
            image_payloads=image_payloads,
            model=model,
        )
    )
    attempt_rows = [attempt]
    failure_rows = [] if bool(attempt["accepted"]) else [attempt]
    page_rows = (
        materialize_whole_document_page_rows(
            context=context,
            rewritten_markdown=str(attempt["rewritten_markdown"]),
        )
        if bool(attempt["accepted"])
        else materialize_failed_whole_document_page_rows(
            context=context,
            failure_reason=str(attempt["failure_reason"]),
        )
    )
    append_jsonl(attempts_path, attempt_rows)
    write_jsonl(failures_path, failure_rows)
    write_rewrite_artifacts(
        output_dir,
        context=context,
        page_rows=page_rows,
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
        model=model,
        target_profile=target_profile,
        attached_assets=attached_assets,
        content_markdown=str(attempt["rewritten_markdown"]) if bool(attempt["accepted"]) else None,
        rewrite_mode="whole_document",
    )

    rewritten_page_count = sum(1 for row in page_rows if row.get("status") == "rewritten")
    failed_page_count = sum(1 for row in page_rows if row.get("status") == "failed")
    return SourceWholeRewriteOutputs(
        source_id=context.source_id,
        output_dir=output_dir,
        rewritten_page_count=rewritten_page_count,
        failed_page_count=failed_page_count,
        page_rows=page_rows,
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
    )


def rewrite_whole_source_materials(
    parsed_root: Path,
    *,
    output_root: Path = DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
) -> SourceWholeRewriteBatchOutputs:
    source_outputs: list[SourceWholeRewriteOutputs] = []
    skipped_sources: list[dict[str, object]] = []
    for bundle_dir in discover_parsed_bundles(parsed_root):
        skip_reason = skip_reason_for_bundle(bundle_dir)
        if skip_reason:
            skipped_sources.append({"source_dir": str(bundle_dir), "reason": skip_reason})
            _report(progress, bundle_dir.name, f"skipping: {skip_reason}")
            continue
        source_outputs.append(
            rewrite_whole_source_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                target_profile=target_profile,
                require_images=require_images,
                progress=progress,
            )
        )
    return SourceWholeRewriteBatchOutputs(
        parsed_root=parsed_root,
        output_root=output_root,
        source_outputs=source_outputs,
        skipped_sources=skipped_sources,
    )


def build_context_packet(parsed_bundle_dir: Path) -> ContextPacket:
    manifest_path = parsed_bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SourceRewriteError(f"Missing parsed source manifest: {manifest_path}")
    manifest = read_json_object(manifest_path)
    parser = mapping_value(manifest, "parser")
    if str(parser.get("status", "")) != "parsed":
        raise SourceRewriteError(f"Source bundle is not parsed: {parsed_bundle_dir}")
    source = mapping_value(manifest, "source")
    files = mapping_value(manifest, "files")
    chunks_path = parsed_bundle_dir / str(files.get("chunks_json") or "chunks.json")
    content_path = parsed_bundle_dir / str(files.get("content_markdown") or "content.md")
    quality_report_path = parsed_bundle_dir / str(files.get("quality_report_json") or "quality_report.json")
    if not chunks_path.is_file():
        raise SourceRewriteError(f"Missing parsed chunks file: {chunks_path}")
    if not content_path.is_file():
        raise SourceRewriteError(f"Missing parsed content markdown: {content_path}")
    if not quality_report_path.is_file():
        raise SourceRewriteError(f"Missing parsed quality report: {quality_report_path}")

    chunks = read_json_list(chunks_path)
    quality_report = read_json_object(quality_report_path)
    source_id = str(source.get("id") or parsed_bundle_dir.name)
    assets = assets_from_quality_report(quality_report)
    assets_by_path = {asset.path: asset for asset in assets}
    pages: list[RewritePage] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        page = int(chunk.get("page") or len(pages) + 1)
        source_text = str(chunk.get("text") or "")
        context_markdown, page_assets = interleave_page_context(
            page=page,
            source_text=source_text,
            assets_by_path=assets_by_path,
        )
        pages.append(
            RewritePage(
                page=page,
                source_text=source_text,
                context_markdown=context_markdown,
                assets=tuple(page_assets),
            )
        )

    content_markdown = render_context_packet_markdown(
        source_id=source_id,
        source=source,
        parser=parser,
        assets=assets,
        pages=pages,
    )
    return ContextPacket(
        source_id=source_id,
        source_sha256=str(source.get("sha256") or ""),
        source_filename=str(source.get("filename") or ""),
        parser_name=str(parser.get("name") or ""),
        parser_version=str(parser.get("version") or ""),
        content_markdown=content_markdown,
        pages=tuple(pages),
        assets=tuple(assets),
    )


def discover_parsed_bundles(parsed_root: Path) -> list[Path]:
    if not parsed_root.is_dir():
        raise SourceRewriteError(f"Parsed source root does not exist: {parsed_root}")
    return sorted(
        path for path in parsed_root.iterdir() if path.is_dir() and (path / "manifest.json").is_file()
    )


def skip_reason_for_bundle(bundle_dir: Path) -> str:
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return "missing manifest.json"
    try:
        manifest = read_json_object(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"invalid manifest.json: {exc}"
    parser = manifest.get("parser")
    if not isinstance(parser, Mapping) or parser.get("status") != "parsed":
        return "parser status is not parsed"
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        return "missing file map"
    for key, default in (
        ("content_markdown", "content.md"),
        ("chunks_json", "chunks.json"),
        ("quality_report_json", "quality_report.json"),
    ):
        path = bundle_dir / str(files.get(key) or default)
        if not path.is_file():
            return f"missing {path.name}"
    return ""


def interleave_page_context(
    *,
    page: int,
    source_text: str,
    assets_by_path: Mapping[str, RewriteAsset],
) -> tuple[str, list[RewriteAsset]]:
    lines = source_text.splitlines()
    rendered: list[str] = [f"<!-- page:{page} -->"]
    page_assets: list[RewriteAsset] = []
    for index, line in enumerate(lines):
        for image_path in IMAGE_SRC_PATTERN.findall(line):
            asset = assets_by_path.get(image_path) or fallback_asset(image_path, page=page)
            asset = RewriteAsset(
                asset_id=asset.asset_id,
                path=asset.path,
                kind=asset.kind,
                page=asset.page or page,
                width=asset.width,
                height=asset.height,
                mime_type=asset.mime_type,
                byte_size=asset.byte_size,
                caption=find_nearby_caption(lines, index),
            )
            page_assets.append(asset)
            rendered.append(render_asset_marker(asset))
        rendered.append(line)
    return "\n".join(rendered).strip(), page_assets


def render_context_packet_markdown(
    *,
    source_id: str,
    source: Mapping[str, object],
    parser: Mapping[str, object],
    assets: Sequence[RewriteAsset],
    pages: Sequence[RewritePage],
) -> str:
    lines = [
        "# Source Context Packet",
        "",
        f"source_id: {source_id}",
        f"source_filename: {source.get('filename', '')}",
        f"source_sha256: {source.get('sha256', '')}",
        f"parser: {parser.get('name', '')} {parser.get('version', '')}".strip(),
        "",
        "## Attached Assets",
    ]
    if assets:
        lines.extend(asset.prompt_row() for asset in assets)
    else:
        lines.append("- none")
    lines.extend(["", "## Interleaved Content"])
    for page in pages:
        lines.extend(["", page.context_markdown])
    return "\n".join(lines).strip() + "\n"


def render_asset_marker(asset: RewriteAsset) -> str:
    caption = f' caption="{asset.caption}"' if asset.caption else ""
    return (
        f'[[asset id="{asset.asset_id}" path="{asset.path}" page="{asset.page or ""}" '
        f'kind="{asset.kind}" width="{asset.width}" height="{asset.height}" '
        f'mime_type="{asset.mime_type}"{caption}]]'
    )


def find_nearby_caption(lines: Sequence[str], image_line_index: int) -> str:
    for line in lines[image_line_index + 1 : image_line_index + 6]:
        stripped = line.strip()
        if not stripped:
            continue
        if IMAGE_SRC_PATTERN.search(stripped):
            return ""
        return strip_html(stripped)
    return ""


def strip_html(text: str) -> str:
    return HTML_TAG_PATTERN.sub("", text).strip()


def fallback_asset(path: str, *, page: int) -> RewriteAsset:
    return RewriteAsset(
        asset_id=Path(path).stem,
        path=path,
        kind="unknown",
        page=page,
        width=0,
        height=0,
        mime_type="",
        byte_size=0,
    )


def assets_from_quality_report(quality_report: Mapping[str, object]) -> list[RewriteAsset]:
    raw_assets = quality_report.get("assets")
    if not isinstance(raw_assets, list):
        return []
    assets: list[RewriteAsset] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, Mapping):
            continue
        path = str(raw_asset.get("path") or "")
        if not path:
            continue
        page = int_or_none(raw_asset.get("page")) or page_from_path(path)
        assets.append(
            RewriteAsset(
                asset_id=str(raw_asset.get("id") or Path(path).stem),
                path=path,
                kind=str(raw_asset.get("kind") or "unknown"),
                page=page,
                width=int(raw_asset.get("width") or 0),
                height=int(raw_asset.get("height") or 0),
                mime_type=str(raw_asset.get("mime_type") or ""),
                byte_size=int(raw_asset.get("byte_size") or 0),
            )
        )
    return assets


def page_from_path(path: str) -> int | None:
    match = PAGE_NUMBER_PATTERN.search(path)
    if not match:
        return None
    return int(match.group(1))


def attachable_assets(parsed_bundle_dir: Path, assets: Sequence[RewriteAsset]) -> list[RewriteAsset]:
    return [
        asset
        for asset in assets
        if (parsed_bundle_dir / asset.path).is_file()
        and (
            asset.mime_type.startswith("image/")
            or Path(asset.path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
        )
    ]


def assert_client_supports_images(client: SupportsCompletion) -> None:
    preflight = getattr(client, "assert_supports_images", None)
    if not callable(preflight):
        raise SourceRewriteError(
            "The selected completion client cannot verify image support. "
            "Use an Ollama vision-capable model/client for source-material rewrites."
        )
    try:
        preflight()
    except LLMConfigurationError:
        raise
    except (LLMRequestError, LLMResponseError) as exc:
        raise SourceRewriteError(f"Could not verify Ollama image support: {exc}") from exc


def image_payloads_for_assets(parsed_bundle_dir: Path, assets: Sequence[RewriteAsset]) -> tuple[str, ...]:
    payloads: list[str] = []
    for asset in assets:
        asset_path = parsed_bundle_dir / asset.path
        if not asset_path.is_file():
            raise SourceRewriteError(f"Missing asset file for rewrite prompt: {asset_path}")
        payloads.append(base64.b64encode(asset_path.read_bytes()).decode("ascii"))
    return tuple(payloads)


def rewrite_page_with_full_context(
    client: SupportsCompletion,
    *,
    context: ContextPacket,
    page: RewritePage,
    attached_assets: Sequence[RewriteAsset],
    image_payloads: tuple[str, ...],
    model: str,
) -> dict[str, object]:
    user_prompt = build_page_rewrite_prompt(context, page, attached_assets)
    metrics: dict[str, object] = source_page_metrics(page.source_text)
    try:
        result = client.complete(
            [
                ChatMessage(role="system", content=GRADE_ONE_REWRITE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt, images=image_payloads),
            ],
            temperature=0.2,
            max_output_tokens=max(2048, min(8192, len(page.source_text) * 2)),
            json_mode=True,
        )
        rewritten_markdown = parse_and_validate_rewrite_response(
            result,
            context=context,
            page=page,
        )
        metrics = {
            **metrics,
            **rewritten_page_metrics(
                source_text=page.source_text,
                rewritten_markdown=rewritten_markdown,
            ),
        }
        return {
            "source_id": context.source_id,
            "page": page.page,
            "model": model,
            "system_prompt": GRADE_ONE_REWRITE_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "attached_asset_ids": [asset.asset_id for asset in attached_assets],
            "response": result.content,
            "rewritten_markdown": rewritten_markdown,
            "accepted": True,
            "failure_reason": "",
            "metrics": metrics,
        }
    except (LLMConfigurationError, LLMRequestError, LLMResponseError, SourceRewriteError) as exc:
        return {
            "source_id": context.source_id,
            "page": page.page,
            "model": model,
            "system_prompt": GRADE_ONE_REWRITE_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "attached_asset_ids": [asset.asset_id for asset in attached_assets],
            "response": "",
            "rewritten_markdown": "",
            "accepted": False,
            "failure_reason": str(exc),
            "metrics": metrics,
        }


def build_page_rewrite_prompt(
    context: ContextPacket,
    page: RewritePage,
    attached_assets: Sequence[RewriteAsset],
) -> str:
    asset_lines = (
        "\n".join(
            asset.prompt_row(image_index=index)
            for index, asset in enumerate(attached_assets, start=1)
        )
        if attached_assets
        else "No images are attached."
    )
    required_shape = {
        "source_id": context.source_id,
        "page": page.page,
        "rewritten_markdown": "...",
    }
    return "\n".join(
        [
            "Full interleaved document context packet:",
            context.content_markdown,
            "",
            "Attached image order:",
            asset_lines,
            "",
            "Rewrite request:",
            f"source_id: {context.source_id}",
            f"current_page: {page.page}",
            "",
            "Current page source markdown:",
            page.context_markdown,
            "",
            "Return JSON exactly in this shape:",
            json.dumps(required_shape, ensure_ascii=False),
        ]
    )


def rewrite_whole_document_with_full_context(
    client: SupportsCompletion,
    *,
    context: ContextPacket,
    attached_assets: Sequence[RewriteAsset],
    image_payloads: tuple[str, ...],
    model: str,
) -> dict[str, object]:
    user_prompt = build_whole_document_rewrite_prompt(context, attached_assets)
    metrics: dict[str, object] = source_document_metrics(context)
    result: CompletionResult | None = None
    try:
        result = client.complete(
            [
                ChatMessage(role="system", content=GRADE_ONE_REWRITE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt, images=image_payloads),
            ],
            temperature=0.2,
            max_output_tokens=whole_document_max_output_tokens(context),
            json_mode=True,
        )
        rewritten_markdown = parse_and_validate_whole_rewrite_response(
            result,
            context=context,
        )
        metrics = {
            **metrics,
            **rewritten_document_metrics(
                context=context,
                rewritten_markdown=rewritten_markdown,
            ),
        }
        return {
            "source_id": context.source_id,
            "page": None,
            "rewrite_mode": "whole_document",
            "model": model,
            "system_prompt": GRADE_ONE_REWRITE_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "attached_asset_ids": [asset.asset_id for asset in attached_assets],
            "response": result.content,
            "rewritten_markdown": rewritten_markdown,
            "accepted": True,
            "failure_reason": "",
            "metrics": metrics,
        }
    except (LLMConfigurationError, LLMRequestError, LLMResponseError, SourceRewriteError) as exc:
        return {
            "source_id": context.source_id,
            "page": None,
            "rewrite_mode": "whole_document",
            "model": model,
            "system_prompt": GRADE_ONE_REWRITE_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "attached_asset_ids": [asset.asset_id for asset in attached_assets],
            "response": result.content if result is not None else "",
            "rewritten_markdown": "",
            "accepted": False,
            "failure_reason": str(exc),
            "metrics": metrics,
        }


def failed_whole_document_attempt(
    *,
    context: ContextPacket,
    model: str,
    attached_assets: Sequence[RewriteAsset],
    failure_reason: str,
) -> dict[str, object]:
    return {
        "source_id": context.source_id,
        "page": None,
        "rewrite_mode": "whole_document",
        "model": model,
        "system_prompt": GRADE_ONE_REWRITE_SYSTEM_PROMPT,
        "user_prompt": build_whole_document_rewrite_prompt(context, attached_assets),
        "attached_asset_ids": [asset.asset_id for asset in attached_assets],
        "response": "",
        "rewritten_markdown": "",
        "accepted": False,
        "failure_reason": failure_reason,
        "metrics": source_document_metrics(context),
    }


def build_whole_document_rewrite_prompt(
    context: ContextPacket,
    attached_assets: Sequence[RewriteAsset],
) -> str:
    asset_lines = (
        "\n".join(
            asset.prompt_row(image_index=index)
            for index, asset in enumerate(attached_assets, start=1)
        )
        if attached_assets
        else "No images are attached."
    )
    required_shape = {
        "source_id": context.source_id,
        "rewritten_markdown": "...",
    }
    page_markers = ", ".join(f"<!-- page:{page.page} -->" for page in context.pages)
    return "\n".join(
        [
            "Full interleaved document context packet:",
            context.content_markdown,
            "",
            "Attached image order:",
            asset_lines,
            "",
            "Whole-document rewrite request:",
            f"source_id: {context.source_id}",
            "Rewrite the entire source material at once for Korean 1학년 readers.",
            "Use the interleaved content as the source material. Do not rewrite the context packet metadata.",
            f"Preserve every page marker exactly: {page_markers}",
            "Preserve every image or asset markdown/html block exactly where it belongs in the rewritten document.",
            "Return one complete rewritten Markdown document, not page-by-page fragments.",
            "",
            "Return JSON exactly in this shape:",
            json.dumps(required_shape, ensure_ascii=False),
        ]
    )


def whole_document_max_output_tokens(context: ContextPacket) -> int:
    return max(8192, min(32768, len(context.content_markdown) * 2))


def parse_and_validate_rewrite_response(
    result: CompletionResult,
    *,
    context: ContextPacket,
    page: RewritePage,
) -> str:
    if MARKDOWN_FENCE in result.content:
        raise SourceRewriteError("Model response included a markdown fence.")
    parsed = parse_json_object(result.content)
    if str(parsed.get("source_id") or "") != context.source_id:
        raise SourceRewriteError(f"Model response omitted requested source_id '{context.source_id}'.")
    if int_or_none(parsed.get("page")) != page.page:
        raise SourceRewriteError(f"Model response omitted requested page {page.page}.")
    rewritten_markdown = str(parsed.get("rewritten_markdown") or "").strip()
    if not rewritten_markdown:
        raise SourceRewriteError(f"Model response for page {page.page} had empty rewritten_markdown.")
    if MARKDOWN_FENCE in rewritten_markdown:
        raise SourceRewriteError("Rewritten markdown included a markdown fence.")
    if not KOREAN_PATTERN.search(rewritten_markdown):
        raise SourceRewriteError(f"Rewritten page {page.page} did not contain Korean text.")
    missing_asset_paths = [
        path for path in image_paths_in_text(page.source_text) if path not in rewritten_markdown
    ]
    if missing_asset_paths:
        raise SourceRewriteError(
            f"Rewritten page {page.page} omitted asset reference(s): "
            + ", ".join(missing_asset_paths)
        )
    return rewritten_markdown


def parse_and_validate_whole_rewrite_response(
    result: CompletionResult,
    *,
    context: ContextPacket,
) -> str:
    if MARKDOWN_FENCE in result.content:
        raise SourceRewriteError("Model response included a markdown fence.")
    parsed = parse_json_object(result.content)
    if str(parsed.get("source_id") or "") != context.source_id:
        raise SourceRewriteError(f"Model response omitted requested source_id '{context.source_id}'.")
    rewritten_markdown = str(parsed.get("rewritten_markdown") or "").strip()
    if not rewritten_markdown:
        raise SourceRewriteError("Whole-document model response had empty rewritten_markdown.")
    if MARKDOWN_FENCE in rewritten_markdown:
        raise SourceRewriteError("Whole-document rewritten markdown included a markdown fence.")
    if not KOREAN_PATTERN.search(rewritten_markdown):
        raise SourceRewriteError("Whole-document rewritten markdown did not contain Korean text.")

    missing_page_markers = [
        page.page for page in context.pages if page_marker(page.page) not in rewritten_markdown
    ]
    if missing_page_markers:
        formatted = ", ".join(str(page) for page in missing_page_markers)
        raise SourceRewriteError(f"Whole-document rewrite omitted page marker(s): {formatted}.")

    missing_asset_paths = [
        path for path in source_document_asset_paths(context) if path not in rewritten_markdown
    ]
    if missing_asset_paths:
        raise SourceRewriteError(
            "Whole-document rewrite omitted asset reference(s): "
            + ", ".join(missing_asset_paths)
        )
    return rewritten_markdown


def source_page_metrics(source_text: str) -> dict[str, object]:
    source_asset_paths = image_paths_in_text(source_text)
    return {
        "source_chars": len(source_text),
        "source_image_refs": len(source_asset_paths),
        "source_asset_paths": source_asset_paths,
        "source_heading_count": markdown_heading_count(source_text),
    }


def rewritten_page_metrics(*, source_text: str, rewritten_markdown: str) -> dict[str, object]:
    source_asset_paths = image_paths_in_text(source_text)
    output_asset_paths = image_paths_in_text(rewritten_markdown)
    source_heading_count = markdown_heading_count(source_text)
    output_heading_count = markdown_heading_count(rewritten_markdown)
    non_space_chars = [char for char in rewritten_markdown if not char.isspace()]
    korean_chars = KOREAN_PATTERN.findall(rewritten_markdown)
    return {
        "source_chars": len(source_text),
        "output_chars": len(rewritten_markdown),
        "korean_chars": len(korean_chars),
        "korean_char_ratio": round(len(korean_chars) / len(non_space_chars), 3)
        if non_space_chars
        else 0.0,
        "source_image_refs": len(source_asset_paths),
        "output_image_refs": len(output_asset_paths),
        "source_asset_paths": source_asset_paths,
        "output_asset_paths": output_asset_paths,
        "asset_paths_used": output_asset_paths,
        "image_ref_count_preserved": len(source_asset_paths) == len(output_asset_paths),
        "source_heading_count": source_heading_count,
        "output_heading_count": output_heading_count,
        "heading_count_preserved": source_heading_count == output_heading_count,
        "asset_refs_preserved": all(path in rewritten_markdown for path in source_asset_paths),
    }


def source_document_metrics(context: ContextPacket) -> dict[str, object]:
    source_asset_paths = source_document_asset_paths(context)
    source_text = "\n\n".join(page.source_text for page in context.pages)
    return {
        "source_chars": len(source_text),
        "source_pages": len(context.pages),
        "source_image_refs": len(source_asset_paths),
        "source_asset_paths": source_asset_paths,
        "source_heading_count": markdown_heading_count(source_text),
    }


def rewritten_document_metrics(*, context: ContextPacket, rewritten_markdown: str) -> dict[str, object]:
    source_asset_paths = source_document_asset_paths(context)
    output_asset_paths = image_paths_in_text(rewritten_markdown)
    page_markers = page_markers_in_text(rewritten_markdown)
    output_heading_count = markdown_heading_count(rewritten_markdown)
    non_space_chars = [char for char in rewritten_markdown if not char.isspace()]
    korean_chars = KOREAN_PATTERN.findall(rewritten_markdown)
    return {
        "output_chars": len(rewritten_markdown),
        "output_pages": len(page_markers),
        "korean_chars": len(korean_chars),
        "korean_char_ratio": round(len(korean_chars) / len(non_space_chars), 3)
        if non_space_chars
        else 0.0,
        "output_image_refs": len(output_asset_paths),
        "output_asset_paths": output_asset_paths,
        "asset_paths_used": output_asset_paths,
        "image_ref_count_preserved": len(source_asset_paths) == len(output_asset_paths),
        "output_heading_count": output_heading_count,
        "page_markers": page_markers,
        "page_markers_preserved": all(page.page in page_markers for page in context.pages),
        "asset_refs_preserved": all(path in rewritten_markdown for path in source_asset_paths),
    }


def materialize_page_rows(
    pages: Sequence[RewritePage],
    rewritten_by_page: Mapping[int, str],
    failure_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    failure_by_page = {int(row["page"]): row for row in failure_rows if row.get("page")}
    rows: list[dict[str, object]] = []
    for page in pages:
        rewritten_markdown = rewritten_by_page.get(page.page, "")
        failure = failure_by_page.get(page.page)
        rows.append(
            {
                "page": page.page,
                "status": "rewritten" if rewritten_markdown else "failed",
                "source_text": page.source_text,
                "rewritten_markdown": rewritten_markdown,
                "assets": [asset.json_row() for asset in page.assets],
                "metrics": failure.get("metrics", {}) if failure else rewritten_page_metrics(
                    source_text=page.source_text,
                    rewritten_markdown=rewritten_markdown,
                )
                if rewritten_markdown
                else source_page_metrics(page.source_text),
                "failure_reason": str(failure.get("failure_reason", "")) if failure else "",
            }
        )
    return rows


def materialize_whole_document_page_rows(
    *,
    context: ContextPacket,
    rewritten_markdown: str,
) -> list[dict[str, object]]:
    rewritten_by_page = split_rewritten_markdown_by_page(rewritten_markdown)
    rows: list[dict[str, object]] = []
    for page in context.pages:
        rewritten_page_markdown = rewritten_by_page.get(page.page, "")
        rows.append(
            {
                "page": page.page,
                "status": "rewritten" if rewritten_page_markdown else "failed",
                "source_text": page.source_text,
                "rewritten_markdown": rewritten_page_markdown,
                "assets": [asset.json_row() for asset in page.assets],
                "metrics": rewritten_page_metrics(
                    source_text=page.source_text,
                    rewritten_markdown=rewritten_page_markdown,
                )
                if rewritten_page_markdown
                else source_page_metrics(page.source_text),
                "failure_reason": "" if rewritten_page_markdown else "missing rewritten page segment",
            }
        )
    return rows


def materialize_failed_whole_document_page_rows(
    *,
    context: ContextPacket,
    failure_reason: str,
) -> list[dict[str, object]]:
    return [
        {
            "page": page.page,
            "status": "failed",
            "source_text": page.source_text,
            "rewritten_markdown": "",
            "assets": [asset.json_row() for asset in page.assets],
            "metrics": source_page_metrics(page.source_text),
            "failure_reason": failure_reason,
        }
        for page in context.pages
    ]


def write_rewrite_artifacts(
    output_dir: Path,
    *,
    context: ContextPacket,
    page_rows: Sequence[Mapping[str, object]],
    attempt_rows: Sequence[Mapping[str, object]],
    failure_rows: Sequence[Mapping[str, object]],
    model: str,
    target_profile: str,
    attached_assets: Sequence[RewriteAsset],
    content_markdown: str | None = None,
    rewrite_mode: str = "page",
) -> None:
    (output_dir / "content.md").write_text(
        (content_markdown.strip() + "\n")
        if content_markdown is not None
        else compose_rewritten_markdown(page_rows),
        encoding="utf-8",
    )
    (output_dir / "chunks.json").write_text(
        json.dumps(list(page_rows), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "quality_report.json").write_text(
        json.dumps(
            build_quality_report(
                context=context,
                page_rows=page_rows,
                failure_rows=failure_rows,
                model=model,
                target_profile=target_profile,
                attached_assets=attached_assets,
                rewrite_mode=rewrite_mode,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(
            build_rewrite_manifest(
                context=context,
                model=model,
                target_profile=target_profile,
                attached_assets=attached_assets,
                rewrite_mode=rewrite_mode,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_jsonl(output_dir / "attempts.jsonl", list(attempt_rows))  # compact checkpoints after each page
    write_jsonl(output_dir / "failures.jsonl", list(failure_rows))


def compose_rewritten_markdown(page_rows: Sequence[Mapping[str, object]]) -> str:
    rendered_pages: list[str] = []
    for row in page_rows:
        page = int(row.get("page") or len(rendered_pages) + 1)
        rewritten = str(row.get("rewritten_markdown") or "").strip()
        if not rewritten:
            failure = str(row.get("failure_reason") or "rewrite failed").strip()
            rewritten = f"<!-- rewrite failed: {failure} -->"
        rendered_pages.append(f"<!-- page:{page} -->\n\n{rewritten}")
    return "\n\n---\n\n".join(rendered_pages).strip() + "\n"


def build_quality_report(
    *,
    context: ContextPacket,
    page_rows: Sequence[Mapping[str, object]],
    failure_rows: Sequence[Mapping[str, object]],
    model: str,
    target_profile: str,
    attached_assets: Sequence[RewriteAsset],
    rewrite_mode: str = "page",
) -> dict[str, object]:
    rewritten_count = sum(1 for row in page_rows if row.get("status") == "rewritten")
    failed_count = sum(1 for row in page_rows if row.get("status") == "failed")
    return {
        "schema_version": SOURCE_REWRITE_SCHEMA_VERSION,
        "source_id": context.source_id,
        "target_profile": target_profile,
        "rewrite_mode": rewrite_mode,
        "model": model,
        "page_count": len(page_rows),
        "rewritten_page_count": rewritten_count,
        "failed_page_count": failed_count,
        "failed_attempt_count": len(failure_rows),
        "asset_count": len(context.assets),
        "attached_image_count": len(attached_assets),
        "pages": [
            {
                "page": row.get("page"),
                "status": row.get("status"),
                "metrics": row.get("metrics", {}),
                "failure_reason": row.get("failure_reason", ""),
            }
            for row in page_rows
        ],
    }


def build_rewrite_manifest(
    *,
    context: ContextPacket,
    model: str,
    target_profile: str,
    attached_assets: Sequence[RewriteAsset],
    rewrite_mode: str = "page",
) -> dict[str, object]:
    return {
        "schema_version": SOURCE_REWRITE_SCHEMA_VERSION,
        "source": {
            "id": context.source_id,
            "filename": context.source_filename,
            "sha256": context.source_sha256,
        },
        "parser": {
            "name": context.parser_name,
            "version": context.parser_version,
        },
        "rewrite": {
            "target_profile": target_profile,
            "mode": rewrite_mode,
            "model": model,
            "asset_mode": "ollama-images-required",
            "attached_image_count": len(attached_assets),
            "rewritten_at": datetime.now(timezone.utc).isoformat(),
        },
        "files": {
            "assets": "assets/",
            "content_markdown": "content.md",
            "chunks_json": "chunks.json",
            "context_packet_markdown": "context_packet.md",
            "attempts_jsonl": "attempts.jsonl",
            "failures_jsonl": "failures.jsonl",
            "quality_report_json": "quality_report.json",
        },
    }


def prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def copy_assets(parsed_bundle_dir: Path, output_dir: Path) -> None:
    source_assets = parsed_bundle_dir / "assets"
    target_assets = output_dir / "assets"
    if source_assets.is_dir():
        shutil.copytree(source_assets, target_assets)
    else:
        target_assets.mkdir(parents=True, exist_ok=True)


def image_paths_in_text(text: str) -> list[str]:
    return IMAGE_SRC_PATTERN.findall(text)


def source_document_asset_paths(context: ContextPacket) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for page in context.pages:
        for path in image_paths_in_text(page.source_text):
            if path in seen:
                continue
            paths.append(path)
            seen.add(path)
    return paths


def page_marker(page: int) -> str:
    return f"<!-- page:{page} -->"


def page_markers_in_text(text: str) -> list[int]:
    return [int(match.group(1)) for match in CONTENT_PAGE_MARKER_PATTERN.finditer(text)]


def split_rewritten_markdown_by_page(rewritten_markdown: str) -> dict[int, str]:
    matches = list(CONTENT_PAGE_MARKER_PATTERN.finditer(rewritten_markdown))
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(rewritten_markdown)
        pages[page] = strip_page_separator_edges(rewritten_markdown[start:end])
    return pages


def strip_page_separator_edges(text: str) -> str:
    lines = text.strip().splitlines()
    while lines and lines[0].strip() == "---":
        lines.pop(0)
    while lines and lines[-1].strip() == "---":
        lines.pop()
    return "\n".join(lines).strip()


def markdown_heading_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def read_json_object(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise SourceRewriteError(f"Expected JSON object in {path}.")
    return parsed


def read_json_list(path: Path) -> list[Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise SourceRewriteError(f"Expected JSON list in {path}.")
    return parsed


def mapping_value(row: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = row.get(key)
    if not isinstance(value, Mapping):
        raise SourceRewriteError(f"Expected '{key}' object in parsed source metadata.")
    return value


def int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _report(progress: ProgressCallback | None, source_id: str, detail: str) -> None:
    if progress is not None:
        progress(source_id, detail)
