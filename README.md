# wordbyword

Extract Korean vocabulary candidates from PDFs and text files for study workflows.

## Folder Layout

- Put original books and documents in `original/`.
- Run the pipeline with `uv --no-cache run python -m wordbyword`.
- Generated files are written to `output/`.

Supported source formats:

- `.pdf`
- `.txt`
- `.md`

## Outputs

The pipeline writes these files:

- `00_summary.json`: counts, scanned documents, generated files, and ordered pipeline stages.
- `01_extracted_pages.csv`: cleaned page-level text extracted from each PDF or text source.
- `02_sentences.csv`: every parsed sentence after layout cleanup, with document, page, and sentence index.
- `03_tokens.csv`: every tokenizer occurrence with document, page, sentence, form, POS tag, and offsets.
- `04_surface_words.csv`: surface-level words as they appear in the source text.
- `05_vocabulary_raw.csv`: raw unique vocabulary terms from Korean-aware morphology.
- `06_candidate_terms.csv`: deterministically cleaned term candidates with canonical keys, variants, quality scores, and ROI scores.
- `07_rejected_terms.csv`: vocabulary terms removed by deterministic cleanup, with rejection reasons.
- `08_dictionary_candidates.jsonl`: structured vocabulary candidate packets for a later LLM curation stage.
- `09_example_sentences.csv`: high-signal sentence catalog filtered by canonical-key coverage, density, sentence shape, and normalized quality scores.
- `10_cleaned_example_sentences.csv`: optional Ollama-curated sentence set that keeps, lightly fixes, rebuilds, or deletes `09` rows with a strong bias toward minimal edits.
- `11_cleaned_dictionary_candidates.jsonl`: optional Ollama-filtered learner-dictionary candidate set with low-signal names and debris removed from `08`.
- `12_dictionary_candidates_with_cleaned_examples.jsonl`: deterministic stage that keeps the `11` row shape, promotes `curation_status` to `rewrite_ready`, and replaces `example` with the winning cleaned sentence from `10` when one is assigned.
- `13_dictionary_candidates_with_generated_examples.jsonl`: optional Ollama stage that keeps the `12` row shape, preserves cleaned-example wins, and writes a completely new candidate example sentence for rows where `example_replaced_with_cleaned_sentence` is `false`.
- `13_generated_example_attempts.jsonl`: sidecar log of every stage-13 generated sentence attempt, including rejected outputs.
- `13_generated_example_failures.jsonl`: sidecar log of the currently unresolved stage-13 failures after validation and any approved retries.
- `14_dictionary_candidates_with_meanings.jsonl`: optional stage that keeps the `13` row shape and adds a `meaning` field. Stage 14A (`--add-dictionary-meanings`) populates the field from the bundled 한국어기초사전 lookup, falling back to `"N/A"` for terms the dictionary does not cover. Stage 14B (`--fill-missing-meanings`) sends the remaining `N/A` rows to Ollama in batches of 40 and checkpoints the file after every batch.
- `14_dictionary_meaning_attempts.jsonl`: sidecar log of every stage-14B Ollama attempt.
- `14_dictionary_meaning_failures.jsonl`: sidecar log of the currently unresolved stage-14B failures.
- `README.md`: a generated guide for reading the output folder in pipeline order.

When output quality looks wrong, inspect the numbered files in order. Start with `01_extracted_pages.csv` to see whether PDF text extraction is already noisy, then check `02_sentences.csv` to see whether layout fragments were split or merged incorrectly.

## Usage

```bash
uv --no-cache run python -m wordbyword
```

Useful options:

```bash
uv --no-cache run python -m wordbyword --input original --output output
uv --no-cache run python -m wordbyword --include-punctuation
uv --no-cache run python -m wordbyword --include-numbers
uv --no-cache run python -m wordbyword --clean-example-sentences
uv --no-cache run python -m wordbyword --clean-dictionary-candidates
uv --no-cache run python -m wordbyword --link-cleaned-examples
uv --no-cache run python -m wordbyword --retry-generated-examples
uv --no-cache run python -m wordbyword --add-dictionary-meanings
uv --no-cache run python -m wordbyword --fill-missing-meanings
```

The default pipeline run is deterministic and rebuilds `01` through `09` from `original/`.

The LLM cleanup stages are intentionally separate and reuse saved artifacts from `output/`:

```bash
uv --no-cache run python -m wordbyword
uv --no-cache run python -m wordbyword --clean-example-sentences
uv --no-cache run python -m wordbyword --clean-dictionary-candidates
uv --no-cache run python -m wordbyword --link-cleaned-examples
uv --no-cache run python -m wordbyword --retry-generated-examples
```

