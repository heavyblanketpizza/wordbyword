from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from PIL import Image

from wordbyword.classroom_printout_studio.class_printing.printable import (
    DEFAULT_PRINT_OUTPUT_ROOT,
    PrintableMaterialError,
    PrintableProgress,
    build_printable_context,
    build_printable_prompt,
    parse_and_validate_print_plan_response,
    print_class_material,
    print_class_materials,
)
from wordbyword.cli import main
from wordbyword.dictionary_build.llm import ChatMessage, CompletionResult


class PrintableContextTest(unittest.TestCase):
    def test_default_print_outputs_use_separate_printable_root(self) -> None:
        self.assertEqual(
            DEFAULT_PRINT_OUTPUT_ROOT,
            Path("classroom-printout-studio/printable-for-class") / "grade1-ko-a4-ollama",
        )

    def test_prompt_uses_rewritten_content_and_asset_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            bundle_dir = write_rewritten_fixture(Path(workspace) / "rewritten")

            context = build_printable_context(bundle_dir)
            prompt = build_printable_prompt(context)

            self.assertEqual(context.source_id, "lesson")
            self.assertIn("asset_catalog", prompt.user_prompt)
            self.assertIn('"asset_id": "fig1"', prompt.user_prompt)
            self.assertIn("rewritten_content_markdown", prompt.user_prompt)
            self.assertIn("어린이가 읽기 쉬운 과학 이야기입니다.", prompt.user_prompt)
            self.assertIn("Do not treat this as a one-page limit", prompt.user_prompt)
            self.assertIn("Preserve the full rewritten reading content", prompt.user_prompt)
            self.assertIn("visual_candidate_asset_ids", prompt.user_prompt)
            self.assertIn("copy one exact string from visual_candidate_asset_ids", prompt.user_prompt)
            self.assertIn("Do not use asset path values", prompt.user_prompt)
            self.assertIn("Never use an asset path, image filename", prompt.messages[0].content)
            self.assertIn("Return JSON exactly in this shape", prompt.user_prompt)
            image_messages = [message for message in prompt.messages if message.images]
            self.assertEqual(len(image_messages), 1)
            self.assertIn("asset_id: fig1", image_messages[0].content)
            self.assertEqual(prompt.attached_assets[0].asset_id, "fig1")
            self.assertNotIn("page-0001", [asset.asset_id for asset in prompt.attached_assets])
            self.assertTrue(all("images" not in row for row in prompt.prompt_messages))
            self.assertNotIn(image_messages[0].images[0], json.dumps(prompt.prompt_messages, ensure_ascii=False))
            with Image.open(io.BytesIO(base64.b64decode(image_messages[0].images[0]))) as preview:
                self.assertLessEqual(max(preview.size), 768)

    def test_prompt_uses_page_renders_only_as_visual_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            bundle_dir = write_rewritten_fixture(Path(workspace) / "rewritten", include_extracted_asset=False)

            prompt = build_printable_prompt(build_printable_context(bundle_dir))

            self.assertEqual([asset.asset_id for asset in prompt.attached_assets], ["page-0001"])


