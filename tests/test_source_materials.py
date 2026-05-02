from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from wordbyword.cli import main
from wordbyword.source_parsing.paddleocr_vl import PaddleOCRVLInterruptedError, PaddleOCRVLSettings
from wordbyword.source_parsing.materials import (
    SourceMaterialProgress,
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

    def test_paddleocr_vl_pdf_parser_writes_page_image_parse_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)

            FakePaddleOCRVL.init_kwargs = None
            FakePaddleOCRVL.predict_input = None
            with (
                patch("wordbyword.source_parsing.paddleocr_vl._load_paddleocr_vl_class", return_value=FakePaddleOCRVL),
                patch("wordbyword.source_parsing.paddleocr_vl.paddleocr_vl_version", return_value="3.3.0"),
            ):
                bundle = parse_source_material(
                    source_path,
                    output_root=output_root,
                    pdf_parser="paddleocr-vl",
                    paddleocr_vl_settings=PaddleOCRVLSettings(device="cpu", isolated_process=False),
                )

            manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["parser"]["name"], "paddleocr-vl-1.5")
            self.assertEqual(manifest["parser"]["version"], "3.3.0")
            self.assertEqual(manifest["parser"]["settings"]["device"], "cpu")
            self.assertEqual(FakePaddleOCRVL.init_kwargs["pipeline_version"], "v1.5")
            self.assertEqual(FakePaddleOCRVL.init_kwargs["device"], "cpu")
            self.assertEqual(len(FakePaddleOCRVL.predict_input), 1)
            self.assertTrue(str(FakePaddleOCRVL.predict_input[0]).endswith("page-0001.png"))

            content = (bundle.output_dir / "content.md").read_text(encoding="utf-8")
            self.assertIn("PaddleOCR-VL text from page-0001.png", content)

            chunks = json.loads((bundle.output_dir / "chunks.json").read_text(encoding="utf-8"))
            self.assertEqual(chunks[0]["metadata"]["parser"], "paddleocr-vl")
            self.assertEqual(chunks[0]["metadata"]["input_image_path"], "assets/page-0001.png")

            parser_output = json.loads((bundle.output_dir / "parser_output.json").read_text(encoding="utf-8"))
            self.assertEqual(parser_output["schema_version"], "wordbyword.paddleocr_vl_pdf_parse.v1")
            self.assertEqual(parser_output["page_results"][0]["raw_json"]["parsing_res_list"][0]["block_label"], "text")

            quality = json.loads((bundle.output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["schema_version"], "wordbyword.paddleocr_vl_pdf_parse.v1")
            self.assertEqual(quality["page_render_count"], 1)

    def test_paddleocr_vl_pdf_parser_reports_page_progress(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)
            events: list[SourceMaterialProgress] = []

            with (
                patch("wordbyword.source_parsing.paddleocr_vl._load_paddleocr_vl_class", return_value=FakePaddleOCRVL),
                patch("wordbyword.source_parsing.paddleocr_vl.paddleocr_vl_version", return_value="3.3.0"),
            ):
                parse_source_material(
                    source_path,
                    output_root=output_root,
                    pdf_parser="paddleocr-vl",
                    paddleocr_vl_settings=PaddleOCRVLSettings(
                        device="progress-cpu",
                        isolated_process=False,
                    ),
                    progress=events.append,
                )

            phases = [event.phase for event in events]
            self.assertIn("start", phases)
            self.assertIn("render", phases)
            self.assertIn("load", phases)
            self.assertIn("ocr", phases)
            self.assertIn("write", phases)
            self.assertEqual(phases[-1], "complete")

            render_done = next(
                event
                for event in events
                if event.phase == "render" and event.completed_pages == event.total_pages
            )
            self.assertEqual(render_done.completed_pages, 1)
            self.assertEqual(render_done.total_pages, 1)

            ocr_done = next(
                event
                for event in events
                if event.phase == "ocr" and event.completed_pages == event.total_pages
            )
            self.assertEqual(ocr_done.detail, "recognized page 1/1")

    def test_paddleocr_vl_sidecar_merges_incremental_page_results(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            worker_script = root / "fake_sidecar_worker.py"
            terminated_marker = root / "terminated.txt"
            write_sample_pdf(source_path)
            worker_script.write_text(
                textwrap.dedent(
                    """
                    from __future__ import annotations

                    import argparse
                    import json
                    import signal
                    import sys
                    import time
                    from pathlib import Path


                    def write_json(path: Path, payload: object) -> None:
                        temporary_path = path.with_suffix(path.suffix + ".tmp")
                        temporary_path.write_text(json.dumps(payload), encoding="utf-8")
                        temporary_path.replace(path)


                    parser = argparse.ArgumentParser()
                    parser.add_argument("--job-json", type=Path, required=True)
                    parser.add_argument("--output-dir", type=Path, required=True)
                    parser.add_argument("--terminated-marker", type=Path, required=True)
                    args = parser.parse_args()


                    def terminate(signum: int, frame: object) -> None:
                        args.terminated_marker.write_text("terminated", encoding="utf-8")
                        raise SystemExit(0)


                    signal.signal(signal.SIGTERM, terminate)
                    job = json.loads(args.job_json.read_text(encoding="utf-8"))
                    args.output_dir.mkdir(parents=True, exist_ok=True)
                    for page_job in job["pages"]:
                        page = int(page_job["page"])
                        write_json(
                            args.output_dir / f"page-{page:04d}.json",
                            {
                                "page": page,
                                "input_image_path": f"assets/page-{page:04d}.png",
                                "markdown": f"Sidecar text from page {page}",
                                "raw_json": {"source": "fake-sidecar", "page": page},
                                "markdown_asset_paths": [],
                            },
                        )
                    write_json(args.output_dir / "complete.json", {"parser_version": "sidecar-test"})
                    while True:
                        time.sleep(1)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            def fake_sidecar_worker_command(
                *,
                settings: PaddleOCRVLSettings,
                settings_path: Path,
                job_path: Path,
                result_dir: Path,
                assets_dir: Path,
            ) -> list[str]:
                return [
                    sys.executable,
                    worker_script.as_posix(),
                    "--job-json",
                    job_path.as_posix(),
                    "--output-dir",
                    result_dir.as_posix(),
                    "--terminated-marker",
                    terminated_marker.as_posix(),
                ]

            with patch("wordbyword.source_parsing.paddleocr_vl._sidecar_worker_command", side_effect=fake_sidecar_worker_command):
                bundle = parse_source_material(
                    source_path,
                    output_root=output_root,
                    pdf_parser="paddleocr-vl",
                    paddleocr_vl_settings=PaddleOCRVLSettings(
                        sidecar_python=sys.executable,
                        sidecar_poll_seconds=0.05,
                    ),
                )

            content = (bundle.output_dir / "content.md").read_text(encoding="utf-8")
            self.assertIn("Sidecar text from page 1", content)
            self.assertFalse(list(bundle.output_dir.glob("page-*.json")))
            self.assertTrue(terminated_marker.is_file())

            parser_output = json.loads((bundle.output_dir / "parser_output.json").read_text(encoding="utf-8"))
            self.assertTrue(parser_output["complete"])
            self.assertEqual(parser_output["completed_pages"], 1)
            self.assertEqual(parser_output["parser"]["version"], "sidecar-test")

    def test_paddleocr_vl_interrupt_removes_partial_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)

            with patch("wordbyword.source_parsing.materials.run_paddleocr_vl", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    parse_source_material(
                        source_path,
                        output_root=output_root,
                        pdf_parser="paddleocr-vl",
                        paddleocr_vl_settings=PaddleOCRVLSettings(
                            device="cpu",
                            isolated_process=False,
                        ),
                    )

            self.assertFalse((output_root / "sample").exists())

    def test_paddleocr_vl_worker_interrupt_removes_partial_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source_path = root / "sample.pdf"
            output_root = root / "parsed"
            write_sample_pdf(source_path)

            with patch(
                "wordbyword.source_parsing.materials.run_paddleocr_vl",
                side_effect=PaddleOCRVLInterruptedError("worker stopped"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    parse_source_material(
                        source_path,
                        output_root=output_root,
                        pdf_parser="paddleocr-vl",
                        paddleocr_vl_settings=PaddleOCRVLSettings(
                            device="cpu",
                            isolated_process=False,
                        ),
                    )

            self.assertFalse((output_root / "sample").exists())

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


class FakePaddleOCRVL:
    init_kwargs: dict[str, object] | None = None
    predict_input: list[str] | None = None

    def __init__(self, **kwargs: object) -> None:
        FakePaddleOCRVL.init_kwargs = kwargs

    def predict(self, *, input: list[str], **kwargs: object) -> list["FakePaddleOCRVLResult"]:
        FakePaddleOCRVL.predict_input = input
        return [FakePaddleOCRVLResult(path) for path in input]


class FakePaddleOCRVLResult:
    def __init__(self, input_path: str) -> None:
        self.input_path = input_path

    @property
    def json(self) -> dict[str, object]:
        return {
            "input_path": self.input_path,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": f"PaddleOCR-VL text from {Path(self.input_path).name}",
                    "block_bbox": [10, 20, 100, 40],
                }
            ],
        }

    @property
    def markdown(self) -> dict[str, object]:
        return {
            "markdown_texts": f"PaddleOCR-VL text from {Path(self.input_path).name}",
            "markdown_images": {},
        }
