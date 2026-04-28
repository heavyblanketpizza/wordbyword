from __future__ import annotations

BASE_OUTPUT_FILES = [
    "00_summary.json",
    "01_extracted_pages.csv",
    "02_sentences.csv",
    "03_tokens.csv",
    "04_surface_words.csv",
    "05_vocabulary_raw.csv",
    "06_candidate_terms.csv",
    "07_rejected_terms.csv",
    "08_dictionary_candidates.jsonl",
    "09_example_sentences.csv",
    "README.md",
]

OPTIONAL_OUTPUT_FILES = [
    "10_cleaned_example_sentences.csv",
    "11_cleaned_dictionary_candidates.jsonl",
    "12_dictionary_candidates_with_cleaned_examples.jsonl",
    "13_dictionary_candidates_with_generated_examples.jsonl",
    "13_generated_example_attempts.jsonl",
    "13_generated_example_failures.jsonl",
    "14_dictionary_candidates_with_meanings.jsonl",
    "14_dictionary_meaning_attempts.jsonl",
    "14_dictionary_meaning_failures.jsonl",
]

LEGACY_OUTPUT_FILES = [
    "summary.json",
    "sentences.csv",
    "tokens.csv",
    "surface_words.csv",
    "vocabulary.csv",
    "expressions.csv",
    "candidate_terms.csv",
    "candidate_expressions.csv",
    "rejected_candidates.csv",
    "dictionary_candidates.jsonl",
    "06_expressions_raw.csv",
    "07_candidate_terms.csv",
    "08_candidate_expressions.csv",
    "09_rejected_candidates.csv",
    "10_dictionary_candidates.jsonl",
    "filtered_example_sentence.csv",
]

BASE_PIPELINE_STAGES = [
    {
        "stage": "00",
        "file": "00_summary.json",
        "description": "Run metadata, counts, and this ordered stage map.",
    },
    {
        "stage": "01",
        "file": "01_extracted_pages.csv",
        "description": "Cleaned page-level text extracted from each PDF or text source.",
    },
    {
        "stage": "02",
        "file": "02_sentences.csv",
        "description": "Sentence-like chunks after layout cleanup and noise filtering.",
    },
    {
        "stage": "03",
        "file": "03_tokens.csv",
        "description": "Kiwi tokenizer output with forms, POS tags, offsets, and source sentence.",
    },
    {
        "stage": "04",
        "file": "04_surface_words.csv",
        "description": "Visible word forms as they appeared in the cleaned source text.",
    },
    {
        "stage": "05",
        "file": "05_vocabulary_raw.csv",
        "description": "Raw lexical vocabulary terms counted from Kiwi morphology.",
    },
    {
        "stage": "06",
        "file": "06_candidate_terms.csv",
        "description": "Cleaned, grouped, and scored word candidates.",
    },
    {
        "stage": "07",
        "file": "07_rejected_terms.csv",
        "description": "Vocabulary terms removed by deterministic cleanup rules.",
    },
    {
        "stage": "08",
        "file": "08_dictionary_candidates.jsonl",
        "description": "Vocabulary candidate packets for later manual or LLM curation.",
    },
    {
        "stage": "09",
        "file": "09_example_sentences.csv",
        "description": (
            "High-signal sentence catalog filtered by canonical-key coverage, density, "
            "sentence shape, and normalized quality scores."
        ),
    },
]

OPTIONAL_PIPELINE_STAGES = [
    {
        "stage": "10",
        "file": "10_cleaned_example_sentences.csv",
        "description": "Ollama-curated source sentences kept, lightly fixed, rebuilt, or deleted with minimal-edit bias.",
    },
    {
        "stage": "11",
        "file": "11_cleaned_dictionary_candidates.jsonl",
        "description": "Ollama-filtered learner-dictionary candidates with low-signal names and debris removed.",
    },
    {
        "stage": "12",
        "file": "12_dictionary_candidates_with_cleaned_examples.jsonl",
        "description": "Dictionary candidates whose examples are replaced with unique cleaned sentences from stage 10 when available.",
    },
    {
        "stage": "13",
        "file": "13_dictionary_candidates_with_generated_examples.jsonl",
        "description": "Stage-12 dictionary candidates with new Ollama-written candidate examples for rows that still lacked a cleaned replacement.",
    },
    {
        "stage": "14",
        "file": "14_dictionary_candidates_with_meanings.jsonl",
        "description": (
            "Stage-13 dictionary candidates with a 'meaning' field populated from the bundled "
            "한국어기초사전 lookup, falling back to N/A for terms that need an Ollama-written meaning."
        ),
    },
]


def all_output_files(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_dictionary_meanings: bool = False,
) -> list[str]:
    return [
        *BASE_OUTPUT_FILES,
        *(["10_cleaned_example_sentences.csv"] if include_cleaned_example_sentences else []),
        *(["11_cleaned_dictionary_candidates.jsonl"] if include_cleaned_dictionary_candidates else []),
        *(
            ["12_dictionary_candidates_with_cleaned_examples.jsonl"]
            if include_linked_dictionary_candidates
            else []
        ),
        *(
            ["13_dictionary_candidates_with_generated_examples.jsonl"]
            if include_generated_dictionary_candidates
            else []
        ),
        *(
            [
                "13_generated_example_attempts.jsonl",
                "13_generated_example_failures.jsonl",
            ]
            if include_generated_dictionary_candidates
            else []
        ),
        *(
            [
                "14_dictionary_candidates_with_meanings.jsonl",
                "14_dictionary_meaning_attempts.jsonl",
                "14_dictionary_meaning_failures.jsonl",
            ]
            if include_dictionary_meanings
            else []
        ),
    ]


def pipeline_stages(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_dictionary_meanings: bool = False,
) -> list[dict[str, str]]:
    return [
        *BASE_PIPELINE_STAGES,
        *([OPTIONAL_PIPELINE_STAGES[0]] if include_cleaned_example_sentences else []),
        *([OPTIONAL_PIPELINE_STAGES[1]] if include_cleaned_dictionary_candidates else []),
        *([OPTIONAL_PIPELINE_STAGES[2]] if include_linked_dictionary_candidates else []),
        *([OPTIONAL_PIPELINE_STAGES[3]] if include_generated_dictionary_candidates else []),
        *([OPTIONAL_PIPELINE_STAGES[4]] if include_dictionary_meanings else []),
    ]
