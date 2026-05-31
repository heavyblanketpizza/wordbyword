# wordbyword Beginner's Guide

This guide is the simple version. The technical truth lives in `README.md`.

`wordbyword` helps with three jobs:

1. Make a Korean vocabulary dictionary from source documents.
2. Turn hard class readings into easier Korean handouts.
3. Learn from class handouts after they were used.

## Before You Start

Install the project and check that the command is available:

```bash
uv sync --dev
uv run wordbyword --help
```

The project expects Python `>=3.14`; `uv` should handle that for normal local development. You can also open the GUI with:

```bash
uv run wordbyword-gui
```

Put dictionary source files here:

```text
build-dictionary/original/
```

After adding files, preview and apply managed source names:

```bash
uv run wordbyword --rename-dictionary-source-archive --ollama-model gemma4:31b
uv run wordbyword --rename-dictionary-source-archive --apply-dictionary-source-renames --ollama-model gemma4:31b
```

Supported dictionary files:

```text
.pdf
.txt
.md
```

Put class source PDFs here:

```text
classroom-printout-studio/class-source-materials/grade1/
```

Model-backed steps use Ollama by default. If you use Ollama, make sure it is running:

```bash
ollama serve
ollama pull gemma4:31b
```

Use a different pulled model if you also pass that model with `--llm-model` or the older `--ollama-model` alias. For text-only model steps, you can also use `--llm-provider openai`, `--llm-provider claude`, or `--llm-provider google` with the matching API key in your environment. API keys are not saved by the app.

Check the project:

```bash
uv run pytest -q
uv run ruff check .
```

## Build The Dictionary

For normal use, run one command:

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b
```

This reads managed files in `build-dictionary/original/` and writes the finished dictionary files in `build-dictionary/`.

If the dictionary build says a source file is unmanaged, run the rename preview and apply commands above first. If it reports duplicate source materials, quarantine the duplicate copies before continuing:

```bash
uv run wordbyword --resolve-dictionary-source-duplicates
uv run wordbyword --resolve-dictionary-source-duplicates --yes
```

It checks the saved numbered files first. If a saved step is still valid, it skips that step and continues from the next needed step.

To rebuild everything from the beginning on purpose, use:

```bash
uv run wordbyword --build-dictionary --force --ollama-model gemma4:31b
```

The final dictionary is:

```text
build-dictionary/15_final_dictionary.jsonl
```

If you are fixing one stage by hand, these recovery commands still exist:

```bash
uv run wordbyword --prep-dictionary
uv run wordbyword --clean-example-sentences --ollama-model gemma4:31b
uv run wordbyword --clean-dictionary-candidates --ollama-model gemma4:31b
uv run wordbyword --link-cleaned-examples
uv run wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv run wordbyword --retry-generated-examples --ollama-model gemma4:31b
uv run wordbyword --add-reference-meanings
uv run wordbyword --generate-missing-meanings --ollama-model gemma4:31b
```

Important: `--prep-dictionary` builds deterministic stages `00` through `09`. Use `--build-dictionary` when you want the finished `15_final_dictionary.jsonl`.

## Make Class Printouts

For normal use, run one command:

```bash
uv run wordbyword --build-class-printouts --ollama-model gemma4:31b
```

This does three things:

1. Reads source PDFs with PaddleOCR-VL.
2. Rewrites them into easier Korean with vocabulary help.
3. Makes editable Word handouts.

After you have built the live classroom lexicon, the rewrite step automatically uses `wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl`. Before that file exists, it falls back to `build-dictionary/15_final_dictionary.jsonl`.

The workflow keeps a content-hash ledger under the parsed source-material folder. If the same source bytes were already printed successfully, the next run skips them before parsing or calling Ollama. If you really want to regenerate already printed sources, add `--force-class-printouts`.

The Word files are written under:

```text
classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama/
```

Only parse class source materials when you want to inspect OCR output:

```bash
uv run wordbyword --parse-source-materials
```

Only print already rewritten materials when you do not want to parse and rewrite again:

```bash
uv run wordbyword --print-class-materials --ollama-model gemma4:31b
```

## PaddleOCR-VL

PaddleOCR-VL usually lives in a separate Python environment. If this folder exists, `wordbyword` uses it automatically:

```text
.venv-paddleocr-vl/
```

It also auto-checks `.venv-paddleocr/`. A manually supplied sidecar path wins over environment variables, and environment variables win over auto-discovery.

You should not need to type `PYTHONPATH`.

If you need to choose the sidecar manually:

```bash
uv run wordbyword --parse-source-materials --paddleocr-vl-sidecar-env .venv-paddleocr-vl --paddleocr-vl-device cpu
```

## Archive Used Class Handouts

Put used handouts here:

```text
wbw-vocabulary-intelligence/class-session-archive/
```

Preview safe filenames:

```bash
uv run wordbyword --rename-class-session-archive --ollama-model gemma4:31b
```

Apply the safe filenames:

```bash
uv run wordbyword --rename-class-session-archive --apply-archive-renames --ollama-model gemma4:31b
```

If the tool reports duplicate class printouts, quarantine the duplicate copies before continuing:

```bash
uv run wordbyword --resolve-archive-duplicates
```

If it reports model-reviewed near duplicates, rerun the resolver with Ollama review enabled:

```bash
uv run wordbyword --resolve-archive-duplicates --include-near-duplicates --ollama-model gemma4:31b
```

Then count vocabulary from used handouts:

```bash
uv run wordbyword --build-usage-signals
```

Update the live classroom vocabulary list:

```bash
uv run wordbyword --update-live-lexicon
```

This maintains the canonical live lexicon, the supplemental non-canonical live lexicon, and the unmatched usage audit file.

## Make An After-Class Report

After a used handout has a managed archive filename, make one adult-facing report:

```bash
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --ollama-model gemma4:31b
```

Bare filenames are read from `wbw-vocabulary-intelligence/class-session-archive/`. You can still pass an explicit path for unusual/manual workflows.

The report is written under:

```text
wbw-vocabulary-intelligence/after-class-reports/
```

## What The Main Files Mean

Read dictionary files in number order:

| Stage | File | Simple meaning |
| --- | --- | --- |
| 00 | `00_summary.json` | Run summary. |
| 01 | `01_extracted_pages.csv` | Text pulled from documents. |
| 02 | `02_sentences.csv` | Text split into sentences. |
| 03 | `03_tokens.csv` | Korean word pieces. |
| 04 | `04_surface_words.csv` | Words as they appeared. |
| 05 | `05_vocabulary_raw.csv` | Raw word counts. |
| 06 | `06_candidate_terms.csv` | Cleaned word candidates. |
| 07 | `07_rejected_terms.csv` | Removed bad candidates. |
| 08 | `08_dictionary_candidates.jsonl` | Word packets for checking. |
| 09 | `09_example_sentences.csv` | Good example sentence candidates. |
| 10 | `10_cleaned_example_sentences.csv` | Model-checked examples. |
| 11 | `11_cleaned_dictionary_candidates.jsonl` | Model-checked words. |
| 12 | `12_dictionary_candidates_with_cleaned_examples.jsonl` | Words linked to better examples. |
| 13 | `13_vocabulary_with_generated_examples.jsonl` | Missing examples filled. |
| 14 | `14_vocabulary_with_meanings.jsonl` | Meanings copied from reference. |
| 15 | `15_final_dictionary.jsonl` | Final dictionary. |

If something looks wrong, check the numbered files from low to high. The first strange file usually shows where the problem started.
