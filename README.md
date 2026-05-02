# wordbyword

Internal progress note for building a 학년별 dynamic Korean vocabulary dictionary that can guide LLM rewrites of mature reading materials, especially newspapers and scientific or research articles, into grade-appropriate Korean reading.

The project is currently a vocabulary-and-example preparation pipeline. It does not yet perform the final grade-aware rewrite of source articles. The useful work so far is the upstream dictionary substrate: source text extraction, Korean morphology, canonical vocabulary grouping, candidate scoring, learner-dictionary cleanup, cleaned example selection, and missing example generation.

## Current Shape

- Input: Korean PDFs, `.txt`, and `.md` files under `build-dictionary/original/`.
- Base run: deterministic extraction and scoring from source documents into numbered CSV/JSONL artifacts.
- Optional LLM runs: saved-output cleanup stages that use Ollama to curate example sentences, filter dictionary candidates, link cleaned examples, generate missing examples, and retry failed generated examples.
- Near-term product objective: turn the cleaned dictionary candidates into a 학년별 vocabulary profile that an LLM can use while rewriting mature source material for younger readers.
- Not implemented yet: grade-level bands, learner difficulty calibration, rewrite orchestration for full articles, and evaluation of whether rewritten text stays faithful to the original.

## Folder Layout

- Python implementation modules are grouped by workflow under `wordbyword/dictionary_build/`, `wordbyword/source_parsing/`, `wordbyword/class_rewriting/`, and `wordbyword/cli/`.
- Put original books and documents in `build-dictionary/original/`.
- Run the pipeline with `uv --no-cache run python -m wordbyword`.
- Generated files are written to `build-dictionary/`.
- Put the reference meaning dictionary at `build-dictionary/references/reference-dictionary.jsonl`.
- Only `.gitkeep` placeholders are tracked for those local input folders; PDFs and reference JSONL stay ignored.

Supported source formats:

- `.pdf`
- `.txt`
- `.md`

## Source Material Parsing

Use this first to create one isolated parse workspace per mature class material:

```bash
uv --no-cache run python -m wordbyword --parse-source-materials class-source-materials
```

The command writes one source-specific folder under `class-source-materials/parsed/<source-id>/`:

- `manifest.json`: source metadata, checksum, parser status, and bundle file map.
- `content.md`: the primary human-readable and LLM-ready Markdown output for PDFs.
- `chunks.json`: page-level Markdown chunks with parser metadata.
- `document_model.json`: validated sidecar tying source metadata, pages, assets, and outputs together.
- `parser_output.json`: raw structured PyMuPDF4LLM layout output for debugging and traceability.
- `quality_report.json`: per-page extraction diagnostics, OCR flags, table warnings, and asset inventory.
- `assets/`: a single flat folder for parser-generated images, tables, page crops, and other visual assets.

The heuristic PDF parser is Markdown-first. It uses PyMuPDF4LLM for reading order, Markdown conversion, page chunks, and image extraction; PyMuPDF for page inspection and full-page review renders; Pillow for asset metadata; and Pydantic for validated sidecars. OCR is not run in this heuristic pass. Pages with little or no selectable text are flagged in `quality_report.json` for a later OCR or vision parser.

PaddleOCR-VL-1.5 can be used as an optional PDF parser lane:

```bash
uv --no-cache run python -m wordbyword --parse-source-materials class-source-materials --pdf-parser paddleocr-vl --paddleocr-vl-device cpu
uv --no-cache run python -m wordbyword --parse-source-materials class-source-materials --pdf-parser paddleocr-vl --paddleocr-vl-sidecar-env .venv-paddleocr --paddleocr-vl-device cpu
```

This mode renders each PDF page to `assets/page-0001.png`, sends the page images to PaddleOCR-VL-1.5, and writes the same bundle contract: `content.md`, `chunks.json`, `document_model.json`, `parser_output.json`, and `quality_report.json`. PaddleOCR-VL is intentionally not a base dependency. Paddle documents Python 3.9-3.13 as the verified manual-install range for this path, while this project currently runs on Python 3.14, so use a separate PaddleOCR virtual environment, Docker image, or PaddleOCR-VL service when running the model locally.

