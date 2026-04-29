from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from wordbyword.cli import main
from wordbyword.source_materials import parse_source_material, source_id_for


class SourceMaterialParserTest(unittest.TestCase):
    def test_source_id_for_uses_stable_filesystem_slug(self) -> None:
        self.assertEqual(source_id_for(Path("Popular Chemistry Prize 2025.pdf")), "popular-chemistry-prize-2025")

    def test_pdf_bundle_writes_canonical_ir_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)

            bundle = parse_source_material(source_path, output_root=output_root)

            self.assertEqual(bundle.source_id, "sample")
            self.assertTrue(bundle.manifest_path.is_file())
            self.assertTrue(bundle.canonical_json_path.is_file())
            self.assertTrue(bundle.canonical_jsonl_path.is_file())
            self.assertTrue((bundle.output_dir / "assets" / "tables").is_dir())
            self.assertTrue((bundle.output_dir / "assets" / "drawings").is_dir())
            self.assertTrue((bundle.output_dir / "assets" / "images").is_dir())

            canonical = json.loads(bundle.canonical_json_path.read_text(encoding="utf-8"))
            block_types = {block["type"] for block in canonical["blocks"]}
            self.assertIn("heading", block_types)
            self.assertIn("paragraph", block_types)
            self.assertIn("table", block_types)
            self.assertIn("drawing", block_types)
            self.assertEqual(canonical["schema_version"], "wordbyword.source_material_ir.v1")
            self.assertEqual(canonical["source"]["filename"], "sample.pdf")
            self.assertEqual(canonical["statistics"]["block_count"], len(canonical["blocks"]))
            self.assertGreaterEqual(canonical["statistics"]["asset_counts"]["table"], 2)
            self.assertGreaterEqual(canonical["statistics"]["asset_counts"]["drawing"], 1)

            table_block = next(block for block in canonical["blocks"] if block["type"] == "table")
            self.assertEqual(table_block["rows"], [["Term", "Meaning"], ["H2O", "water"]])
            self.assertEqual(table_block["text"], "Term\tMeaning\nH2O\twater")
            self.assertTrue(table_block["assets"])

            asset_paths = [bundle.output_dir / asset["path"] for asset in canonical["assets"]]
            self.assertTrue(all(path.is_file() for path in asset_paths))

            jsonl_rows = [
                json.loads(line)
                for line in bundle.canonical_jsonl_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(jsonl_rows), len(canonical["blocks"]))
            self.assertEqual([row["order"] for row in jsonl_rows], list(range(1, len(jsonl_rows) + 1)))
            self.assertTrue(all(row["id"] == f"b{row['order']:06d}" for row in jsonl_rows))

    def test_image_source_becomes_ocr_and_vision_placeholder_block(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "worksheet.png"
            output_root = root / "parsed"
            source_path.write_bytes(base64.b64decode(SMALL_PNG_BASE64))

            bundle = parse_source_material(source_path, output_root=output_root)

            canonical = json.loads(bundle.canonical_json_path.read_text(encoding="utf-8"))
            self.assertEqual(len(canonical["blocks"]), 1)
            block = canonical["blocks"][0]
            self.assertEqual(block["type"], "image")
            self.assertTrue(block["needs_ocr"])
            self.assertTrue(block["needs_vision"])
            self.assertEqual(canonical["statistics"]["asset_counts"]["image"], 1)

    def test_cli_parses_one_source_material_without_running_dictionary_pipeline(self) -> None:
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
            self.assertTrue((output_root / "lesson" / "canonical_content.json").is_file())


def write_sample_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=400, height=400)
    page.insert_text((40, 40), "Water Cycle", fontsize=18)
    page.insert_text((40, 80), "Water moves through the air and land.", fontsize=11)

    x0, y0 = 40, 120
    cell_width, cell_height = 120, 30
    for row in range(3):
        y = y0 + row * cell_height
        page.draw_line((x0, y), (x0 + 2 * cell_width, y))
    for column in range(3):
        x = x0 + column * cell_width
        page.draw_line((x, y0), (x, y0 + 2 * cell_height))
    page.insert_text((50, 140), "Term", fontsize=10)
    page.insert_text((170, 140), "Meaning", fontsize=10)
    page.insert_text((50, 170), "H2O", fontsize=10)
    page.insert_text((170, 170), "water", fontsize=10)

    page.draw_circle((280, 260), 36)
    page.draw_line((244, 260), (316, 260))
    page.draw_line((280, 224), (280, 296))
    document.save(path)
    document.close()


SMALL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
