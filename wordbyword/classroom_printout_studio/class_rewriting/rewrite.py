from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, cast

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
from wordbyword.dictionary_build.candidates import normalized_learning_signal_score, parse_roi_score, row_learning_signal_score
from wordbyword.dictionary_build.output_files import append_jsonl, initialize_jsonl_file, read_jsonl, write_jsonl
from wordbyword.grade_profiles import (
    DEFAULT_TARGET_GRADE,
    default_vocabulary_guided_rewrite_output_root,
    default_whole_rewrite_output_root,
    grade_from_target_profile,
    grade_profile,
    normalize_target_grade,
    target_profile_for_grade,
)

SOURCE_REWRITE_SCHEMA_VERSION = "wordbyword.source_rewrite.v1"
DEFAULT_TARGET_PROFILE = target_profile_for_grade()
REWRITE_MODE_WHOLE_DOCUMENT = "whole_document"
REWRITE_MODE_VOCABULARY_GUIDED = "vocabulary_guided"
DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT = default_whole_rewrite_output_root()
DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT = default_vocabulary_guided_rewrite_output_root()
VOCABULARY_GUIDED_CANDIDATE_LIMIT = 250
VOCABULARY_GUIDED_SELECTION_LIMIT = 20
VOCABULARY_GUIDED_SCORING_POLICY = (
    "selection_score=roi_score when present; otherwise normalized learning_signal_score; "
    "ties prefer higher class_printout_count, class_count, learning_signal_score, count"
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

VOCABULARY_SELECTION_SYSTEM_PROMPT = """You choose Korean learner-dictionary vocabulary to guide a story-style rewrite.
Read the full class-source material and figures for context.
Choose vocabulary that helps a Korean 1학년 student understand the source ideas.
You may choose concrete familiar words for metaphors when the source concept is too abstract.
Choose only from the offered vocabulary list.
Return only the requested JSON object."""

VOCABULARY_GUIDED_REWRITE_SYSTEM_PROMPT = """You rewrite mature class-source materials into Korean for 1학년 students, about seven years old.
Use short, natural Korean sentences.
Keep the source concepts faithful, but write in a story-like style when it helps young readers.
Use the selected vocabulary naturally as a guide, including metaphor when useful.
Do not add facts that are not present in the source.
Preserve the original page markers and every image or asset markdown/html block exactly.
Return only the requested JSON object."""


def rewrite_system_prompt_for_grade(target_grade: str) -> str:
    profile = grade_profile(target_grade)
    if profile.target_grade == DEFAULT_TARGET_GRADE:
        return GRADE_ONE_REWRITE_SYSTEM_PROMPT
    return f"""You rewrite mature class-source materials into Korean for {profile.reader_description}.
Use Korean sentence length, vocabulary, and explanation density appropriate for {profile.korean_label}.
Keep the meaning faithful to the source.
Use the whole document and figures for context.
Do not add facts that are not present in the source.
Do not use any built vocabulary, dictionary artifact, vocabulary list, or external glossary.
Preserve the original page's image and asset markdown/html blocks exactly.
Return only the requested JSON object."""


def vocabulary_selection_system_prompt_for_grade(target_grade: str) -> str:
    profile = grade_profile(target_grade)
    if profile.target_grade == DEFAULT_TARGET_GRADE:
        return VOCABULARY_SELECTION_SYSTEM_PROMPT
    return f"""You choose Korean learner-dictionary vocabulary to guide a rewrite.
Read the full class-source material and figures for context.
Choose vocabulary that helps {profile.reader_description} understand the source ideas.
You may choose concrete familiar words for metaphors when the source concept is too abstract.
Choose only from the offered vocabulary list.
Return only the requested JSON object."""


def vocabulary_guided_rewrite_system_prompt_for_grade(target_grade: str) -> str:
    profile = grade_profile(target_grade)
    if profile.target_grade == DEFAULT_TARGET_GRADE:
        return VOCABULARY_GUIDED_REWRITE_SYSTEM_PROMPT
    return f"""You rewrite mature class-source materials into Korean for {profile.reader_description}.
Use Korean sentence length, vocabulary, and explanation density appropriate for {profile.korean_label}.
Keep the source concepts faithful, but use narrative or explanatory flow when it helps readers.
Use the selected vocabulary naturally as a guide, including metaphor when useful.
Do not add facts that are not present in the source.
Preserve the original page markers and every image or asset markdown/html block exactly.
Return only the requested JSON object."""


class SourceRewriteError(RuntimeError):
    """Raised when parsed source material cannot be rewritten safely."""


class SupportsImagePreflight(Protocol):
    def assert_supports_images(self) -> None: ...


SourceRewriteProgressPhase = Literal["start", "load", "select", "rewrite", "write", "complete", "skip"]


@dataclass(frozen=True)
class SourceRewriteProgress:
    phase: SourceRewriteProgressPhase
    source_id: str
    detail: str
    source_index: int = 1
    total_sources: int = 1
    rewrite_mode: str = ""
    output_dir: Path | None = None
    rewritten_pages: int = 0
    failed_pages: int = 0
    selected_vocabulary: int = 0


ProgressCallback = Callable[[SourceRewriteProgress], None]


def resolve_target_grade_and_profile(
    *,
    target_grade: str | None,
    target_profile: str,
) -> tuple[str, str]:
    resolved_grade = normalize_target_grade(
        target_grade if target_grade is not None else grade_from_target_profile(target_profile)
    )
    resolved_profile = target_profile_for_grade(resolved_grade) if target_grade is not None else target_profile
    return resolved_grade, resolved_profile


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
    target_grade: str
    source_sha256: str
    source_filename: str
    parser_name: str
    parser_version: str
    source_content_markdown: str
    content_markdown: str
    pages: tuple[RewritePage, ...]
    assets: tuple[RewriteAsset, ...]


@dataclass(frozen=True)
class PromptMessage:
    message: ChatMessage
    attached_assets: tuple[RewriteAsset, ...] = ()


@dataclass(frozen=True)
class RewritePrompt:
    messages: tuple[ChatMessage, ...]
    user_prompt: str
    prompt_messages: tuple[dict[str, object], ...]
    attached_assets: tuple[RewriteAsset, ...]


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


@dataclass(frozen=True)
class VocabularyCandidate:
    canonical_key: str
    example: str
    learning_signal_score: float
    roi_score: float | None
    selection_score: float
    count: int
    class_count: int = 0
    class_printout_count: int = 0

    def prompt_row(self) -> dict[str, object]:
        return {
            "canonical_key": self.canonical_key,
            "example": self.example,
        }

    def json_row(self, *, reason: str = "", role: str = "") -> dict[str, object]:
        return {
            "canonical_key": self.canonical_key,
            "example": self.example,
            "learning_signal_score": self.learning_signal_score,
            "roi_score": self.roi_score,
            "selection_score": self.selection_score,
            "count": self.count,
            "reason": reason,
            "role": role,
        }


@dataclass(frozen=True)
class SelectedVocabulary:
    candidate: VocabularyCandidate
    reason: str = ""
    role: str = ""

    def json_row(self) -> dict[str, object]:
        return self.candidate.json_row(reason=self.reason, role=self.role)


@dataclass(frozen=True)
class SourceVocabularyGuidedRewriteOutputs:
    source_id: str
    output_dir: Path
    rewritten_page_count: int
    failed_page_count: int
    page_rows: list[dict[str, object]]
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]
    selection_attempt_rows: list[dict[str, object]]
    selected_vocabulary: list[dict[str, object]]


