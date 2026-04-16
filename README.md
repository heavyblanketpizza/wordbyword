# wordbyword

Extract Korean vocabulary and expression candidates from PDFs and text files for study workflows.

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

- `tokens.csv`: every tokenizer occurrence with document, page, sentence, form, POS tag, and offsets.
- `vocabulary.csv`: unique educational vocabulary terms from Korean-aware morphology.
- `surface_words.csv`: surface-level words as they appear in the source text.
- `expressions.csv`: surface n-gram expression candidates, including idiom-like phrases.
- `candidate_terms.csv`: deterministically cleaned term candidates with canonical keys, variants, quality scores, and ROI scores.
- `candidate_expressions.csv`: deterministically cleaned expression candidates with canonical keys, variants, quality scores, and ROI scores.
- `rejected_candidates.csv`: candidates removed by deterministic cleanup, with rejection reasons.
- `dictionary_candidates.jsonl`: structured candidate packets for a later LLM curation stage.
- `summary.json`: counts, scanned documents, and output metadata.

Idioms are detected as expression candidates. Exact idiom classification needs a curated idiom dictionary later, because idiomatic meaning cannot be determined reliably from tokenization alone.

## Usage

```bash
uv --no-cache run python -m wordbyword
```

Useful options:

```bash
uv --no-cache run python -m wordbyword --input original --output output
uv --no-cache run python -m wordbyword --min-expression-count 2
uv --no-cache run python -m wordbyword --max-expression-size 6
uv --no-cache run python -m wordbyword --include-punctuation
uv --no-cache run python -m wordbyword --include-numbers
```

The default expression settings save every 2- to 5-word surface phrase candidate. Pure numbers are ignored in vocabulary, surface word, and expression outputs unless `--include-numbers` is set. If the output is too large, raise `--min-expression-count`.

The candidate files are the first deterministic cleanup layer. They keep the raw files intact, then group obvious variants such as particles and endings, normalize verb/adjective stems to dictionary-style forms, reject layout fragments, and assign rough deterministic quality and ROI scores for downstream review.

## Verify

```bash
uv --no-cache run python -m unittest discover -s tests
```
