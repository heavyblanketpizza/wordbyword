from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from kiwipiepy import Kiwi
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from wordbyword.class_archive.archive import DEFAULT_VOCABULARY_INTELLIGENCE_ROOT, is_managed_archive_name
from wordbyword.class_archive.usage_signals import (
    SUPPORTED_USAGE_SIGNAL_EXTENSIONS,
    read_usage_signal_document,
)
from wordbyword.classroom_printout_studio.class_printing.printable import (
    PrintableMaterialError,
    add_docx_paragraph,
    configure_docx_styles,
    resolve_korean_font,
    set_docx_east_asia_font,
)
from wordbyword.dictionary_build.analyzer import KoreanAnalyzer
from wordbyword.dictionary_build.artifacts import ArtifactId, artifact_filename, artifact_path
from wordbyword.dictionary_build.candidates import normalized_learning_signal_score, row_learning_signal_score
from wordbyword.dictionary_build.llm import (
    ChatMessage,
    CompletionResult,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseError,
    SupportsCompletion,
    parse_json_object,
)
from wordbyword.dictionary_build.output_files import (
    append_jsonl,
    initialize_jsonl_file,
    read_jsonl,
    read_rows,
    write_jsonl,
)
from wordbyword.dictionary_build.text import clean_example_text
from wordbyword.dictionary_build.writers import write_outputs
from wordbyword.grade_profiles import (
    UNKNOWN_GRADE,
    default_after_class_report_output_root,
    extract_grade_from_source_id,
    grade_label,
    report_target_profile_for_grade,
)

AFTER_CLASS_REPORT_SCHEMA_VERSION = "wordbyword.after_class_report.v1"
DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT = default_after_class_report_output_root()
RESERVED_MAINTENANCE_OUTPUT_ROOTS = (
    DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "usage-signal-build",
    DEFAULT_VOCABULARY_INTELLIGENCE_ROOT / "live-grade-lexicon",
)
HEURISTIC_ANALYSIS_DIRNAME = "heuristic-analysis"
REPORT_PLAN_FILENAME = "report_plan.json"
CONTENT_MARKDOWN_FILENAME = "content.md"
DOCX_FILENAME = "after_class_report.docx"
ATTEMPTS_FILENAME = "attempts.jsonl"
FAILURES_FILENAME = "failures.jsonl"
QUALITY_REPORT_FILENAME = "quality_report.json"
MANIFEST_FILENAME = "manifest.json"
DEFAULT_TARGET_PROFILE = "elementary-ko-a4-adult-report"
UNKNOWN_GRADE_LEVEL = UNKNOWN_GRADE
UNKNOWN_GRADE_LABEL = "초등학생"
AFTER_CLASS_INSTRUCTIONAL_GOAL = (
    "학생이 본문을 읽고 핵심 낱말을 사용해 자기 말로 설명하고 질문에 답하며 "
    "생활·실험·비유로 생각을 넓히는 한국어 이해·표현 능력 강화"
)
VOCABULARY_CONTEXT_LIMIT = 60
EXAMPLE_CONTEXT_LIMIT = 40
FULL_TEXT_CHAR_LIMIT = 60000
TRUNCATED_TEXT_HEAD_CHARS = 40000
TRUNCATED_TEXT_TAIL_CHARS = 10000
AFTER_CLASS_REPORT_MAX_OUTPUT_TOKENS = 32768
KOREAN_PATTERN = re.compile(r"[가-힣]")
MARKDOWN_FENCE = "```"
TOKYO_NIGHT_DOCX_COLORS = {
    "blue": "7AA2F7",
    "cyan": "7DCFFF",
    "green": "9ECE6A",
    "purple": "BB9AF7",
    "yellow": "E0AF68",
}
CARD_WIDTH_DXA = 10200
CARD_COLUMNS_DXA = (2160, 8040)
LEGEND_COLUMNS_DXA = (3400, 3400, 3400)
AFTER_CLASS_NEUTRAL_BORDER = "D8DEE9"
AFTER_CLASS_NEUTRAL_FILL = "F8FAFC"
AFTER_CLASS_SECTION_ACCENT = TOKYO_NIGHT_DOCX_COLORS["blue"]
PRIMARY_WORKSHEET_PATTERN = re.compile(
    r"(?:worksheet|student worksheet|quiz|answer key|test|exam|워크시트|문제지|퀴즈|정답지|시험지|학생용\s*활동지)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PriorityVisualStyle:
    label: str
    heading: str
    accent: str
    fill: str
    purpose: str


PRIORITY_VISUAL_STYLES = {
    "core": PriorityVisualStyle(
        label="핵심",
        heading="핵심 어휘",
        accent=TOKYO_NIGHT_DOCX_COLORS["green"],
        fill="F1F8E8",
        purpose="먼저 붙잡고 말하게 할 낱말",
    ),
    "practice": PriorityVisualStyle(
        label="연습",
        heading="연습 어휘",
        accent=TOKYO_NIGHT_DOCX_COLORS["cyan"],
        fill="ECF9FF",
        purpose="본문 설명을 넓히는 낱말",
    ),
    "enrichment": PriorityVisualStyle(
        label="확장",
        heading="확장 어휘",
        accent=TOKYO_NIGHT_DOCX_COLORS["purple"],
        fill="F6F1FF",
        purpose="어려운 주제를 조심스럽게 만지는 낱말",
    ),
}

AFTER_CLASS_REPORT_SYSTEM_PROMPT = """You write Korean after-class reading guidance for ordinary teachers and parents.
The adults may not be specialists in the class topic. Help them guide young students after class without needing expert knowledge.
The class goal is Korean reading comprehension, vocabulary use, speaking, explanation, and thinking, not specialist subject mastery.
Use only the supplied class printout text and deterministic vocabulary analysis.
If the material is niche or technical, turn it into plain Korean discussion handles, everyday analogies, and safe mini activities.
Write Korean only, except stable technical field names and source_id values.
Do not make the primary output a quiz, answer key, graded test, or standalone student worksheet.
Do include adult-guided conversation prompts and short student activities that adults can say or run immediately.
Choose exactly 15 vocabulary items from the supplied candidate list:
- exactly 5 priority="core" for general comprehension and Korean output
- exactly 5 priority="practice" for discussing the text in Korean
- exactly 5 priority="enrichment" for topic-specific extension without expert depth
Create exactly 5 deep_teaching_cards, one for each core vocabulary item.
Keep every meaning, example, prompt, caution, and watch point concrete, plain, and usable by normal adults.
Avoid English glosses, unnecessary Hanja breakdowns, LaTeX/math markup, and specialist framing.
Return only the requested JSON object."""

AfterClassReportProgressPhase = Literal["start", "analyze", "plan", "write", "complete"]


@dataclass(frozen=True)
class AfterClassReportProgress:
    phase: AfterClassReportProgressPhase
    source_id: str
    detail: str
    output_dir: Path | None = None
    heuristic_analysis_dir: Path | None = None
    accepted: bool | None = None
    selected_vocabulary_count: int = 0
    docx_path: Path | None = None
    failure_reason: str = ""


ProgressCallback = Callable[[AfterClassReportProgress], None]
Priority = Literal["core", "practice", "enrichment"]


class AfterClassReportError(RuntimeError):
    """Raised when an after-class reading report cannot be generated safely."""


class AfterClassVocabularyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_key: str = Field(min_length=1)
    display: str = Field(min_length=1)
    in_text_form: str = Field(min_length=1)
    priority: Priority
    student_friendly_meaning: str = Field(min_length=1)
    source_sentence: str = Field(min_length=1)
    practice_sentence: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    caution: str = Field(min_length=1)

    @field_validator(
        "canonical_key",
        "display",
        "in_text_form",
        "student_friendly_meaning",
        "source_sentence",
        "practice_sentence",
        "why_it_matters",
        "caution",
        mode="before",
    )
    @classmethod
    def normalize_text_field(cls, value: object) -> str:
        return clean_example_text(str(value or ""))

    @model_validator(mode="after")
    def validate_korean_learning_content(self) -> "AfterClassVocabularyItem":
        for label, value in (
            ("student_friendly_meaning", self.student_friendly_meaning),
            ("source_sentence", self.source_sentence),
            ("practice_sentence", self.practice_sentence),
            ("why_it_matters", self.why_it_matters),
            ("caution", self.caution),
        ):
            ensure_korean_text(value, label=label)
        return self


class AfterClassDeepTeachingCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_key: str = Field(min_length=1)
    word_part_note: str = Field(min_length=1)
    similar_but_different: str = Field(min_length=1)
    adult_prompt: str = Field(min_length=1)
    good_response: str = Field(min_length=1)
    watch_for: str = Field(min_length=1)

    @field_validator(
        "canonical_key",
        "word_part_note",
        "similar_but_different",
        "adult_prompt",
        "good_response",
        "watch_for",
        mode="before",
    )
    @classmethod
    def normalize_text_field(cls, value: object) -> str:
        return clean_example_text(str(value or ""))

    @model_validator(mode="after")
    def validate_korean_teaching_content(self) -> "AfterClassDeepTeachingCard":
        for label, value in (
            ("word_part_note", self.word_part_note),
            ("similar_but_different", self.similar_but_different),
            ("adult_prompt", self.adult_prompt),
            ("good_response", self.good_response),
            ("watch_for", self.watch_for),
        ):
            ensure_korean_text(value, label=label)
        return self


class AfterClassReportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    lesson_summary: str = Field(min_length=1)
    core_teaching_points: list[str] = Field(min_length=3)
    vocabulary_overview: list[AfterClassVocabularyItem] = Field(min_length=15, max_length=15)
    deep_teaching_cards: list[AfterClassDeepTeachingCard] = Field(min_length=5, max_length=5)
    discussion_questions: list[str] = Field(min_length=5)
    teacher_activities: list[str] = Field(min_length=3)
    parent_activities: list[str] = Field(min_length=3)
    watch_points: list[str] = Field(min_length=3)
    spaced_review: list[str] = Field(min_length=3)

    @field_validator("source_id", "title", "lesson_summary", mode="before")
    @classmethod
    def normalize_text_field(cls, value: object) -> str:
        return clean_example_text(str(value or ""))

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: object) -> dict[str, str]:
        if not isinstance(value, Mapping):
            return {}
        normalized: dict[str, str] = {}
        for key, item in value.items():
            normalized[str(key)] = clean_example_text(str(item or ""))
        return normalized

    @field_validator(
        "core_teaching_points",
        "discussion_questions",
        "teacher_activities",
        "parent_activities",
        "watch_points",
        "spaced_review",
        mode="before",
    )
    @classmethod
    def normalize_text_list(cls, value: object) -> list[str]:
        return normalize_text_list(value)

    @model_validator(mode="after")
    def validate_report_contract(self) -> "AfterClassReportDocument":
        ensure_korean_text(self.title, label="title")
        ensure_korean_text(self.lesson_summary, label="lesson_summary")
        if PRIMARY_WORKSHEET_PATTERN.search(self.title) or PRIMARY_WORKSHEET_PATTERN.search(self.lesson_summary):
            raise ValueError("after-class report must be adult-facing, not a student worksheet or quiz")

        canonical_keys = [item.canonical_key for item in self.vocabulary_overview]
        if len(set(canonical_keys)) != 15:
            raise ValueError("vocabulary_overview must contain 15 unique canonical_key values")

        priority_counts = Counter(item.priority for item in self.vocabulary_overview)
        expected_counts = {"core": 5, "practice": 5, "enrichment": 5}
        if {priority: priority_counts.get(priority, 0) for priority in expected_counts} != expected_counts:
            raise ValueError("vocabulary_overview must include exactly 5 core, 5 practice, and 5 enrichment items")

        core_keys = {item.canonical_key for item in self.vocabulary_overview if item.priority == "core"}
        deep_keys = {card.canonical_key for card in self.deep_teaching_cards}
        if len(deep_keys) != 5 or deep_keys != core_keys:
            raise ValueError("deep_teaching_cards must contain exactly one card for each core vocabulary item")

        adult_sections = {
            "core_teaching_points": self.core_teaching_points,
            "discussion_questions": self.discussion_questions,
            "teacher_activities": self.teacher_activities,
            "parent_activities": self.parent_activities,
            "watch_points": self.watch_points,
            "spaced_review": self.spaced_review,
        }
        for section_name, values in adult_sections.items():
            for index, value in enumerate(values, start=1):
                ensure_korean_text(value, label=f"{section_name}[{index}]")
        return self


