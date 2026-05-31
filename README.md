# wordbyword

`wordbyword` builds Korean learner-vocabulary artifacts from source documents, rewrites class source materials into grade-friendly Korean, renders editable DOCX handouts, and tracks vocabulary signals from materials used in class.

The root `README.md` is the canonical technical guide. `BEGINNERS_GUIDE.md` is the tracked plain-language guide. Generated output folders do not own project documentation.

## Prerequisites

- `uv` for dependency and command execution.
- Python `>=3.14`, as declared in `pyproject.toml`. Let `uv` provision the interpreter when possible.
- An LLM provider for model-backed workflows. The default is local Ollama with `gemma4:31b`; OpenAI, Anthropic/Claude, and Google/Gemini are also supported for text-only LLM tasks.
- PaddleOCR-VL for class-source PDF parsing. The project dependency set does not install PaddleOCR-VL into the main environment; use a sidecar environment or a current Python environment that can import `paddleocr`.
- A Korean-capable font for DOCX output. The renderer auto-detects common macOS, Linux, and Noto/Nanum fonts; pass an explicit font path only when needed.

## Quick Start

Install and verify the project:

```bash
uv sync --dev
uv run wordbyword --help
uv run pytest -q
uv run ruff check .
```

Command examples use the installed console script, `uv run wordbyword`. Running from source with `uv run python -m wordbyword` is equivalent. The GUI entrypoint is:

```bash
uv run wordbyword-gui
```

Manage dictionary source filenames after adding files to `build-dictionary/original/`:

```bash
uv run wordbyword --rename-dictionary-source-archive --ollama-model gemma4:31b
uv run wordbyword --rename-dictionary-source-archive --apply-dictionary-source-renames --ollama-model gemma4:31b
```

Build the full dictionary from the managed source archive:

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b
```

That command resumes from the latest verified checkpoint, runs only the dirty stages through `15`, stops if generated-example failures remain after `13`, then writes `14_vocabulary_with_meanings.jsonl` and `15_final_dictionary.jsonl`.

Build only the deterministic base when debugging early artifacts:

```bash
uv run wordbyword --prep-dictionary
```

Build class printouts from source materials:

```bash
uv run wordbyword --build-class-printouts --ollama-model gemma4:31b
```

That full class-printout command starts from `classroom-printout-studio/class-source-materials/grade1`, hashes source files before parsing, skips sources already tied to a successful printable DOCX in the class-printout ledger, parses new PDFs with PaddleOCR-VL, rewrites with the canonical live lexicon when it exists, and writes editable DOCX handouts under `classroom-printout-studio/printable-for-class/grade1-ko-a4-ollama/`.

## Local Folders

Tracked placeholders:

- `build-dictionary/original/.gitkeep`
- `build-dictionary/references/.gitkeep`

Local ignored working folders:

- `build-dictionary/`: generated dictionary artifacts plus local managed source/reference files.
- `classroom-printout-studio/`: local class source materials, parsed bundles, rewritten bundles, and printouts.
- `wbw-vocabulary-intelligence/`: local class archive, usage signals, live lexicon, and after-class reports.

Put dictionary source files in:

```text
build-dictionary/original/
```

Then run the dictionary source archive rename dry run and apply command before building. The archive ledger is local and ignored, like the source files themselves.

Put the reference meaning dictionary at:

```text
build-dictionary/references/reference-dictionary.jsonl
```

Put class source PDFs in:

```text
classroom-printout-studio/class-source-materials/grade1/
```

Generated stage outputs are replaceable. The full dictionary build records verified checkpoint cache metadata in `00_summary.json` and reuses saved stages only when schemas, inputs, options, dependencies, and artifact hashes still match. Per-source parse, rewrite, print, and after-class report outputs are regenerated in their output folders.

## Dictionary Workflow

The normal full workflow reads managed `.pdf`, `.txt`, and `.md` files from `build-dictionary/original/` and writes stages `00` through `15`:

```bash
uv run wordbyword --rename-dictionary-source-archive --ollama-model gemma4:31b
uv run wordbyword --rename-dictionary-source-archive --apply-dictionary-source-renames --ollama-model gemma4:31b
```

The rename command is a dry run unless `--apply-dictionary-source-renames` is supplied. It writes `build-dictionary/original/.wordbyword-dictionary-source-ledger.json`, keyed by source SHA-256. Builds reject unmanaged, renamed, changed, or duplicate source files before writing dictionary artifacts.

If the source archive contains exact duplicate files or cross-format exact text duplicates, quarantine duplicates first:

```bash
uv run wordbyword --resolve-dictionary-source-duplicates
uv run wordbyword --resolve-dictionary-source-duplicates --yes
```

The resolver never deletes files. It moves duplicates into `build-dictionary/original/.wordbyword-dictionary-source-duplicates/` and writes an audit manifest.

After sources are managed, build:

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b
```