With `--paddleocr-vl-sidecar-env`, wordbyword activates the sidecar by running that environment's `bin/python` or `Scripts/python.exe`. `--paddleocr-vl-sidecar-python` can point directly at a Python executable instead. The main process renders page images, launches the sidecar PaddleOCR worker for each PDF, polls page results as they are produced, incrementally rewrites the merged `content.md`, `chunks.json`, and `parser_output.json`, then writes final `document_model.json` and `quality_report.json`. Final parsed outputs are per source file, not per page. When the page results are complete, wordbyword terminates any still-running sidecar process so PaddleOCR resources are released.

PaddleOCR-VL inference runs in an isolated child process when no sidecar env is configured. If you stop the command with `Ctrl-C`, wordbyword terminates the child process, removes the interrupted source's partial parse workspace, and exits with code `130`.

Useful source-material options:

```bash
uv --no-cache run python -m wordbyword --parse-source-material class-source-materials/popular-physicsprize2025.pdf
uv --no-cache run python -m wordbyword --parse-source-materials class-source-materials --no-page-renders
uv --no-cache run python -m wordbyword --parse-source-materials class-source-materials --skip-pdf-parse
uv --no-cache run python -m wordbyword --parse-source-materials class-source-materials --pdf-parser paddleocr-vl --paddleocr-vl-rec-backend vllm-server --paddleocr-vl-rec-server-url http://localhost:8000/v1 --paddleocr-vl-rec-api-model-name PaddlePaddle/PaddleOCR-VL-1.5
```

## Source Material Rewriting

After source materials are parsed, run the Ollama rewrite stage to create Korean 1학년-friendly materials. This stage reads only parsed source bundles under `class-source-materials/parsed`; it does not read or reference the built vocabulary under `build-dictionary/`.

```bash
uv --no-cache run python -m wordbyword --rewrite-source-materials class-source-materials/parsed --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --rewrite-source-material class-source-materials/parsed/popular-physicsprize2025 --ollama-model gemma4:31b
```

The rewrite stage reads each parsed source's `content.md`, sends its text and linked image assets to Ollama in document order, and writes outputs under `rewritten-for-class/grade1-ko-ollama/<source-id>/`:

- `content.md`: rewritten Korean Markdown for young readers, preserving page boundaries and image placements.
- `chunks.json`: source page text, rewritten page text, asset references, QA metrics, and failure reasons.
- `context_packet.md`: audit copy of parsed source metadata, assets, and interleaved content for debugging.
- `attempts.jsonl`: every page rewrite attempt, ordered prompt messages, response, attached asset ids, and validation result.
- `failures.jsonl`: unresolved page rewrite failures.
- `quality_report.json`: rewrite counts, image attachment counts, and soft QA metrics.
- `manifest.json`: source checksum, parser metadata, model, target profile, and output file map.
- `assets/`: copied parsed assets so Markdown image links remain local.

For comparison, the whole-document rewrite workflow sends the full parsed `content.md` to Ollama in one interleaved text/image request per source, instead of one request per page:

```bash
uv --no-cache run python -m wordbyword --rewrite-whole-source-materials class-source-materials/parsed --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --rewrite-whole-source-material class-source-materials/parsed/popular-physicsprize2025 --ollama-model gemma4:31b
```