@dataclass(frozen=True)
class SourceVocabularyGuidedRewriteBatchOutputs:
    parsed_root: Path
    output_root: Path
    source_outputs: list[SourceVocabularyGuidedRewriteOutputs]
    skipped_sources: list[dict[str, object]]


def rewrite_whole_source_material(
    parsed_bundle_dir: Path,
    *,
    output_root: Path = DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    target_grade: str | None = None,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
    source_index: int = 1,
    total_sources: int = 1,
) -> SourceWholeRewriteOutputs:
    resolved_target_grade, target_profile = resolve_target_grade_and_profile(
        target_grade=target_grade,
        target_profile=target_profile,
    )
    context = build_context_packet(parsed_bundle_dir)
    validate_context_target_grade(context, resolved_target_grade, parsed_bundle_dir)
    if not context.pages:
        raise SourceRewriteError(f"No page chunks found in {parsed_bundle_dir}.")

    output_dir = output_root / context.source_id
    _report(
        progress,
        phase="start",
        source_id=context.source_id,
        detail=f"preparing whole-document rewrite output at {output_dir}",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_WHOLE_DOCUMENT,
        output_dir=output_dir,
    )
    prepare_output_dir(output_dir)
    copy_assets(parsed_bundle_dir, output_dir)
    (output_dir / "context_packet.md").write_text(context.content_markdown, encoding="utf-8")

    attempts_path = output_dir / "attempts.jsonl"
    failures_path = output_dir / "failures.jsonl"
    initialize_jsonl_file(attempts_path)
    initialize_jsonl_file(failures_path)

    content_assets = content_linked_assets(context)
    attached_assets: tuple[RewriteAsset, ...] = ()
    image_payloads_by_path: dict[str, str] = {}
    preflight_error = ""
    try:
        if require_images and content_assets:
            assert_client_supports_images(client)
        image_payloads_by_path = image_payloads_by_asset_path(
            parsed_bundle_dir,
            content_assets,
            require_images=require_images,
        )
        attached_assets = assets_with_payloads(content_assets, image_payloads_by_path)
    except (LLMConfigurationError, LLMRequestError, LLMResponseError, SourceRewriteError) as exc:
        preflight_error = str(exc)

    _report(
        progress,
        phase="rewrite",
        source_id=context.source_id,
        detail="rewriting whole document",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_WHOLE_DOCUMENT,
        output_dir=output_dir,
    )
    attempt = (
        failed_whole_document_attempt(
            context=context,
            model=model,
            target_grade=resolved_target_grade,
            image_payloads_by_path=image_payloads_by_path,
            failure_reason=preflight_error,
        )
        if preflight_error
        else rewrite_whole_document_with_full_context(
            client,
            context=context,
            image_payloads_by_path=image_payloads_by_path,
            model=model,
            target_grade=resolved_target_grade,
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
    _report(
        progress,
        phase="write",
        source_id=context.source_id,
        detail="writing whole-document rewrite bundle",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_WHOLE_DOCUMENT,
        output_dir=output_dir,
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
        target_grade=resolved_target_grade,
        attached_assets=attached_assets,
        content_markdown=str(attempt["rewritten_markdown"]) if bool(attempt["accepted"]) else None,
        rewrite_mode=REWRITE_MODE_WHOLE_DOCUMENT,
    )

    rewritten_page_count = sum(1 for row in page_rows if row.get("status") == "rewritten")
    failed_page_count = sum(1 for row in page_rows if row.get("status") == "failed")
    _report(
        progress,
        phase="complete",
        source_id=context.source_id,
        detail=f"rewritten pages {rewritten_page_count}; failed pages {failed_page_count}",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_WHOLE_DOCUMENT,
        output_dir=output_dir,
        rewritten_pages=rewritten_page_count,
        failed_pages=failed_page_count,
    )
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
    target_grade: str | None = None,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
) -> SourceWholeRewriteBatchOutputs:
    source_outputs: list[SourceWholeRewriteOutputs] = []
    skipped_sources: list[dict[str, object]] = []
    bundle_dirs = discover_parsed_bundles(parsed_root)
    total_sources = len(bundle_dirs)
    for source_index, bundle_dir in enumerate(bundle_dirs, start=1):
        skip_reason = skip_reason_for_bundle(bundle_dir)
        if skip_reason:
            skipped_sources.append({"source_dir": str(bundle_dir), "reason": skip_reason})
            _report(
                progress,
                phase="skip",
                source_id=bundle_dir.name,
                detail=f"skipping: {skip_reason}",
                source_index=source_index,
                total_sources=total_sources,
                rewrite_mode=REWRITE_MODE_WHOLE_DOCUMENT,
            )
            continue
        source_outputs.append(
            rewrite_whole_source_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model=model,
                target_profile=target_profile,
                target_grade=target_grade,
                require_images=require_images,
                progress=progress,
                source_index=source_index,
                total_sources=total_sources,
            )
        )
    return SourceWholeRewriteBatchOutputs(
        parsed_root=parsed_root,
        output_root=output_root,
        source_outputs=source_outputs,
        skipped_sources=skipped_sources,
    )