The full workflow is resumable by default. It prints `SKIP`, `RUN`, or `REBUILD` for each checkpoint and stores cache metadata under `00_summary.json["build_cache"]`. Use `--force` when you intentionally want to ignore valid saved work:

```bash
uv run wordbyword --build-dictionary --force --ollama-model gemma4:31b
```

The full workflow is strict. If `13_generated_example_failures.jsonl` still has unresolved rows, the command returns nonzero and stops before meaning assignment. Repair stage `13` with the recovery commands, then run `--add-reference-meanings` and `--generate-missing-meanings` to finish from the repaired saved artifacts.

The prep command writes deterministic stages `00` through `09` and intentionally does not call Ollama:

```bash
uv run wordbyword --prep-dictionary
```

Useful prep options:

```bash
uv run wordbyword --prep-dictionary --input build-dictionary/original --output build-dictionary
uv run wordbyword --prep-dictionary --include-numbers
uv run wordbyword --prep-dictionary --include-punctuation
```

Recovery and debugging commands reuse saved artifacts:

```bash
uv run wordbyword --clean-example-sentences --ollama-model gemma4:31b
uv run wordbyword --clean-dictionary-candidates --ollama-model gemma4:31b
uv run wordbyword --link-cleaned-examples
uv run wordbyword --generate-missing-examples --ollama-model gemma4:31b
uv run wordbyword --retry-generated-examples --ollama-model gemma4:31b
uv run wordbyword --add-reference-meanings
uv run wordbyword --generate-missing-meanings --ollama-model gemma4:31b
```

Inspect the latest dictionary-shaped JSONL:

```bash
uv run wordbyword --show-distribution
uv run wordbyword --show-distribution build-dictionary/15_final_dictionary.jsonl
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
uv run wordbyword --build-class-printouts --ollama-model gemma4:31b
```

Stage-specific commands are for debugging or rerunning one part of the workflow.

Parse source materials only:

```bash
uv run wordbyword --parse-source-materials
uv run wordbyword --parse-source-material classroom-printout-studio/class-source-materials/grade1/example.pdf
```

The default parse directory is `classroom-printout-studio/class-source-materials/grade1`. PDFs are parsed with PaddleOCR-VL. Non-PDF source-material extensions may be discovered, but only PDFs currently produce the full parsed bundle.

PaddleOCR-VL sidecar behavior:

- Explicit `--paddleocr-vl-sidecar-python` or `--paddleocr-vl-sidecar-env` wins.
- Then `WORDBYWORD_PADDLEOCR_VL_SIDECAR_PYTHON` or `WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV`.
- Then auto-discovery checks `.venv-paddleocr-vl` and `.venv-paddleocr` under the current working directory and project root.
- If no sidecar is found, the current Python environment must be able to import `paddleocr`.
- Users should not need to export `PYTHONPATH`; the sidecar launch injects the repo root.

Useful parse options:

```bash
uv run wordbyword --parse-source-materials --paddleocr-vl-device cpu
uv run wordbyword --parse-source-materials --paddleocr-vl-sidecar-env .venv-paddleocr-vl --paddleocr-vl-device cpu
uv run wordbyword --parse-source-materials --skip-pdf-parse
uv run wordbyword --parse-source-materials --pdf-render-dpi 180
uv run wordbyword --parse-source-materials --paddleocr-vl-rec-backend vllm-server --paddleocr-vl-rec-server-url http://localhost:8000/v1 --paddleocr-vl-rec-api-model-name PaddlePaddle/PaddleOCR-VL-1.5
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
uv run wordbyword --rewrite-vocabulary-guided-source-materials --ollama-model gemma4:31b
uv run wordbyword --rewrite-vocabulary-guided-source-material classroom-printout-studio/class-source-materials/parsed/grade1/example --ollama-model gemma4:31b
uv run wordbyword --rewrite-whole-source-materials --ollama-model gemma4:31b
```

Vocabulary-guided rewrites default to `classroom-printout-studio/class-source-materials/parsed/grade1`. If no `--rewrite-dictionary` is supplied, vocabulary guidance comes from `wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl` when it exists, otherwise `build-dictionary/15_final_dictionary.jsonl`. They write bundles under `classroom-printout-studio/rewritten-for-class/grade1-ko-ollama-vocabulary-guided/`.