@dataclass(frozen=True)
class HeuristicAnalysisResult:
    source_id: str
    heuristic_analysis_dir: Path
    extracted_pages: int
    sentences: int
    token_occurrences: int
    vocabulary_terms: int
    surface_words: int


@dataclass(frozen=True)
class AfterClassReportContext:
    source_id: str
    source_path: Path
    source_filename: str
    grade_level: str
    grade_label: str
    prompt_target_profile: str
    output_dir: Path
    heuristic_analysis_dir: Path
    summary: Mapping[str, Any]
    sentence_count: int
    normalized_printout_text: str
    printout_text_was_truncated: bool
    vocabulary_candidates: tuple[Mapping[str, object], ...]
    top_vocabulary_candidates: tuple[Mapping[str, object], ...]
    top_example_sentences: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class AfterClassReportPrompt:
    messages: tuple[ChatMessage, ...]
    user_prompt: str
    prompt_messages: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class AfterClassReportOutputs:
    source_id: str
    output_dir: Path
    heuristic_analysis_dir: Path
    accepted: bool
    failure_reason: str
    selected_vocabulary_count: int
    docx_path: Path | None
    attempt_rows: list[dict[str, object]]
    failure_rows: list[dict[str, object]]


def validate_after_class_report_input(printout_path: Path) -> str:
    path = Path(printout_path)
    if not path.exists():
        raise AfterClassReportError(f"after-class report input does not exist: {path}")
    if path.is_dir():
        raise AfterClassReportError(f"after-class report input must be one exact .docx or .pdf file, not a directory: {path}")
    if path.name.startswith("~$"):
        raise AfterClassReportError(f"after-class report input is a temporary Office file: {path}")
    if path.suffix.lower() not in SUPPORTED_USAGE_SIGNAL_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_USAGE_SIGNAL_EXTENSIONS))
        raise AfterClassReportError(f"after-class report input must be one exact .docx or .pdf file ({supported}): {path}")
    if not is_managed_archive_name(path.stem):
        raise AfterClassReportError(
            f"after-class report input must use the managed class-session archive filename pattern: {path.name}. "
            "Hint: run --rename-class-session-archive first, then pass the renamed file."
        )
    return path.stem


