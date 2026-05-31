from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGET_GRADE = "grade1"
UNKNOWN_GRADE = "grade-unknown"
VALID_TARGET_GRADES = tuple(f"grade{grade}" for grade in range(1, 13))
GRADE_WITH_UNKNOWN_PATTERN = r"grade(?:1[0-2]|[1-9]|-unknown)"
TARGET_GRADE_PATTERN = r"grade(?:1[0-2]|[1-9])"
GRADE_TOKEN_PATTERN = re.compile(r"grade(?:1[0-2]|[1-9])")

DICTIONARY_ROOT = Path("build-dictionary")
DICTIONARY_SOURCE_ROOT = DICTIONARY_ROOT / "original"
DICTIONARY_OUTPUT_ROOT = DICTIONARY_ROOT / "output"
REFERENCE_DICTIONARY_PATH = DICTIONARY_ROOT / "references" / "reference-dictionary.jsonl"
CLASS_SOURCE_MATERIAL_ROOT = Path("classroom-printout-studio/class-source-materials")
CLASS_SOURCE_ORIGINAL_ROOT = CLASS_SOURCE_MATERIAL_ROOT / "original"
CLASS_PARSED_SOURCE_ROOT = CLASS_SOURCE_MATERIAL_ROOT / "parsed"
CLASS_SOURCE_REWRITE_ROOT = Path("classroom-printout-studio/rewritten-for-class")
CLASS_PRINT_OUTPUT_ROOT = Path("classroom-printout-studio/printable-for-class")
VOCABULARY_INTELLIGENCE_ROOT = Path("wbw-vocabulary-intelligence")
CLASS_SESSION_ARCHIVE_ROOT = VOCABULARY_INTELLIGENCE_ROOT / "class-session-archive"
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


@dataclass(frozen=True)
class GradeWorkspace:
    target_grade: str
    profile: GradeProfile
    dictionary_input_dir: Path
    dictionary_output_dir: Path
    reference_dictionary_path: Path
    class_source_dir: Path
    source_output_root: Path
    whole_rewrite_output_root: Path
    rewrite_output_root: Path
    print_output_root: Path
    archive_root: Path
    usage_signal_output_root: Path
    usage_candidates_path: Path
    live_lexicon_dir: Path
    live_lexicon_path: Path
    supplemental_lexicon_path: Path
    unmatched_usage_output_path: Path
    after_class_report_output_root: Path


def normalize_grade_level(value: object, *, allow_unknown: bool = True) -> str:
    text = str(value or "").strip().lower()
    compact = re.sub(r"[\s_-]+", "", text)
    if compact in {"", "unknown", "gradeunknown", "학년모름", "미상"} or text == UNKNOWN_GRADE:
        return UNKNOWN_GRADE if allow_unknown else DEFAULT_TARGET_GRADE

    grade_match = re.fullmatch(r"grade(1[0-2]|[1-9])", compact)
    if grade_match:
        return f"grade{grade_match.group(1)}"
    embedded_grade_match = re.search(r"grade(1[0-2]|[1-9])", compact)
    if embedded_grade_match:
        return f"grade{embedded_grade_match.group(1)}"

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


def grade_workspace(value: object = DEFAULT_TARGET_GRADE) -> GradeWorkspace:
    profile = grade_profile(value)
    grade = profile.target_grade
    usage_signal_output_root = USAGE_SIGNAL_BUILD_ROOT / grade
    live_lexicon_dir = LIVE_GRADE_LEXICON_ROOT / grade
    return GradeWorkspace(
        target_grade=grade,
        profile=profile,
        dictionary_input_dir=DICTIONARY_SOURCE_ROOT / grade,
        dictionary_output_dir=DICTIONARY_OUTPUT_ROOT / grade,
        reference_dictionary_path=REFERENCE_DICTIONARY_PATH,
        class_source_dir=CLASS_SOURCE_MATERIAL_ROOT / grade,
        source_output_root=CLASS_PARSED_SOURCE_ROOT / grade,
        whole_rewrite_output_root=CLASS_SOURCE_REWRITE_ROOT / f"{grade}-ko-ollama-whole-document",
        rewrite_output_root=CLASS_SOURCE_REWRITE_ROOT / f"{grade}-ko-ollama-vocabulary-guided",
        print_output_root=CLASS_PRINT_OUTPUT_ROOT / f"{grade}-ko-a4-ollama",
        archive_root=CLASS_SESSION_ARCHIVE_ROOT / grade,
        usage_signal_output_root=usage_signal_output_root,
        usage_candidates_path=usage_signal_output_root / "08_dictionary_candidates.jsonl",
        live_lexicon_dir=live_lexicon_dir,
        live_lexicon_path=live_lexicon_dir / "canonical.jsonl",
        supplemental_lexicon_path=live_lexicon_dir / "supplemental.jsonl",
        unmatched_usage_output_path=usage_signal_output_root / "10_unmatched_usage_candidates.jsonl",
        after_class_report_output_root=AFTER_CLASS_REPORT_ROOT / f"{grade}-ko-a4-ollama",
    )


def grade_from_target_profile(target_profile: str, *, default: str = DEFAULT_TARGET_GRADE) -> str:
    match = re.search(r"grade(?P<grade>1[0-2]|[1-9])(?:-|$)", target_profile)
    if match is None:
        return normalize_target_grade(default)
    return f"grade{match.group('grade')}"


def default_whole_rewrite_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return grade_workspace(value).whole_rewrite_output_root


def default_vocabulary_guided_rewrite_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return grade_workspace(value).rewrite_output_root


def default_print_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return grade_workspace(value).print_output_root


def default_usage_signal_output_root(value: object = DEFAULT_TARGET_GRADE, *, explicit_target: bool = False) -> Path:
    del explicit_target
    return grade_workspace(value).usage_signal_output_root


def default_live_lexicon_path(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return grade_workspace(value).live_lexicon_path


def default_supplemental_lexicon_path(value: object = DEFAULT_TARGET_GRADE) -> Path:
    return grade_workspace(value).supplemental_lexicon_path


def default_unmatched_usage_output_path(value: object = DEFAULT_TARGET_GRADE, *, explicit_target: bool = False) -> Path:
    del explicit_target
    return grade_workspace(value).unmatched_usage_output_path


def default_after_class_report_output_root(value: object = DEFAULT_TARGET_GRADE) -> Path:
    grade = normalize_grade_level(value)
    if grade == UNKNOWN_GRADE:
        return AFTER_CLASS_REPORT_ROOT / "elementary-ko-a4-ollama"
    return grade_workspace(grade).after_class_report_output_root


def path_grade_tokens(path: Path | str) -> set[str]:
    return {match.group(0) for match in GRADE_TOKEN_PATTERN.finditer(str(path))}


def path_mentions_other_grade(path: Path | str, target_grade: object) -> bool:
    grade = normalize_target_grade(target_grade)
    tokens = path_grade_tokens(path)
    return bool(tokens and tokens != {grade})


def extract_grade_from_source_id(source_id: str) -> str:
    match = re.search(r"(?:^|_)grade(?P<grade>1[0-2]|[1-9]|-unknown)(?:-|$)", source_id)
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
