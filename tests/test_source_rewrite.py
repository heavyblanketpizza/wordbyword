from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wordbyword.cli import main
from wordbyword.dictionary_build.llm import ChatMessage, CompletionResult, LLMConfigurationError, LLMResponseError
from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
    SourceRewriteProgress,
    SourceRewriteError,
    VocabularyCandidate,
    build_context_packet,
    load_vocabulary_guided_candidates,
    parse_and_validate_vocabulary_selection_response,
    rewrite_vocabulary_guided_source_material,
    rewrite_whole_source_material,
    rewrite_whole_source_materials,
)


class SourceRewriteContextTest(unittest.TestCase):
    def test_default_rewrite_outputs_use_rewritten_for_class_root(self) -> None:
        self.assertEqual(
            DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
            Path("classroom-printout-studio/rewritten-for-class") / "grade1-ko-ollama-whole-document",
        )
        self.assertEqual(
            DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
            Path("classroom-printout-studio/rewritten-for-class") / "grade1-ko-ollama-vocabulary-guided",
        )

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


class WholeDocumentRewriteTest(unittest.TestCase):
    def test_whole_document_rewrite_sends_one_request_with_full_context(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_parsed_fixture(
                root / "parsed",
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            client = RecordingWholeRewriteClient()
            progress_events: list[SourceRewriteProgress] = []

            outputs = rewrite_whole_source_material(
                bundle_dir,
                output_root=root / "rewritten-whole",
                client=client,
                model="fake-vision",
                progress=progress_events.append,
            )

            self.assertEqual(outputs.failed_page_count, 0)
            self.assertEqual(outputs.rewritten_page_count, 2)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "rewrite", "write", "complete"],
            )
            self.assertEqual(progress_events[-1].rewritten_pages, 2)
            self.assertEqual(progress_events[-1].failed_pages, 0)
            self.assertEqual(client.preflight_count, 1)
            self.assertEqual(len(client.calls), 1)
            messages = client.calls[0][1]
            joined_prompt = joined_message_content(messages)
            image_messages = [message for message in messages if message.images]
            self.assertIn("The machine shows energy.", joined_prompt)
            self.assertIn("Second page source explains the ending.", joined_prompt)
            self.assertIn("<!-- page:1 -->", joined_prompt)
            self.assertIn("<!-- page:2 -->", joined_prompt)
            self.assertNotIn("asset_id=fig1", joined_prompt)
            self.assertNotIn("# Source Context Packet", joined_prompt)
            self.assertNotIn("[[asset", joined_prompt)
            self.assertEqual(len(image_messages), 1)
            self.assertEqual(image_messages[0].images, (base64.b64encode(b"fig1").decode("ascii"),))

            content = (outputs.output_dir / "content.md").read_text(encoding="utf-8")
            self.assertIn("<!-- page:1 -->", content)
            self.assertIn("<!-- page:2 -->", content)
            self.assertIn('<img src="assets/fig1.png"', content)
            self.assertIn("전체 문서를 한 번에 쉽게 쓴 첫 쪽입니다.", content)
            self.assertTrue((outputs.output_dir / "context_packet.md").is_file())
            self.assertTrue((outputs.output_dir / "attempts.jsonl").is_file())
            self.assertTrue((outputs.output_dir / "failures.jsonl").is_file())
            self.assertTrue((outputs.output_dir / "assets" / "fig1.png").is_file())

            chunks = json.loads((outputs.output_dir / "chunks.json").read_text(encoding="utf-8"))
            self.assertEqual([chunk["page"] for chunk in chunks], [1, 2])
            self.assertEqual([chunk["status"] for chunk in chunks], ["rewritten", "rewritten"])
            self.assertIn("둘째 쪽입니다.", chunks[1]["rewritten_markdown"])

            manifest = json.loads((outputs.output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["rewrite"]["mode"], "whole_document")
            self.assertEqual(manifest["rewrite"]["asset_mode"], "content-linked-interleaved-images")
            self.assertEqual(manifest["rewrite"]["attached_image_count"], 1)
            quality = json.loads((outputs.output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["rewrite_mode"], "whole_document")
            self.assertEqual(quality["failed_page_count"], 0)
            self.assertEqual(quality["asset_mode"], "content-linked-interleaved-images")
            self.assertEqual(quality["attached_image_count"], 1)

    def test_whole_document_rewrite_records_preflight_failure(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_parsed_fixture(root / "parsed")

            outputs = rewrite_whole_source_material(
                bundle_dir,
                output_root=root / "rewritten-whole",
                client=UnsupportedVisionClient(),
                model="text-only",
            )

            self.assertEqual(outputs.rewritten_page_count, 0)
            self.assertEqual(outputs.failed_page_count, 1)
            self.assertEqual(len(outputs.failure_rows), 1)
            self.assertIn("no vision", outputs.failure_rows[0]["failure_reason"])
            failures = (outputs.output_dir / "failures.jsonl").read_text(encoding="utf-8")
            self.assertIn("no vision", failures)

    def test_whole_document_rewrite_rejects_invalid_outputs(self) -> None:
        scenarios = [
            (
                "missing marker",
                {
                    "source_id": "lesson",
                    "rewritten_markdown": (
                        '<!-- page:1 -->\n첫 쪽입니다.\n\n'
                        '<div><img src="assets/fig1.png" alt="Figure" /></div>'
                    ),
                },
                "omitted page marker",
            ),
            (
                "missing asset",
                {
                    "source_id": "lesson",
                    "rewritten_markdown": (
                        "<!-- page:1 -->\n첫 쪽입니다.\n\n"
                        "<!-- page:2 -->\n둘째 쪽입니다."
                    ),
                },
                "omitted asset reference",
            ),
            ("malformed json", "{not json", "Expected a JSON object"),
        ]
        for name, response, expected_failure in scenarios:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as workspace:
                    root = Path(workspace)
                    bundle_dir = write_parsed_fixture(
                        root / "parsed",
                        pages=[
                            page_one_text(),
                            "# Later page\n\nSecond page source explains the ending.",
                        ],
                    )
                    client = RecordingWholeRewriteClient(response=response)

                    outputs = rewrite_whole_source_material(
                        bundle_dir,
                        output_root=root / "rewritten-whole",
                        client=client,
                        model="fake-vision",
                    )

                    self.assertEqual(outputs.rewritten_page_count, 0)
                    self.assertEqual(outputs.failed_page_count, 2)
                    self.assertEqual(len(client.calls), 1)
                    self.assertIn(expected_failure, outputs.failure_rows[0]["failure_reason"])

    def test_whole_document_rewrite_batch_reports_skipped_sources_with_indices(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            write_parsed_fixture(parsed_root)
            write_skipped_fixture(parsed_root / "sample")
            progress_events: list[SourceRewriteProgress] = []

            outputs = rewrite_whole_source_materials(
                parsed_root,
                output_root=root / "rewritten-whole",
                client=RecordingWholeRewriteClient(),
                model="fake-vision",
                progress=progress_events.append,
            )

            self.assertEqual(len(outputs.source_outputs), 1)
            self.assertEqual(len(outputs.skipped_sources), 1)
            skip_events = [event for event in progress_events if event.phase == "skip"]
            self.assertEqual(len(skip_events), 1)
            self.assertEqual(skip_events[0].source_id, "sample")
            self.assertEqual(skip_events[0].source_index, 2)
            self.assertEqual(skip_events[0].total_sources, 2)


class VocabularyGuidedRewriteTest(unittest.TestCase):
    def test_dictionary_candidates_rank_by_roi_or_learning_signal_and_cap_at_top_250(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dictionary_path = Path(workspace) / "15_final_dictionary.jsonl"
            rows = [
                {
                    "canonical_key": "missing-example",
                    "example": "",
                    "learning_signal_score": 0.5,
                    "roi_score": 1.0,
                    "count": 999,
                },
                {
                    "canonical_key": "roi-wins",
                    "example": "선물 예문입니다.",
                    "learning_signal_score": -0.4,
                    "roi_score": 1.0,
                    "count": 1,
                },
                {
                    "canonical_key": "learning-only",
                    "example": "학교 예문입니다.",
                    "learning_signal_score": 0.49,
                    "count": 1,
                },
                *[
                    {
                        "canonical_key": f"단어{index:03d}",
                        "example": f"단어{index:03d} 예문입니다.",
                        "learning_signal_score": round(0.3 - (index * 0.001), 3),
                        "count": index,
                    }
                    for index in range(260)
                ],
            ]
            write_jsonl(dictionary_path, rows)

            candidates = load_vocabulary_guided_candidates(dictionary_path)

            self.assertEqual(len(candidates), 250)
            self.assertEqual(candidates[0].canonical_key, "roi-wins")
            self.assertEqual(candidates[1].canonical_key, "learning-only")
            self.assertNotIn("missing-example", {candidate.canonical_key for candidate in candidates})

    def test_live_lexicon_candidates_skip_inactive_rows_and_rank_by_roi(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            live_path = Path(workspace) / "grade1-live-lexicon-canonical.jsonl"
            write_jsonl(
                live_path,
                [
                    {
                        "canonical_key": "inactive",
                        "example": "쓰지 않을 예문입니다.",
                        "learning_signal_score": 0.99,
                        "roi_score": 1.0,
                        "active": False,
                        "class_count": 99,
                    },
                    {
                        "canonical_key": "tie-low-printout",
                        "example": "낮은 예문입니다.",
                        "learning_signal_score": 0.5,
                        "roi_score": 0.8,
                        "class_count": 4,
                        "class_printout_count": 1,
                    },
                    {
                        "canonical_key": "roi-wins",
                        "example": "높은 예문입니다.",
                        "learning_signal_score": 0.2,
                        "roi_score": 0.91,
                        "class_count": 1,
                    },
                    {
                        "canonical_key": "tie-high-printout",
                        "example": "높은 출력 예문입니다.",
                        "learning_signal_score": 0.5,
                        "roi_score": 0.8,
                        "class_count": 2,
                        "class_printout_count": 3,
                    },
                ],
            )

            candidates = load_vocabulary_guided_candidates(live_path)

            self.assertEqual(
                [candidate.canonical_key for candidate in candidates],
                ["roi-wins", "tie-high-printout", "tie-low-printout"],
            )
            self.assertEqual(candidates[1].class_printout_count, 3)
            self.assertNotIn("inactive", {candidate.canonical_key for candidate in candidates})

    def test_dictionary_candidates_reject_invalid_roi_scores(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dictionary_path = Path(workspace) / "15_final_dictionary.jsonl"
            write_jsonl(
                dictionary_path,
                [
                    {
                        "canonical_key": "학교",
                        "example": "학교 예문입니다.",
                        "learning_signal_score": 0.4,
                        "roi_score": -0.1,
                    }
                ],
            )

            with self.assertRaises(SourceRewriteError):
                load_vocabulary_guided_candidates(dictionary_path)

    def test_vocabulary_guided_rewrite_rejects_parsed_bundle_from_another_grade(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_parsed_fixture(root / "parsed", target_grade="grade1")
            dictionary_path = root / "15_final_dictionary.jsonl"
            write_dictionary_fixture(dictionary_path, count=20)

            with self.assertRaises(SourceRewriteError) as context:
                rewrite_vocabulary_guided_source_material(
                    bundle_dir,
                    dictionary_path=dictionary_path,
                    vocabulary_source_kind="dictionary_seed",
                    vocabulary_source_sha256="dictionary",
                    output_root=root / "rewritten",
                    client=RecordingVocabularyGuidedRewriteClient(),
                    model="fake-vision",
                    target_grade="grade7",
                )

            self.assertIn("does not match target grade grade7", str(context.exception))

    def test_vocabulary_selection_response_validation_rejects_bad_shapes(self) -> None:
        context = minimal_context_packet()
        candidates = [
            VocabularyCandidate("선물", "선물을 받았다.", 0.4, None, 0.9, 1),
            VocabularyCandidate("학교", "학교에 갔다.", 0.3, None, 0.8, 1),
        ]
        scenarios = [
            ("wrong source", {"source_id": "other", "selected_vocabulary": ["선물", "학교"]}),
            ("duplicate", {"source_id": "lesson", "selected_vocabulary": ["선물", "선물"]}),
            ("unknown", {"source_id": "lesson", "selected_vocabulary": ["선물", "바다"]}),
            ("wrong count", {"source_id": "lesson", "selected_vocabulary": ["선물"]}),
            ("malformed", "{not json"),
        ]

        for name, payload in scenarios:
            with self.subTest(name=name):
                content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                with self.assertRaises((SourceRewriteError, LLMResponseError)):
                    parse_and_validate_vocabulary_selection_response(
                        CompletionResult(provider="ollama", model="fake-vision", content=content),
                        context=context,
                        candidate_pool=candidates,
                    )

    def test_vocabulary_guided_rewrite_runs_selection_then_story_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_parsed_fixture(
                root / "parsed",
                target_grade="grade7",
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            dictionary_path = root / "15_final_dictionary.jsonl"
            write_dictionary_fixture(dictionary_path, count=20)
            client = RecordingVocabularyGuidedRewriteClient()
            progress_events: list[SourceRewriteProgress] = []

            outputs = rewrite_vocabulary_guided_source_material(
                bundle_dir,
                dictionary_path=dictionary_path,
                vocabulary_source_kind="live_lexicon",
                vocabulary_source_sha256="abc123",
                output_root=root / "rewritten-vocabulary",
                client=client,
                model="fake-vision",
                target_grade="grade7",
                progress=progress_events.append,
            )

            self.assertEqual(outputs.failed_page_count, 0)
            self.assertEqual(outputs.rewritten_page_count, 2)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "load", "select", "rewrite", "write", "complete"],
            )
            self.assertEqual(progress_events[-1].selected_vocabulary, 20)
            self.assertEqual(progress_events[-1].rewritten_pages, 2)
            self.assertEqual(client.preflight_count, 1)
            self.assertEqual(len(client.calls), 2)
            selection_prompt = joined_message_content(client.calls[0][1])
            rewrite_prompt = joined_message_content(client.calls[1][1])
            self.assertIn("candidate_vocabulary", selection_prompt)
            self.assertIn('"canonical_key": "선물"', selection_prompt)
            self.assertNotIn("selection_score", selection_prompt)
            self.assertIn("중학교 1학년 학생", selection_prompt)
            self.assertNotIn("Korean 1학년", selection_prompt)
            self.assertIn("selected_vocabulary", rewrite_prompt)
            self.assertIn('"canonical_key": "선물"', rewrite_prompt)
            self.assertIn("중학교 1학년 학생", rewrite_prompt)

            content = (outputs.output_dir / "content.md").read_text(encoding="utf-8")
            self.assertIn("<!-- page:1 -->", content)
            self.assertIn("<!-- page:2 -->", content)
            self.assertIn('<img src="assets/fig1.png"', content)
            self.assertTrue((outputs.output_dir / "selection_attempts.jsonl").is_file())
            selected = json.loads((outputs.output_dir / "selected_vocabulary.json").read_text(encoding="utf-8"))
            self.assertEqual(selected["target_grade"], "grade7")
            self.assertEqual(selected["target_profile"], "grade7-ko")
            self.assertEqual(selected["selected_count"], 20)
            self.assertEqual(
                selected["vocabulary_source"],
                {
                    "kind": "live_lexicon",
                    "path": str(dictionary_path),
                    "sha256": "abc123",
                },
            )
            self.assertEqual(selected["selected_vocabulary"][0]["canonical_key"], "선물")
            manifest = json.loads((outputs.output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["rewrite"]["target_grade"], "grade7")
            self.assertEqual(manifest["rewrite"]["target_profile"], "grade7-ko")
            self.assertEqual(manifest["rewrite"]["mode"], "vocabulary_guided")
            self.assertEqual(manifest["rewrite"]["vocabulary_guidance"]["vocabulary_source_kind"], "live_lexicon")
            self.assertEqual(manifest["rewrite"]["vocabulary_guidance"]["vocabulary_source_sha256"], "abc123")
            self.assertIn("selected_vocabulary_json", manifest["files"])
            quality = json.loads((outputs.output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["vocabulary_guidance"]["selected_count"], 20)

    def test_vocabulary_guided_rewrite_failure_does_not_run_rewrite_call(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            bundle_dir = write_parsed_fixture(root / "parsed")
            dictionary_path = root / "15_final_dictionary.jsonl"
            write_dictionary_fixture(dictionary_path, count=20)
            client = RecordingVocabularyGuidedRewriteClient(selection_payload={
                "source_id": "lesson",
                "selected_vocabulary": ["unknown"],
            })
            progress_events: list[SourceRewriteProgress] = []

            outputs = rewrite_vocabulary_guided_source_material(
                bundle_dir,
                dictionary_path=dictionary_path,
                output_root=root / "rewritten-vocabulary",
                client=client,
                model="fake-vision",
                progress=progress_events.append,
            )

            self.assertEqual(len(client.calls), 1)
            self.assertEqual(outputs.rewritten_page_count, 0)
            self.assertEqual(outputs.failed_page_count, 1)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "load", "select", "write", "complete"],
            )
            self.assertIn("Vocabulary selection failed", outputs.failure_rows[0]["failure_reason"])
            selection_attempts = read_jsonl(outputs.output_dir / "selection_attempts.jsonl")
            self.assertFalse(selection_attempts[0]["accepted"])


class SourceRewriteCliTest(unittest.TestCase):
    def test_cli_rejects_removed_page_rewrite_commands(self) -> None:
        for flag in ("--rewrite-source-material", "--rewrite-source-materials"):
            with self.subTest(flag=flag):
                stderr = io.StringIO()
                with patch("sys.stderr", stderr):
                    with self.assertRaises(SystemExit) as error:
                        main([flag, "classroom-printout-studio/class-source-materials/parsed"])

                self.assertEqual(error.exception.code, 2)
                self.assertIn(f"unrecognized arguments: {flag}", stderr.getvalue())

    def test_cli_rewrites_all_parsed_source_materials_as_whole_documents(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            output_root = root / "rewritten-whole"
            write_parsed_fixture(
                parsed_root,
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            write_skipped_fixture(parsed_root / "sample")
            client = RecordingWholeRewriteClient()

            with patch("wordbyword.cli.app.ollama_client", return_value=client):
                with patch("sys.stdout", io.StringIO()):
                    exit_code = main(
                        [
                            "--target-grade",
                            "grade1",
                            "--rewrite-whole-source-materials",
                            str(parsed_root),
                            "--rewrite-output-root",
                            str(output_root),
                            "--ollama-model",
                            "fake-vision",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(client.calls), 1)
            self.assertTrue((output_root / "lesson" / "content.md").is_file())
            self.assertTrue((output_root / "lesson" / "context_packet.md").is_file())
            self.assertTrue((output_root / "lesson" / "assets" / "fig1.png").is_file())

    def test_cli_rewrites_one_source_material_with_default_vocabulary_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            output_root = root / "rewritten-vocabulary"
            dictionary_output = root / "dictionary"
            dictionary_output.mkdir()
            bundle_dir = write_parsed_fixture(
                parsed_root,
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            write_dictionary_fixture(dictionary_output / "15_final_dictionary.jsonl", count=20)
            client = RecordingVocabularyGuidedRewriteClient()

            with (
                patch("wordbyword.cli.app.default_live_lexicon_path", return_value=root / "missing-live.jsonl"),
                patch("wordbyword.cli.app.ollama_client", return_value=client),
                patch("sys.stdout", io.StringIO()),
            ):
                exit_code = main(
                    [
                        "--rewrite-vocabulary-guided-source-material",
                        str(bundle_dir),
                        "--target-grade",
                        "grade1",
                        "--rewrite-output-root",
                        str(output_root),
                        "--output",
                        str(dictionary_output),
                        "--ollama-model",
                        "fake-vision",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(client.calls), 2)
            self.assertTrue((output_root / "lesson" / "content.md").is_file())
            selected = json.loads((output_root / "lesson" / "selected_vocabulary.json").read_text(encoding="utf-8"))
            self.assertEqual(
                selected["dictionary_path"],
                str(dictionary_output / "15_final_dictionary.jsonl"),
            )
            self.assertEqual(selected["vocabulary_source"]["kind"], "dictionary_seed")

    def test_cli_rewrites_one_source_material_defaults_to_live_lexicon_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            output_root = root / "rewritten-vocabulary"
            dictionary_output = root / "dictionary"
            dictionary_output.mkdir()
            bundle_dir = write_parsed_fixture(
                parsed_root,
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            write_dictionary_fixture(dictionary_output / "15_final_dictionary.jsonl", count=20)
            live_path = root / "live-grade-lexicon" / "grade1" / "canonical.jsonl"
            write_dictionary_fixture(live_path, count=20)
            client = RecordingVocabularyGuidedRewriteClient()

            with (
                patch("wordbyword.cli.app.default_live_lexicon_path", return_value=live_path),
                patch("wordbyword.cli.app.ollama_client", return_value=client),
                patch("sys.stdout", io.StringIO()),
            ):
                exit_code = main(
                    [
                        "--rewrite-vocabulary-guided-source-material",
                        str(bundle_dir),
                        "--target-grade",
                        "grade1",
                        "--rewrite-output-root",
                        str(output_root),
                        "--output",
                        str(dictionary_output),
                        "--ollama-model",
                        "fake-vision",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(client.calls), 2)
            selected = json.loads((output_root / "lesson" / "selected_vocabulary.json").read_text(encoding="utf-8"))
            self.assertEqual(selected["dictionary_path"], str(live_path))
            self.assertEqual(selected["vocabulary_source"]["kind"], "live_lexicon")

    def test_cli_rewrites_all_source_materials_with_explicit_vocabulary_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            parsed_root = root / "parsed"
            output_root = root / "rewritten-vocabulary"
            dictionary_path = root / "custom-dictionary.jsonl"
            write_parsed_fixture(
                parsed_root,
                pages=[
                    page_one_text(),
                    "# Later page\n\nSecond page source explains the ending.",
                ],
            )
            write_skipped_fixture(parsed_root / "sample")
            write_dictionary_fixture(dictionary_path, count=20)
            client = RecordingVocabularyGuidedRewriteClient()

            with patch("wordbyword.cli.app.ollama_client", return_value=client):
                with patch("sys.stdout", io.StringIO()):
                    exit_code = main(
                        [
                            "--target-grade",
                            "grade1",
                            "--rewrite-vocabulary-guided-source-materials",
                            str(parsed_root),
                            "--rewrite-output-root",
                            str(output_root),
                            "--rewrite-dictionary",
                            str(dictionary_path),
                            "--ollama-model",
                            "fake-vision",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(client.calls), 2)
            selected = json.loads((output_root / "lesson" / "selected_vocabulary.json").read_text(encoding="utf-8"))
            self.assertEqual(selected["dictionary_path"], str(dictionary_path))
            self.assertEqual(selected["vocabulary_source"]["kind"], "explicit")

    def test_cli_rejects_combined_rewrite_commands(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as error:
                main(
                    [
                        "--rewrite-whole-source-materials",
                        "classroom-printout-studio/class-source-materials/parsed",
                        "--rewrite-vocabulary-guided-source-materials",
                        "classroom-printout-studio/class-source-materials/parsed",
                    ]
                )

        self.assertEqual(error.exception.code, 2)
        self.assertIn("parse/rewrite/print source-material commands cannot be combined", stderr.getvalue())


class RecordingWholeRewriteClient:
    def __init__(self, response: object | None = None) -> None:
        self.preflight_count = 0
        self.calls: list[tuple[dict[str, object], list[ChatMessage]]] = []
        self.response = response

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
        if self.response is None:
            content = {
                "source_id": "lesson",
                "rewritten_markdown": (
                    "<!-- page:1 -->\n"
                    "# 쉬운 과학 이야기\n\n"
                    "전체 문서를 한 번에 쉽게 쓴 첫 쪽입니다.\n\n"
                    '<div><img src="assets/fig1.png" alt="Figure" /></div>\n\n'
                    "그림도 함께 봅니다.\n\n"
                    "---\n\n"
                    "<!-- page:2 -->\n"
                    "# 다음 쪽\n\n"
                    "둘째 쪽입니다. 앞의 내용을 이어서 설명합니다."
                ),
            }
        else:
            content = self.response
        return CompletionResult(
            provider="ollama",
            model="fake-vision",
            content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        )


class RecordingVocabularyGuidedRewriteClient:
    def __init__(self, selection_payload: object | None = None) -> None:
        self.preflight_count = 0
        self.calls: list[tuple[dict[str, object], list[ChatMessage]]] = []
        self.selection_payload = selection_payload

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
        if len(self.calls) == 1:
            content = self.selection_payload or {
                "source_id": "lesson",
                "selected_vocabulary": [
                    {
                        "canonical_key": canonical_key,
                        "reason": "source relevant",
                        "role": "metaphor" if canonical_key == "선물" else "support",
                    }
                    for canonical_key in dictionary_fixture_keys()[:20]
                ],
            }
        else:
            content = {
                "source_id": "lesson",
                "rewritten_markdown": (
                    "<!-- page:1 -->\n"
                    "# 선물처럼 쉬운 과학 이야기\n\n"
                    "선물 이야기를 떠올리며 첫 쪽 내용을 쉽게 읽어요.\n\n"
                    '<div><img src="assets/fig1.png" alt="Figure" /></div>\n\n'
                    "그림도 함께 봅니다.\n\n"
                    "---\n\n"
                    "<!-- page:2 -->\n"
                    "# 다음 쪽\n\n"
                    "둘째 쪽입니다. 앞의 내용을 이어서 설명합니다."
                ),
            }
        return CompletionResult(
            provider="ollama",
            model="fake-vision",
            content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        )


class UnsupportedVisionClient:
    def assert_supports_images(self) -> None:
        raise LLMConfigurationError("no vision")


def joined_message_content(messages: list[ChatMessage]) -> str:
    return "\n\n".join(message.content for message in messages)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def minimal_context_packet() -> object:
    with tempfile.TemporaryDirectory() as workspace:
        return build_context_packet(write_parsed_fixture(Path(workspace) / "parsed"))


def dictionary_fixture_keys() -> list[str]:
    return [
        "선물",
        "학교",
        "친구",
        "마음",
        "빛",
        "문",
        "길",
        "이야기",
        "그림",
        "생각",
        "씨앗",
        "물",
        "손",
        "집",
        "꿈",
        "달",
        "별",
        "바람",
        "꽃",
        "노래",
    ]


def write_dictionary_fixture(path: Path, *, count: int = 20) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "canonical_key": key,
            "display": key,
            "example": f"{key} 예문입니다.",
            "learning_signal_score": round(0.45 - (index * 0.01), 3),
            "count": 100 - index,
            "meaning": f"{key}의 뜻.",
        }
        for index, key in enumerate(dictionary_fixture_keys()[:count])
    ]
    write_jsonl(path, rows)


def write_parsed_fixture(
    parsed_root: Path,
    *,
    source_id: str = "lesson",
    target_grade: str = "grade1",
    pages: list[str] | None = None,
) -> Path:
    bundle_dir = parsed_root / source_id
    assets_dir = bundle_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "fig1.png").write_bytes(b"fig1")
    (assets_dir / "page-0001.png").write_bytes(b"page1")
    page_texts = pages or [page_one_text()]
    (bundle_dir / "content.md").write_text(
        "\n\n---\n\n".join(
            f"<!-- page:{index} -->\n\n{page_text}"
            for index, page_text in enumerate(page_texts, start=1)
        ),
        encoding="utf-8",
    )
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
                "target_grade": target_grade,
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