def generate_after_class_reading_report(
    printout_path: Path,
    *,
    output_root: Path = DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT,
    client: SupportsCompletion,
    model: str,
    target_profile: str = DEFAULT_TARGET_PROFILE,
    font_path: Path | None = None,
    include_punctuation: bool = False,
    include_numbers: bool = False,
    progress: ProgressCallback | None = None,
) -> AfterClassReportOutputs:
    source_path = Path(printout_path)
    source_id = validate_after_class_report_input(source_path)
    output_dir = Path(output_root) / source_id
    heuristic_analysis_dir = output_dir / HEURISTIC_ANALYSIS_DIRNAME
    prevent_input_overwrite(source_path, output_dir)
    prevent_reserved_maintenance_output(output_dir)

    _report(
        progress,
        phase="start",
        source_id=source_id,
        detail=f"preparing after-class report output at {output_dir}",
        output_dir=output_dir,
        heuristic_analysis_dir=heuristic_analysis_dir,
    )
    prepare_output_dir(output_dir)
    attempts_path = output_dir / ATTEMPTS_FILENAME
    failures_path = output_dir / FAILURES_FILENAME
    initialize_jsonl_file(attempts_path)
    initialize_jsonl_file(failures_path)

    context: AfterClassReportContext | None = None
    prompt: AfterClassReportPrompt | None = None
    result: CompletionResult | None = None
    document: AfterClassReportDocument | None = None
    resolved_font_path: Path | None = None
    resolved_font_name = ""
    audit_rows_written = False

    try:
        _report(
            progress,
            phase="analyze",
            source_id=source_id,
            detail=f"building isolated 00-09 heuristic analysis at {heuristic_analysis_dir}",
            output_dir=output_dir,
            heuristic_analysis_dir=heuristic_analysis_dir,
        )
        build_isolated_heuristic_analysis(
            source_path,
            heuristic_analysis_dir=heuristic_analysis_dir,
            include_punctuation=include_punctuation,
            include_numbers=include_numbers,
        )
        context = build_after_class_report_context(
            source_path,
            output_dir=output_dir,
            heuristic_analysis_dir=heuristic_analysis_dir,
        )
        prompt = build_after_class_report_prompt(context)

        _report(
            progress,
            phase="plan",
            source_id=source_id,
            detail="generating adult-facing Korean report plan with Ollama",
            output_dir=output_dir,
            heuristic_analysis_dir=heuristic_analysis_dir,
        )
        result = client.complete(
            list(prompt.messages),
            temperature=0.2,
            max_output_tokens=after_class_report_max_output_tokens(context),
            json_mode=True,
        )
        document = parse_and_validate_after_class_report_response(result, context=context)
        resolved_font_path, resolved_font_name = resolve_after_class_report_font(font_path)
        _report(
            progress,
            phase="write",
            source_id=source_id,
            detail="writing after-class report DOCX bundle",
            output_dir=output_dir,
            heuristic_analysis_dir=heuristic_analysis_dir,
            selected_vocabulary_count=len(document.vocabulary_overview),
            docx_path=output_dir / DOCX_FILENAME,
        )
        write_after_class_report_artifacts(
            output_dir,
            context=context,
            prompt=prompt,
            document=document,
            model=model,
            target_profile=target_profile,
            font_path=resolved_font_path,
            font_name=resolved_font_name,
        )
        attempt = after_class_attempt_row(
            source_id=source_id,
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
        AfterClassReportError,
        LLMConfigurationError,
        LLMRequestError,
        LLMResponseError,
        PrintableMaterialError,
        ValidationError,
        OSError,
        ValueError,
    ) as exc:
        failure_reason = str(exc)
        attempt = after_class_attempt_row(
            source_id=source_id,
            context=context,
            prompt=prompt,
            model=model,
            response=result.content if result is not None else "",
            document=None,
            accepted=False,
            failure_reason=failure_reason,
        )
        failure_rows = [attempt]
        append_jsonl(attempts_path, [attempt])
        write_jsonl(failures_path, failure_rows)
        audit_rows_written = True
        _report(
            progress,
            phase="write",
            source_id=source_id,
            detail="writing failed after-class report audit bundle",
            output_dir=output_dir,
            heuristic_analysis_dir=heuristic_analysis_dir,
            accepted=False,
            failure_reason=failure_reason,
        )
        write_failed_after_class_report_artifacts(
            output_dir,
            source_id=source_id,
            source_path=source_path,
            heuristic_analysis_dir=heuristic_analysis_dir,
            context=context,
            prompt=prompt,
            model=model,
            target_profile=target_profile,
            failure_reason=failure_reason,
        )

    attempt_rows = [attempt]
    if not audit_rows_written:
        append_jsonl(attempts_path, attempt_rows)
        write_jsonl(failures_path, failure_rows)
    accepted = not failure_rows
    failure_reason = str(attempt.get("failure_reason") or "")
    selected_vocabulary_count = len(document.vocabulary_overview) if accepted and document else 0
    docx_path = output_dir / DOCX_FILENAME if accepted else None
    _report(
        progress,
        phase="complete",
        source_id=source_id,
        detail="after-class report DOCX written" if accepted else f"after-class report failed: {failure_reason}",
        output_dir=output_dir,
        heuristic_analysis_dir=heuristic_analysis_dir,
        accepted=accepted,
        selected_vocabulary_count=selected_vocabulary_count,
        docx_path=docx_path,
        failure_reason=failure_reason,
    )
    return AfterClassReportOutputs(
        source_id=source_id,
        output_dir=output_dir,
        heuristic_analysis_dir=heuristic_analysis_dir,
        accepted=accepted,
        failure_reason=failure_reason,
        selected_vocabulary_count=selected_vocabulary_count,
        docx_path=docx_path,
        attempt_rows=attempt_rows,
        failure_rows=failure_rows,
    )


def build_isolated_heuristic_analysis(
    printout_path: Path,
    *,
    heuristic_analysis_dir: Path,
    include_punctuation: bool = False,
    include_numbers: bool = False,
) -> HeuristicAnalysisResult:
    source_path = Path(printout_path)
    source_id = validate_after_class_report_input(source_path)
    kiwi = Kiwi()
    analyzer = KoreanAnalyzer(
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
        kiwi=kiwi,
    )
    pages = list(read_usage_signal_document(source_path, source_id))
    if not pages:
        raise AfterClassReportError(f"after-class report input did not yield any readable text: {source_path}")
    analysis = analyzer.analyze_pages(pages)
    write_outputs(
        heuristic_analysis_dir,
        pages=pages,
        sentences=analysis["sentences"],
        tokens=analysis["tokens"],
        vocabulary=analysis["vocabulary"],
        surface_words=analysis["surface_words"],
        document_totals=analysis["document_totals"],
        documents=[Path(source_id)],
        kiwi=kiwi,
    )
    return HeuristicAnalysisResult(
        source_id=source_id,
        heuristic_analysis_dir=heuristic_analysis_dir,
        extracted_pages=len(pages),
        sentences=len(analysis["sentences"]),
        token_occurrences=len(analysis["tokens"]),
        vocabulary_terms=len(analysis["vocabulary"]),
        surface_words=len(analysis["surface_words"]),
    )