def rewrite_vocabulary_guided_source_material(
    parsed_bundle_dir: Path,
    *,
    dictionary_path: Path,
    vocabulary_source_kind: str = "explicit",
    vocabulary_source_sha256: str | None = None,
    output_root: Path = DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    target_grade: str | None = None,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
    source_index: int = 1,
    total_sources: int = 1,
) -> SourceVocabularyGuidedRewriteOutputs:
    resolved_target_grade, target_profile = resolve_target_grade_and_profile(
        target_grade=target_grade,
        target_profile=target_profile,
    )
    context = build_context_packet(parsed_bundle_dir)
    validate_context_target_grade(context, resolved_target_grade, parsed_bundle_dir)
    if not context.pages:
        raise SourceRewriteError(f"No page chunks found in {parsed_bundle_dir}.")

    output_dir = output_root / context.source_id
    _report(
        progress,
        phase="start",
        source_id=context.source_id,
        detail=f"preparing vocabulary-guided rewrite output at {output_dir}",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
        output_dir=output_dir,
    )
    _report(
        progress,
        phase="load",
        source_id=context.source_id,
        detail=f"loading vocabulary guidance from {dictionary_path}",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
        output_dir=output_dir,
    )
    candidate_pool = load_vocabulary_guided_candidates(dictionary_path)
    prepare_output_dir(output_dir)
    copy_assets(parsed_bundle_dir, output_dir)
    (output_dir / "context_packet.md").write_text(context.content_markdown, encoding="utf-8")

    attempts_path = output_dir / "attempts.jsonl"
    failures_path = output_dir / "failures.jsonl"
    selection_attempts_path = output_dir / "selection_attempts.jsonl"
    initialize_jsonl_file(attempts_path)
    initialize_jsonl_file(failures_path)
    initialize_jsonl_file(selection_attempts_path)

    content_assets = content_linked_assets(context)
    attached_assets: tuple[RewriteAsset, ...] = ()
    image_payloads_by_path: dict[str, str] = {}
    preflight_error = ""
    try:
        if require_images and content_assets:
            assert_client_supports_images(client)
        image_payloads_by_path = image_payloads_by_asset_path(
            parsed_bundle_dir,
            content_assets,
            require_images=require_images,
        )
        attached_assets = assets_with_payloads(content_assets, image_payloads_by_path)
    except (LLMConfigurationError, LLMRequestError, LLMResponseError, SourceRewriteError) as exc:
        preflight_error = str(exc)

    _report(
        progress,
        phase="select",
        source_id=context.source_id,
        detail="selecting vocabulary guidance",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
        output_dir=output_dir,
    )
    if preflight_error:
        selection_attempt, selected = failed_vocabulary_selection_attempt(
            context=context,
            candidate_pool=candidate_pool,
            image_payloads_by_path=image_payloads_by_path,
            model=model,
            target_grade=resolved_target_grade,
            failure_reason=preflight_error,
        )
    elif not candidate_pool:
        selection_attempt, selected = failed_vocabulary_selection_attempt(
            context=context,
            candidate_pool=candidate_pool,
            image_payloads_by_path=image_payloads_by_path,
            model=model,
            target_grade=resolved_target_grade,
            failure_reason=f"No eligible vocabulary rows found in {dictionary_path}.",
        )
    else:
        selection_attempt, selected = select_vocabulary_for_rewrite(
            client,
            context=context,
            candidate_pool=candidate_pool,
            image_payloads_by_path=image_payloads_by_path,
            model=model,
            target_grade=resolved_target_grade,
        )

    selection_attempt_rows = [selection_attempt]
    append_jsonl(selection_attempts_path, selection_attempt_rows)
    selected_vocabulary_rows = [item.json_row() for item in selected]
    write_selected_vocabulary_artifact(
        output_dir / "selected_vocabulary.json",
        context=context,
        dictionary_path=dictionary_path,
        target_grade=resolved_target_grade,
        target_profile=target_profile,
        vocabulary_source_kind=vocabulary_source_kind,
        vocabulary_source_sha256=vocabulary_source_sha256,
        candidate_pool=candidate_pool,
        selected_vocabulary_rows=selected_vocabulary_rows,
        failure_reason="" if bool(selection_attempt["accepted"]) else str(selection_attempt["failure_reason"]),
    )

    if bool(selection_attempt["accepted"]):
        _report(
            progress,
            phase="rewrite",
            source_id=context.source_id,
            detail="rewriting whole document with selected vocabulary",
            source_index=source_index,
            total_sources=total_sources,
            rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
            output_dir=output_dir,
            selected_vocabulary=len(selected_vocabulary_rows),
        )
        attempt = rewrite_vocabulary_guided_document_with_full_context(
            client,
            context=context,
            selected_vocabulary=selected,
            image_payloads_by_path=image_payloads_by_path,
            model=model,
            target_grade=resolved_target_grade,
        )
    else:
        attempt = failed_vocabulary_guided_document_attempt(
            context=context,
            model=model,
            target_grade=resolved_target_grade,
            failure_reason="Vocabulary selection failed: " + str(selection_attempt["failure_reason"]),
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
    _report(
        progress,
        phase="write",
        source_id=context.source_id,
        detail="writing vocabulary-guided rewrite bundle",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
        output_dir=output_dir,
        selected_vocabulary=len(selected_vocabulary_rows),
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
        target_grade=resolved_target_grade,
        attached_assets=attached_assets,
        content_markdown=str(attempt["rewritten_markdown"]) if bool(attempt["accepted"]) else None,
        rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
        vocabulary_guidance=vocabulary_guidance_metadata(
            dictionary_path=dictionary_path,
            target_grade=resolved_target_grade,
            target_profile=target_profile,
            vocabulary_source_kind=vocabulary_source_kind,
            vocabulary_source_sha256=vocabulary_source_sha256,
            candidate_pool=candidate_pool,
            selected_vocabulary_rows=selected_vocabulary_rows,
        ),
    )

    rewritten_page_count = sum(1 for row in page_rows if row.get("status") == "rewritten")
    failed_page_count = sum(1 for row in page_rows if row.get("status") == "failed")
    _report(
        progress,
        phase="complete",
        source_id=context.source_id,
        detail=f"selected vocabulary {len(selected_vocabulary_rows)}; rewritten pages {rewritten_page_count}; failed pages {failed_page_count}",
        source_index=source_index,
        total_sources=total_sources,
        rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
        output_dir=output_dir,
        rewritten_pages=rewritten_page_count,
        failed_pages=failed_page_count,
        selected_vocabulary=len(selected_vocabulary_rows),
    )
    return SourceVocabularyGuidedRewriteOutputs(
        source_id=context.source_id,
        output_dir=output_dir,
        rewritten_page_count=rewritten_page_count,
        failed_page_count=failed_page_count,
        page_rows=page_rows,
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
        selection_attempt_rows=selection_attempt_rows,
        selected_vocabulary=selected_vocabulary_rows,
    )


def rewrite_vocabulary_guided_source_materials(
    parsed_root: Path,
    *,
    dictionary_path: Path,
    vocabulary_source_kind: str = "explicit",
    vocabulary_source_sha256: str | None = None,
    output_root: Path = DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    target_grade: str | None = None,
    require_images: bool = True,
    progress: ProgressCallback | None = None,
) -> SourceVocabularyGuidedRewriteBatchOutputs:
    source_outputs: list[SourceVocabularyGuidedRewriteOutputs] = []
    skipped_sources: list[dict[str, object]] = []
    bundle_dirs = discover_parsed_bundles(parsed_root)
    total_sources = len(bundle_dirs)
    for source_index, bundle_dir in enumerate(bundle_dirs, start=1):
        skip_reason = skip_reason_for_bundle(bundle_dir)
        if skip_reason:
            skipped_sources.append({"source_dir": str(bundle_dir), "reason": skip_reason})
            _report(
                progress,
                phase="skip",
                source_id=bundle_dir.name,
                detail=f"skipping: {skip_reason}",
                source_index=source_index,
                total_sources=total_sources,
                rewrite_mode=REWRITE_MODE_VOCABULARY_GUIDED,
            )
            continue
        source_outputs.append(
            rewrite_vocabulary_guided_source_material(
                bundle_dir,
                dictionary_path=dictionary_path,
                vocabulary_source_kind=vocabulary_source_kind,
                vocabulary_source_sha256=vocabulary_source_sha256,
                output_root=output_root,
                client=client,
                model=model,
                target_profile=target_profile,
                target_grade=target_grade,
                require_images=require_images,
                progress=progress,
                source_index=source_index,
                total_sources=total_sources,
            )
        )
    return SourceVocabularyGuidedRewriteBatchOutputs(
        parsed_root=parsed_root,
        output_root=output_root,
        source_outputs=source_outputs,
        skipped_sources=skipped_sources,
    )


def load_vocabulary_guided_candidates(dictionary_path: Path) -> list[VocabularyCandidate]:
    if not dictionary_path.is_file():
        raise SourceRewriteError(f"Missing vocabulary-guided rewrite dictionary: {dictionary_path}")
    try:
        rows = read_jsonl(dictionary_path)
    except (OSError, ValueError) as exc:
        raise SourceRewriteError(f"Could not read vocabulary-guided rewrite dictionary {dictionary_path}: {exc}") from exc

    candidates: list[VocabularyCandidate] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SourceRewriteError(f"Dictionary row {index} in {dictionary_path} is not a JSON object.")
        candidate = vocabulary_candidate_from_dictionary_row(row, row_number=index, dictionary_path=dictionary_path)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(
        key=lambda candidate: (
            -candidate.selection_score,
            -candidate.class_printout_count,
            -candidate.class_count,
            -candidate.learning_signal_score,
            -candidate.count,
            candidate.canonical_key,
        )
    )
    return candidates[:VOCABULARY_GUIDED_CANDIDATE_LIMIT]


def vocabulary_candidate_from_dictionary_row(
    row: Mapping[str, object],
    *,
    row_number: int,
    dictionary_path: Path,
) -> VocabularyCandidate | None:
    if row.get("active") is False:
        return None
    canonical_key = str(row.get("canonical_key", "") or "").strip()
    example = str(row.get("example", "") or "").strip()
    if not canonical_key or not example:
        return None
    learning_signal_score = row_learning_signal_score(row)
    normalized_signal_score = normalized_learning_signal_score(row)
    try:
        roi_score = parse_roi_score(row.get("roi_score"))
    except ValueError as exc:
        raise SourceRewriteError(f"Invalid roi_score in row {row_number} of {dictionary_path}: {exc}") from exc
    count = parse_nonnegative_int(
        first_present(row, "count", "class_count", "canonical_count", default=0),
        field_name="count",
        row_number=row_number,
        dictionary_path=dictionary_path,
    )
    class_count = parse_nonnegative_int(
        row.get("class_count", 0),
        field_name="class_count",
        row_number=row_number,
        dictionary_path=dictionary_path,
    )
    class_printout_count = parse_nonnegative_int(
        row.get("class_printout_count", 0),
        field_name="class_printout_count",
        row_number=row_number,
        dictionary_path=dictionary_path,
    )
    selection_score = roi_score if roi_score is not None else normalized_signal_score
    return VocabularyCandidate(
        canonical_key=canonical_key,
        example=example,
        learning_signal_score=learning_signal_score,
        roi_score=roi_score,
        selection_score=selection_score,
        count=count,
        class_count=class_count,
        class_printout_count=class_printout_count,
    )


def first_present(row: Mapping[str, object], *field_names: str, default: object = None) -> object:
    for field_name in field_names:
        value = row.get(field_name)
        if value is not None:
            return value
    return default


def parse_nonnegative_int(
    value: object,
    *,
    field_name: str,
    row_number: int,
    dictionary_path: Path,
) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise SourceRewriteError(f"Invalid {field_name} in row {row_number} of {dictionary_path}: {value!r}")
    try:
        count = int(cast(str | bytes | bytearray | int | float, value))
    except (TypeError, ValueError) as exc:
        raise SourceRewriteError(f"Invalid {field_name} in row {row_number} of {dictionary_path}: {value!r}") from exc
    return max(count, 0)


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
    manifest_target_grade = str(manifest.get("target_grade") or "").strip()
    if not manifest_target_grade:
        raise SourceRewriteError(f"Parsed source bundle has no target_grade: {manifest_path}")
    try:
        resolved_target_grade = normalize_target_grade(manifest_target_grade)
    except ValueError as error:
        raise SourceRewriteError(f"Parsed source bundle has invalid target_grade: {manifest_path}") from error
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
    source_content_markdown = content_path.read_text(encoding="utf-8")
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
        target_grade=resolved_target_grade,
        source_sha256=str(source.get("sha256") or ""),
        source_filename=str(source.get("filename") or ""),
        parser_name=str(parser.get("name") or ""),
        parser_version=str(parser.get("version") or ""),
        source_content_markdown=source_content_markdown,
        content_markdown=content_markdown,
        pages=tuple(pages),
        assets=tuple(assets),
    )


