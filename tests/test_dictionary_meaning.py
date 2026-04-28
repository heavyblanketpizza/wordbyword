from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from wordbyword.dictionary_meaning import (
    MISSING_MEANING_PLACEHOLDER,
    build_dictionary_meaning_generation_outputs,
    build_dictionary_meaning_lookup_outputs,
    needs_generated_meaning,
)
from wordbyword.krdict import (
    KrdictDataset,
    KrdictEntry,
    ensure_krdict_dataset,
    iter_krdict_entries,
    load_krdict_index,
    parse_krdict_source,
    write_krdict_index,
)
from wordbyword.llm import CompletionResult
from wordbyword.pipeline import run_pipeline


SAMPLE_KRDICT_XML = """<?xml version="1.0" encoding="utf-8"?>
<channel>
  <item>
    <word_info>
      <word>학교</word>
      <pos>명사</pos>
      <pos_info>
        <sense_info>
          <definition>학생들이 모여 공부를 하는 곳.</definition>
        </sense_info>
      </pos_info>
    </word_info>
  </item>
  <item>
    <word_info>
      <word>읽다</word>
      <pos>동사</pos>
      <pos_info>
        <sense_info>
          <definition>글을 보고 그 뜻을 알다.</definition>
        </sense_info>
      </pos_info>
    </word_info>
  </item>
</channel>
"""


