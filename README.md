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
```

Pure numbers are ignored in vocabulary and surface word outputs unless `--include-numbers` is set.

The candidate files are the first deterministic cleanup layer. They keep the raw files intact, then group obvious variants such as particles and endings, normalize verb/adjective stems to dictionary-style forms, reject layout fragments, and assign rough deterministic quality and ROI scores for downstream review.

## Verify

```bash
uv --no-cache run python -m unittest discover -s tests
```