def validate_context_target_grade(
    context: ContextPacket,
    target_grade: str,
    parsed_bundle_dir: Path,
) -> None:
    if context.target_grade != target_grade:
        raise SourceRewriteError(
            f"Parsed source bundle grade {context.target_grade} does not match target grade {target_grade}: {parsed_bundle_dir}"
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


def fallback_asset(path: str, *, page: int | None) -> RewriteAsset:
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


def content_linked_assets(context: ContextPacket) -> tuple[RewriteAsset, ...]:
    assets_by_path = {asset.path: asset for asset in context.assets}
    linked_assets: list[RewriteAsset] = []
    seen: set[str] = set()
    for path, page in content_image_references(context.source_content_markdown):
        if path in seen:
            continue
        linked_assets.append(assets_by_path.get(path) or fallback_asset(path, page=page))
        seen.add(path)
    return tuple(linked_assets)


def content_image_references(content_markdown: str) -> list[tuple[str, int | None]]:
    references: list[tuple[str, int | None]] = []
    current_page: int | None = None
    for line in content_markdown.splitlines():
        page_match = CONTENT_PAGE_MARKER_PATTERN.search(line)
        if page_match:
            current_page = int(page_match.group(1))
        for path in IMAGE_SRC_PATTERN.findall(line):
            references.append((path, current_page or page_from_path(path)))
    return references


def is_image_asset(asset: RewriteAsset) -> bool:
    return asset.mime_type.startswith("image/") or Path(asset.path).suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".gif",
    }