class KrdictParserTest(unittest.TestCase):
    def test_parses_word_and_definition_from_xml(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source_dir = Path(workspace) / "source"
            source_dir.mkdir()
            (source_dir / "sample.xml").write_text(SAMPLE_KRDICT_XML, encoding="utf-8")

            dataset = parse_krdict_source(source_dir)

        self.assertTrue(dataset.has("학교"))
        self.assertTrue(dataset.has("읽다"))
        self.assertFalse(dataset.has("없는단어"))

        school_entries = dataset.lookup("학교")
        self.assertEqual(len(school_entries), 1)
        self.assertEqual(school_entries[0].pos, "명사")
        self.assertEqual(school_entries[0].definition, "학생들이 모여 공부를 하는 곳.")

    def test_index_round_trip_preserves_entries(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source_dir = Path(workspace) / "source"
            source_dir.mkdir()
            (source_dir / "sample.xml").write_text(SAMPLE_KRDICT_XML, encoding="utf-8")
            dataset = parse_krdict_source(source_dir)

            index_path = Path(workspace) / "index.json"
            write_krdict_index(index_path, dataset)
            reloaded = load_krdict_index(index_path)

        self.assertEqual(reloaded.headword_count(), dataset.headword_count())
        self.assertEqual(
            reloaded.lookup("학교")[0].definition,
            dataset.lookup("학교")[0].definition,
        )


class EnsureKrdictDatasetTest(unittest.TestCase):
    def test_downloads_when_cache_missing_and_caches_index(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            cache_dir = Path(workspace) / "krdict_data"

            archive_buffer = io.BytesIO()
            with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
                xml_bytes = SAMPLE_KRDICT_XML.encode("utf-8")
                info = tarfile.TarInfo(name="sample.xml")
                info.size = len(xml_bytes)
                archive.addfile(info, io.BytesIO(xml_bytes))
            archive_bytes = archive_buffer.getvalue()

            class FakeResponse:
                def __init__(self, payload: bytes) -> None:
                    self._buffer = io.BytesIO(payload)

                def __enter__(self) -> "FakeResponse":
                    return self

                def __exit__(self, *exc_info: Any) -> None:
                    self._buffer.close()

                def read(self, size: int = -1) -> bytes:
                    return self._buffer.read(size)

            with mock.patch(
                "wordbyword.krdict.request.urlopen",
                return_value=FakeResponse(archive_bytes),
            ):
                dataset = ensure_krdict_dataset(cache_dir)

            self.assertTrue((cache_dir / "index.json").is_file())
            self.assertTrue((cache_dir / "source" / "sample.xml").is_file())
            self.assertTrue(dataset.has("학교"))

            with mock.patch(
                "wordbyword.krdict.request.urlopen",
                side_effect=AssertionError("network must not be called on cache hit"),
            ):
                reloaded = ensure_krdict_dataset(cache_dir)
            self.assertTrue(reloaded.has("읽다"))


class DictionaryMeaningLookupTest(unittest.TestCase):
    def test_lookup_marks_missing_terms_as_na(self) -> None:
        dataset = KrdictDataset(
            entries_by_headword={
                "학교": [KrdictEntry(headword="학교", pos="명사", definition="학생들이 모여 공부를 하는 곳.")],
            }
        )
        rows = [
            {"canonical_key": "학교", "display": "학교", "pos_group": "noun", "example": "학교에 갔다."},
            {"canonical_key": "버스", "display": "버스", "pos_group": "noun", "example": "버스를 탔다."},
        ]
        outputs = build_dictionary_meaning_lookup_outputs(rows, dataset)

        self.assertEqual(outputs.found_count, 1)
        self.assertEqual(outputs.missing_count, 1)
        self.assertEqual(outputs.rows[0]["meaning"], "학생들이 모여 공부를 하는 곳.")
        self.assertEqual(outputs.rows[0]["meaning_source"], "krdict")
        self.assertEqual(outputs.rows[1]["meaning"], MISSING_MEANING_PLACEHOLDER)
        self.assertEqual(outputs.rows[1]["meaning_source"], "missing")

    def test_lookup_falls_back_to_canonical_key_when_display_missing(self) -> None:
        dataset = KrdictDataset(
            entries_by_headword={
                "읽다": [KrdictEntry(headword="읽다", pos="동사", definition="글을 보고 뜻을 알다.")],
            }
        )
        rows = [{"canonical_key": "읽다", "display": "", "pos_group": "verb", "example": ""}]
        outputs = build_dictionary_meaning_lookup_outputs(rows, dataset)
        self.assertEqual(outputs.rows[0]["meaning"], "글을 보고 뜻을 알다.")


class DictionaryMeaningGenerationTest(unittest.TestCase):
    def test_only_na_rows_are_sent_to_ollama(self) -> None:
        rows = [
            {"canonical_key": "학교", "display": "학교", "pos_group": "noun", "example": "학교 예문.", "meaning": "학생들이 모여 공부를 하는 곳.", "meaning_source": "krdict"},
            {"canonical_key": "버스", "display": "버스", "pos_group": "noun", "example": "버스 예문.", "meaning": MISSING_MEANING_PLACEHOLDER, "meaning_source": "missing"},
        ]
        client = StubMeaningClient(meanings_by_headword={"버스": "탈 수 있는 큰 차."})
        outputs = build_dictionary_meaning_generation_outputs(rows, client=client)

        self.assertEqual(outputs.attempted_count, 1)
        self.assertEqual(outputs.generated_count, 1)
        self.assertEqual(outputs.failed_count, 0)
        self.assertEqual(outputs.rows[0]["meaning"], "학생들이 모여 공부를 하는 곳.")
        self.assertEqual(outputs.rows[1]["meaning"], "탈 수 있는 큰 차.")
        self.assertEqual(outputs.rows[1]["meaning_source"], "ollama")

    def test_failed_rows_keep_na_and_appear_in_failure_list(self) -> None:
        rows = [
            {"canonical_key": "버스", "display": "버스", "pos_group": "noun", "example": "버스 예문.", "meaning": MISSING_MEANING_PLACEHOLDER, "meaning_source": "missing"},
        ]
        client = StubMeaningClient(meanings_by_headword={})  # unknown -> empty
        outputs = build_dictionary_meaning_generation_outputs(rows, client=client)

        self.assertEqual(outputs.failed_count, 1)
        self.assertEqual(outputs.rows[0]["meaning"], MISSING_MEANING_PLACEHOLDER)
        self.assertEqual(outputs.failure_rows[0]["canonical_key"], "버스")


class PipelineStageFourteenTest(unittest.TestCase):
    def test_add_dictionary_meanings_writes_meaning_field_using_provided_krdict(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            krdict_dir = root / "krdict_data"
            input_dir.mkdir()
            output_dir.mkdir()
            (krdict_dir / "source").mkdir(parents=True)
            (krdict_dir / "source" / "sample.xml").write_text(SAMPLE_KRDICT_XML, encoding="utf-8")

            (output_dir / "13_dictionary_candidates_with_generated_examples.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "canonical_key": "학교",
                                "display": "학교",
                                "kind": "term",
                                "variants": ["학교"],
                                "count": 4,
                                "document_count": 1,
                                "quality_score": 0.9,
                                "roi_score": 0.5,
                                "example": "학교에 갔다.",
                                "curation_status": "rewrite_ready",
                                "example_replaced_with_cleaned_sentence": True,
                                "example_generated_with_ollama": False,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "canonical_key": "버스",
                                "display": "버스",
                                "kind": "term",
                                "variants": ["버스"],
                                "count": 3,
                                "document_count": 1,
                                "quality_score": 0.8,
                                "roi_score": 0.5,
                                "example": "버스를 탔다.",
                                "curation_status": "rewrite_ready",
                                "example_replaced_with_cleaned_sentence": False,
                                "example_generated_with_ollama": True,
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")

            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                add_dictionary_meanings=True,
                krdict_dir=krdict_dir,
            )

            meaning_path = output_dir / "14_dictionary_candidates_with_meanings.jsonl"
            self.assertTrue(meaning_path.is_file())
            rows = [
                json.loads(line)
                for line in meaning_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["meaning"], "학생들이 모여 공부를 하는 곳.")
            self.assertEqual(rows[0]["meaning_source"], "krdict")
            self.assertEqual(rows[1]["meaning"], MISSING_MEANING_PLACEHOLDER)
            self.assertEqual(rows[1]["meaning_source"], "missing")
            for row in rows:
                self.assertIn("example", row)
                self.assertIn("canonical_key", row)

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertIn("14_dictionary_candidates_with_meanings.jsonl", summary["outputs"])
            self.assertEqual(summary["dictionary_meaning_lookup_reviewed"], 2)
            self.assertEqual(summary["dictionary_meaning_lookup_found"], 1)
            self.assertEqual(summary["dictionary_meaning_lookup_missing"], 1)

    def test_fill_missing_meanings_replaces_na_rows_and_checkpoints_per_batch(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            input_dir = root / "original"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            entries = [
                {
                    "canonical_key": "학교",
                    "display": "학교",
                    "kind": "term",
                    "variants": ["학교"],
                    "example": "학교에 갔다.",
                    "meaning": "학생들이 모여 공부를 하는 곳.",
                    "meaning_source": "krdict",
                },
            ]
            for index in range(41):
                entries.append(
                    {
                        "canonical_key": f"단어{index}",
                        "display": f"단어{index}",
                        "kind": "term",
                        "variants": [f"단어{index}"],
                        "example": f"예문 {index}.",
                        "meaning": MISSING_MEANING_PLACEHOLDER,
                        "meaning_source": "missing",
                    }
                )

            (output_dir / "14_dictionary_candidates_with_meanings.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in entries) + "\n",
                encoding="utf-8",
            )
            (output_dir / "00_summary.json").write_text("{}\n", encoding="utf-8")

            client = CheckpointingMeaningClient(output_dir)
            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                fill_missing_meanings=True,
                dictionary_meaning_client=client,
            )

            self.assertEqual(client.batch_sizes, [40, 1])
            self.assertEqual(client.first_checkpoint_filled_count, 40)

            rows = [
                json.loads(line)
                for line in (
                    output_dir / "14_dictionary_candidates_with_meanings.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 42)
            self.assertEqual(rows[0]["meaning"], "학생들이 모여 공부를 하는 곳.")
            self.assertEqual(rows[0]["meaning_source"], "krdict")
            self.assertTrue(all(row["meaning_source"] == "ollama" for row in rows[1:]))
            self.assertTrue(all(row["meaning"] != MISSING_MEANING_PLACEHOLDER for row in rows[1:]))

            self.assertTrue((output_dir / "14_dictionary_meaning_attempts.jsonl").is_file())
            attempt_rows = [
                json.loads(line)
                for line in (
                    output_dir / "14_dictionary_meaning_attempts.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(attempt_rows), 41)
            self.assertTrue(all(row["accepted"] for row in attempt_rows))
            self.assertEqual(
                (output_dir / "14_dictionary_meaning_failures.jsonl").read_text(encoding="utf-8"),
                "",
            )

            summary = json.loads((output_dir / "00_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["dictionary_meaning_generation_attempted"], 41)
            self.assertEqual(summary["dictionary_meaning_generation_generated"], 41)
            self.assertEqual(summary["dictionary_meaning_generation_failed"], 0)


class StubMeaningClient:
    def __init__(self, meanings_by_headword: dict[str, str]) -> None:
        self.meanings_by_headword = meanings_by_headword

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        lines = []
        for row in rows:
            meaning = self.meanings_by_headword.get(row["headword"], "")
            if not meaning:
                continue
            lines.append(f"{row['id']}\t{meaning}")
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines) if lines else "EMPTY_RESPONSE_FALLBACK",
        )


class CheckpointingMeaningClient:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.batch_sizes: list[int] = []
        self.first_checkpoint_filled_count: int | None = None

    def complete(self, messages, *args, **kwargs) -> CompletionResult:
        rows = [
            json.loads(line)
            for line in messages[-1].content.splitlines()[1:]
            if line.strip()
        ]
        self.batch_sizes.append(len(rows))
        if len(self.batch_sizes) == 2:
            saved = [
                json.loads(line)
                for line in (
                    self.output_dir / "14_dictionary_candidates_with_meanings.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.first_checkpoint_filled_count = sum(
                1 for entry in saved if entry.get("meaning_source") == "ollama"
            )
        lines = [f"{row['id']}\t{row['headword']}의 뜻." for row in rows]
        return CompletionResult(
            provider="ollama",
            model="gemma4:31b",
            content="\n".join(lines),
        )


if __name__ == "__main__":
    unittest.main()