Whole-document outputs mirror the same artifact bundle under `rewritten-for-class/grade1-ko-ollama-whole-document/<source-id>/`. This mode does not fall back to page rewriting or truncate oversized documents; model/context failures are written to `failures.jsonl` and return a nonzero CLI exit.

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
- `08_dictionary_candidates.jsonl`: structured vocabulary candidate packets for learner-dictionary curation.
- `09_example_sentences.csv`: high-signal sentence catalog filtered by canonical-key coverage, density, sentence shape, and normalized quality scores.
- `10_cleaned_example_sentences.csv`: optional Ollama-curated sentence set that keeps, lightly fixes, rebuilds, or deletes `09` rows with a strong bias toward minimal edits.
- `11_cleaned_dictionary_candidates.jsonl`: optional Ollama-filtered learner-dictionary candidate set with low-signal names and debris removed from `08`.
- `12_dictionary_candidates_with_cleaned_examples.jsonl`: deterministic stage that keeps the `11` row shape, promotes `curation_status` to `rewrite_ready`, and replaces `example` with the winning cleaned sentence from `10` when one is assigned.
- `13_vocabulary_with_generated_examples.jsonl`: optional Ollama stage that keeps the `12` row shape, treats the rows as selected vocabulary, preserves cleaned-example wins, and writes a completely new example sentence for rows where `example_replaced_with_cleaned_sentence` is `false`.
- `13_generated_example_attempts.jsonl`: sidecar log of every stage-13 generated sentence attempt, including rejected outputs.
- `13_generated_example_failures.jsonl`: sidecar log of the currently unresolved stage-13 failures after validation and any approved retries.
- `14_vocabulary_with_meanings.jsonl`: deterministic stage that keeps the `13` row shape, sets `curation_status` to `confirmed`, and adds a `meaning` field from `build-dictionary/references/reference-dictionary.jsonl` when `canonical_key` matches. Rows without a reference match get `meaning: ""` for later LLM completion.
- `15_final_dictionary.jsonl`: final dictionary artifact. It keeps the `14` row shape and writes Korean meanings for rows whose `meaning` field is empty, using the canonical key and example sentence as context.
- `README.md`: a generated guide for reading the `build-dictionary/` folder in pipeline order.

When artifact quality looks wrong, inspect the numbered files in order. Start with `01_extracted_pages.csv` to see whether PDF text extraction is already noisy, then check `02_sentences.csv` to see whether layout fragments were split or merged incorrectly.

## Usage

```bash
uv --no-cache run python -m wordbyword
```

Useful options:

```bash
uv --no-cache run python -m wordbyword --input build-dictionary/original --output build-dictionary
uv --no-cache run python -m wordbyword --include-punctuation
uv --no-cache run python -m wordbyword --include-numbers
uv --no-cache run python -m wordbyword --clean-example-sentences
uv --no-cache run python -m wordbyword --clean-dictionary-candidates
uv --no-cache run python -m wordbyword --link-cleaned-examples
uv --no-cache run python -m wordbyword --generate-missing-examples
uv --no-cache run python -m wordbyword --retry-generated-examples
uv --no-cache run python -m wordbyword --add-reference-meanings
uv --no-cache run python -m wordbyword --generate-missing-meanings
uv --no-cache run python -m wordbyword --show-distribution
```

The default pipeline run is deterministic and rebuilds `01` through `09` from `build-dictionary/original/`.

The LLM cleanup stages are intentionally separate and reuse saved artifacts from `build-dictionary/`:

```bash
uv --no-cache run python -m wordbyword
uv --no-cache run python -m wordbyword --clean-example-sentences
uv --no-cache run python -m wordbyword --clean-dictionary-candidates
uv --no-cache run python -m wordbyword --link-cleaned-examples
uv --no-cache run python -m wordbyword --generate-missing-examples
uv --no-cache run python -m wordbyword --retry-generated-examples
uv --no-cache run python -m wordbyword --add-reference-meanings
uv --no-cache run python -m wordbyword --generate-missing-meanings
```

- `--clean-example-sentences` reads saved `build-dictionary/06_candidate_terms.csv` and `build-dictionary/09_example_sentences.csv` to write `10_cleaned_example_sentences.csv`.
- `--clean-dictionary-candidates` reads saved `build-dictionary/06_candidate_terms.csv` and `build-dictionary/08_dictionary_candidates.jsonl` to write `11_cleaned_dictionary_candidates.jsonl`.
- `--link-cleaned-examples` reads saved `build-dictionary/10_cleaned_example_sentences.csv` and `build-dictionary/11_cleaned_dictionary_candidates.jsonl` to write `12_dictionary_candidates_with_cleaned_examples.jsonl`.
- `--generate-missing-examples` reads saved `build-dictionary/12_dictionary_candidates_with_cleaned_examples.jsonl` to write selected vocabulary in `13_vocabulary_with_generated_examples.jsonl` and the stage-13 attempt/failure logs.
- `--retry-generated-examples` reads saved `build-dictionary/13_vocabulary_with_generated_examples.jsonl` and `build-dictionary/13_generated_example_failures.jsonl` to run only the stage-13 repair pass on unresolved failures.
- `--add-reference-meanings` reads saved `build-dictionary/13_vocabulary_with_generated_examples.jsonl` and `build-dictionary/references/reference-dictionary.jsonl` to write `14_vocabulary_with_meanings.jsonl`. Use `--reference-dictionary path/to/reference-dictionary.jsonl` to override the reference source.
- `--generate-missing-meanings` reads saved `build-dictionary/14_vocabulary_with_meanings.jsonl` and asks Ollama to write Korean meanings for rows whose `meaning` field is empty, writing `15_final_dictionary.jsonl`.
- `--show-distribution` renders the terminal distribution panel for an existing `11_cleaned_dictionary_candidates.jsonl` without making LLM calls.
- When `10_cleaned_example_sentences.csv` already exists, stage `11` reuses it as support context; otherwise it falls back to saved `09_example_sentences.csv`.
- When both `10` and `11` exist, stage `12` is also refreshed automatically after either cleanup stage runs.