def image_payloads_by_asset_path(
    parsed_bundle_dir: Path,
    assets: Sequence[RewriteAsset],
    *,
    require_images: bool,
) -> dict[str, str]:
    payloads: dict[str, str] = {}
    for asset in assets:
        if not is_image_asset(asset):
            if require_images:
                raise SourceRewriteError(f"Referenced asset is not an image for rewrite prompt: {asset.path}")
            continue
        asset_path = parsed_bundle_dir / asset.path
        if not asset_path.is_file():
            if require_images:
                raise SourceRewriteError(f"Missing asset file for rewrite prompt: {asset_path}")
            continue
        payloads[asset.path] = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return payloads


def assets_with_payloads(
    assets: Sequence[RewriteAsset],
    image_payloads_by_path: Mapping[str, str],
) -> tuple[RewriteAsset, ...]:
    return tuple(asset for asset in assets if asset.path in image_payloads_by_path)


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


def build_interleaved_rewrite_prompt(
    context: ContextPacket,
    *,
    final_instruction: str,
    image_payloads_by_path: Mapping[str, str],
    system_prompt: str = GRADE_ONE_REWRITE_SYSTEM_PROMPT,
) -> RewritePrompt:
    assets_by_path = {asset.path: asset for asset in context.assets}
    prompt_messages: list[PromptMessage] = [
        PromptMessage(
            ChatMessage(
                role="system",
                content=system_prompt,
            )
        ),
        PromptMessage(
            ChatMessage(
                role="user",
                content=(
                    "Parsed source content.md follows. Text and images are in the original "
                    "document order."
                ),
            )
        ),
    ]
    prompt_messages.extend(
        interleaved_content_messages(
            context.source_content_markdown,
            assets_by_path=assets_by_path,
            image_payloads_by_path=image_payloads_by_path,
        )
    )
    prompt_messages.append(PromptMessage(ChatMessage(role="user", content=final_instruction)))
    rows = tuple(prompt_message_row(prompt_message) for prompt_message in prompt_messages)
    attached_assets = unique_prompt_assets(prompt_messages)
    return RewritePrompt(
        messages=tuple(prompt_message.message for prompt_message in prompt_messages),
        user_prompt=debug_user_prompt(rows),
        prompt_messages=rows,
        attached_assets=attached_assets,
    )


def interleaved_content_messages(
    content_markdown: str,
    *,
    assets_by_path: Mapping[str, RewriteAsset],
    image_payloads_by_path: Mapping[str, str],
) -> tuple[PromptMessage, ...]:
    messages: list[PromptMessage] = []
    text_buffer: list[str] = []
    current_page: int | None = None

    def flush_text() -> None:
        content = "".join(text_buffer).strip()
        if content:
            messages.append(PromptMessage(ChatMessage(role="user", content=content)))
        text_buffer.clear()

    for line in content_markdown.splitlines(keepends=True):
        page_match = CONTENT_PAGE_MARKER_PATTERN.search(line)
        if page_match:
            current_page = int(page_match.group(1))
        image_paths = IMAGE_SRC_PATTERN.findall(line)
        if not image_paths:
            text_buffer.append(line)
            continue

        line_assets: list[RewriteAsset] = []
        line_payloads: list[str] = []
        for image_path in image_paths:
            payload = image_payloads_by_path.get(image_path)
            if not payload:
                continue
            line_assets.append(
                assets_by_path.get(image_path)
                or fallback_asset(image_path, page=current_page or page_from_path(image_path))
            )
            line_payloads.append(payload)

        if not line_payloads:
            text_buffer.append(line)
            continue

        flush_text()
        messages.append(
            PromptMessage(
                ChatMessage(role="user", content=line.strip(), images=tuple(line_payloads)),
                attached_assets=tuple(line_assets),
            )
        )

    flush_text()
    return tuple(messages)


def prompt_message_row(prompt_message: PromptMessage) -> dict[str, object]:
    return {
        "role": prompt_message.message.role,
        "content": prompt_message.message.content,
        "attached_asset_ids": [asset.asset_id for asset in prompt_message.attached_assets],
    }


def debug_user_prompt(prompt_messages: Sequence[Mapping[str, object]]) -> str:
    return "\n\n".join(
        str(message.get("content") or "")
        for message in prompt_messages
        if message.get("role") == "user"
    ).strip()


def unique_prompt_assets(prompt_messages: Sequence[PromptMessage]) -> tuple[RewriteAsset, ...]:
    assets: list[RewriteAsset] = []
    seen: set[str] = set()
    for prompt_message in prompt_messages:
        for asset in prompt_message.attached_assets:
            if asset.path in seen:
                continue
            assets.append(asset)
            seen.add(asset.path)
    return tuple(assets)


def rewrite_whole_document_with_full_context(
    client: SupportsCompletion,
    *,
    context: ContextPacket,
    image_payloads_by_path: Mapping[str, str],
    model: str,
    target_grade: str = DEFAULT_TARGET_GRADE,
) -> dict[str, object]:
    system_prompt = rewrite_system_prompt_for_grade(target_grade)
    prompt = build_whole_document_rewrite_prompt(context, image_payloads_by_path, target_grade=target_grade)
    metrics: dict[str, object] = source_document_metrics(context)
    result: CompletionResult | None = None
    try:
        result = client.complete(
            list(prompt.messages),
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
            "rewrite_mode": REWRITE_MODE_WHOLE_DOCUMENT,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": prompt.user_prompt,
            "prompt_messages": list(prompt.prompt_messages),
            "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
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
            "rewrite_mode": REWRITE_MODE_WHOLE_DOCUMENT,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": prompt.user_prompt,
            "prompt_messages": list(prompt.prompt_messages),
            "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
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
    target_grade: str = DEFAULT_TARGET_GRADE,
    image_payloads_by_path: Mapping[str, str],
    failure_reason: str,
) -> dict[str, object]:
    system_prompt = rewrite_system_prompt_for_grade(target_grade)
    prompt = build_whole_document_rewrite_prompt(context, image_payloads_by_path, target_grade=target_grade)
    return {
        "source_id": context.source_id,
        "page": None,
        "rewrite_mode": REWRITE_MODE_WHOLE_DOCUMENT,
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": prompt.user_prompt,
        "prompt_messages": list(prompt.prompt_messages),
        "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
        "response": "",
        "rewritten_markdown": "",
        "accepted": False,
        "failure_reason": failure_reason,
        "metrics": source_document_metrics(context),
    }


