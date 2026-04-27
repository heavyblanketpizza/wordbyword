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
- `12_dictionary_candidates_with_cleaned_examples.jsonl`: deterministic stage that greedily assigns unique cleaned sentences from `10` onto sorted dictionary candidates from `11`, while marking the rows that still need example rewrites.
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
```

The default pipeline run is deterministic and rebuilds `01` through `09` from `original/`.

The LLM cleanup stages are intentionally separate and reuse saved artifacts from `output/`:

```bash
uv --no-cache run python -m wordbyword
uv --no-cache run python -m wordbyword --clean-example-sentences
uv --no-cache run python -m wordbyword --clean-dictionary-candidates
uv --no-cache run python -m wordbyword --link-cleaned-examples
```

- `--clean-example-sentences` reads saved `06_candidate_terms.csv` and `09_example_sentences.csv` to write `10_cleaned_example_sentences.csv`.
- `--clean-dictionary-candidates` reads saved `06_candidate_terms.csv` and `08_dictionary_candidates.jsonl` to write `11_cleaned_dictionary_candidates.jsonl`.
- `--link-cleaned-examples` reads saved `10_cleaned_example_sentences.csv` and `11_cleaned_dictionary_candidates.jsonl` to write `12_dictionary_candidates_with_cleaned_examples.jsonl`.
- When `10_cleaned_example_sentences.csv` already exists, stage `11` reuses it as support context; otherwise it falls back to saved `09_example_sentences.csv`.
- When both `10` and `11` exist, stage `12` is also refreshed automatically after either cleanup stage runs.

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

The optional sentence cleanup stage uses Ollama and defaults to `gemma4:31b` when `--clean-example-sentences` is enabled. Override it at runtime if needed:

```bash
uv --no-cache run python -m wordbyword --clean-example-sentences --ollama-model gemma4:31b
```

The cleanup stage sends saved `09_example_sentences.csv` rows to Ollama in batches of `50` rows by default.

The optional dictionary cleanup stage sends saved `08_dictionary_candidates.jsonl` rows to Ollama in batches of `40` rows by default and writes `11_cleaned_dictionary_candidates.jsonl`.

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