def build_after_class_report_context(
    printout_path: Path,
    *,
    output_dir: Path,
    heuristic_analysis_dir: Path,
) -> AfterClassReportContext:
    source_path = Path(printout_path)
    source_id = validate_after_class_report_input(source_path)
    required_artifacts = (
        ArtifactId.SUMMARY,
        ArtifactId.EXTRACTED_PAGES,
        ArtifactId.SENTENCES,
        ArtifactId.CANDIDATE_TERMS,
        ArtifactId.DICTIONARY_CANDIDATES,
        ArtifactId.EXAMPLE_SENTENCES,
    )
    for artifact_id in required_artifacts:
        path = artifact_path(heuristic_analysis_dir, artifact_id)
        if not path.is_file():
            raise AfterClassReportError(
                f"Missing isolated heuristic artifact {artifact_filename(artifact_id)} in {heuristic_analysis_dir}."
            )

    summary = load_json_object(artifact_path(heuristic_analysis_dir, ArtifactId.SUMMARY))
    page_rows = read_rows(artifact_path(heuristic_analysis_dir, ArtifactId.EXTRACTED_PAGES))
    sentence_rows = read_rows(artifact_path(heuristic_analysis_dir, ArtifactId.SENTENCES))
    candidate_rows = read_rows(artifact_path(heuristic_analysis_dir, ArtifactId.CANDIDATE_TERMS))
    dictionary_rows = read_jsonl(artifact_path(heuristic_analysis_dir, ArtifactId.DICTIONARY_CANDIDATES))
    example_rows = read_rows(artifact_path(heuristic_analysis_dir, ArtifactId.EXAMPLE_SENTENCES))
    full_text = "\n\n".join(clean_example_text(row.get("text", "")) for row in page_rows if row.get("text", "").strip())
    normalized_text, truncated = bounded_printout_text(full_text)
    vocabulary_candidates = merge_candidate_rows(candidate_rows, dictionary_rows)
    top_vocabulary = tuple(sorted(vocabulary_candidates, key=vocabulary_candidate_sort_key)[:VOCABULARY_CONTEXT_LIMIT])
    top_examples = tuple(sorted(example_rows, key=example_sentence_sort_key)[:EXAMPLE_CONTEXT_LIMIT])
    grade_level = after_class_grade_level(source_id)
    return AfterClassReportContext(
        source_id=source_id,
        source_path=source_path,
        source_filename=source_path.name,
        grade_level=grade_level,
        grade_label=after_class_grade_label(grade_level),
        prompt_target_profile=after_class_prompt_target_profile(grade_level),
        output_dir=output_dir,
        heuristic_analysis_dir=heuristic_analysis_dir,
        summary=summary,
        sentence_count=len(sentence_rows),
        normalized_printout_text=normalized_text,
        printout_text_was_truncated=truncated,
        vocabulary_candidates=tuple(vocabulary_candidates),
        top_vocabulary_candidates=top_vocabulary,
        top_example_sentences=top_examples,
    )