def select_vocabulary_for_rewrite(
    client: SupportsCompletion,
    *,
    context: ContextPacket,
    candidate_pool: Sequence[VocabularyCandidate],
    image_payloads_by_path: Mapping[str, str],
    model: str,
    target_grade: str = DEFAULT_TARGET_GRADE,
) -> tuple[dict[str, object], list[SelectedVocabulary]]:
    prompt = build_vocabulary_selection_prompt(
        context,
        candidate_pool,
        image_payloads_by_path,
        target_grade=target_grade,
    )
    result: CompletionResult | None = None
    try:
        result = client.complete(
            list(prompt.messages),
            temperature=0.2,
            max_output_tokens=max(1024, min(8192, len(candidate_pool) * 96)),
            json_mode=True,
        )
        selected = parse_and_validate_vocabulary_selection_response(
            result,
            context=context,
            candidate_pool=candidate_pool,
        )
        return (
            vocabulary_selection_attempt_row(
                context=context,
                prompt=prompt,
                candidate_pool=candidate_pool,
                model=model,
                response=result.content,
                selected_vocabulary=selected,
                accepted=True,
                failure_reason="",
            ),
            selected,
        )
    except (LLMConfigurationError, LLMRequestError, LLMResponseError, SourceRewriteError) as exc:
        return (
            vocabulary_selection_attempt_row(
                context=context,
                prompt=prompt,
                candidate_pool=candidate_pool,
                model=model,
                response=result.content if result is not None else "",
                selected_vocabulary=[],
                accepted=False,
                failure_reason=str(exc),
            ),
            [],
        )


def vocabulary_selection_attempt_row(
    *,
    context: ContextPacket,
    prompt: RewritePrompt,
    candidate_pool: Sequence[VocabularyCandidate],
    model: str,
    response: str,
    selected_vocabulary: Sequence[SelectedVocabulary],
    accepted: bool,
    failure_reason: str,
) -> dict[str, object]:
    system_prompt = str(prompt.prompt_messages[0].get("content") or VOCABULARY_SELECTION_SYSTEM_PROMPT)
    return {
        "source_id": context.source_id,
        "rewrite_mode": REWRITE_MODE_VOCABULARY_GUIDED,
        "attempt_kind": "vocabulary_selection",
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": prompt.user_prompt,
        "prompt_messages": list(prompt.prompt_messages),
        "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
        "candidate_count": len(candidate_pool),
        "requested_count": expected_selected_vocabulary_count(candidate_pool),
        "response": response,
        "selected_canonical_keys": [item.candidate.canonical_key for item in selected_vocabulary],
        "accepted": accepted,
        "failure_reason": failure_reason,
    }


def failed_vocabulary_selection_attempt(
    *,
    context: ContextPacket,
    candidate_pool: Sequence[VocabularyCandidate],
    image_payloads_by_path: Mapping[str, str],
    model: str,
    target_grade: str = DEFAULT_TARGET_GRADE,
    failure_reason: str,
) -> tuple[dict[str, object], list[SelectedVocabulary]]:
    prompt = build_vocabulary_selection_prompt(
        context,
        candidate_pool,
        image_payloads_by_path,
        target_grade=target_grade,
    )
    return (
        vocabulary_selection_attempt_row(
            context=context,
            prompt=prompt,
            candidate_pool=candidate_pool,
            model=model,
            response="",
            selected_vocabulary=[],
            accepted=False,
            failure_reason=failure_reason,
        ),
        [],
    )


def rewrite_vocabulary_guided_document_with_full_context(
    client: SupportsCompletion,
    *,
    context: ContextPacket,
    selected_vocabulary: Sequence[SelectedVocabulary],
    image_payloads_by_path: Mapping[str, str],
    model: str,
    target_grade: str = DEFAULT_TARGET_GRADE,
) -> dict[str, object]:
    prompt = build_vocabulary_guided_rewrite_prompt(
        context,
        selected_vocabulary,
        image_payloads_by_path,
        target_grade=target_grade,
    )
    system_prompt = vocabulary_guided_rewrite_system_prompt_for_grade(target_grade)
    metrics: dict[str, object] = {
        **source_document_metrics(context),
        "selected_vocabulary_count": len(selected_vocabulary),
        "selected_canonical_keys": [item.candidate.canonical_key for item in selected_vocabulary],
    }
    result: CompletionResult | None = None
    try:
        result = client.complete(
            list(prompt.messages),
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
            "rewrite_mode": REWRITE_MODE_VOCABULARY_GUIDED,
            "attempt_kind": "rewrite",
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": prompt.user_prompt,
            "prompt_messages": list(prompt.prompt_messages),
            "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
            "selected_vocabulary": [item.json_row() for item in selected_vocabulary],
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
            "rewrite_mode": REWRITE_MODE_VOCABULARY_GUIDED,
            "attempt_kind": "rewrite",
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": prompt.user_prompt,
            "prompt_messages": list(prompt.prompt_messages),
            "attached_asset_ids": [asset.asset_id for asset in prompt.attached_assets],
            "selected_vocabulary": [item.json_row() for item in selected_vocabulary],
            "response": result.content if result is not None else "",
            "rewritten_markdown": "",
            "accepted": False,
            "failure_reason": str(exc),
            "metrics": metrics,
        }


def failed_vocabulary_guided_document_attempt(
    *,
    context: ContextPacket,
    model: str,
    target_grade: str = DEFAULT_TARGET_GRADE,
    failure_reason: str,
) -> dict[str, object]:
    return {
        "source_id": context.source_id,
        "page": None,
        "rewrite_mode": REWRITE_MODE_VOCABULARY_GUIDED,
        "attempt_kind": "rewrite",
        "model": model,
        "system_prompt": vocabulary_guided_rewrite_system_prompt_for_grade(target_grade),
        "user_prompt": "",
        "prompt_messages": [],
        "attached_asset_ids": [],
        "selected_vocabulary": [],
        "response": "",
        "rewritten_markdown": "",
        "accepted": False,
        "failure_reason": failure_reason,
        "metrics": source_document_metrics(context),
    }


