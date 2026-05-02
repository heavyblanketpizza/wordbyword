from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wordbyword.cli import main
from wordbyword.dictionary_build.llm import ChatMessage, CompletionResult, LLMConfigurationError
from wordbyword.class_rewriting.rewrite import (
    SourceRewriteError,
    build_context_packet,
    rewrite_source_material,
)


class SourceRewriteContextTest(unittest.TestCase):
    def test_context_packet_interleaves_asset_marker_before_image_block(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            parsed_root = Path(workspace) / "parsed"
            bundle_dir = write_parsed_fixture(parsed_root)

            packet = build_context_packet(bundle_dir)

            self.assertIn("source_id: lesson", packet.content_markdown)
            text_index = packet.content_markdown.index("The machine shows energy.")
            marker_index = packet.content_markdown.index('[[asset id="fig1"')
            image_index = packet.content_markdown.index('<img src="assets/fig1.png"')
            caption_index = packet.content_markdown.index("<div>Energy picture.</div>")
            self.assertLess(text_index, marker_index)
            self.assertLess(marker_index, image_index)
            self.assertLess(image_index, caption_index)
            self.assertIn('caption="Energy picture."', packet.content_markdown)

    def test_rewrite_sends_attached_images_and_asset_ids(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            output_root = root / "rewritten"
            bundle_dir = write_parsed_fixture(parsed_root)
            client = RecordingRewriteClient()

            outputs = rewrite_source_material(
                bundle_dir,
                output_root=output_root,
                client=client,
                model="fake-vision",
            )

            self.assertEqual(outputs.failed_page_count, 0)
            self.assertEqual(client.preflight_count, 1)
            first_user_message = client.calls[0][1][1]
            self.assertIn("asset_id=fig1", first_user_message.content)
            self.assertIn("asset_id=page-0001", first_user_message.content)
            self.assertEqual(first_user_message.images[0], base64.b64encode(b"fig1").decode("ascii"))
            self.assertEqual(first_user_message.images[1], base64.b64encode(b"page1").decode("ascii"))

    def test_unsupported_image_model_fails_before_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            bundle_dir = write_parsed_fixture(Path(workspace) / "parsed")

            with self.assertRaises(LLMConfigurationError):
                rewrite_source_material(
                    bundle_dir,
                    output_root=Path(workspace) / "rewritten",
                    client=UnsupportedVisionClient(),
                    model="text-only",
                )

    def test_every_page_request_receives_full_document_context(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            bundle_dir = write_parsed_fixture(
                Path(workspace) / "parsed",
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            client = RecordingRewriteClient()

            rewrite_source_material(
                bundle_dir,
                output_root=Path(workspace) / "rewritten",
                client=client,
                model="fake-vision",
            )

            first_page_prompt = client.calls[0][1][1].content
            second_page_prompt = client.calls[1][1][1].content
            self.assertIn("Second page source explains the ending.", first_page_prompt)
            self.assertIn("The machine shows energy.", second_page_prompt)
            self.assertIn("current_page: 1", first_page_prompt)
            self.assertIn("current_page: 2", second_page_prompt)

    def test_rewritten_markdown_preserves_page_markers_and_asset_links(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_parsed_fixture(
                root / "parsed",
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )

            outputs = rewrite_source_material(
                bundle_dir,
                output_root=root / "rewritten",
                client=RecordingRewriteClient(),
                model="fake-vision",
            )

            content = (outputs.output_dir / "content.md").read_text(encoding="utf-8")
            self.assertIn("<!-- page:1 -->", content)
            self.assertIn("<!-- page:2 -->", content)
            self.assertIn('<img src="assets/fig1.png"', content)
            self.assertIn("어린이가 읽기 쉬운 첫 쪽입니다.", content)
            self.assertIn("---", content)
            chunks = json.loads((outputs.output_dir / "chunks.json").read_text(encoding="utf-8"))
            self.assertEqual(chunks[0]["status"], "rewritten")
            self.assertEqual(chunks[1]["status"], "rewritten")


class SourceRewriteCliTest(unittest.TestCase):
    def test_cli_rewrites_all_parsed_source_materials(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            output_root = root / "rewritten"
            write_parsed_fixture(parsed_root)
            write_skipped_fixture(parsed_root / "sample")
            client = RecordingRewriteClient()

            with patch("wordbyword.cli.app.ollama_client", return_value=client):
                with patch("sys.stdout", io.StringIO()):
                    exit_code = main(
                        [
                            "--rewrite-source-materials",
                            str(parsed_root),
                            "--rewrite-output-root",
                            str(output_root),
                            "--ollama-model",
                            "fake-vision",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_root / "lesson" / "content.md").is_file())
            self.assertTrue((output_root / "lesson" / "context_packet.md").is_file())
            self.assertTrue((output_root / "lesson" / "assets" / "fig1.png").is_file())


class RecordingRewriteClient:
    def __init__(self) -> None:
        self.preflight_count = 0
        self.calls: list[tuple[dict[str, object], list[ChatMessage]]] = []

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
        page = 1 if "current_page: 1" in messages[1].content else 2
        image_block = (
            '\n\n<div><img src="assets/fig1.png" alt="Figure" /></div>\n\n'
            if page == 1
            else "\n\n"
        )
        rewritten = (
            f"# 쉬운 과학 이야기\n\n어린이가 읽기 쉬운 {'첫' if page == 1 else '둘째'} 쪽입니다."
            f"{image_block}그림도 함께 봅니다."
        )
        return CompletionResult(
            provider="ollama",
            model="fake-vision",
            content=json.dumps(
                {
                    "source_id": "lesson",
                    "page": page,
                    "rewritten_markdown": rewritten,
                },
                ensure_ascii=False,
            ),
        )


class UnsupportedVisionClient(RecordingRewriteClient):
    def assert_supports_images(self) -> None:
        raise LLMConfigurationError("no vision")


def write_parsed_fixture(
    parsed_root: Path,
    *,
    source_id: str = "lesson",
    pages: list[str] | None = None,
) -> Path:
    bundle_dir = parsed_root / source_id
    assets_dir = bundle_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "fig1.png").write_bytes(b"fig1")
    (assets_dir / "page-0001.png").write_bytes(b"page1")
    page_texts = pages or [page_one_text()]
    (bundle_dir / "content.md").write_text("\n\n---\n\n".join(page_texts), encoding="utf-8")
    (bundle_dir / "chunks.json").write_text(
        json.dumps(
            [
                {
                    "page": index,
                    "text": page_text,
                    "metadata": {"markdown_asset_paths": ["assets/fig1.png"] if index == 1 else []},
                }
                for index, page_text in enumerate(page_texts, start=1)
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "wordbyword.source_material_workspace.v1",
                "source": {
                    "id": source_id,
                    "path": f"{source_id}.pdf",
                    "filename": f"{source_id}.pdf",
                    "sha256": "sha",
                },
                "parser": {"status": "parsed", "name": "test-parser", "version": "1"},
                "files": {
                    "assets": "assets/",
                    "content_markdown": "content.md",
                    "chunks_json": "chunks.json",
                    "quality_report_json": "quality_report.json",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "quality_report.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "id": "fig1",
                        "path": "assets/fig1.png",
                        "kind": "extracted_image",
                        "page": 1,
                        "width": 10,
                        "height": 20,
                        "mime_type": "image/png",
                        "byte_size": 4,
                    },
                    {
                        "id": "page-0001",
                        "path": "assets/page-0001.png",
                        "kind": "page_render",
                        "page": 1,
                        "width": 100,
                        "height": 200,
                        "mime_type": "image/png",
                        "byte_size": 5,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return bundle_dir


def write_skipped_fixture(bundle_dir: Path) -> None:
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source": {"id": "sample"},
                "parser": {"status": "skipped"},
                "files": {},
            }
        ),
        encoding="utf-8",
    )


def page_one_text() -> str:
    return (
        "# Science\n\n"
        "The machine shows energy.\n\n"
        '<div><img src="assets/fig1.png" alt="Figure" /></div>\n\n'
        "<div>Energy picture.</div>\n\n"
        "The final sentence stays after the caption."
    )