- `--clean-example-sentences` reads saved `06_candidate_terms.csv` and `09_example_sentences.csv` to write `10_cleaned_example_sentences.csv`.
- `--clean-dictionary-candidates` reads saved `06_candidate_terms.csv` and `08_dictionary_candidates.jsonl` to write `11_cleaned_dictionary_candidates.jsonl`.
- `--link-cleaned-examples` reads saved `10_cleaned_example_sentences.csv` and `11_cleaned_dictionary_candidates.jsonl` to write `12_dictionary_candidates_with_cleaned_examples.jsonl`.
- `--retry-generated-examples` reads saved `13_dictionary_candidates_with_generated_examples.jsonl` and `13_generated_example_failures.jsonl` to run only the stage-13 repair pass on unresolved failures.
- `--add-dictionary-meanings` reads saved `13_dictionary_candidates_with_generated_examples.jsonl` and writes `14_dictionary_candidates_with_meanings.jsonl`. On the first run it downloads the bundled 한국어기초사전 source into `./krdict_data/` (configurable via `--krdict-dir`) and parses it into `krdict_data/index.json`; subsequent runs reuse the cached index. Each row gets a `meaning` field populated from the dictionary, or `"N/A"` when the headword is not found.
- `--fill-missing-meanings` reads saved `14_dictionary_candidates_with_meanings.jsonl`, sends only the `N/A` rows to Ollama in batches of 40, checkpoints the file after every batch, and logs attempts and unresolved failures to `14_dictionary_meaning_attempts.jsonl` and `14_dictionary_meaning_failures.jsonl`.
- When `10_cleaned_example_sentences.csv` already exists, stage `11` reuses it as support context; otherwise it falls back to saved `09_example_sentences.csv`.
- When both `10` and `11` exist, stage `12` is also refreshed automatically after either cleanup stage runs.

Stage 14 is run as two separate passes (lookup first, Ollama fill second) so the deterministic dictionary join stays inspectable on its own:

```bash
uv --no-cache run python -m wordbyword --add-dictionary-meanings
uv --no-cache run python -m wordbyword --fill-missing-meanings --ollama-model gemma4:31b
```

Pure numbers are ignored in vocabulary and surface word outputs unless `--include-numbers` is set.

The candidate files are the first deterministic cleanup layer. They keep the raw files intact, then group obvious variants such as particles and endings, normalize verb/adjective stems to dictionary-style forms, reject layout fragments, and assign rough deterministic quality and ROI scores for downstream review.

## LLM Providers

The package now exposes a small provider layer for the next rewrite and dictionary-curation stage:

- `openai`
- `claude`
- `google`
- `ollama`

Set credentials with environment variables before calling the LLM helpers:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
export OLLAMA_BASE_URL=http://localhost:11434
```

Optional model overrides:

```bash
export WORDBYWORD_OPENAI_MODEL=gpt-4o
export WORDBYWORD_CLAUDE_MODEL=claude-3-5-sonnet-20240620
export WORDBYWORD_GOOGLE_MODEL=gemini-1.5-pro
export WORDBYWORD_OLLAMA_MODEL=llama3.1
```

All LLM helper clients default to `temperature=0.0`. Override with `default_temperature=` only when you intentionally want sampling.

The optional sentence cleanup stage uses Ollama and defaults to `gemma4:31b` when `--clean-example-sentences` is enabled. Override it at runtime if needed:

```bash
uv --no-cache run python -m wordbyword --clean-example-sentences --ollama-model gemma4:31b
```

The cleanup stage sends saved `09_example_sentences.csv` rows to Ollama in batches of `50` rows by default.

The optional dictionary cleanup stage sends saved `08_dictionary_candidates.jsonl` rows to Ollama in batches of `40` rows by default and writes `11_cleaned_dictionary_candidates.jsonl`.

The optional missing-example generation stage sends saved `12_dictionary_candidates_with_cleaned_examples.jsonl` rows whose `example_replaced_with_cleaned_sentence` flag is `false` to Ollama in batches of `40` rows by default, checkpoints `13_dictionary_candidates_with_generated_examples.jsonl` after each batch, logs every attempt to `13_generated_example_attempts.jsonl`, and keeps unresolved failures in `13_generated_example_failures.jsonl`. When a generated sentence fails validation, the CLI can prompt you to retry only the failed subset; the retry pass compares the source example and the failed generated sentence, then decides whether to keep the generated sentence, rewrite it to match the source meaning, rewrite the source for learner quality, or write a new example from scratch:

```bash
uv --no-cache run python -m wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --retry-generated-examples --ollama-model gemma4:31b
```

Programmatic example:

```python
from wordbyword import build_dictionary_entry, openai_client, rewrite_sentence

client = openai_client()
rewritten = rewrite_sentence(client, sentence="학생이 책을 읽고 있다.", focus_terms=["책", "읽다"])
entry = build_dictionary_entry(
    client,
    candidate={"canonical_key": "읽다", "display": "읽다", "pos_group": "verb"},
    example_sentences=[rewritten],
)
```

## Verify

```bash
uv --no-cache run python -m unittest discover -s tests
```

Run the live Ollama rewrite smoke test only when a local `gemma4:31b` is available:

```bash
WORDBYWORD_RUN_OLLAMA_LIVE_TESTS=1 uv --no-cache run python -m unittest tests.test_llm.LiveOllamaIntegrationTest
```