def build_vocabulary_selection_prompt(
    context: ContextPacket,
    candidate_pool: Sequence[VocabularyCandidate],
    image_payloads_by_path: Mapping[str, str],
    *,
    target_grade: str = DEFAULT_TARGET_GRADE,
) -> RewritePrompt:
    profile = grade_profile(target_grade)
    required_shape = {
        "source_id": context.source_id,
        "selected_vocabulary": [
            {
                "canonical_key": "...",
                "reason": "...",
                "role": "...",
            }
        ],
    }
    candidate_rows = [candidate.prompt_row() for candidate in candidate_pool]
    instruction = "\n".join(
        [
            "Vocabulary-selection request:",
            f"source_id: {context.source_id}",
            f"Choose exactly {expected_selected_vocabulary_count(candidate_pool)} vocabulary items.",
            "Choose only from the offered candidate_vocabulary array.",
            (
                "Use the parsed source material above to decide which words best support "
                f"a rewrite for {profile.reader_description}."
            ),
            "The offered rows contain only canonical_key and example.",
            "",
            "candidate_vocabulary:",
            json.dumps(candidate_rows, ensure_ascii=False),
            "",
            "Return JSON exactly in this shape:",
            json.dumps(required_shape, ensure_ascii=False),
        ]
    )
    return build_interleaved_rewrite_prompt(
        context,
        final_instruction=instruction,
        image_payloads_by_path=image_payloads_by_path,
        system_prompt=vocabulary_selection_system_prompt_for_grade(target_grade),
    )


def build_vocabulary_guided_rewrite_prompt(
    context: ContextPacket,
    selected_vocabulary: Sequence[SelectedVocabulary],
    image_payloads_by_path: Mapping[str, str],
    *,
    target_grade: str = DEFAULT_TARGET_GRADE,
) -> RewritePrompt:
    profile = grade_profile(target_grade)
    required_shape = {
        "source_id": context.source_id,
        "rewritten_markdown": "...",
    }
    page_markers = ", ".join(f"<!-- page:{page.page} -->" for page in context.pages)
    selected_rows = [item.json_row() for item in selected_vocabulary]
    instruction = "\n".join(
        [
            "Vocabulary-guided whole-document rewrite request:",
            f"source_id: {context.source_id}",
            f"Rewrite the entire parsed content.md above as faithful Korean reading material for {profile.reader_description}.",
            "Use the selected vocabulary as the learner-friendly word palette.",
            "Use each selected vocabulary item naturally when it can fit without distorting the source concepts.",
            "Concrete selected words may be used as metaphors for difficult source concepts.",
            f"Preserve every page marker exactly: {page_markers}",
            "Preserve every image/html block exactly where it belongs in the rewritten document.",
            "Return one complete rewritten Markdown document, not page-by-page fragments.",
            "",
            "selected_vocabulary:",
            json.dumps(selected_rows, ensure_ascii=False),
            "",
            "Return JSON exactly in this shape:",
            json.dumps(required_shape, ensure_ascii=False),
        ]
    )
    return build_interleaved_rewrite_prompt(
        context,
        final_instruction=instruction,
        image_payloads_by_path=image_payloads_by_path,
        system_prompt=vocabulary_guided_rewrite_system_prompt_for_grade(target_grade),
    )


def parse_and_validate_vocabulary_selection_response(
    result: CompletionResult,
    *,
    context: ContextPacket,
    candidate_pool: Sequence[VocabularyCandidate],
) -> list[SelectedVocabulary]:
    if MARKDOWN_FENCE in result.content:
        raise SourceRewriteError("Vocabulary-selection response included a markdown fence.")
    parsed = parse_json_object(result.content)
    if str(parsed.get("source_id") or "") != context.source_id:
        raise SourceRewriteError(f"Vocabulary-selection response omitted requested source_id '{context.source_id}'.")
    raw_selected = parsed.get("selected_vocabulary")
    if not isinstance(raw_selected, list):
        raise SourceRewriteError("Vocabulary-selection response did not include selected_vocabulary as a list.")

    candidate_by_key = {candidate.canonical_key: candidate for candidate in candidate_pool}
    expected_count = expected_selected_vocabulary_count(candidate_pool)
    if len(raw_selected) != expected_count:
        raise SourceRewriteError(
            f"Vocabulary-selection response selected {len(raw_selected)} row(s); expected {expected_count}."
        )

    selected: list[SelectedVocabulary] = []
    seen_keys: set[str] = set()
    for item in raw_selected:
        if isinstance(item, str):
            canonical_key = item.strip()
            reason = ""
            role = ""
        elif isinstance(item, Mapping):
            canonical_key = str(item.get("canonical_key", "") or "").strip()
            reason = str(item.get("reason", "") or "").strip()
            role = str(item.get("role", "") or "").strip()
        else:
            raise SourceRewriteError("Vocabulary-selection entries must be objects or canonical-key strings.")
        if not canonical_key:
            raise SourceRewriteError("Vocabulary-selection response included an empty canonical_key.")
        if canonical_key in seen_keys:
            raise SourceRewriteError(f"Vocabulary-selection response repeated canonical_key '{canonical_key}'.")
        if canonical_key not in candidate_by_key:
            raise SourceRewriteError(f"Vocabulary-selection response included unknown canonical_key '{canonical_key}'.")
        seen_keys.add(canonical_key)
        selected.append(SelectedVocabulary(candidate_by_key[canonical_key], reason=reason, role=role))
    return selected


def expected_selected_vocabulary_count(candidate_pool: Sequence[VocabularyCandidate]) -> int:
    return min(VOCABULARY_GUIDED_SELECTION_LIMIT, len(candidate_pool))