def build_after_class_report_prompt(context: AfterClassReportContext) -> AfterClassReportPrompt:
    required_shape = {
        "source_id": context.source_id,
        "title": "수업 후 읽기 지도 보고서",
        "metadata": {
            "source_filename": context.source_filename,
            "audience": "교사와 학부모",
            "language": "ko",
            "grade_level": context.grade_level,
            "grade_label": context.grade_label,
            "target_profile": context.prompt_target_profile,
            "instructional_goal": AFTER_CLASS_INSTRUCTIONAL_GOAL,
        },
        "lesson_summary": "...",
        "core_teaching_points": ["...", "...", "..."],
        "vocabulary_overview": [
            {
                "canonical_key": "...",
                "display": "...",
                "in_text_form": "...",
                "priority": "core",
                "student_friendly_meaning": "...",
                "source_sentence": "...",
                "practice_sentence": "...",
                "why_it_matters": "...",
                "caution": "...",
            }
        ],
        "deep_teaching_cards": [
            {
                "canonical_key": "...",
                "word_part_note": "...",
                "similar_but_different": "...",
                "adult_prompt": "...",
                "good_response": "...",
                "watch_for": "...",
            }
        ],
        "discussion_questions": ["...", "...", "...", "...", "..."],
        "teacher_activities": ["...", "...", "..."],
        "parent_activities": ["...", "...", "..."],
        "watch_points": ["...", "...", "..."],
        "spaced_review": ["...", "...", "..."],
    }
    prompt_packet = {
        "source_id": context.source_id,
        "source_filename": context.source_filename,
        "grade_level": context.grade_level,
        "grade_label": context.grade_label,
        "instructional_goal": AFTER_CLASS_INSTRUCTIONAL_GOAL,
        "heuristic_summary": context.summary,
        "sentence_count": context.sentence_count,
        "printout_text_was_truncated": context.printout_text_was_truncated,
        "normalized_printout_text": context.normalized_printout_text,
        "top_vocabulary_candidates": [prompt_candidate_row(row) for row in context.top_vocabulary_candidates],
        "top_example_sentences": [prompt_example_row(row) for row in context.top_example_sentences],
        "report_contract": {
            "language": "Korean only",
            "audience": (
                f"ordinary teachers and parents guiding {context.grade_label} students after class; "
                "assume adults are not specialists in the source topic"
            ),
            "primary_goal": "Korean reading comprehension, vocabulary use, speaking, explanation, and thinking",
            "allowed_guidance": [
                "adult-guided conversation prompts",
                "ready-to-say questions for students",
                "short student activities adults can run after class",
                "simple everyday analogies grounded in the source text",
            ],
            "not_allowed": [
                "quiz as the primary output",
                "answer key",
                "graded test",
                "standalone student worksheet",
                "invented vocabulary not present in candidate list",
                "specialist subject-mastery framing",
                "English glosses such as How vs Why",
                "unnecessary Hanja character breakdowns",
                "LaTeX or math markup",
            ],
            "vocabulary_count": 15,
            "priority_split": {"core": 5, "practice": 5, "enrichment": 5},
            "priority_purpose": {
                "core": "general comprehension and Korean output",
                "practice": "discussing the text in Korean",
                "enrichment": "topic-specific extension without expert depth",
            },
            "guidance_flow": "본문에서 찾기 → 자기 말로 말하기 → 생활/실험/비유로 연결하기",
            "deep_teaching_cards": "exactly 5 and canonical_key must match the 5 core vocabulary items",
        },
        "required_json_shape": required_shape,
    }
    user_prompt = "\n".join(
        [
            "After-class reading report request:",
            f"Build a structured Korean after-class guidance plan for ordinary adults helping {context.grade_label} students.",
            "Optimize for Korean comprehension and expression, not specialist subject mastery.",
            "Select vocabulary only from top_vocabulary_candidates. Use canonical_key values exactly.",
            "Prefer useful Korean words for meaning-making and student output over niche proper nouns, specialist terms, and layout debris.",
            "Use source_sentence values grounded in the supplied printout or high-signal example sentences.",
            "When the source is technical, simplify it into plain Korean handles adults can use without expert knowledge.",
            "Make prompts follow: 본문에서 찾기 → 자기 말로 말하기 → 생활/실험/비유로 연결하기.",
            "Keep teacher guidance first; include the parent companion section near the end.",
            "Do not use English glosses, unnecessary Hanja breakdowns, LaTeX/math markup, or subject-mastery lecture framing.",
            "Return JSON exactly in the required shape with all required keys.",
            "",
            json.dumps(prompt_packet, ensure_ascii=False, indent=2),
        ]
    )
    messages = (
        ChatMessage(role="system", content=AFTER_CLASS_REPORT_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    )
    return AfterClassReportPrompt(
        messages=messages,
        user_prompt=user_prompt,
        prompt_messages=tuple({"role": message.role, "content": message.content} for message in messages),
    )


def parse_and_validate_after_class_report_response(
    result: CompletionResult,
    *,
    context: AfterClassReportContext,
) -> AfterClassReportDocument:
    if MARKDOWN_FENCE in result.content:
        raise AfterClassReportError("After-class report response included a markdown fence.")
    parsed = parse_json_object(result.content)
    try:
        document = AfterClassReportDocument.model_validate(parsed)
    except ValidationError as exc:
        raise AfterClassReportError(f"After-class report response failed schema validation: {exc}") from exc
    if document.source_id != context.source_id:
        raise AfterClassReportError(f"After-class report omitted requested source_id '{context.source_id}'.")
    candidate_keys = {str(row.get("canonical_key") or "") for row in context.vocabulary_candidates}
    unknown_keys = [
        item.canonical_key
        for item in document.vocabulary_overview
        if candidate_keys and item.canonical_key not in candidate_keys
    ]
    if unknown_keys:
        formatted = ", ".join(unknown_keys[:5])
        raise AfterClassReportError(f"After-class report selected vocabulary outside the isolated analysis: {formatted}")
    return document


def write_after_class_report_artifacts(
    output_dir: Path,
    *,
    context: AfterClassReportContext,
    prompt: AfterClassReportPrompt,
    document: AfterClassReportDocument,
    model: str,
    target_profile: str,
    font_path: Path,
    font_name: str,
) -> None:
    (output_dir / REPORT_PLAN_FILENAME).write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / CONTENT_MARKDOWN_FILENAME).write_text(
        render_after_class_report_markdown(document),
        encoding="utf-8",
    )
    render_after_class_report_docx(
        output_dir / DOCX_FILENAME,
        document=document,
        font_name=font_name,
    )
    write_quality_and_manifest(
        output_dir,
        source_id=context.source_id,
        source_path=context.source_path,
        heuristic_analysis_dir=context.heuristic_analysis_dir,
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


def write_failed_after_class_report_artifacts(
    output_dir: Path,
    *,
    source_id: str,
    source_path: Path,
    heuristic_analysis_dir: Path,
    context: AfterClassReportContext | None,
    prompt: AfterClassReportPrompt | None,
    model: str,
    target_profile: str,
    failure_reason: str,
) -> None:
    write_quality_and_manifest(
        output_dir,
        source_id=source_id,
        source_path=source_path,
        heuristic_analysis_dir=heuristic_analysis_dir,
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


def render_after_class_report_markdown(document: AfterClassReportDocument) -> str:
    lines = [f"# {document.title}", "", f"- source_id: {document.source_id}"]
    for key, value in document.metadata.items():
        if value:
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## 수업 요약", "", document.lesson_summary])
    append_bullets(lines, "핵심 지도 포인트", document.core_teaching_points)
    lines.extend(["", "## 어휘 지도 개요", ""])
    for item in document.vocabulary_overview:
        lines.extend(
            [
                f"### {item.display} ({item.priority})",
                "",
                f"- 본문 표현: {item.in_text_form}",
                f"- 뜻: {item.student_friendly_meaning}",
                f"- 본문 문장: {item.source_sentence}",
                f"- 연습 문장: {item.practice_sentence}",
                f"- 중요한 이유: {item.why_it_matters}",
                f"- 주의점: {item.caution}",
                "",
            ]
        )
    lines.extend(["## 깊이 지도 카드", ""])
    for card in document.deep_teaching_cards:
        lines.extend(
            [
                f"### {card.canonical_key}",
                "",
                f"- 낱말 살피기: {card.word_part_note}",
                f"- 비슷하지만 다른 점: {card.similar_but_different}",
                f"- 어른 질문: {card.adult_prompt}",
                f"- 좋은 반응: {card.good_response}",
                f"- 살펴볼 점: {card.watch_for}",
                "",
            ]
        )
    append_bullets(lines, "토의 질문", document.discussion_questions)
    append_bullets(lines, "교사용 후속 활동", document.teacher_activities)
    append_bullets(lines, "학부모 동반 활동", document.parent_activities)
    append_bullets(lines, "살펴볼 점", document.watch_points)
    append_bullets(lines, "간격 복습", document.spaced_review)
    return "\n".join(lines).strip() + "\n"


def render_after_class_report_docx(
    path: Path,
    *,
    document: AfterClassReportDocument,
    font_name: str,
) -> None:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(16)
    section.bottom_margin = Mm(16)
    section.left_margin = Mm(15)
    section.right_margin = Mm(15)
    configure_docx_styles(doc, font_name=font_name, body_font_size=10)
    configure_table_styles(doc, font_name=font_name)

    add_docx_paragraph(doc, document.title, font_name=font_name, size=18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_docx_paragraph(doc, "교사용 지도 안내 및 학부모 동반 활동", font_name=font_name, size=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_docx_paragraph(doc, f"자료 ID: {document.source_id}", font_name=font_name, size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_guidance_strip(doc, font_name=font_name)

    add_docx_heading(doc, "수업 요약", font_name=font_name)
    add_docx_paragraph(doc, document.lesson_summary, font_name=font_name, size=10)
    add_docx_bullets(doc, "핵심 지도 포인트", document.core_teaching_points, font_name=font_name)
    add_vocabulary_guide(doc, document.vocabulary_overview, font_name=font_name)

    add_docx_heading(doc, "깊이 지도 카드", font_name=font_name)
    for card in document.deep_teaching_cards:
        add_deep_teaching_card(doc, card, font_name=font_name)

    doc.add_section(WD_SECTION_START.CONTINUOUS)
    add_docx_bullets(doc, "토의 질문", document.discussion_questions, font_name=font_name)
    add_docx_bullets(doc, "교사용 후속 활동", document.teacher_activities, font_name=font_name)
    add_docx_bullets(doc, "학부모 동반 활동", document.parent_activities, font_name=font_name)
    add_docx_bullets(doc, "살펴볼 점", document.watch_points, font_name=font_name)
    add_docx_bullets(doc, "간격 복습", document.spaced_review, font_name=font_name)
    doc.save(path)


def add_guidance_strip(doc: Document, *, font_name: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    configure_card_table(table, column_widths=(CARD_WIDTH_DXA,), border_color=AFTER_CLASS_SECTION_ACCENT)
    cell = table.rows[0].cells[0]
    set_cell_shading(cell, "EDF7F9")
    set_cell_margins(cell, top=120, bottom=120, start=180, end=180)
    paragraph = cell.paragraphs[0]
    set_paragraph_spacing(paragraph, after=0)
    run = paragraph.add_run("본문에서 찾기 → 자기 말로 말하기 → 생활/실험/비유로 연결하기")
    run.bold = True
    run.font.name = font_name
    run.font.size = Pt(10)
    run.font.color.rgb = rgb_color(AFTER_CLASS_SECTION_ACCENT)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
    add_spacer_paragraph(doc, before=0, after=6)


def add_vocabulary_guide(
    doc: Document,
    vocabulary: Sequence[AfterClassVocabularyItem],
    *,
    font_name: str,
) -> None:
    add_docx_heading(doc, "어휘 지도 개요", font_name=font_name)
    add_priority_legend(doc, font_name=font_name)
    grouped: dict[Priority, list[AfterClassVocabularyItem]] = {
        "core": [],
        "practice": [],
        "enrichment": [],
    }
    for item in vocabulary:
        grouped[item.priority].append(item)
    for priority in ("core", "practice", "enrichment"):
        style = PRIORITY_VISUAL_STYLES[priority]
        add_priority_group_heading(
            doc,
            f"{style.heading} {len(grouped[priority])}개",
            style=style,
            font_name=font_name,
        )
        for item in grouped[priority]:
            add_vocabulary_card(doc, item, style=style, font_name=font_name)


def add_priority_legend(doc: Document, *, font_name: str) -> None:
    add_docx_paragraph(doc, "어휘 중요도 안내", font_name=font_name, size=10, bold=True)
    table = doc.add_table(rows=1, cols=3)
    configure_card_table(table, column_widths=LEGEND_COLUMNS_DXA, border_color=AFTER_CLASS_NEUTRAL_BORDER)
    for index, priority in enumerate(("core", "practice", "enrichment")):
        style = PRIORITY_VISUAL_STYLES[priority]
        cell = table.rows[0].cells[index]
        set_cell_shading(cell, style.fill)
        set_cell_margins(cell, top=110, bottom=110, start=130, end=130)
        add_cell_heading(cell, f"{style.label} · {style.heading}", font_name=font_name, color=style.accent, size=9.5)
        add_cell_text_line(cell, style.purpose, font_name=font_name, size=8.5)
    add_spacer_paragraph(doc, before=0, after=4)


def add_priority_group_heading(
    doc: Document,
    text: str,
    *,
    style: PriorityVisualStyle,
    font_name: str,
) -> None:
    table = doc.add_table(rows=1, cols=1)
    configure_card_table(table, column_widths=(CARD_WIDTH_DXA,), border_color=style.accent)
    cell = table.rows[0].cells[0]
    set_cell_shading(cell, style.fill)
    set_cell_margins(cell, top=95, bottom=95, start=150, end=150)
    paragraph = cell.paragraphs[0]
    set_paragraph_spacing(paragraph, after=0)
    run = paragraph.add_run(f"{text} · {style.purpose}")
    run.bold = True
    run.font.name = font_name
    run.font.size = Pt(10)
    run.font.color.rgb = rgb_color(style.accent)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def add_vocabulary_card(
    doc: Document,
    item: AfterClassVocabularyItem,
    *,
    style: PriorityVisualStyle,
    font_name: str,
) -> None:
    table = doc.add_table(rows=1, cols=2)
    configure_card_table(table, column_widths=CARD_COLUMNS_DXA, border_color=style.accent)
    left_cell, right_cell = table.rows[0].cells
    set_cell_shading(left_cell, style.fill)
    set_cell_margins(left_cell, top=125, bottom=125, start=130, end=130)
    set_cell_margins(right_cell, top=125, bottom=125, start=150, end=150)
    add_cell_heading(left_cell, style.label, font_name=font_name, color=style.accent, size=9)
    add_cell_heading(left_cell, item.display, font_name=font_name, color="111827", size=12)
    add_cell_text_line(left_cell, f"본문: {item.in_text_form}", font_name=font_name, size=9)
    add_cell_text_line(left_cell, style.purpose, font_name=font_name, size=8.5)
    add_cell_labeled_line(right_cell, "뜻", item.student_friendly_meaning, font_name=font_name, accent=style.accent)
    add_cell_labeled_line(right_cell, "본문", item.source_sentence, font_name=font_name, accent=style.accent)
    add_cell_labeled_line(right_cell, "연습", item.practice_sentence, font_name=font_name, accent=style.accent)
    add_cell_labeled_line(right_cell, "왜 중요한가", item.why_it_matters, font_name=font_name, accent=style.accent)
    add_cell_labeled_line(
        right_cell,
        "주의",
        item.caution,
        font_name=font_name,
        accent=style.accent,
        label_color=TOKYO_NIGHT_DOCX_COLORS["yellow"],
    )
    add_spacer_paragraph(doc, before=0, after=3)


def add_deep_teaching_card(
    doc: Document,
    card: AfterClassDeepTeachingCard,
    *,
    font_name: str,
) -> None:
    table = doc.add_table(rows=1, cols=1)
    configure_card_table(table, column_widths=(CARD_WIDTH_DXA,), border_color=AFTER_CLASS_NEUTRAL_BORDER)
    cell = table.rows[0].cells[0]
    set_cell_shading(cell, AFTER_CLASS_NEUTRAL_FILL)
    set_cell_margins(cell, top=130, bottom=130, start=160, end=160)
    add_cell_heading(cell, card.canonical_key, font_name=font_name, color=AFTER_CLASS_SECTION_ACCENT, size=11)
    add_cell_labeled_line(cell, "낱말 살피기", card.word_part_note, font_name=font_name, accent=AFTER_CLASS_SECTION_ACCENT)
    add_cell_labeled_line(cell, "비슷하지만 다른 점", card.similar_but_different, font_name=font_name, accent=AFTER_CLASS_SECTION_ACCENT)
    add_cell_labeled_line(cell, "질문", card.adult_prompt, font_name=font_name, accent=AFTER_CLASS_SECTION_ACCENT)
    add_cell_labeled_line(cell, "좋은 반응", card.good_response, font_name=font_name, accent=AFTER_CLASS_SECTION_ACCENT)
    add_cell_labeled_line(cell, "살펴볼 점", card.watch_for, font_name=font_name, accent=AFTER_CLASS_SECTION_ACCENT)
    add_spacer_paragraph(doc, before=0, after=3)


def configure_table_styles(doc: Document, *, font_name: str) -> None:
    for style_name in ("Table Grid",):
        style = doc.styles[style_name]
        style.font.name = font_name
        style.font.size = Pt(8)
        set_docx_east_asia_font(style, font_name)


def configure_card_table(
    table: Any,
    *,
    column_widths: Sequence[int],
    border_color: str,
) -> None:
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_width(table, sum(column_widths))
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            set_cell_width(cell, column_widths[index])
            set_cell_border(cell, color=border_color)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP


def set_table_width(table: Any, width_dxa: int) -> None:
    tbl_pr = table._tbl.tblPr
    tbl_width = tbl_pr.find(qn("w:tblW"))
    if tbl_width is None:
        tbl_width = OxmlElement("w:tblW")
        tbl_pr.append(tbl_width)
    tbl_width.set(qn("w:type"), "dxa")
    tbl_width.set(qn("w:w"), str(width_dxa))


def set_cell_width(cell: Any, width_dxa: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_width = tc_pr.find(qn("w:tcW"))
    if tc_width is None:
        tc_width = OxmlElement("w:tcW")
        tc_pr.append(tc_width)
    tc_width.set(qn("w:type"), "dxa")
    tc_width.set(qn("w:w"), str(width_dxa))


def set_cell_shading(cell: Any, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def set_cell_border(cell: Any, *, color: str, size: int = 8) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_cell_margins(
    cell: Any,
    *,
    top: int,
    bottom: int,
    start: int,
    end: int,
) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = tc_pr.find(qn("w:tcMar"))
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for edge, value in {"top": top, "bottom": bottom, "start": start, "end": end}.items():
        element = margins.find(qn(f"w:{edge}"))
        if element is None:
            element = OxmlElement(f"w:{edge}")
            margins.append(element)
        element.set(qn("w:w"), str(value))
        element.set(qn("w:type"), "dxa")


def add_cell_heading(
    cell: Any,
    text: str,
    *,
    font_name: str,
    color: str,
    size: float,
) -> None:
    paragraph = next_cell_paragraph(cell)
    set_paragraph_spacing(paragraph, after=2)
    run = paragraph.add_run(text)
    format_run(run, font_name=font_name, size=size, bold=True, color=color)


def add_cell_text_line(cell: Any, text: str, *, font_name: str, size: float) -> None:
    paragraph = next_cell_paragraph(cell)
    set_paragraph_spacing(paragraph, after=2)
    run = paragraph.add_run(text)
    format_run(run, font_name=font_name, size=size, color="374151")


def add_cell_labeled_line(
    cell: Any,
    label: str,
    text: str,
    *,
    font_name: str,
    accent: str,
    label_color: str | None = None,
) -> None:
    paragraph = next_cell_paragraph(cell)
    set_paragraph_spacing(paragraph, after=2)
    label_run = paragraph.add_run(f"{label}: ")
    format_run(label_run, font_name=font_name, size=9.5, bold=True, color=label_color or accent)
    value_run = paragraph.add_run(text)
    format_run(value_run, font_name=font_name, size=9.5, color="111827")


def next_cell_paragraph(cell: Any) -> Any:
    first = cell.paragraphs[0]
    if len(cell.paragraphs) == 1 and not first.text.strip():
        return first
    return cell.add_paragraph()


def add_spacer_paragraph(doc: Document, *, before: float, after: float) -> None:
    paragraph = doc.add_paragraph()
    set_paragraph_spacing(paragraph, before=before, after=after)


def set_paragraph_spacing(paragraph: Any, *, before: float = 0, after: float = 0) -> None:
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = 1.08


def format_run(
    run: Any,
    *,
    font_name: str,
    size: float,
    bold: bool = False,
    color: str | None = None,
) -> None:
    run.bold = bold
    run.font.name = font_name
    run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = rgb_color(color)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def rgb_color(hex_color: str) -> RGBColor:
    color = hex_color.strip().lstrip("#")
    return RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def set_cell_text(cell: Any, text: str, *, font_name: str, bold: bool = False) -> None:
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.name = font_name
    run.font.size = Pt(8)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def add_docx_heading(doc: Document, text: str, *, font_name: str) -> None:
    add_docx_paragraph(doc, text, font_name=font_name, size=13, bold=True)


def add_docx_bullets(doc: Document, heading: str, values: Sequence[str], *, font_name: str) -> None:
    add_docx_heading(doc, heading, font_name=font_name)
    for value in values:
        add_docx_bullet(doc, value, font_name=font_name)


def add_docx_bullet(doc: Document, value: str, *, font_name: str) -> None:
    paragraph = doc.add_paragraph(style="List Bullet")
    run = paragraph.add_run(value)
    run.font.name = font_name
    run.font.size = Pt(10)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def write_quality_and_manifest(
    output_dir: Path,
    *,
    source_id: str,
    source_path: Path,
    heuristic_analysis_dir: Path,
    context: AfterClassReportContext | None,
    prompt: AfterClassReportPrompt | None,
    document: AfterClassReportDocument | None,
    model: str,
    target_profile: str,
    accepted: bool,
    failure_reason: str,
    font_path: Path | None,
    font_name: str,
) -> None:
    quality = build_quality_report(
        source_id=source_id,
        heuristic_analysis_dir=heuristic_analysis_dir,
        context=context,
        document=document,
        model=model,
        target_profile=target_profile,
        accepted=accepted,
        failure_reason=failure_reason,
        font_path=font_path,
        font_name=font_name,
    )
    manifest = build_after_class_manifest(
        source_id=source_id,
        source_path=source_path,
        heuristic_analysis_dir=heuristic_analysis_dir,
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
    (output_dir / QUALITY_REPORT_FILENAME).write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_quality_report(
    *,
    source_id: str,
    heuristic_analysis_dir: Path,
    context: AfterClassReportContext | None,
    document: AfterClassReportDocument | None,
    model: str,
    target_profile: str,
    accepted: bool,
    failure_reason: str,
    font_path: Path | None,
    font_name: str,
) -> dict[str, object]:
    priority_counts = Counter(item.priority for item in document.vocabulary_overview) if document else Counter()
    return {
        "schema_version": AFTER_CLASS_REPORT_SCHEMA_VERSION,
        "source_id": source_id,
        "target_profile": target_profile,
        "model": model,
        "status": "written" if accepted else "failed",
        "failure_reason": failure_reason,
        "heuristic_analysis_dir": str(heuristic_analysis_dir),
        "candidate_context_count": len(context.top_vocabulary_candidates) if context else 0,
        "example_context_count": len(context.top_example_sentences) if context else 0,
        "selected_vocabulary_count": len(document.vocabulary_overview) if document else 0,
        "core_vocabulary_count": priority_counts.get("core", 0),
        "practice_vocabulary_count": priority_counts.get("practice", 0),
        "enrichment_vocabulary_count": priority_counts.get("enrichment", 0),
        "deep_teaching_card_count": len(document.deep_teaching_cards) if document else 0,
        "font_path": str(font_path) if font_path is not None else "",
        "font_name": font_name,
        "output_formats": ["docx"] if accepted else [],
        "requires_human_revision": accepted,
        "revision_target": DOCX_FILENAME if accepted else "",
    }


def build_after_class_manifest(
    *,
    source_id: str,
    source_path: Path,
    heuristic_analysis_dir: Path,
    context: AfterClassReportContext | None,
    prompt: AfterClassReportPrompt | None,
    document: AfterClassReportDocument | None,
    model: str,
    target_profile: str,
    accepted: bool,
    failure_reason: str,
    font_path: Path | None,
    font_name: str,
) -> dict[str, object]:
    files: dict[str, object] = {
        "heuristic_analysis": f"{HEURISTIC_ANALYSIS_DIRNAME}/",
        "attempts_jsonl": ATTEMPTS_FILENAME,
        "failures_jsonl": FAILURES_FILENAME,
        "quality_report_json": QUALITY_REPORT_FILENAME,
        "manifest_json": MANIFEST_FILENAME,
    }
    if accepted:
        files.update(
            {
                "report_plan_json": REPORT_PLAN_FILENAME,
                "content_markdown": CONTENT_MARKDOWN_FILENAME,
                "docx": DOCX_FILENAME,
            }
        )
    return {
        "schema_version": AFTER_CLASS_REPORT_SCHEMA_VERSION,
        "source": {
            "id": source_id,
            "filename": source_path.name,
            "path": str(source_path),
        },
        "after_class_report": {
            "target_profile": target_profile,
            "model": model,
            "status": "written" if accepted else "failed",
            "failure_reason": failure_reason,
            "selected_vocabulary_count": len(document.vocabulary_overview) if document else 0,
            "font_path": str(font_path) if font_path is not None else "",
            "font_name": font_name,
            "requires_human_revision": accepted,
            "revision_target": DOCX_FILENAME if accepted else "",
            "rendered_at": datetime.now(timezone.utc).isoformat(),
        },
        "heuristic_analysis": {
            "dir": str(heuristic_analysis_dir),
            "summary": dict(context.summary) if context else {},
        },
        "prompt": {
            "system_prompt": AFTER_CLASS_REPORT_SYSTEM_PROMPT if prompt is not None else "",
            "prompt_messages": list(prompt.prompt_messages) if prompt is not None else [],
        },
        "files": files,
    }


def after_class_attempt_row(
    *,
    source_id: str,
    context: AfterClassReportContext | None,
    prompt: AfterClassReportPrompt | None,
    model: str,
    response: str,
    document: AfterClassReportDocument | None,
    accepted: bool,
    failure_reason: str,
) -> dict[str, object]:
    priority_counts = Counter(item.priority for item in document.vocabulary_overview) if document else Counter()
    return {
        "source_id": source_id,
        "model": model,
        "system_prompt": AFTER_CLASS_REPORT_SYSTEM_PROMPT,
        "user_prompt": prompt.user_prompt if prompt is not None else "",
        "prompt_messages": list(prompt.prompt_messages) if prompt is not None else [],
        "response": response,
        "accepted": accepted,
        "failure_reason": failure_reason,
        "candidate_context_count": len(context.top_vocabulary_candidates) if context else 0,
        "example_context_count": len(context.top_example_sentences) if context else 0,
        "selected_vocabulary_count": len(document.vocabulary_overview) if document else 0,
        "core_vocabulary_count": priority_counts.get("core", 0),
        "practice_vocabulary_count": priority_counts.get("practice", 0),
        "enrichment_vocabulary_count": priority_counts.get("enrichment", 0),
        "deep_teaching_card_count": len(document.deep_teaching_cards) if document else 0,
    }


def merge_candidate_rows(
    candidate_rows: Sequence[Mapping[str, str]],
    dictionary_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    dictionary_by_key = {str(row.get("canonical_key") or ""): row for row in dictionary_rows}
    merged: list[dict[str, object]] = []
    for row in candidate_rows:
        canonical_key = str(row.get("canonical_key") or "").strip()
        if not canonical_key:
            continue
        dictionary_row = dictionary_by_key.get(canonical_key, {})
        merged.append(
            {
                "canonical_key": canonical_key,
                "display": str(row.get("display") or dictionary_row.get("display") or canonical_key),
                "pos_group": str(row.get("pos_group") or ""),
                "count": int_value(row.get("count")),
                "surface_count": int_value(row.get("surface_count")),
                "document_count": int_value(row.get("document_count")),
                "variant_count": int_value(row.get("variant_count")),
                "variants": split_joined(row.get("variants") or dictionary_row.get("variants") or ""),
                "learning_signal_score": row_learning_signal_score(row),
                "frequency_signal_score": float_value(score_field(row, "frequency_signal_score", "frequency_score_v2")),
                "dispersion_signal_score": float_value(score_field(row, "dispersion_signal_score", "dispersion_score_v2")),
                "pos_signal_score": float_value(score_field(row, "pos_signal_score", "pos_score_v2")),
                "variant_signal_score": float_value(score_field(row, "variant_signal_score", "variant_score_v2")),
                "roi_score": optional_float(row.get("roi_score")),
                "example": clean_example_text(str(row.get("example") or dictionary_row.get("example") or "")),
            }
        )
    return merged


def vocabulary_candidate_sort_key(row: Mapping[str, object]) -> tuple[float, float, int, int, int, str]:
    roi = row.get("roi_score")
    roi_score = float(roi) if isinstance(roi, (int, float)) else -1.0
    return (
        -roi_score,
        -normalized_learning_signal_score(row),
        -int_value(row.get("count")),
        -int_value(row.get("variant_count")),
        -int_value(row.get("document_count")),
        str(row.get("canonical_key") or ""),
    )


def example_sentence_sort_key(row: Mapping[str, object]) -> tuple[float, int, int, str]:
    return (
        -float_value(row.get("quality_score")),
        -int_value(row.get("covered_canonical_key_count")),
        -int_value(row.get("matched_canonical_key_count")),
        str(row.get("sentence") or ""),
    )


def prompt_candidate_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "canonical_key": row.get("canonical_key"),
        "display": row.get("display"),
        "pos_group": row.get("pos_group"),
        "count": row.get("count"),
        "surface_count": row.get("surface_count"),
        "document_count": row.get("document_count"),
        "variant_count": row.get("variant_count"),
        "variants": row.get("variants"),
        "learning_signal_score": row.get("learning_signal_score"),
        "roi_score": row.get("roi_score"),
        "example": row.get("example"),
    }


def prompt_example_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "document": row.get("document"),
        "page": row.get("page"),
        "sentence_index": row.get("sentence_index"),
        "sentence": clean_example_text(str(row.get("sentence") or "")),
        "quality_score": float_value(row.get("quality_score")),
        "covered_canonical_keys": split_joined(row.get("covered_canonical_keys") or ""),
        "matched_canonical_keys": split_joined(row.get("matched_canonical_keys") or ""),
    }


def bounded_printout_text(text: str) -> tuple[str, bool]:
    cleaned = clean_example_text(text)
    if len(cleaned) <= FULL_TEXT_CHAR_LIMIT:
        return cleaned, False
    head = cleaned[:TRUNCATED_TEXT_HEAD_CHARS].rstrip()
    tail = cleaned[-TRUNCATED_TEXT_TAIL_CHARS:].lstrip()
    return f"{head}\n\n[중간 일부 생략: prompt size]\n\n{tail}", True


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AfterClassReportError(f"Invalid JSON in {path}: {error.msg}") from error
    if not isinstance(parsed, dict):
        raise AfterClassReportError(f"Expected JSON object in {path}.")
    return parsed


def resolve_after_class_report_font(font_path: Path | None) -> tuple[Path, str]:
    try:
        return resolve_korean_font(font_path)
    except PrintableMaterialError as error:
        message = str(error).replace("--print-font-path", "--after-class-report-font-path")
        raise AfterClassReportError(message) from error


def after_class_report_max_output_tokens(context: AfterClassReportContext) -> int:
    prompt_scale = len(context.top_vocabulary_candidates) * 120 + len(context.top_example_sentences) * 80
    return max(16384, min(AFTER_CLASS_REPORT_MAX_OUTPUT_TOKENS, prompt_scale + 12288))


def prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def prevent_input_overwrite(source_path: Path, output_dir: Path) -> None:
    resolved_source = source_path.resolve()
    resolved_output = output_dir.resolve()
    if resolved_source == resolved_output or resolved_source.parent == resolved_output:
        raise AfterClassReportError(f"After-class report output would overwrite the input printout: {source_path}")


def prevent_reserved_maintenance_output(output_dir: Path) -> None:
    resolved_output = output_dir.resolve()
    for reserved_root in RESERVED_MAINTENANCE_OUTPUT_ROOTS:
        resolved_reserved = reserved_root.resolve()
        if resolved_output == resolved_reserved or is_relative_to(resolved_output, resolved_reserved):
            raise AfterClassReportError(
                f"After-class report output cannot be written inside maintenance path: {reserved_root}"
            )


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def append_bullets(lines: list[str], heading: str, values: Sequence[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    for value in values:
        lines.append(f"- {value}")


def ensure_korean_text(value: str, *, label: str) -> None:
    if not KOREAN_PATTERN.search(value):
        raise ValueError(f"{label} must include Korean text")


def normalize_text_list(value: object) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence):
        values = [str(item or "") for item in value if not isinstance(item, (bytes, bytearray))]
    else:
        values = []
    return [clean_example_text(item) for item in values if clean_example_text(item)]


def split_joined(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_example_text(str(item or "")) for item in value if clean_example_text(str(item or ""))]
    text = str(value or "")
    return [part.strip() for part in text.split("|") if part.strip()]


def int_value(value: object) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def float_value(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def score_field(row: Mapping[str, object], primary: str, legacy: str) -> object:
    if primary in row:
        return row.get(primary)
    return row.get(legacy)


def optional_float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def after_class_grade_level(source_id: str) -> str:
    return extract_grade_from_source_id(source_id)


def after_class_grade_label(grade_level: str) -> str:
    return grade_label(grade_level, unknown_label=UNKNOWN_GRADE_LABEL)


def after_class_prompt_target_profile(grade_level: str) -> str:
    return report_target_profile_for_grade(grade_level)


def _report(
    progress: ProgressCallback | None,
    *,
    phase: AfterClassReportProgressPhase,
    source_id: str,
    detail: str,
    output_dir: Path | None = None,
    heuristic_analysis_dir: Path | None = None,
    accepted: bool | None = None,
    selected_vocabulary_count: int = 0,
    docx_path: Path | None = None,
    failure_reason: str = "",
) -> None:
    if progress is not None:
        progress(
            AfterClassReportProgress(
                phase=phase,
                source_id=source_id,
                detail=detail,
                output_dir=output_dir,
                heuristic_analysis_dir=heuristic_analysis_dir,
                accepted=accepted,
                selected_vocabulary_count=selected_vocabulary_count,
                docx_path=docx_path,
                failure_reason=failure_reason,
            )
        )
