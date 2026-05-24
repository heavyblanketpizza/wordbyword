# wordbyword

`wordbyword` builds Korean learner-vocabulary artifacts from source documents, rewrites class source materials into grade-friendly Korean, renders editable DOCX handouts, and tracks vocabulary signals from materials used in class.

The root `README.md` is the canonical technical guide. `BEGINNERS_GUIDE.md` is the tracked plain-language guide. Generated output folders do not own project documentation.

## Quick Start

Install and verify the project:

```bash
uv sync --dev
uv run pytest -q
uv run ruff check .
```

Build the full dictionary from `build-dictionary/original/`:

```bash
uv --no-cache run python -m wordbyword --build-dictionary --ollama-model gemma4:31b
```

That command resumes from the latest verified checkpoint, runs only the dirty stages through `15`, stops if generated-example failures remain after `13`, then writes `14_vocabulary_with_meanings.jsonl` and `15_final_dictionary.jsonl`.

Build only the deterministic base when debugging early artifacts:

```bash
uv --no-cache run python -m wordbyword --prep-dictionary
```

Build class printouts from source materials:

```bash
uv --no-cache run python -m wordbyword --build-class-printouts --ollama-model gemma4:31b
```

That full class-printout command starts from `classroom-printout-studio/class-source-materials`, parses PDFs with PaddleOCR-VL, rewrites only the sources parsed in that run with vocabulary guidance from `build-dictionary/15_final_dictionary.jsonl`, and writes editable DOCX handouts under `classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama/`.

## Local Folders

Tracked placeholders:

- `build-dictionary/original/.gitkeep`
- `build-dictionary/references/.gitkeep`

Local ignored working folders:

- `build-dictionary/`: generated dictionary artifacts plus local source/reference files.
- `classroom-printout-studio/`: local class source materials, parsed bundles, rewritten bundles, and printouts.
- `wbw-vocabulary-intelligence/`: local class archive, usage signals, live lexicon, and after-class reports.

Put dictionary source files in:

```text
build-dictionary/original/
```

Put the reference meaning dictionary at:

```text
build-dictionary/references/reference-dictionary.jsonl
```

Put class source PDFs in:

```text
classroom-printout-studio/class-source-materials/
```

Generated stage outputs are replaceable. The full dictionary build records verified checkpoint cache metadata in `00_summary.json` and reuses saved stages only when schemas, inputs, options, dependencies, and artifact hashes still match. Per-source parse, rewrite, print, and after-class report outputs are regenerated in their output folders.

## Dictionary Workflow

The normal full workflow reads `.pdf`, `.txt`, and `.md` files from `build-dictionary/original/` and writes stages `00` through `15`:

```bash
uv --no-cache run python -m wordbyword --build-dictionary --ollama-model gemma4:31b
```

The full workflow is resumable by default. It prints `SKIP`, `RUN`, or `REBUILD` for each checkpoint and stores cache metadata under `00_summary.json["build_cache"]`. Use `--force` when you intentionally want to ignore valid saved work:

```bash
uv --no-cache run python -m wordbyword --build-dictionary --force --ollama-model gemma4:31b
```

The full workflow is strict. If `13_generated_example_failures.jsonl` still has unresolved rows, the command returns nonzero and stops before meaning assignment. Repair stage `13` with the recovery commands, then run `--add-reference-meanings` and `--generate-missing-meanings` to finish from the repaired saved artifacts.

The prep command writes deterministic stages `00` through `09` and intentionally does not call Ollama:

```bash
uv --no-cache run python -m wordbyword --prep-dictionary
```

Useful prep options:

```bash
uv --no-cache run python -m wordbyword --prep-dictionary --input build-dictionary/original --output build-dictionary
uv --no-cache run python -m wordbyword --prep-dictionary --include-numbers
uv --no-cache run python -m wordbyword --prep-dictionary --include-punctuation
```

Recovery and debugging commands reuse saved artifacts:

```bash
uv --no-cache run python -m wordbyword --clean-example-sentences --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --clean-dictionary-candidates --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --link-cleaned-examples
uv --no-cache run python -m wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --retry-generated-examples --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --add-reference-meanings
uv --no-cache run python -m wordbyword --generate-missing-meanings --ollama-model gemma4:31b
```

Inspect the latest dictionary-shaped JSONL:

```bash
uv --no-cache run python -m wordbyword --show-distribution
uv --no-cache run python -m wordbyword --show-distribution build-dictionary/15_final_dictionary.jsonl
```

