from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wordbyword.grade_workspace_migration import (
    MIGRATION_MANIFEST_FILENAME,
    NEEDS_GRADE_REVIEW,
    migrate_grade_workspaces,
)


class GradeWorkspaceMigrationTest(unittest.TestCase):
    def test_dry_run_plans_legacy_and_review_bucket_moves_without_moving_files(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            grade7_original = root / "build-dictionary" / "original" / "middle-grade7-reading.pdf"
            unknown_original = root / "build-dictionary" / "original" / "unclassified.pdf"
            legacy_dictionary = root / "build-dictionary" / "15_final_dictionary.jsonl"
            legacy_live = root / "wbw-vocabulary-intelligence" / "live-grade-lexicon" / "grade1-live-lexicon-canonical.jsonl"
            for path in (grade7_original, unknown_original, legacy_dictionary, legacy_live):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")

            result = migrate_grade_workspaces(root=root, apply=False)

            self.assertTrue(result.dry_run)
            self.assertFalse((root / MIGRATION_MANIFEST_FILENAME).exists())
            self.assertTrue(grade7_original.exists())
            self.assertTrue(unknown_original.exists())
            self.assertTrue(legacy_dictionary.exists())
            self.assertIn(
                root / "build-dictionary" / "original" / "grade7" / grade7_original.name,
                {move.target_path for move in result.moves},
            )
            self.assertIn(
                root / "build-dictionary" / "original" / NEEDS_GRADE_REVIEW / unknown_original.name,
                {move.target_path for move in result.moves},
            )
            self.assertIn(
                root / "build-dictionary" / "output" / "grade2" / legacy_dictionary.name,
                {move.target_path for move in result.moves},
            )
            self.assertIn(
                root / "wbw-vocabulary-intelligence" / "live-grade-lexicon" / "grade1" / "canonical.jsonl",
                {move.target_path for move in result.moves},
            )

    def test_apply_moves_grade_workspaces_and_writes_manifest_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            files = [
                root / "build-dictionary" / "original" / "grade12-literature.pdf",
                root / "build-dictionary" / "original" / "needs-label.pdf",
                root / "build-dictionary" / "15_final_dictionary.jsonl",
                root / "classroom-printout-studio" / "class-source-materials" / "lesson.txt",
                root / "classroom-printout-studio" / "class-source-materials" / "original" / "grade1" / "legacy.pdf",
                root
                / "classroom-printout-studio"
                / "class-source-materials"
                / "parsed"
                / "lesson"
                / "manifest.json",
                root / "wbw-vocabulary-intelligence" / "class-session-archive" / "2026-05-10_grade7-reading.docx",
                root / "wbw-vocabulary-intelligence" / "class-session-archive" / "loose.docx",
                root / "wbw-vocabulary-intelligence" / "usage-signal-build" / "00_summary.json",
                root / "wbw-vocabulary-intelligence" / "live-grade-lexicon" / "grade1-live-lexicon-supplemental.jsonl",
            ]
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")

            result = migrate_grade_workspaces(root=root, apply=True)

            self.assertFalse(result.dry_run)
            self.assertEqual(result.applied_count, result.planned_count)
            self.assertTrue((root / "build-dictionary" / "original" / "grade12" / "grade12-literature.pdf").is_file())
            self.assertTrue(
                (root / "build-dictionary" / "original" / NEEDS_GRADE_REVIEW / "needs-label.pdf").is_file()
            )
            self.assertTrue((root / "build-dictionary" / "output" / "grade2" / "15_final_dictionary.jsonl").is_file())
            self.assertTrue(
                (
                    root
                    / "classroom-printout-studio"
                    / "class-source-materials"
                    / "grade2"
                    / "lesson.txt"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / "classroom-printout-studio"
                    / "class-source-materials"
                    / "grade1"
                    / "legacy.pdf"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / "classroom-printout-studio"
                    / "class-source-materials"
                    / "parsed"
                    / "grade2"
                    / "lesson"
                    / "manifest.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / "wbw-vocabulary-intelligence"
                    / "class-session-archive"
                    / "grade7"
                    / "2026-05-10_grade7-reading.docx"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / "wbw-vocabulary-intelligence"
                    / "class-session-archive"
                    / NEEDS_GRADE_REVIEW
                    / "loose.docx"
                ).is_file()
            )
            self.assertTrue(
                (root / "wbw-vocabulary-intelligence" / "usage-signal-build" / "grade2" / "00_summary.json").is_file()
            )
            self.assertTrue(
                (
                    root
                    / "wbw-vocabulary-intelligence"
                    / "live-grade-lexicon"
                    / "grade1"
                    / "supplemental.jsonl"
                ).is_file()
            )
            manifest = json.loads((root / MIGRATION_MANIFEST_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "wordbyword.grade_workspace_migration.v1")
            self.assertEqual(manifest["applied_count"], result.applied_count)

            second = migrate_grade_workspaces(root=root, apply=True)
            self.assertEqual(second.applied_count, 0)
            self.assertEqual(second.planned_count, 0)

    def test_grade_two_textbook_sources_and_flat_dictionary_outputs_migrate_together(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_names = ["2-1_국어[나].pdf", "2-1_국어활동.pdf", "2-1국어[가].pdf"]
            for name in source_names:
                path = root / "build-dictionary" / "original" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source", encoding="utf-8")
            final_dictionary = root / "build-dictionary" / "15_final_dictionary.jsonl"
            final_dictionary.write_text("{}\n", encoding="utf-8")

            result = migrate_grade_workspaces(root=root, apply=False)
            targets = {move.target_path for move in result.moves}

            for name in source_names:
                self.assertIn(root / "build-dictionary" / "original" / "grade2" / name, targets)
            self.assertIn(
                root / "build-dictionary" / "output" / "grade2" / "15_final_dictionary.jsonl",
                targets,
            )
            self.assertNotIn(
                root / "build-dictionary" / "original" / NEEDS_GRADE_REVIEW / "2-1_국어[나].pdf",
                targets,
            )