Pure numbers are ignored in vocabulary and surface word outputs unless `--include-numbers` is set.

The candidate files are the first deterministic cleanup layer. They keep the raw files intact, then group obvious variants such as particles and endings, normalize verb/adjective stems to dictionary-style forms, reject layout fragments, and assign rough deterministic quality and ROI scores for downstream review.

## LLM Providers And Cleanup

The package exposes a small provider layer for rewrite and dictionary-curation work:

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

All LLM helper clients default to `temperature=0.7`. Override with `default_temperature=` only when you need a different sampling level.

The optional sentence cleanup stage uses Ollama and defaults to `gemma4:31b` when `--clean-example-sentences` is enabled. Override it at runtime if needed:

```bash
uv --no-cache run python -m wordbyword --clean-example-sentences --ollama-model gemma4:31b
```

The cleanup stage sends saved `09_example_sentences.csv` rows to Ollama in batches of `50` rows by default.

The optional dictionary cleanup stage sends saved `08_dictionary_candidates.jsonl` rows to Ollama in batches of `40` rows by default and writes `11_cleaned_dictionary_candidates.jsonl`.

The optional missing-example generation stage sends saved `12_dictionary_candidates_with_cleaned_examples.jsonl` rows whose `example_replaced_with_cleaned_sentence` flag is `false` to Ollama in batches of `40` rows by default, checkpoints selected vocabulary in `13_vocabulary_with_generated_examples.jsonl` after each batch, logs every attempt to `13_generated_example_attempts.jsonl`, and keeps unresolved failures in `13_generated_example_failures.jsonl`. When a generated sentence fails validation, the CLI can prompt you to retry only the failed subset; the retry pass compares the source example, the failed generated sentence, and the validation failure reason, then decides whether to keep the generated sentence, rewrite it to match the source meaning, rewrite the source for learner quality, or write a new example from scratch:

```bash
uv --no-cache run python -m wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --retry-generated-examples --ollama-model gemma4:31b
```

The reference-meaning stage is deterministic and does not call an LLM. Before writing stage 14, it deletes the stage-13 attempt log and removes the failure log when it is empty. It checkpoints `14_vocabulary_with_meanings.jsonl` row by row while matching each stage-13 `canonical_key` against `build-dictionary/references/reference-dictionary.jsonl`:

```bash
uv --no-cache run python -m wordbyword --add-reference-meanings
```

The optional missing-meaning generation stage sends saved `14_vocabulary_with_meanings.jsonl` rows whose `meaning` field is empty to Ollama in batches of `40` rows by default. Each prompt includes the row id, `canonical_key`, display form, and example sentence. The stage checkpoints `15_final_dictionary.jsonl` after each batch and marks generated rows with `meaning_generated_with_ollama: true`:

```bash
uv --no-cache run python -m wordbyword --generate-missing-meanings --ollama-model gemma4:31b
```

## What This Enables Next

The current `15_final_dictionary.jsonl` shape is the final dictionary artifact for this build. The next layer should probably add grade metadata around those headwords rather than changing the extraction pipeline:

- grade band or target reader level for each canonical key
- allowed forms, variants, and example sentences per grade band
- difficulty evidence from frequency, dispersion, source examples, and manual/LLM judgment
- rewrite prompts that tell the LLM which mature terms to preserve, simplify, gloss, or replace
- evaluation rows comparing original and rewritten article sentences for faithfulness, readability, and vocabulary-band fit

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