`--build-class-printouts` stores workflow state in `classroom-printout-studio/class-source-materials/parsed/grade1/.wordbyword-class-printout-ledger.json`. The ledger is keyed by source file SHA-256, so a renamed source with identical bytes is skipped after a successful printout, while changed bytes under the same filename are treated as a new source. Use `--force-class-printouts` to regenerate parse, rewrite, and print outputs for sources that the ledger would otherwise skip.

Print rewritten materials manually:

```bash
uv run wordbyword --print-class-materials --ollama-model gemma4:31b
uv run wordbyword --print-class-material classroom-printout-studio/rewritten-for-class/grade1-ko-ollama-vocabulary-guided/example --ollama-model gemma4:31b
uv run wordbyword --print-class-materials --print-body-font-size 13
uv run wordbyword --print-class-materials --print-font-path /System/Library/Fonts/AppleSDGothicNeo.ttc
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
uv run wordbyword --rename-class-session-archive --ollama-model gemma4:31b
```

Apply archive renames:

```bash
uv run wordbyword --rename-class-session-archive --apply-archive-renames --ollama-model gemma4:31b
```

The rename command considers direct `.docx` and `.pdf` files only. It never edits document contents, never deletes files, never moves files outside the archive, and never overwrites an existing file.

If the archive contains duplicate class printouts, rename and usage-signal commands stop before writing outputs. Review and quarantine duplicates interactively:

```bash
uv run wordbyword --resolve-archive-duplicates
```

To also review conservative near-duplicate candidates with Ollama before quarantining, include the near-duplicate layer:

```bash
uv run wordbyword --resolve-archive-duplicates --include-near-duplicates --ollama-model gemma4:31b
```

For explicit non-interactive cleanup, apply the recommended quarantine plan:

```bash
uv run wordbyword --resolve-archive-duplicates --yes
```

The resolver never deletes files. It moves duplicates into `wbw-vocabulary-intelligence/class-session-archive/.wordbyword-duplicates/` and writes an audit manifest.

Build deterministic usage signals from managed archive filenames:

```bash
uv run wordbyword --build-usage-signals
```

Update the live grade lexicon:

```bash
uv run wordbyword --update-live-lexicon
```

`--build-usage-signals` writes `00` through `09` usage artifacts under `wbw-vocabulary-intelligence/usage-signal-build/`. `--update-live-lexicon` maintains `wbw-vocabulary-intelligence/live-grade-lexicon/grade1-live-lexicon-canonical.jsonl`, `grade1-live-lexicon-supplemental.jsonl`, and the raw unmatched usage audit file.

## After-Class Reports

Generate one adult-facing Korean report from one managed archived class printout:

```bash
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --ollama-model gemma4:31b
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
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --after-class-report-output-root wbw-vocabulary-intelligence/after-class-reports/grade1-ko-a4-ollama
uv run wordbyword --generate-after-class-reading-report 2026-05-10_grade1-reading-apples.docx --after-class-report-font-path /System/Library/Fonts/AppleSDGothicNeo.ttc
```

## LLM Configuration

Model-backed workflows default to local Ollama:

```bash
uv run wordbyword --build-dictionary --llm-provider ollama --llm-model gemma4:31b
uv run wordbyword --build-dictionary --llm-provider ollama --llm-model gemma4:31b --llm-base-url http://localhost:11434
```

The old Ollama flags still work as aliases:

```bash
uv run wordbyword --build-dictionary --ollama-model gemma4:31b --ollama-base-url http://localhost:11434
```

For hosted text-only LLM tasks, choose a provider and set its API key in the environment. API keys are not stored in GUI settings or project files.

```bash
export OPENAI_API_KEY=...
uv run wordbyword --build-dictionary --llm-provider openai --llm-model gpt-4o

export ANTHROPIC_API_KEY=...
uv run wordbyword --rename-class-session-archive --llm-provider claude --llm-model claude-3-5-sonnet-20240620

export GEMINI_API_KEY=...
uv run wordbyword --generate-missing-meanings --llm-provider google --llm-model gemini-1.5-pro
```

Provider aliases are accepted: `anthropic` maps to `claude`, and `gemini` maps to `google`. Non-Ollama providers are text-first in this release. Image-backed source-material rewrites and printable image selection still require an Ollama vision-capable model.

## Verification

```bash
uv run pytest -q
uv run ruff check .
```

Run the live Ollama smoke test only when a local `gemma4:31b` is available:

```bash
WORDBYWORD_RUN_OLLAMA_LIVE_TESTS=1 uv run python -m unittest tests.test_llm.LiveOllamaIntegrationTest
```
