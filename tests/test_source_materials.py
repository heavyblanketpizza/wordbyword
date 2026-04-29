from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wordbyword.cli import main
from wordbyword.source_materials import discover_source_materials, parse_source_material, parse_source_materials, source_id_for


class SourceMaterialWorkspaceTest(unittest.TestCase):
    def test_source_id_for_uses_stable_filesystem_slug(self) -> None:
        self.assertEqual(source_id_for(Path("Popular Chemistry Prize 2025.pdf")), "popular-chemistry-prize-2025")

    def test_single_source_prepares_manifest_and_flat_assets_folder(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            source_path.write_bytes(b"%PDF-1.7\n")

            stale_output = output_root / "sample"
            stale_output.mkdir(parents=True)
            (stale_output / "canonical_content.json").write_text("bad", encoding="utf-8")
            (stale_output / "canonical_blocks.json").write_text("bad", encoding="utf-8")
            (stale_output / "canonical_blocks.jsonl").write_text("bad", encoding="utf-8")
            for old_folder in ("images", "tables", "drawings", "pages"):
                folder = stale_output / "assets" / old_folder
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "old.txt").write_text("bad", encoding="utf-8")

            bundle = parse_source_material(source_path, output_root=output_root)

            self.assertEqual(bundle.source_id, "sample")
            self.assertTrue(bundle.manifest_path.is_file())
            self.assertTrue(bundle.assets_dir.is_dir())
            self.assertFalse((bundle.output_dir / "canonical_content.json").exists())
            self.assertFalse((bundle.output_dir / "canonical_blocks.json").exists())
            self.assertFalse((bundle.output_dir / "canonical_blocks.jsonl").exists())
            self.assertFalse(any((bundle.assets_dir / old).exists() for old in ("images", "tables", "drawings", "pages")))

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "wordbyword.source_material_workspace.v1")
            self.assertEqual(manifest["source"]["filename"], "sample.pdf")
            self.assertEqual(manifest["parser"]["status"], "not_run")
            self.assertEqual(manifest["files"], {"assets": "assets/"})

    def test_cli_prepares_one_source_material_without_running_dictionary_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "lesson.txt"
            output_root = root / "parsed"
            source_path.write_text("물은 생명에 필요하다.", encoding="utf-8")

            with patch("sys.stdout", io.StringIO()):
                exit_code = main(
                    [
                        "--parse-source-material",
                        str(source_path),
                        "--source-output-root",
                        str(output_root),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_root / "lesson" / "manifest.json").is_file())
            self.assertTrue((output_root / "lesson" / "assets").is_dir())
            self.assertFalse((output_root / "lesson" / "canonical_content.json").exists())

    def test_batch_prepares_separate_folder_for_each_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_dir = root / "class-source-materials"
            output_root = source_dir / "parsed"
            source_dir.mkdir()
            (source_dir / "lesson.txt").write_text("첫 번째 자료.", encoding="utf-8")
            nested_dir = source_dir / "nested"
            nested_dir.mkdir()
            (nested_dir / "lesson.txt").write_text("두 번째 자료.", encoding="utf-8")
            output_root.mkdir()
            (output_root / "generated.txt").write_text("do not parse generated output", encoding="utf-8")

            discovered = discover_source_materials(source_dir, output_root=output_root)
            result = parse_source_materials(source_dir, output_root=output_root)

            self.assertEqual([path.name for path in discovered], ["lesson.txt", "lesson.txt"])
            self.assertEqual(len(result.bundles), 2)
            self.assertEqual({bundle.source_id for bundle in result.bundles}, {"lesson", "nested-lesson"})
            self.assertTrue((output_root / "lesson" / "manifest.json").is_file())
            self.assertTrue((output_root / "nested-lesson" / "manifest.json").is_file())
            self.assertFalse((output_root / "generated" / "manifest.json").exists())

    def test_cli_batch_prepares_one_folder_per_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_dir = root / "sources"
            output_root = root / "parsed"
            source_dir.mkdir()
            (source_dir / "alpha.txt").write_text("알파 자료.", encoding="utf-8")
            (source_dir / "beta.md").write_text("# 베타 자료", encoding="utf-8")

            with patch("sys.stdout", io.StringIO()):
                exit_code = main(
                    [
                        "--parse-source-materials",
                        str(source_dir),
                        "--source-output-root",
                        str(output_root),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_root / "alpha" / "manifest.json").is_file())
            self.assertTrue((output_root / "beta" / "manifest.json").is_file())