def write_selected_vocabulary_artifact(
    path: Path,
    *,
    context: ContextPacket,
    dictionary_path: Path,
    target_grade: str = DEFAULT_TARGET_GRADE,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    vocabulary_source_kind: str = "explicit",
    vocabulary_source_sha256: str | None = None,
    candidate_pool: Sequence[VocabularyCandidate],
    selected_vocabulary_rows: Sequence[Mapping[str, object]],
    failure_reason: str = "",
) -> None:
    resolved_vocabulary_source_sha256 = vocabulary_source_sha256 or file_sha256_if_present(dictionary_path)
    path.write_text(
        json.dumps(
            {
                "source_id": context.source_id,
                "target_grade": target_grade,
                "target_profile": target_profile,
                "dictionary_path": str(dictionary_path),
                "vocabulary_source": {
                    "kind": vocabulary_source_kind,
                    "path": str(dictionary_path),
                    "sha256": resolved_vocabulary_source_sha256,
                },
                "candidate_count": len(candidate_pool),
                "selected_count": len(selected_vocabulary_rows),
                "selection_scoring_policy": VOCABULARY_GUIDED_SCORING_POLICY,
                "selected_canonical_keys": [row.get("canonical_key", "") for row in selected_vocabulary_rows],
                "selected_vocabulary": list(selected_vocabulary_rows),
                "failure_reason": failure_reason,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def vocabulary_guidance_metadata(
    *,
    dictionary_path: Path,
    target_grade: str = DEFAULT_TARGET_GRADE,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    vocabulary_source_kind: str = "explicit",
    vocabulary_source_sha256: str | None = None,
    candidate_pool: Sequence[VocabularyCandidate],
    selected_vocabulary_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    resolved_vocabulary_source_sha256 = vocabulary_source_sha256 or file_sha256_if_present(dictionary_path)
    return {
        "target_grade": target_grade,
        "target_profile": target_profile,
        "dictionary_path": str(dictionary_path),
        "vocabulary_source": {
            "kind": vocabulary_source_kind,
            "path": str(dictionary_path),
            "sha256": resolved_vocabulary_source_sha256,
        },
        "vocabulary_source_kind": vocabulary_source_kind,
        "vocabulary_source_sha256": resolved_vocabulary_source_sha256,
        "candidate_count": len(candidate_pool),
        "selected_count": len(selected_vocabulary_rows),
        "selected_canonical_keys": [row.get("canonical_key", "") for row in selected_vocabulary_rows],
        "selection_scoring_policy": VOCABULARY_GUIDED_SCORING_POLICY,
    }


def file_sha256_if_present(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_whole_document_rewrite_prompt(
    context: ContextPacket,
    image_payloads_by_path: Mapping[str, str],
    *,
    target_grade: str = DEFAULT_TARGET_GRADE,
) -> RewritePrompt:
    profile = grade_profile(target_grade)
    required_shape = {
        "source_id": context.source_id,
        "rewritten_markdown": "...",
    }
    page_markers = ", ".join(f"<!-- page:{page.page} -->" for page in context.pages)
    instruction = "\n".join(
        [
            "Whole-document rewrite request:",
            f"source_id: {context.source_id}",
            f"Rewrite the entire parsed content.md above at once for {profile.reader_description}.",
            "Use the full parsed document and interleaved images above as the source material.",
            f"Preserve every page marker exactly: {page_markers}",
            "Preserve every image/html block exactly where it belongs in the rewritten document.",
            "Return one complete rewritten Markdown document, not page-by-page fragments.",
            "",
            "Return JSON exactly in this shape:",
            json.dumps(required_shape, ensure_ascii=False),
        ]
    )
    return build_interleaved_rewrite_prompt(
        context,
        final_instruction=instruction,
        image_payloads_by_path=image_payloads_by_path,
        system_prompt=rewrite_system_prompt_for_grade(target_grade),
    )


def whole_document_max_output_tokens(context: ContextPacket) -> int:
    return max(8192, min(32768, len(context.source_content_markdown) * 2))


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
    target_grade: str,
    attached_assets: Sequence[RewriteAsset],
    rewrite_mode: str,
    content_markdown: str | None = None,
    vocabulary_guidance: Mapping[str, object] | None = None,
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
                target_grade=target_grade,
                attached_assets=attached_assets,
                rewrite_mode=rewrite_mode,
                vocabulary_guidance=vocabulary_guidance,
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
                target_grade=target_grade,
                attached_assets=attached_assets,
                rewrite_mode=rewrite_mode,
                vocabulary_guidance=vocabulary_guidance,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_jsonl(output_dir / "attempts.jsonl", list(attempt_rows))
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
    target_grade: str,
    attached_assets: Sequence[RewriteAsset],
    rewrite_mode: str,
    vocabulary_guidance: Mapping[str, object] | None = None,
) -> dict[str, object]:
    rewritten_count = sum(1 for row in page_rows if row.get("status") == "rewritten")
    failed_count = sum(1 for row in page_rows if row.get("status") == "failed")
    report = {
        "schema_version": SOURCE_REWRITE_SCHEMA_VERSION,
        "source_id": context.source_id,
        "target_grade": target_grade,
        "target_profile": target_profile,
        "rewrite_mode": rewrite_mode,
        "model": model,
        "page_count": len(page_rows),
        "rewritten_page_count": rewritten_count,
        "failed_page_count": failed_count,
        "failed_attempt_count": len(failure_rows),
        "asset_count": len(context.assets),
        "asset_mode": "content-linked-interleaved-images",
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
    if vocabulary_guidance is not None:
        report["vocabulary_guidance"] = dict(vocabulary_guidance)
    return report


def build_rewrite_manifest(
    *,
    context: ContextPacket,
    model: str,
    target_profile: str,
    target_grade: str,
    attached_assets: Sequence[RewriteAsset],
    rewrite_mode: str,
    vocabulary_guidance: Mapping[str, object] | None = None,
) -> dict[str, object]:
    rewrite = {
        "target_grade": target_grade,
        "target_profile": target_profile,
        "mode": rewrite_mode,
        "model": model,
        "asset_mode": "content-linked-interleaved-images",
        "attached_image_count": len(attached_assets),
        "rewritten_at": datetime.now(timezone.utc).isoformat(),
    }
    files = {
        "assets": "assets/",
        "content_markdown": "content.md",
        "chunks_json": "chunks.json",
        "context_packet_markdown": "context_packet.md",
        "attempts_jsonl": "attempts.jsonl",
        "failures_jsonl": "failures.jsonl",
        "quality_report_json": "quality_report.json",
    }
    if vocabulary_guidance is not None:
        rewrite["vocabulary_guidance"] = dict(vocabulary_guidance)
        files["selection_attempts_jsonl"] = "selection_attempts.jsonl"
        files["selected_vocabulary_json"] = "selected_vocabulary.json"

    return {
        "schema_version": SOURCE_REWRITE_SCHEMA_VERSION,
        "source": {
            "id": context.source_id,
            "target_grade": context.target_grade,
            "filename": context.source_filename,
            "sha256": context.source_sha256,
        },
        "parser": {
            "name": context.parser_name,
            "version": context.parser_version,
        },
        "rewrite": rewrite,
        "files": files,
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
    return read_json_object_file(path, error_type=SourceRewriteError)


def read_json_list(path: Path) -> list[Any]:
    return read_json_list_file(path, error_type=SourceRewriteError)


def mapping_value(row: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = row.get(key)
    if not isinstance(value, Mapping):
        raise SourceRewriteError(f"Expected '{key}' object in parsed source metadata.")
    return value


def int_or_none(value: object) -> int | None:
    try:
        return int(cast(str | bytes | bytearray | int | float, value))
    except (TypeError, ValueError):
        return None


def _report(
    progress: ProgressCallback | None,
    *,
    phase: SourceRewriteProgressPhase,
    source_id: str,
    detail: str,
    source_index: int = 1,
    total_sources: int = 1,
    rewrite_mode: str = "",
    output_dir: Path | None = None,
    rewritten_pages: int = 0,
    failed_pages: int = 0,
    selected_vocabulary: int = 0,
) -> None:
    if progress is not None:
        progress(
            SourceRewriteProgress(
                phase=phase,
                source_id=source_id,
                detail=detail,
                source_index=source_index,
                total_sources=total_sources,
                rewrite_mode=rewrite_mode,
                output_dir=output_dir,
                rewritten_pages=rewritten_pages,
                failed_pages=failed_pages,
                selected_vocabulary=selected_vocabulary,
            )
        )
