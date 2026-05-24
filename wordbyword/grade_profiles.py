from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGET_GRADE = "grade1"
UNKNOWN_GRADE = "grade-unknown"
VALID_TARGET_GRADES = tuple(f"grade{grade}" for grade in range(1, 13))
GRADE_WITH_UNKNOWN_PATTERN = r"grade(?:[1-9]|1[0-2]|-unknown)"
TARGET_GRADE_PATTERN = r"grade(?:[1-9]|1[0-2])"

CLASS_SOURCE_REWRITE_ROOT = Path("classroom-printout-studio/rewritten-for-class")
CLASS_PRINT_OUTPUT_ROOT = Path("classroom-printout-studio/printable-for-class")
VOCABULARY_INTELLIGENCE_ROOT = Path("wbw-vocabulary-intelligence")
LIVE_GRADE_LEXICON_ROOT = VOCABULARY_INTELLIGENCE_ROOT / "live-grade-lexicon"
USAGE_SIGNAL_BUILD_ROOT = VOCABULARY_INTELLIGENCE_ROOT / "usage-signal-build"
AFTER_CLASS_REPORT_ROOT = VOCABULARY_INTELLIGENCE_ROOT / "after-class-reports"


@dataclass(frozen=True)
class GradeProfile:
    target_grade: str
    grade_number: int
    school_level: str
    school_grade_number: int
    korean_label: str
    reader_description: str
    target_profile: str
    report_target_profile: str


def normalize_grade_level(value: object, *, allow_unknown: bool = True) -> str:
    text = str(value or "").strip().lower()
    compact = re.sub(r"[\s_-]+", "", text)
    if compact in {"", "unknown", "gradeunknown", "학년모름", "미상"} or text == UNKNOWN_GRADE:
        return UNKNOWN_GRADE if allow_unknown else DEFAULT_TARGET_GRADE

    grade_match = re.fullmatch(r"grade([1-9]|1[0-2])", compact)
    if grade_match:
        return f"grade{grade_match.group(1)}"

    school_match = _school_relative_grade(compact)
    if school_match is not None:
        return f"grade{school_match}"

    digit_match = re.search(r"(1[0-2]|[1-9])", compact)
    if digit_match and ("grade" in compact or "학년" in compact or compact == digit_match.group(1)):
        return f"grade{digit_match.group(1)}"

    return UNKNOWN_GRADE if allow_unknown else DEFAULT_TARGET_GRADE


def normalize_target_grade(value: object) -> str:
    if not str(value or "").strip():
        return DEFAULT_TARGET_GRADE
    grade = normalize_grade_level(value)
    if grade not in VALID_TARGET_GRADES:
        raise ValueError(f"Unsupported target grade: {value!r}. Expected grade1 through grade12.")
    return grade


def grade_profile(value: object = DEFAULT_TARGET_GRADE) -> GradeProfile:
    target_grade = normalize_target_grade(value)
    grade_number = int(target_grade.removeprefix("grade"))
    if grade_number <= 6:
        school_level = "elementary"
        school_grade_number = grade_number
        korean_label = f"{school_grade_number}학년"
        reader_description = f"초등학교 {school_grade_number}학년 학생"
    elif grade_number <= 9:
        school_level = "middle"
        school_grade_number = grade_number - 6
        korean_label = f"중학교 {school_grade_number}학년"
        reader_description = f"중학교 {school_grade_number}학년 학생"
    else:
        school_level = "high"
        school_grade_number = grade_number - 9
        korean_label = f"고등학교 {school_grade_number}학년"
        reader_description = f"고등학교 {school_grade_number}학년 학생"
    return GradeProfile(
        target_grade=target_grade,
        grade_number=grade_number,
        school_level=school_level,
        school_grade_number=school_grade_number,
        korean_label=korean_label,
        reader_description=reader_description,
        target_profile=f"{target_grade}-ko",
        report_target_profile=f"{target_grade}-ko-a4-adult-report",
    )


def grade_label(value: object, *, unknown_label: str = "초등학생") -> str:
    grade = normalize_grade_level(value)
    if grade == UNKNOWN_GRADE:
        return unknown_label
    return grade_profile(grade).korean_label


def target_profile_for_grade(value: object = DEFAULT_TARGET_GRADE) -> str:
    return grade_profile(value).target_profile


def report_target_profile_for_grade(value: object = DEFAULT_TARGET_GRADE) -> str:
    grade = normalize_grade_level(value)
    if grade == UNKNOWN_GRADE:
        return "elementary-ko-a4-adult-report"
    return grade_profile(grade).report_target_profile


def grade_from_target_profile(target_profile: str, *, default: str = DEFAULT_TARGET_GRADE) -> str:
    match = re.search(r"grade(?P<grade>[1-9]|1[0-2])(?:-|$)", target_profile)
    if match is None:
        return normalize_target_grade(default)
    return f"grade{match.group('grade')}"


def default_whole_rewrite_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return CLASS_SOURCE_REWRITE_ROOT / f"{normalize_target_grade(value)}-ko-ollama-whole-document"


def default_vocabulary_guided_rewrite_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return CLASS_SOURCE_REWRITE_ROOT / f"{normalize_target_grade(value)}-ko-ollama-vocabulary-guided"


def default_print_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return CLASS_PRINT_OUTPUT_ROOT / f"{normalize_target_grade(value)}-ko-a4-ollama"


def default_usage_signal_output_root(value: object = DEFAULT_TARGET_GRADE, *, explicit_target: bool = False) -> Path:
    grade = normalize_target_grade(value)
    if grade == DEFAULT_TARGET_GRADE and not explicit_target:
        return USAGE_SIGNAL_BUILD_ROOT
    return USAGE_SIGNAL_BUILD_ROOT / grade


def default_live_lexicon_path(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return LIVE_GRADE_LEXICON_ROOT / f"{normalize_target_grade(value)}-live-lexicon-canonical.jsonl"


def default_supplemental_lexicon_path(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return LIVE_GRADE_LEXICON_ROOT / f"{normalize_target_grade(value)}-live-lexicon-supplemental.jsonl"


def default_unmatched_usage_output_path(value: object = DEFAULT_TARGET_GRADE, *, explicit_target: bool = False) -> Path:
    return default_usage_signal_output_root(value, explicit_target=explicit_target) / "10_unmatched_usage_candidates.jsonl"


def default_after_class_report_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    grade = normalize_grade_level(value)
    if grade == UNKNOWN_GRADE:
        return AFTER_CLASS_REPORT_ROOT / "elementary-ko-a4-ollama"
    return AFTER_CLASS_REPORT_ROOT / f"{normalize_target_grade(grade)}-ko-a4-ollama"


def extract_grade_from_source_id(source_id: str) -> str:
    match = re.search(r"(?:^|_)grade(?P<grade>[1-9]|1[0-2]|-unknown)(?:-|$)", source_id)
    if match is None:
        return UNKNOWN_GRADE
    grade = match.group("grade")
    if grade == "-unknown":
        return UNKNOWN_GRADE
    return f"grade{grade}"


def _school_relative_grade(compact: str) -> int | None:
    match = re.search(r"([1-6])", compact)
    if match is None:
        return None
    number = int(match.group(1))
    if any(marker in compact for marker in ("초등", "초교", "초")) and 1 <= number <= 6:
        return number
    if any(marker in compact for marker in ("중학교", "중등", "중")) and 1 <= number <= 3:
        return number + 6
    if any(marker in compact for marker in ("고등학교", "고등", "고")) and 1 <= number <= 3:
        return number + 9
    return None
