# wordbyword Beginner's Guide

This guide is the simple version. The technical truth lives in `README.md`.

`wordbyword` helps with three jobs:

1. Make a Korean vocabulary dictionary from source documents.
2. Turn hard class readings into easier Korean handouts.
3. Learn from class handouts after they were used.

## Before You Start

Put dictionary source files here:

```text
build-dictionary/original/
```

Supported dictionary files:

```text
.pdf
.txt
.md
```

Put class source PDFs here:

```text
classroom-printout-studio/class-source-materials/
```

If a step uses Ollama, make sure Ollama is running:

```bash
ollama serve
ollama pull gemma4:31b
```

Check the project:

```bash
uv sync --dev
uv run pytest -q
uv run ruff check .
```

## Build The Dictionary

For normal use, run one command:

```bash
uv --no-cache run python -m wordbyword --build-dictionary --ollama-model gemma4:31b
```

This reads `build-dictionary/original/` and writes the finished dictionary files in `build-dictionary/`.

It checks the saved numbered files first. If a saved step is still valid, it skips that step and continues from the next needed step.

To rebuild everything from the beginning on purpose, use:

```bash
uv --no-cache run python -m wordbyword --build-dictionary --force --ollama-model gemma4:31b
```

The final dictionary is:

```text
build-dictionary/15_final_dictionary.jsonl
```

If you are fixing one stage by hand, these recovery commands still exist:

```bash
uv --no-cache run python -m wordbyword --prep-dictionary
uv --no-cache run python -m wordbyword --clean-example-sentences --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --clean-dictionary-candidates --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --link-cleaned-examples
uv --no-cache run python -m wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --retry-generated-examples --ollama-model gemma4:31b
uv --no-cache run python -m wordbyword --add-reference-meanings
uv --no-cache run python -m wordbyword --generate-missing-meanings --ollama-model gemma4:31b
```

Important: `--prep-dictionary` builds deterministic stages `00` through `09`. Use `--build-dictionary` when you want the finished `15_final_dictionary.jsonl`.

## Make Class Printouts

For normal use, run one command:

```bash
uv --no-cache run python -m wordbyword --build-class-printouts --ollama-model gemma4:31b
```

This does three things:

1. Reads source PDFs with PaddleOCR-VL.
2. Rewrites them into easier Korean with vocabulary help.
3. Makes editable Word handouts.

The Word files are written under:

```text
classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama/
```

Only parse class source materials when you want to inspect OCR output:

```bash
uv --no-cache run python -m wordbyword --parse-source-materials
```

Only print already rewritten materials when you do not want to parse and rewrite again:

```bash
uv --no-cache run python -m wordbyword --print-class-materials --ollama-model gemma4:31b
```

## PaddleOCR-VL

PaddleOCR-VL usually lives in a separate Python environment. If this folder exists, `wordbyword` uses it automatically:

```text
.venv-paddleocr-vl/
```

You should not need to type `PYTHONPATH`.

If you need to choose the sidecar manually:

```bash
uv --no-cache run python -m wordbyword --parse-source-materials --paddleocr-vl-sidecar-env .venv-paddleocr-vl --paddleocr-vl-device cpu
```

## Archive Used Class Handouts

Put used handouts here:

```text
wbw-vocabulary-intelligence/class-session-archive/
```

Preview safe filenames:

```bash
uv --no-cache run python -m wordbyword --rename-class-session-archive --ollama-model gemma4:31b
```

Apply the safe filenames:

```bash
uv --no-cache run python -m wordbyword --rename-class-session-archive --apply-archive-renames --ollama-model gemma4:31b
```

Then count vocabulary from used handouts:

```bash
uv --no-cache run python -m wordbyword --build-usage-signals
```

Update the live classroom vocabulary list:

```bash
uv --no-cache run python -m wordbyword --update-live-lexicon
```

## Make An After-Class Report

After a used handout has a managed archive filename, make one adult-facing report:

```bash
uv --no-cache run python -m wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --ollama-model gemma4:31b
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