class PrintableMaterialTest(unittest.TestCase):
    def test_print_class_material_writes_docx_and_audit_artifacts(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_rewritten_fixture(root / "rewritten", target_grade="grade10")
            output_root = root / "printable"
            client = RecordingPrintableClient()
            progress_events: list[PrintableProgress] = []

            outputs = print_class_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model="fake-ollama",
                target_grade="grade10",
                font_path=font_path,
                progress=progress_events.append,
            )

            self.assertTrue(outputs.accepted)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "plan", "write", "complete"],
            )
            self.assertTrue(progress_events[-1].accepted)
            self.assertEqual(progress_events[-1].selected_image_count, 1)
            self.assertEqual(progress_events[-1].body_font_size_pt, 13)
            self.assertEqual(client.preflight_count, 1)
            output_dir = output_root / "lesson"
            for filename in (
                "print_plan.json",
                "content.md",
                "material.docx",
                "attempts.jsonl",
                "failures.jsonl",
                "quality_report.json",
                "manifest.json",
            ):
                self.assertTrue((output_dir / filename).is_file(), filename)
            self.assertFalse((output_dir / "material.pdf").exists())
            self.assertTrue((output_dir / "assets" / "fig1.png").is_file())

            docx = Document(str(output_dir / "material.docx"))
            paragraphs = "\n".join(paragraph.text for paragraph in docx.paragraphs)
            self.assertIn("쉬운 과학 이야기", paragraphs)
            self.assertIn("어린이가 읽기 쉬운 첫 문장입니다.", paragraphs)
            self.assertGreaterEqual(len(docx.inline_shapes), 1)

            quality = json.loads((output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["target_grade"], "grade10")
            self.assertEqual(quality["target_profile"], "grade10-ko")
            self.assertEqual(quality["status"], "written")
            self.assertEqual(quality["selected_image_count"], 1)
            self.assertEqual(quality["output_formats"], ["docx"])
            self.assertTrue(quality["requires_human_revision"])
            self.assertEqual(quality["revision_target"], "material.docx")
            self.assertNotIn("pdf_page_count", quality)
            self.assertEqual(quality["asset_mode"], "vision-candidate-images")
            self.assertEqual(quality["attached_image_count"], 1)
            self.assertEqual(quality["visual_candidate_count"], 1)
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("pdf", manifest["files"])
            self.assertEqual(manifest["files"]["docx"], "material.docx")
            self.assertEqual(manifest["printable"]["target_grade"], "grade10")
            self.assertEqual(manifest["printable"]["target_profile"], "grade10-ko")
            self.assertTrue(manifest["printable"]["requires_human_revision"])
            self.assertEqual(manifest["printable"]["asset_mode"], "vision-candidate-images")
            attempts = read_jsonl(output_dir / "attempts.jsonl")
            self.assertTrue(attempts[0]["accepted"])
            self.assertEqual(attempts[0]["target_grade"], "grade10")
            self.assertIn("고등학교 1학년", attempts[0]["system_prompt"])
            self.assertEqual(attempts[0]["selected_asset_ids"], ["fig1"])
            self.assertEqual(attempts[0]["attached_asset_ids"], ["fig1"])
            self.assertEqual(attempts[0]["visual_candidate_count"], 1)
            self.assertTrue(all("images" not in row for row in attempts[0]["prompt_messages"]))
            image_payload = next(message.images[0] for message in client.calls[0][1] if message.images)
            self.assertNotIn(image_payload, json.dumps(attempts[0], ensure_ascii=False))

    def test_invalid_print_plan_is_rejected_and_logged_without_material_files(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_rewritten_fixture(root / "rewritten")
            output_root = root / "printable"
            client = RecordingPrintableClient(
                response={
                    "source_id": "lesson",
                    "title": "쉬운 과학 이야기",
                    "subtitle": "",
                    "body_font_size_pt": 13,
                    "sections": [{"heading": "이야기", "paragraphs": ["어린이가 읽는 글입니다."]}],
                    "selected_images": [
                        {
                            "asset_id": "missing",
                            "placement": "after_title",
                            "caption": "",
                            "alt_text": "",
                        }
                    ],
                }
            )
            progress_events: list[PrintableProgress] = []

            outputs = print_class_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model="fake-ollama",
                font_path=font_path,
                progress=progress_events.append,
            )

            self.assertFalse(outputs.accepted)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "plan", "write", "complete"],
            )
            self.assertFalse(progress_events[-1].accepted)
            self.assertIn("unknown asset_id", progress_events[-1].failure_reason)
            output_dir = output_root / "lesson"
            self.assertFalse((output_dir / "material.pdf").exists())
            self.assertFalse((output_dir / "material.docx").exists())
            failures = read_jsonl(output_dir / "failures.jsonl")
            self.assertIn("unknown asset_id", failures[0]["failure_reason"])
            quality = json.loads((output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["status"], "failed")
            self.assertEqual(quality["output_formats"], [])
            self.assertFalse(quality["requires_human_revision"])

    def test_print_class_materials_reports_skipped_sources_with_indices(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            rewritten_root = root / "rewritten"
            write_rewritten_fixture(rewritten_root, source_id="lesson")
            (rewritten_root / "missing-manifest").mkdir(parents=True)
            progress_events: list[PrintableProgress] = []

            outputs = print_class_materials(
                rewritten_root,
                output_root=root / "printable",
                client=RecordingPrintableClient(),
                model="fake-ollama",
                font_path=font_path,
                progress=progress_events.append,
            )

            self.assertEqual(len(outputs.source_outputs), 1)
            self.assertEqual(len(outputs.skipped_sources), 1)
            skip_events = [event for event in progress_events if event.phase == "skip"]
            self.assertEqual(len(skip_events), 1)
            self.assertEqual(skip_events[0].source_id, "missing-manifest")
            self.assertEqual(skip_events[0].source_index, 2)
            self.assertEqual(skip_events[0].total_sources, 2)

    def test_print_class_material_fails_when_visual_candidates_need_non_vision_client(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_rewritten_fixture(root / "rewritten")
            output_root = root / "printable"

            outputs = print_class_material(
                bundle_dir,
                output_root=output_root,
                client=NonVisionPrintableClient(),
                model="fake-ollama",
                font_path=font_path,
            )

            self.assertFalse(outputs.accepted)
            failures = read_jsonl(output_root / "lesson" / "failures.jsonl")
            self.assertIn("verify image support", failures[0]["failure_reason"])

    def test_print_plan_validation_rejects_bad_shapes_and_worksheet_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            context = build_printable_context(write_rewritten_fixture(Path(workspace) / "rewritten"))
            base = {
                "source_id": "lesson",
                "title": "쉬운 과학 이야기",
                "subtitle": "",
                "body_font_size_pt": 13,
                "sections": [{"heading": "이야기", "paragraphs": ["어린이가 읽는 글입니다."]}],
                "selected_images": [],
            }
            scenarios = [
                ("wrong source", {**base, "source_id": "other"}, "source_id"),
                ("bad font size", {**base, "body_font_size_pt": 16}, "schema validation"),
                (
                    "too many images",
                    {
                        **base,
                        "selected_images": [
                            {"asset_id": "fig1"},
                            {"asset_id": "page-0001"},
                            {"asset_id": "fig1"},
                        ],
                    },
                    "schema validation",
                ),
                (
                    "worksheet heading",
                    {**base, "sections": [{"heading": "Questions", "paragraphs": ["어린이가 읽는 글입니다."]}]},
                    "worksheet-like",
                ),
                (
                    "raw image markup",
                    {**base, "sections": [{"heading": "이야기", "paragraphs": ["![그림](assets/fig1.png)"]}]},
                    "raw image markup",
                ),
                ("markdown fence", "```json\n{}\n```", "markdown fence"),
            ]

            for name, payload, expected in scenarios:
                with self.subTest(name=name):
                    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                    with self.assertRaises(PrintableMaterialError) as error:
                        parse_and_validate_print_plan_response(
                            CompletionResult(provider="ollama", model="fake", content=content),
                            context=context,
                        )
                    self.assertIn(expected, str(error.exception))

    def test_print_plan_rejects_existing_asset_that_was_not_visually_attached(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            context = build_printable_context(write_rewritten_fixture(Path(workspace) / "rewritten"))
            payload = {
                "source_id": "lesson",
                "title": "쉬운 과학 이야기",
                "subtitle": "",
                "body_font_size_pt": 13,
                "sections": [{"heading": "이야기", "paragraphs": ["어린이가 읽는 글입니다."]}],
                "selected_images": [{"asset_id": "page-0001"}],
            }

            with self.assertRaises(PrintableMaterialError) as error:
                parse_and_validate_print_plan_response(
                    CompletionResult(
                        provider="ollama",
                        model="fake",
                        content=json.dumps(payload, ensure_ascii=False),
                    ),
                    context=context,
                )

            self.assertIn("not attached as a visual candidate", str(error.exception))


class PrintableCliTest(unittest.TestCase):
    def test_cli_prints_one_class_material_without_running_dictionary_pipeline(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_rewritten_fixture(root / "rewritten")
            output_root = root / "printable"
            client = RecordingPrintableClient()

            stdout = io.StringIO()
            with (
                patch("sys.stdout", stdout),
                patch("wordbyword.application.use_cases.build_provider_client", return_value=client),
                patch("wordbyword.application.use_cases.build_resumable_dictionary") as mock_pipeline,
            ):
                exit_code = main(
                    [
                        "--print-class-material",
                        str(bundle_dir),
                        "--print-output-root",
                        str(output_root),
                        "--print-font-path",
                        str(font_path),
                        "--ollama-model",
                        "fake-ollama",
                        "--target-grade",
                        "grade1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertTrue((output_root / "lesson" / "material.docx").is_file())
            self.assertFalse((output_root / "lesson" / "material.pdf").exists())
            self.assertIn("printable class material written", stdout.getvalue())
            self.assertNotIn("pdf:", stdout.getvalue())

    def test_cli_prints_all_class_materials_without_running_dictionary_pipeline(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            rewritten_root = root / "rewritten"
            write_rewritten_fixture(rewritten_root, source_id="lesson")
            write_rewritten_fixture(rewritten_root, source_id="lesson-two")
            output_root = root / "printable"
            client = RecordingPrintableClient()

            stdout = io.StringIO()
            with (
                patch("sys.stdout", stdout),
                patch("wordbyword.application.use_cases.build_provider_client", return_value=client),
                patch("wordbyword.application.use_cases.build_resumable_dictionary") as mock_pipeline,
            ):
                exit_code = main(
                    [
                        "--print-class-materials",
                        str(rewritten_root),
                        "--print-output-root",
                        str(output_root),
                        "--print-font-path",
                        str(font_path),
                        "--ollama-model",
                        "fake-ollama",
                        "--target-grade",
                        "grade1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            self.assertTrue((output_root / "lesson" / "material.docx").is_file())
            self.assertTrue((output_root / "lesson-two" / "material.docx").is_file())
            self.assertIn("written: 2", stdout.getvalue())


class RecordingPrintableClient:
    def __init__(self, response: object | None = None) -> None:
        self.response = response
        self.calls: list[tuple[dict[str, object], list[ChatMessage]]] = []
        self.preflight_count = 0

    def assert_supports_images(self) -> None:
        self.preflight_count += 1

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        self.calls.append(
            (
                {
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    "json_mode": json_mode,
                },
                messages,
            )
        )
        source_id = source_id_from_prompt(messages[-1].content)
        content = self.response or {
            "source_id": source_id,
            "title": "쉬운 과학 이야기",
            "subtitle": "읽기 자료",
            "body_font_size_pt": 13,
            "sections": [
                {
                    "heading": "이야기",
                    "paragraphs": [
                        "어린이가 읽기 쉬운 첫 문장입니다.",
                        "그림을 보며 내용을 천천히 읽습니다.",
                    ],
                }
            ],
            "selected_images": [
                {
                    "asset_id": "fig1",
                    "placement": "after_title",
                    "caption": "그림을 함께 봅니다.",
                    "alt_text": "과학 그림",
                }
            ],
        }
        return CompletionResult(
            provider="ollama",
            model="fake-ollama",
            content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        )


class NonVisionPrintableClient:
    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> CompletionResult:
        raise AssertionError("complete should not be called without printable image preflight")


def write_rewritten_fixture(
    root: Path,
    *,
    source_id: str = "lesson",
    target_grade: str = "grade1",
    include_extracted_asset: bool = True,
) -> Path:
    bundle_dir = root / source_id
    assets_dir = bundle_dir / "assets"
    assets_dir.mkdir(parents=True)
    if include_extracted_asset:
        Image.new("RGB", (160, 100), color=(230, 240, 250)).save(assets_dir / "fig1.png")
    Image.new("RGB", (120, 160), color=(240, 240, 230)).save(assets_dir / "page-0001.png")
    (bundle_dir / "content.md").write_text(
        "\n".join(
            [
                "<!-- page:1 -->",
                "# 쉬운 과학 이야기",
                "",
                "어린이가 읽기 쉬운 과학 이야기입니다.",
                "",
                '<div><img src="assets/fig1.png" alt="Figure" /></div>' if include_extracted_asset else "",
                "",
                "그림도 함께 봅니다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "chunks.json").write_text(
        json.dumps(
            [
                {
                    "page": 1,
                    "status": "rewritten",
                    "source_text": "source",
                    "rewritten_markdown": "어린이가 읽기 쉬운 과학 이야기입니다.",
                    "assets": [
                        {
                            "asset_id": "fig1",
                            "path": "assets/fig1.png",
                            "kind": "extracted_image",
                            "page": 1,
                            "width": 160,
                            "height": 100,
                            "mime_type": "image/png",
                            "byte_size": (assets_dir / "fig1.png").stat().st_size,
                            "caption": "과학 그림",
                        }
                    ]
                    if include_extracted_asset
                    else [],
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "wordbyword.source_rewrite.v1",
                "source": {
                    "id": source_id,
                    "filename": f"{source_id}.pdf",
                    "sha256": "sha",
                    "target_grade": target_grade,
                },
                "parser": {"name": "test-parser", "version": "1"},
                "rewrite": {
                    "target_grade": target_grade,
                    "target_profile": f"{target_grade}-ko",
                    "mode": "vocabulary_guided",
                    "model": "fake-ollama",
                },
                "files": {
                    "assets": "assets/",
                    "content_markdown": "content.md",
                    "chunks_json": "chunks.json",
                    "quality_report_json": "quality_report.json",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "quality_report.json").write_text(
        json.dumps({"schema_version": "wordbyword.source_rewrite.v1"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return bundle_dir


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def source_id_from_prompt(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("source_id:"):
            return line.split(":", 1)[1].strip()
    return "lesson"


def korean_font_or_skip(test_case: unittest.TestCase) -> Path:
    candidates = [
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
        Path("/Library/Fonts/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    test_case.skipTest("No Korean-capable font file available for printable rendering test.")
