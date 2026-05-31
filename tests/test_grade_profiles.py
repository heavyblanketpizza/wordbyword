from __future__ import annotations

from pathlib import Path

import pytest

from wordbyword.grade_profiles import (
    DEFAULT_TARGET_GRADE,
    default_after_class_report_output_root,
    default_live_lexicon_path,
    default_print_output_root,
    default_supplemental_lexicon_path,
    default_unmatched_usage_output_path,
    default_usage_signal_output_root,
    default_vocabulary_guided_rewrite_output_root,
    default_whole_rewrite_output_root,
    extract_grade_from_source_id,
    grade_label,
    grade_profile,
    grade_workspace,
    normalize_grade_level,
    normalize_target_grade,
    report_target_profile_for_grade,
    target_profile_for_grade,
)


def test_grade_profile_keeps_grade1_compatible_paths() -> None:
    assert DEFAULT_TARGET_GRADE == "grade1"
    assert grade_profile("grade1").korean_label == "1학년"
    assert target_profile_for_grade("grade1") == "grade1-ko"
    assert report_target_profile_for_grade("grade1") == "grade1-ko-a4-adult-report"
    assert default_whole_rewrite_output_root("grade1") == (
        Path("classroom-printout-studio/rewritten-for-class") / "grade1-ko-ollama-whole-document"
    )
    assert default_vocabulary_guided_rewrite_output_root("grade1") == (
        Path("classroom-printout-studio/rewritten-for-class") / "grade1-ko-ollama-vocabulary-guided"
    )
    assert default_print_output_root("grade1") == (
        Path("classroom-printout-studio/printable-for-class") / "grade1-ko-a4-ollama"
    )
    assert default_usage_signal_output_root("grade1") == Path("wbw-vocabulary-intelligence/usage-signal-build/grade1")
    assert default_live_lexicon_path("grade1") == (
        Path("wbw-vocabulary-intelligence/live-grade-lexicon") / "grade1" / "canonical.jsonl"
    )


def test_middle_and_high_school_profiles_use_korean_school_labels_and_grade_paths() -> None:
    grade7 = grade_profile("중학교 1학년")
    grade12 = grade_profile("고3")

    assert grade7.target_grade == "grade7"
    assert grade7.korean_label == "중학교 1학년"
    assert grade7.reader_description == "중학교 1학년 학생"
    assert target_profile_for_grade("grade7") == "grade7-ko"
    assert default_vocabulary_guided_rewrite_output_root("grade7") == (
        Path("classroom-printout-studio/rewritten-for-class") / "grade7-ko-ollama-vocabulary-guided"
    )
    assert default_usage_signal_output_root("grade7", explicit_target=True) == (
        Path("wbw-vocabulary-intelligence/usage-signal-build") / "grade7"
    )
    assert default_live_lexicon_path("grade7") == (
        Path("wbw-vocabulary-intelligence/live-grade-lexicon") / "grade7" / "canonical.jsonl"
    )
    assert default_supplemental_lexicon_path("grade7") == (
        Path("wbw-vocabulary-intelligence/live-grade-lexicon") / "grade7" / "supplemental.jsonl"
    )
    assert default_unmatched_usage_output_path("grade7", explicit_target=True) == (
        Path("wbw-vocabulary-intelligence/usage-signal-build") / "grade7" / "10_unmatched_usage_candidates.jsonl"
    )
    assert default_after_class_report_output_root("grade7") == (
        Path("wbw-vocabulary-intelligence/after-class-reports") / "grade7-ko-a4-ollama"
    )

    assert grade12.target_grade == "grade12"
    assert grade12.korean_label == "고등학교 3학년"
    assert grade_label("grade12") == "고등학교 3학년"


@pytest.mark.parametrize(
    ("target_grade", "dictionary_input", "dictionary_output", "archive_root", "live_lexicon"),
    [
        (
            "grade1",
            Path("build-dictionary/original/grade1"),
            Path("build-dictionary/output/grade1"),
            Path("wbw-vocabulary-intelligence/class-session-archive/grade1"),
            Path("wbw-vocabulary-intelligence/live-grade-lexicon/grade1/canonical.jsonl"),
        ),
        (
            "grade7",
            Path("build-dictionary/original/grade7"),
            Path("build-dictionary/output/grade7"),
            Path("wbw-vocabulary-intelligence/class-session-archive/grade7"),
            Path("wbw-vocabulary-intelligence/live-grade-lexicon/grade7/canonical.jsonl"),
        ),
        (
            "grade12",
            Path("build-dictionary/original/grade12"),
            Path("build-dictionary/output/grade12"),
            Path("wbw-vocabulary-intelligence/class-session-archive/grade12"),
            Path("wbw-vocabulary-intelligence/live-grade-lexicon/grade12/canonical.jsonl"),
        ),
    ],
)
def test_grade_workspace_owns_every_grade_scoped_path(
    target_grade: str,
    dictionary_input: Path,
    dictionary_output: Path,
    archive_root: Path,
    live_lexicon: Path,
) -> None:
    workspace = grade_workspace(target_grade)

    assert workspace.dictionary_input_dir == dictionary_input
    assert workspace.dictionary_output_dir == dictionary_output
    assert workspace.class_source_dir == Path("classroom-printout-studio/class-source-materials") / target_grade
    assert workspace.source_output_root == Path("classroom-printout-studio/class-source-materials/parsed") / target_grade
    assert workspace.archive_root == archive_root
    assert workspace.usage_signal_output_root == Path("wbw-vocabulary-intelligence/usage-signal-build") / target_grade
    assert workspace.live_lexicon_path == live_lexicon
    assert workspace.supplemental_lexicon_path == live_lexicon.with_name("supplemental.jsonl")


def test_grade_normalization_accepts_korean_school_shorthand_and_unknown_metadata() -> None:
    assert normalize_grade_level("초등학교 6학년") == "grade6"
    assert normalize_grade_level("중1") == "grade7"
    assert normalize_grade_level("중학교 3학년") == "grade9"
    assert normalize_grade_level("고등학교 1학년") == "grade10"
    assert normalize_grade_level("고3") == "grade12"
    assert normalize_grade_level("/tmp/session-1/middle-grade7-reading.pdf") == "grade7"
    assert normalize_grade_level("/tmp/session-1/grade12-literature.pdf") == "grade12"
    assert normalize_grade_level("중학생") == "grade-unknown"
    assert grade_label("grade-unknown") == "초등학생"
    assert report_target_profile_for_grade("grade-unknown") == "elementary-ko-a4-adult-report"
    assert extract_grade_from_source_id("2026-05-10_grade10-physics-light") == "grade10"


def test_target_grade_rejects_unknown_generation_target() -> None:
    with pytest.raises(ValueError):
        normalize_target_grade("grade-unknown")
