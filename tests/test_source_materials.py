from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from wordbyword.cli import main
from wordbyword.source_materials import (
    AgenticOllamaProgress,
    OllamaVisionClient,
    OllamaVisionResponse,
    discover_source_materials,
    parse_source_material,
    parse_source_materials,
    source_id_for,
)


class SourceMaterialWorkspaceTest(unittest.TestCase):
    def test_source_id_for_uses_stable_filesystem_slug(self) -> None:
        self.assertEqual(source_id_for(Path("Popular Chemistry Prize 2025.pdf")), "popular-chemistry-prize-2025")

    def test_single_pdf_writes_markdown_first_parse_outputs_and_flat_assets_folder(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)

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
            self.assertTrue((bundle.output_dir / "content.md").is_file())
            self.assertTrue((bundle.output_dir / "chunks.json").is_file())
            self.assertTrue((bundle.output_dir / "document_model.json").is_file())
            self.assertTrue((bundle.output_dir / "parser_output.json").is_file())
            self.assertTrue((bundle.output_dir / "quality_report.json").is_file())
            self.assertFalse((bundle.output_dir / "canonical_content.json").exists())
            self.assertFalse((bundle.output_dir / "canonical_blocks.json").exists())
            self.assertFalse((bundle.output_dir / "canonical_blocks.jsonl").exists())
            self.assertFalse(any((bundle.assets_dir / old).exists() for old in ("images", "tables", "drawings", "pages")))

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "wordbyword.source_material_workspace.v1")
            self.assertEqual(manifest["source"]["filename"], "sample.pdf")
            self.assertEqual(manifest["parser"]["status"], "parsed")
            self.assertEqual(manifest["files"]["content_markdown"], "content.md")
            self.assertEqual(manifest["files"]["quality_report_json"], "quality_report.json")

            content = (bundle.output_dir / "content.md").read_text(encoding="utf-8")
            self.assertIn("Water Cycle", content)
            self.assertIn("<!-- page:1 -->", content)

            chunks = json.loads((bundle.output_dir / "chunks.json").read_text(encoding="utf-8"))
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]["page"], 1)
            self.assertIn("Water Cycle", chunks[0]["text"])

            quality = json.loads((bundle.output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["page_count"], 1)
            self.assertEqual(quality["page_render_count"], 1)
            self.assertGreater(quality["markdown_chars"], 0)
            self.assertTrue((bundle.output_dir / "assets" / "page-0001.png").is_file())

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
            self.assertFalse((output_root / "lesson" / "content.md").exists())
            manifest = json.loads((output_root / "lesson" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["parser"]["status"], "skipped")

    def test_agentic_ollama_parse_writes_separate_page_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)
            progress_events: list[AgenticOllamaProgress] = []

            bundle = parse_source_material(
                source_path,
                output_root=output_root,
                agentic_ollama_parse=True,
                agentic_ollama_max_pages=1,
                agentic_ollama_client=FakeOllamaVisionClient(),
                agentic_ollama_progress=progress_events.append,
            )

            self.assertTrue((bundle.output_dir / "agentic_ollama_content.md").is_file())
            self.assertTrue((bundle.output_dir / "agentic_ollama_assets.json").is_file())
            self.assertTrue((bundle.output_dir / "agentic_ollama_pages.json").is_file())
            self.assertTrue((bundle.output_dir / "agentic_ollama_report.json").is_file())

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["files"]["agentic_ollama_content_markdown"], "agentic_ollama_content.md")
            self.assertEqual(manifest["files"]["agentic_ollama_assets_json"], "agentic_ollama_assets.json")
            self.assertEqual(manifest["agentic_parsers"][0]["status"], "parsed")

            content = (bundle.output_dir / "agentic_ollama_content.md").read_text(encoding="utf-8")
            self.assertIn("Agentic Water Cycle", content)

            assets = json.loads((bundle.output_dir / "agentic_ollama_assets.json").read_text(encoding="utf-8"))
            self.assertEqual([asset["kind"] for asset in assets], ["table", "diagram"])
            self.assertEqual(assets[0]["source_image_path"], "assets/page-0001.png")

            pages = json.loads((bundle.output_dir / "agentic_ollama_pages.json").read_text(encoding="utf-8"))
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0]["status"], "parsed")
            self.assertEqual(pages[0]["parsed_json"]["page_number"], 1)

            report = json.loads((bundle.output_dir / "agentic_ollama_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["attempted_pages"], 1)
            self.assertEqual(report["parsed_pages"], 1)
            self.assertEqual(report["failed_pages"], 0)
            self.assertEqual(report["captured_asset_count"], 2)
            self.assertEqual([event.phase for event in progress_events], ["start", "complete"])
            self.assertEqual(progress_events[-1].completed_pages, 1)
            self.assertEqual(progress_events[-1].parsed_pages, 1)

    def test_ollama_vision_client_sends_schema_format_and_base64_image(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            image_path = Path(workspace) / "page.png"
            image_path.write_bytes(b"image-bytes")
            captured: dict[str, object] = {}

            class FakeResponse:
                def __enter__(self) -> "FakeResponse":
                    return self

                def __exit__(self, *args: object) -> None:
                    return None

                def read(self) -> bytes:
                    return json.dumps({"message": {"content": "{}"}}).encode("utf-8")

            def fake_urlopen(http_request: object, timeout: float) -> FakeResponse:
                captured["url"] = getattr(http_request, "full_url")
                captured["timeout"] = timeout
                body = getattr(http_request, "data")
                captured["body"] = json.loads(body.decode("utf-8"))
                return FakeResponse()

            with patch("wordbyword.source_materials.request.urlopen", fake_urlopen):
                client = OllamaVisionClient(
                    model="gemma4:31b",
                    base_url="http://localhost:11434/",
                    timeout_seconds=12.0,
                )
                client.parse_page(
                    image_path=image_path,
                    page_number=1,
                    page_count=1,
                    prompt="parse",
                )

            body = captured["body"]
            self.assertIsInstance(body, dict)
            assert isinstance(body, dict)
            self.assertEqual(captured["url"], "http://localhost:11434/api/chat")
            self.assertEqual(captured["timeout"], 12.0)
            self.assertEqual(body["model"], "gemma4:31b")
            self.assertEqual(body["messages"][0]["images"], ["aW1hZ2UtYnl0ZXM="])
            self.assertEqual(body["format"]["type"], "object")
            self.assertIn("reading_order_markdown", body["format"]["properties"])

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


def write_sample_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=400, height=400)
    page.insert_text((40, 50), "Water Cycle", fontsize=18)
    page.insert_text((40, 95), "Water moves through the air and land.", fontsize=11)
    document.save(path)
    document.close()


class FakeOllamaVisionClient:
    def parse_page(self, **kwargs: object) -> OllamaVisionResponse:
        page_number = int(kwargs["page_number"])
        return OllamaVisionResponse(
            content=json.dumps(
                {
                    "page_number": page_number,
                    "reading_order_markdown": "# Agentic Water Cycle\n\nWater moves through the air and land.",
                    "headings": ["Agentic Water Cycle"],
                    "paragraphs": ["Water moves through the air and land."],
                    "tables": [
                        {
                            "id": "water-cycle-table",
                            "caption": "Cycle stages",
                            "markdown": "| Stage | Description |\n| --- | --- |\n| Evaporation | Water rises. |",
                            "notes": "",
                            "confidence": 0.9,
                        }
                    ],
                    "figures": [
                        {
                            "id": "water-cycle-diagram",
                            "kind": "diagram",
                            "caption": "Water Cycle",
                            "visible_text": "Water Cycle",
                            "description": "A simple water-cycle diagram.",
                            "page_region": "upper page",
                            "confidence": 0.8,
                        }
                    ],
                    "footnotes_or_footer": [],
                    "uncertain_text": [],
                    "parser_notes": [],
                    "confidence": 0.95,
                }
            ),
            raw={"message": {"content": "ok"}},
        )