The bare command no longer builds anything. Use `--prep-dictionary` for deterministic stages `00` through `09`, or `--build-dictionary` to resume the full strict workflow from verified saved artifacts.

## Dictionary Artifacts

| Stage | File | Purpose |
| --- | --- | --- |
| 00 | `00_summary.json` | Run metadata, counts, output list, and pipeline stage map. |
| 01 | `01_extracted_pages.csv` | Cleaned page-level text extracted from each PDF or text source. |
| 02 | `02_sentences.csv` | Sentence-like chunks after layout cleanup and noise filtering. |
| 03 | `03_tokens.csv` | Kiwi tokenizer output with forms, POS tags, offsets, and source sentence. |
| 04 | `04_surface_words.csv` | Visible word forms as they appeared in the cleaned source text. |
| 05 | `05_vocabulary_raw.csv` | Raw lexical vocabulary terms counted from Korean morphology. |
| 06 | `06_candidate_terms.csv` | Cleaned, grouped, and scored word candidates. |
| 07 | `07_rejected_terms.csv` | Vocabulary terms removed by deterministic cleanup rules. |
| 08 | `08_dictionary_candidates.jsonl` | Vocabulary candidate packets for manual or LLM curation. |
| 09 | `09_example_sentences.csv` | High-signal sentence catalog for vocabulary examples. |
| 10 | `10_cleaned_example_sentences.csv` | Ollama-curated source sentences kept, fixed, rebuilt, or deleted. |
| 11 | `11_cleaned_dictionary_candidates.jsonl` | Ollama-filtered learner-dictionary candidates. |
| 12 | `12_dictionary_candidates_with_cleaned_examples.jsonl` | Dictionary candidates linked to unique cleaned examples when available. |
| 13 | `13_vocabulary_with_generated_examples.jsonl` | Selected vocabulary with generated examples for rows still missing a good one. |
| 13 sidecar | `13_generated_example_attempts.jsonl` | Attempt log for generated example passes. |
| 13 sidecar | `13_generated_example_failures.jsonl` | Current unresolved generated example failures. |
| 14 | `14_vocabulary_with_meanings.jsonl` | Vocabulary enriched with reference-dictionary meanings when keys match. |
| 15 | `15_final_dictionary.jsonl` | Final dictionary with Ollama-written Korean meanings for remaining blanks. |

When quality looks wrong, inspect the numbered files in order. The first bad artifact usually identifies the stage that needs attention.

## Class Printout Workflow

Normal class handout generation:

```bash
uv --no-cache run python -m wordbyword --build-class-printouts --ollama-model gemma4:31b
```

Stage-specific commands are for debugging or rerunning one part of the workflow.

Parse source materials only:

```bash
uv --no-cache run python -m wordbyword --parse-source-materials
uv --no-cache run python -m wordbyword --parse-source-material classroom-printout-studio/class-source-materials/example.pdf
```

The default parse directory is `classroom-printout-studio/class-source-materials`. PDFs are parsed with PaddleOCR-VL. Non-PDF source-material extensions may be discovered, but only PDFs currently produce the full parsed bundle.

PaddleOCR-VL sidecar behavior:

- Prefer `.venv-paddleocr-vl` in the repo root.
- Then `.venv-paddleocr`.
- Or set `WORDBYWORD_PADDLEOCR_VL_SIDECAR_PYTHON` / `WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV`.
- Or pass `--paddleocr-vl-sidecar-python` / `--paddleocr-vl-sidecar-env`.
- Users should not need to export `PYTHONPATH`; the sidecar launch injects the repo root.

Useful parse options:

```bash
uv --no-cache run python -m wordbyword --parse-source-materials --paddleocr-vl-device cpu
uv --no-cache run python -m wordbyword --parse-source-materials --paddleocr-vl-sidecar-env .venv-paddleocr-vl --paddleocr-vl-device cpu
uv --no-cache run python -m wordbyword --parse-source-materials --skip-pdf-parse
uv --no-cache run python -m wordbyword --parse-source-materials --pdf-render-dpi 180
uv --no-cache run python -m wordbyword --parse-source-materials --paddleocr-vl-rec-backend vllm-server --paddleocr-vl-rec-server-url http://localhost:8000/v1 --paddleocr-vl-rec-api-model-name PaddlePaddle/PaddleOCR-VL-1.5
```

Parse output per source:

- `manifest.json`
- `content.md`
- `chunks.json`
- `document_model.json`
- `parser_output.json`
- `quality_report.json`
- `assets/`

Rewrite parsed materials manually:

```bash
uv --no-cache run python -m wordbyword --rewrite-vocabulary-guided-source-materials --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --rewrite-vocabulary-guided-source-material classroom-printout-studio/class-source-materials/parsed/example --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --rewrite-whole-source-materials --ollama-model gemma4:31b
```

Vocabulary-guided rewrites default to `classroom-printout-studio/class-source-materials/parsed` and `build-dictionary/15_final_dictionary.jsonl`. They write bundles under `classroom-printout-studio/rewritten-for-class/grade1-ko-ollama-vocabulary-guided/`.

Print rewritten materials manually:

```bash
uv --no-cache run python -m wordbyword --print-class-materials --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --print-class-material classroom-printout-studio/rewritten-for-class/grade1-ko-ollama-vocabulary-guided/example --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --print-class-materials --print-body-font-size 13
uv --no-cache run python -m wordbyword --print-class-materials --print-font-path /System/Library/Fonts/AppleSDGothicNeo.ttc
```

Printable outputs per source:

- `print_plan.json`
- `content.md`
- `material.docx`
- `attempts.jsonl`
- `failures.jsonl`
- `quality_report.json`
- `manifest.json`
- `assets/`

The print stage asks Ollama for a strict JSON print plan, then renders DOCX locally. The model does not write the Word file directly.

## Class Archive And Usage Signals

Drop used class printouts into:

```text
wbw-vocabulary-intelligence/class-session-archive/
```

Preview archive renames:

```bash
uv --no-cache run python -m wordbyword --rename-class-session-archive --ollama-model gemma4:31b
```

Apply archive renames:

```bash
uv --no-cache run python -m wordbyword --rename-class-session-archive --apply-archive-renames --ollama-model gemma4:31b
```

The rename command considers direct `.docx` and `.pdf` files only. It never edits document contents, never deletes files, never moves files outside the archive, and never overwrites an existing file.

Build deterministic usage signals from managed archive filenames:

```bash
uv --no-cache run python -m wordbyword --build-usage-signals
```

Update the live grade lexicon:

```bash
uv --no-cache run python -m wordbyword --update-live-lexicon
```

`--build-usage-signals` writes `00` through `09` usage artifacts under `wbw-vocabulary-intelligence/usage-signal-build/`. `--update-live-lexicon` maintains `wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl`, `grade1-live-lexicon-supplemental.jsonl`, and the raw unmatched usage audit file.

## After-Class Reports

Generate one adult-facing Korean report from one managed archived class printout:

```bash
uv --no-cache run python -m wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --ollama-model gemma4:31b
```

The input must be one `.docx` or `.pdf` file whose filename already matches the managed archive pattern. Bare filenames are resolved inside `wbw-vocabulary-intelligence/class-session-archive/`; explicit paths are still supported for unusual/manual workflows. If the filename does not match the managed archive pattern, run the archive rename dry run and apply step first.

Report outputs are written under `wbw-vocabulary-intelligence/after-class-reports/grade1-ko-a4-ollama/<source-id>/`:

- `heuristic-analysis/`
- `report_plan.json`
- `content.md`
- `after_class_report.docx`
- `attempts.jsonl`
- `failures.jsonl`
- `quality_report.json`
- `manifest.json`

Useful overrides:

```bash
uv --no-cache run python -m wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --after-class-report-output-root wbw-vocabulary-intelligence/after-class-reports/grade1-ko-a4-ollama
uv --no-cache run python -m wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --after-class-report-font-path /System/Library/Fonts/AppleSDGothicNeo.ttc
```

## LLM Providers

The helper layer supports these providers:

- `openai`
- `claude`
- `google`
- `ollama`

Environment variables:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GOOGLE_API_KEY=...
export GEMINI_API_KEY=...
export WORDBYWORD_OLLAMA_BASE_URL=http://localhost:11434
```

Optional model overrides:

```bash
export WORDBYWORD_OPENAI_MODEL=gpt-4o
export WORDBYWORD_CLAUDE_MODEL=claude-3-5-sonnet-20240620
export WORDBYWORD_GOOGLE_MODEL=gemini-1.5-pro
export WORDBYWORD_OLLAMA_MODEL=gemma4:31b
```

The command-line workflows use Ollama for cleanup, rewrite, print planning, archive classification, and after-class report generation.

## Verification

```bash
uv run pytest -q
uv run ruff check .
```

Run the live Ollama smoke test only when a local `gemma4:31b` is available:

```bash
WORDBYWORD_RUN_OLLAMA_LIVE_TESTS=1 uv run python -m unittest tests.test_llm.LiveOllamaIntegrationTest
```
