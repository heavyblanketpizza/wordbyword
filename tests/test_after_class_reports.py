from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from wordbyword.after_class_reports import (
    DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT,
    AfterClassReportDocument,
    AfterClassReportError,
    AfterClassReportProgress,
    build_after_class_report_context,
    build_after_class_report_prompt,
    build_isolated_heuristic_analysis,
    generate_after_class_reading_report,
    validate_after_class_report_input,
)
from wordbyword.after_class_reports.report import resolve_after_class_report_font
from wordbyword.cli import build_parser, main, validate_args
from wordbyword.dictionary_build.llm import ChatMessage, CompletionResult


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class AfterClassReportInputTest(unittest.TestCase):
    def test_validate_input_rejects_non_exact_managed_printout_files(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            directory = root / "archive"
            directory.mkdir()
            unsupported = root / "2026-05-10_grade1-reading-apples.txt"
            unmanaged = root / "reading-apples.docx"
            unsupported.write_text("ignore", encoding="utf-8")
            write_docx(unmanaged, "사과를 읽는 글입니다.")

            scenarios = [
                (directory, "one exact .docx or .pdf file"),
                (unsupported, ".docx or .pdf"),
                (unmanaged, "managed class-session archive filename"),
                (root / "missing.docx", "does not exist"),
            ]
            for path, expected in scenarios:
                with self.subTest(path=path):
                    with self.assertRaises(AfterClassReportError) as error:
                        validate_after_class_report_input(path)
                    self.assertIn(expected, str(error.exception))


class AfterClassHeuristicAnalysisTest(unittest.TestCase):
    def test_isolated_heuristic_analysis_writes_00_to_09_under_report_output_only(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            archive.mkdir()
            source = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            output_root = root / "after-class-reports"
            heuristic_dir = output_root / source.stem / "heuristic-analysis"

            result = build_isolated_heuristic_analysis(source, heuristic_analysis_dir=heuristic_dir)

            self.assertEqual(result.source_id, source.stem)
            for filename in (
                "00_summary.json",
                "01_extracted_pages.csv",
                "02_sentences.csv",
                "03_tokens.csv",
                "04_surface_words.csv",
                "05_vocabulary_raw.csv",
                "06_candidate_terms.csv",
                "07_rejected_terms.csv",
                "08_dictionary_candidates.jsonl",
                "09_example_sentences.csv",
            ):
                self.assertTrue((heuristic_dir / filename).is_file(), filename)
            self.assertFalse((root / "usage-signal-build").exists())


class AfterClassReportValidationTest(unittest.TestCase):
    def test_report_plan_validation_enforces_counts_split_and_deep_cards(self) -> None:
        keys = [f"단어{i}" for i in range(1, 16)]
        payload = valid_report_payload("2026-05-10_grade1-reading-apples", keys)
        document = AfterClassReportDocument.model_validate(payload)
        self.assertEqual(len(document.vocabulary_overview), 15)

        missing_item = {**payload, "vocabulary_overview": payload["vocabulary_overview"][:-1]}
        with self.assertRaises(ValueError):
            AfterClassReportDocument.model_validate(missing_item)

        bad_split = json.loads(json.dumps(payload, ensure_ascii=False))
        bad_split["vocabulary_overview"][5]["priority"] = "core"
        with self.assertRaises(ValueError):
            AfterClassReportDocument.model_validate(bad_split)

        bad_cards = json.loads(json.dumps(payload, ensure_ascii=False))
        bad_cards["deep_teaching_cards"][0]["canonical_key"] = "다른단어"
        with self.assertRaises(ValueError):
            AfterClassReportDocument.model_validate(bad_cards)


class AfterClassReportGenerationTest(unittest.TestCase):
    def test_generate_after_class_report_writes_artifacts_and_korean_docx(self) -> None:
        font_path = korean_font_or_skip(self)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            archive.mkdir()
            source = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            output_root = root / "after-class-reports"
            client = RecordingAfterClassReportClient()
            progress_events: list[AfterClassReportProgress] = []

            outputs = generate_after_class_reading_report(
                source,
                output_root=output_root,
                client=client,
                model="fake-ollama",
                font_path=font_path,
                progress=progress_events.append,
            )

            self.assertTrue(outputs.accepted)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "analyze", "plan", "write", "complete"],
            )
            self.assertTrue(progress_events[-1].accepted)
            self.assertEqual(progress_events[-1].selected_vocabulary_count, 15)
            self.assertEqual(progress_events[-1].docx_path, output_root / source.stem / "after_class_report.docx")
            output_dir = output_root / source.stem
            for filename in (
                "report_plan.json",
                "content.md",
                "after_class_report.docx",
                "attempts.jsonl",
                "failures.jsonl",
                "quality_report.json",
                "manifest.json",
            ):
                self.assertTrue((output_dir / filename).is_file(), filename)
            self.assertTrue((output_dir / "heuristic-analysis" / "00_summary.json").is_file())
            self.assertEqual(outputs.selected_vocabulary_count, 15)

            docx = Document(str(output_dir / "after_class_report.docx"))
            paragraphs = "\n".join(paragraph.text for paragraph in docx.paragraphs)
            self.assertIn("수업 후 읽기 지도 보고서", paragraphs)
            self.assertIn("학부모 동반 활동", paragraphs)
            self.assertIn("교사용 후속 활동", paragraphs)

            quality = json.loads((output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["status"], "written")
            self.assertEqual(quality["selected_vocabulary_count"], 15)
            self.assertEqual(quality["core_vocabulary_count"], 5)
            self.assertEqual(quality["practice_vocabulary_count"], 5)
            self.assertEqual(quality["enrichment_vocabulary_count"], 5)
            self.assertEqual(quality["output_formats"], ["docx"])
            attempts = read_jsonl(output_dir / "attempts.jsonl")
            self.assertTrue(attempts[0]["accepted"])
            self.assertEqual(len(client.calls), 1)

    def test_invalid_ollama_json_writes_failure_artifacts_without_docx(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            archive.mkdir()
            source = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            output_root = root / "after-class-reports"
            progress_events: list[AfterClassReportProgress] = []

            outputs = generate_after_class_reading_report(
                source,
                output_root=output_root,
                client=RecordingAfterClassReportClient(response="{not json"),
                model="fake-ollama",
                progress=progress_events.append,
            )

            output_dir = output_root / source.stem
            self.assertFalse(outputs.accepted)
            self.assertEqual(
                [event.phase for event in progress_events],
                ["start", "analyze", "plan", "write", "complete"],
            )
            self.assertFalse(progress_events[-1].accepted)
            self.assertIn("JSON", progress_events[-1].failure_reason)
            self.assertFalse((output_dir / "after_class_report.docx").exists())
            self.assertFalse((output_dir / "report_plan.json").exists())
            self.assertTrue((output_dir / "attempts.jsonl").is_file())
            self.assertTrue((output_dir / "failures.jsonl").is_file())
            self.assertTrue((output_dir / "quality_report.json").is_file())
            failures = read_jsonl(output_dir / "failures.jsonl")
            self.assertFalse(failures[0]["accepted"])
            self.assertIn("JSON", failures[0]["failure_reason"])
            quality = json.loads((output_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["status"], "failed")
            self.assertEqual(quality["output_formats"], [])

    def test_prompt_uses_target_printout_and_isolated_heuristic_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            archive.mkdir()
            source = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            heuristic_dir = root / "reports" / source.stem / "heuristic-analysis"
            build_isolated_heuristic_analysis(source, heuristic_analysis_dir=heuristic_dir)

            context = build_after_class_report_context(
                source,
                output_dir=heuristic_dir.parent,
                heuristic_analysis_dir=heuristic_dir,
            )
            prompt = build_after_class_report_prompt(context)

            self.assertIn("normalized_printout_text", prompt.user_prompt)
            self.assertIn("사과", prompt.user_prompt)
            self.assertIn("top_vocabulary_candidates", prompt.user_prompt)
            self.assertIn("required_json_shape", prompt.user_prompt)


class AfterClassReportCliTest(unittest.TestCase):
    def test_parser_has_after_class_report_defaults(self) -> None:
        args = build_parser().parse_args(
            ["--generate-after-class-reading-report", "2026-05-10_grade1-reading-apples.docx"]
        )

        self.assertEqual(args.generate_after_class_reading_report, Path("2026-05-10_grade1-reading-apples.docx"))
        self.assertEqual(args.after_class_report_output_root, DEFAULT_AFTER_CLASS_REPORT_OUTPUT_ROOT)
        self.assertIsNone(args.after_class_report_font_path)

    def test_validate_resolves_bare_after_class_report_filename_to_default_archive_root(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            archive.mkdir()
            source = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            parser = build_parser()
            args = parser.parse_args(["--generate-after-class-reading-report", source.name])

            with patch("wordbyword.cli.options.DEFAULT_CLASS_SESSION_ARCHIVE_ROOT", archive):
                validate_args(parser, args)

            self.assertEqual(args.generate_after_class_reading_report, source)

    def test_validate_reports_resolved_default_archive_path_for_missing_bare_filename(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            archive = Path(workspace) / "class-session-archive"
            parser = build_parser()
            filename = "2026-05-10_grade1-reading-apples.docx"
            args = parser.parse_args(["--generate-after-class-reading-report", filename])
            stderr = io.StringIO()

            with (
                patch("sys.stderr", stderr),
                patch("wordbyword.cli.options.DEFAULT_CLASS_SESSION_ARCHIVE_ROOT", archive),
                self.assertRaises(SystemExit) as error,
            ):
                validate_args(parser, args)

            self.assertEqual(error.exception.code, 2)
            self.assertIn(str(archive / filename), stderr.getvalue())

    def test_cli_generates_report_without_running_pipeline_or_live_lexicon(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            output_root = root / "reports"
            fake_outputs = FakeAfterClassReportOutputs(source.stem, output_root / source.stem)
            stdout = io.StringIO()
            with (
                patch("sys.stdout", stdout),
                patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
                patch("wordbyword.cli.app.update_live_lexicon") as mock_update_live_lexicon,
                patch("wordbyword.cli.app.ollama_client", return_value=object()),
                patch("wordbyword.cli.app.warn_if_model_unload_fails"),
                patch("wordbyword.cli.app.generate_after_class_reading_report", return_value=fake_outputs) as mock_generate,
            ):
                exit_code = main(
                    [
                        "--generate-after-class-reading-report",
                        str(source),
                        "--after-class-report-output-root",
                        str(output_root),
                        "--ollama-model",
                        "fake-ollama",
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            mock_update_live_lexicon.assert_not_called()
            mock_generate.assert_called_once()
            self.assertIn("after-class reading report generated", stdout.getvalue())
            self.assertIn("selected_vocabulary: 15", stdout.getvalue())

    def test_cli_resolves_bare_after_class_report_filename_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            archive = root / "class-session-archive"
            archive.mkdir()
            source = archive / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            output_root = root / "reports"
            fake_outputs = FakeAfterClassReportOutputs(source.stem, output_root / source.stem)
            stdout = io.StringIO()
            with (
                patch("sys.stdout", stdout),
                patch("wordbyword.cli.options.DEFAULT_CLASS_SESSION_ARCHIVE_ROOT", archive),
                patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
                patch("wordbyword.cli.app.update_live_lexicon") as mock_update_live_lexicon,
                patch("wordbyword.cli.app.ollama_client", return_value=object()),
                patch("wordbyword.cli.app.warn_if_model_unload_fails"),
                patch("wordbyword.cli.app.generate_after_class_reading_report", return_value=fake_outputs) as mock_generate,
            ):
                exit_code = main(
                    [
                        "--generate-after-class-reading-report",
                        source.name,
                        "--after-class-report-output-root",
                        str(output_root),
                        "--ollama-model",
                        "fake-ollama",
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            mock_update_live_lexicon.assert_not_called()
            mock_generate.assert_called_once()
            self.assertEqual(mock_generate.call_args.args[0], source)

    def test_interactive_cli_uses_styled_after_class_report_terminal_ui(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            output_root = root / "reports"
            fake_outputs = FakeAfterClassReportOutputs(source.stem, output_root / source.stem)
            stdout = TtyStringIO()

            def generate_side_effect(*args: object, **kwargs: object) -> FakeAfterClassReportOutputs:
                progress = kwargs["progress"]
                assert callable(progress)
                progress(
                    AfterClassReportProgress(
                        phase="plan",
                        source_id=source.stem,
                        detail="generating adult-facing Korean report plan with Ollama",
                        output_dir=fake_outputs.output_dir,
                        heuristic_analysis_dir=fake_outputs.heuristic_analysis_dir,
                    )
                )
                return fake_outputs

            with (
                patch.dict("os.environ", {"TERM": "xterm-256color"}),
                patch("sys.stdout", stdout),
                patch("wordbyword.cli.app.run_pipeline") as mock_pipeline,
                patch("wordbyword.cli.app.update_live_lexicon") as mock_update_live_lexicon,
                patch("wordbyword.cli.app.ollama_client", return_value=object()),
                patch("wordbyword.cli.app.warn_if_model_unload_fails"),
                patch(
                    "wordbyword.cli.app.generate_after_class_reading_report",
                    side_effect=generate_side_effect,
                ) as mock_generate,
            ):
                exit_code = main(
                    [
                        "--generate-after-class-reading-report",
                        str(source),
                        "--after-class-report-output-root",
                        str(output_root),
                        "--ollama-model",
                        "fake-ollama",
                    ]
                )

            self.assertEqual(exit_code, 0)
            mock_pipeline.assert_not_called()
            mock_update_live_lexicon.assert_not_called()
            mock_generate.assert_called_once()
            rendered = stdout.getvalue()
            self.assertIn("AFTER-CLASS REPORT // READING REPORT", rendered)
            self.assertIn("PLAN", rendered)
            self.assertIn("after_class_report.docx", rendered)
            self.assertNotIn("after-class report [", rendered)

    def test_cli_rejects_after_class_report_combined_with_live_lexicon(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "2026-05-10_grade1-reading-apples.docx"
            write_docx(source, korean_fixture_text())
            with self.assertRaises(SystemExit) as error:
                main(["--generate-after-class-reading-report", str(source), "--update-live-lexicon"])
            self.assertEqual(error.exception.code, 2)


class RecordingAfterClassReportClient:
    def __init__(self, response: object | None = None) -> None:
        self.response = response
        self.calls: list[tuple[dict[str, object], list[ChatMessage]]] = []

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
        if self.response is not None:
            content = self.response
        else:
            packet = prompt_packet_from_message(messages[-1].content)
            candidates = unique_candidate_keys(packet["top_vocabulary_candidates"])
            if len(candidates) < 15:
                raise AssertionError(f"fixture yielded only {len(candidates)} vocabulary candidates")
            content = valid_report_payload(str(packet["source_id"]), candidates[:15])
        return CompletionResult(
            provider="ollama",
            model="fake-ollama",
            content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        )


class FakeAfterClassReportOutputs:
    def __init__(self, source_id: str, output_dir: Path) -> None:
        self.source_id = source_id
        self.output_dir = output_dir
        self.heuristic_analysis_dir = output_dir / "heuristic-analysis"
        self.accepted = True
        self.failure_reason = ""
        self.selected_vocabulary_count = 15
        self.docx_path = output_dir / "after_class_report.docx"
        self.attempt_rows = []
        self.failure_rows = []


def valid_report_payload(source_id: str, keys: list[str]) -> dict[str, object]:
    priorities = ["core"] * 5 + ["practice"] * 5 + ["enrichment"] * 5
    vocabulary = []
    for index, (key, priority) in enumerate(zip(keys[:15], priorities, strict=True), start=1):
        vocabulary.append(
            {
                "canonical_key": key,
                "display": key,
                "in_text_form": key,
                "priority": priority,
                "student_friendly_meaning": f"{key}은 글의 뜻을 또렷하게 이해하도록 도와주는 말입니다.",
                "source_sentence": f"글에서 {key}을 보고 앞뒤 내용을 함께 생각합니다.",
                "practice_sentence": f"아이와 함께 {key}을 넣어 짧은 문장을 말해 봅니다.",
                "why_it_matters": f"{key}을 알면 글의 중요한 장면과 마음을 더 잘 설명할 수 있습니다.",
                "caution": f"{key}을 외우게 하기보다 본문 속 상황과 연결해 말하게 합니다.",
            }
        )
    core_keys = keys[:5]
    return {
        "source_id": source_id,
        "title": "수업 후 읽기 지도 보고서",
        "metadata": {
            "source_filename": f"{source_id}.docx",
            "audience": "교사와 학부모",
            "language": "ko",
        },
        "lesson_summary": "이 보고서는 오늘 읽은 글의 중심 내용을 다시 살피고 어휘를 생활 대화로 이어 가도록 돕습니다.",
        "core_teaching_points": [
            "아이의 대답보다 근거가 되는 문장을 먼저 찾게 합니다.",
            "낱말 뜻은 본문 장면과 함께 말하게 합니다.",
            "짧은 대화 뒤에 아이가 스스로 한 문장을 다시 말하게 합니다.",
        ],
        "vocabulary_overview": vocabulary,
        "deep_teaching_cards": [
            {
                "canonical_key": key,
                "word_part_note": f"{key}이 문장에서 어떤 역할을 하는지 함께 살펴봅니다.",
                "similar_but_different": f"{key}과 비슷한 말은 장면에 따라 느낌이 조금 다릅니다.",
                "adult_prompt": f"글에서 {key}이 왜 나왔는지 아이에게 물어봅니다.",
                "good_response": f"아이가 {key}을 본문 장면과 연결해 말하면 충분히 좋습니다.",
                "watch_for": f"{key}을 뜻만 외우고 문장 속 쓰임을 놓치지 않는지 봅니다.",
            }
            for key in core_keys
        ],
        "discussion_questions": [
            "이 글에서 가장 중요한 장면은 어디였나요?",
            "그 장면을 알려 주는 낱말은 무엇이었나요?",
            "등장인물의 마음을 알 수 있는 문장은 무엇인가요?",
            "비슷한 일을 겪은 적이 있다면 어떤 느낌이었나요?",
            "오늘 배운 낱말 중 집에서 다시 써 볼 말은 무엇인가요?",
        ],
        "teacher_activities": [
            "본문 문장 하나를 고르고 핵심 낱말을 가려 다시 읽게 합니다.",
            "핵심 어휘 다섯 개를 그림이나 몸짓과 연결해 짧게 확인합니다.",
            "아이들이 서로 다른 근거 문장을 말하도록 짝 대화를 진행합니다.",
        ],
        "parent_activities": [
            "저녁에 오늘 배운 낱말 하나를 골라 가족 문장으로 바꾸어 말합니다.",
            "아이의 설명이 짧아도 본문에서 본 장면을 다시 떠올리게 합니다.",
            "정답을 바로 말하기보다 어떤 문장에서 그렇게 생각했는지 물어봅니다.",
        ],
        "watch_points": [
            "낱말을 소리 내어 읽지만 뜻을 장면과 연결하지 못하는지 살핍니다.",
            "긴 문장을 통째로 외우려 할 때 짧은 의미 단위로 나누어 줍니다.",
            "모르는 낱말이 많을 때는 핵심 다섯 개만 먼저 다룹니다.",
        ],
        "spaced_review": [
            "오늘은 핵심 낱말을 본문 문장과 함께 한 번 확인합니다.",
            "이틀 뒤에는 같은 낱말로 새 문장을 말하게 합니다.",
            "일주일 뒤에는 글의 중심 내용과 낱말 세 개를 함께 복습합니다.",
        ],
    }


def korean_fixture_text() -> str:
    return (
        "사과 바구니를 든 아이가 교실에 들어왔습니다. 친구는 선생님과 함께 나무, 꽃잎, 바람, "
        "구름, 하늘, 물결, 연못, 그림, 책상, 연필, 노래, 운동, 마음, 약속, 이야기를 천천히 "
        "읽었습니다. 아이들은 사과를 나누어 먹고 서로의 생각을 말했습니다. 선생님은 장면을 다시 "
        "떠올리며 낱말의 뜻을 문장 속에서 찾아보자고 했습니다. 집에서는 부모님과 오늘 배운 단어를 "
        "사용해 짧은 문장을 만들기로 했습니다. 아이는 밝게 웃고, 친구는 고맙다고 말했습니다."
    )


def write_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def korean_font_or_skip(test_case: unittest.TestCase) -> Path:
    try:
        font_path, _font_name = resolve_after_class_report_font(None)
    except AfterClassReportError as error:
        test_case.skipTest(str(error))
    return font_path


def prompt_packet_from_message(content: str) -> dict[str, object]:
    start = content.find("{")
    if start < 0:
        raise AssertionError("prompt did not include JSON packet")
    parsed = json.loads(content[start:])
    if not isinstance(parsed, dict):
        raise AssertionError("prompt packet was not a JSON object")
    return parsed


def unique_candidate_keys(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    keys: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("canonical_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys
