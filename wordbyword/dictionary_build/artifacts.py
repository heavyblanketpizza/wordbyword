from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ArtifactFormat(StrEnum):
    CSV = "csv"
    JSONL = "jsonl"
    JSON = "json"
    TEXT = "text"


class ArtifactId(StrEnum):
    SUMMARY = "summary"
    EXTRACTED_PAGES = "extracted_pages"
    SENTENCES = "sentences"
    TOKENS = "tokens"
    SURFACE_WORDS = "surface_words"
    VOCABULARY_RAW = "vocabulary_raw"
    CANDIDATE_TERMS = "candidate_terms"
    REJECTED_TERMS = "rejected_terms"
    DICTIONARY_CANDIDATES = "dictionary_candidates"
    EXAMPLE_SENTENCES = "example_sentences"
    CLEANED_EXAMPLE_SENTENCES = "cleaned_example_sentences"
    CLEANED_DICTIONARY_CANDIDATES = "cleaned_dictionary_candidates"
    LINKED_DICTIONARY_CANDIDATES = "linked_dictionary_candidates"
    GENERATED_EXAMPLE_DICTIONARY = "generated_example_dictionary"
    GENERATED_EXAMPLE_ATTEMPTS = "generated_example_attempts"
    GENERATED_EXAMPLE_FAILURES = "generated_example_failures"
    MEANING_DICTIONARY_CANDIDATES = "meaning_dictionary_candidates"
    FINAL_DICTIONARY = "final_dictionary"


@dataclass(frozen=True)
class ArtifactSpec:
    id: ArtifactId
    filename: str
    format: ArtifactFormat
    stage: str | None = None
    description: str = ""
    event_name: str = ""
    sidecar_of: ArtifactId | None = None

    @property
    def is_pipeline_stage(self) -> bool:
        return self.stage is not None and bool(self.description)


BASE_ARTIFACT_IDS: tuple[ArtifactId, ...] = (
    ArtifactId.SUMMARY,
    ArtifactId.EXTRACTED_PAGES,
    ArtifactId.SENTENCES,
    ArtifactId.TOKENS,
    ArtifactId.SURFACE_WORDS,
    ArtifactId.VOCABULARY_RAW,
    ArtifactId.CANDIDATE_TERMS,
    ArtifactId.REJECTED_TERMS,
    ArtifactId.DICTIONARY_CANDIDATES,
    ArtifactId.EXAMPLE_SENTENCES,
)

OPTIONAL_ARTIFACT_IDS: tuple[ArtifactId, ...] = (
    ArtifactId.CLEANED_EXAMPLE_SENTENCES,
    ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
    ArtifactId.LINKED_DICTIONARY_CANDIDATES,
    ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
    ArtifactId.GENERATED_EXAMPLE_FAILURES,
    ArtifactId.MEANING_DICTIONARY_CANDIDATES,
    ArtifactId.FINAL_DICTIONARY,
)

LEGACY_OUTPUT_FILES: tuple[str, ...] = (
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
    "README.md",
)

_ARTIFACT_SPECS: dict[ArtifactId, ArtifactSpec] = {
    ArtifactId.SUMMARY: ArtifactSpec(
        id=ArtifactId.SUMMARY,
        filename="00_summary.json",
        format=ArtifactFormat.JSON,
        stage="00",
        description="Run metadata, counts, and this ordered stage map.",
    ),
    ArtifactId.EXTRACTED_PAGES: ArtifactSpec(
        id=ArtifactId.EXTRACTED_PAGES,
        filename="01_extracted_pages.csv",
        format=ArtifactFormat.CSV,
        stage="01",
        description="Cleaned page-level text extracted from each PDF or text source.",
    ),
    ArtifactId.SENTENCES: ArtifactSpec(
        id=ArtifactId.SENTENCES,
        filename="02_sentences.csv",
        format=ArtifactFormat.CSV,
        stage="02",
        description="Sentence-like chunks after layout cleanup and noise filtering.",
    ),
    ArtifactId.TOKENS: ArtifactSpec(
        id=ArtifactId.TOKENS,
        filename="03_tokens.csv",
        format=ArtifactFormat.CSV,
        stage="03",
        description="Kiwi tokenizer output with forms, POS tags, offsets, and source sentence.",
    ),
    ArtifactId.SURFACE_WORDS: ArtifactSpec(
        id=ArtifactId.SURFACE_WORDS,
        filename="04_surface_words.csv",
        format=ArtifactFormat.CSV,
        stage="04",
        description="Visible word forms as they appeared in the cleaned source text.",
    ),
    ArtifactId.VOCABULARY_RAW: ArtifactSpec(
        id=ArtifactId.VOCABULARY_RAW,
        filename="05_vocabulary_raw.csv",
        format=ArtifactFormat.CSV,
        stage="05",
        description="Raw lexical vocabulary terms counted from Kiwi morphology.",
    ),
    ArtifactId.CANDIDATE_TERMS: ArtifactSpec(
        id=ArtifactId.CANDIDATE_TERMS,
        filename="06_candidate_terms.csv",
        format=ArtifactFormat.CSV,
        stage="06",
        description="Cleaned, grouped, and scored word candidates.",
    ),
    ArtifactId.REJECTED_TERMS: ArtifactSpec(
        id=ArtifactId.REJECTED_TERMS,
        filename="07_rejected_terms.csv",
        format=ArtifactFormat.CSV,
        stage="07",
        description="Vocabulary terms removed by deterministic cleanup rules.",
    ),
    ArtifactId.DICTIONARY_CANDIDATES: ArtifactSpec(
        id=ArtifactId.DICTIONARY_CANDIDATES,
        filename="08_dictionary_candidates.jsonl",
        format=ArtifactFormat.JSONL,
        stage="08",
        description="Vocabulary candidate packets for later manual or LLM curation.",
    ),
    ArtifactId.EXAMPLE_SENTENCES: ArtifactSpec(
        id=ArtifactId.EXAMPLE_SENTENCES,
        filename="09_example_sentences.csv",
        format=ArtifactFormat.CSV,
        stage="09",
        description=(
            "High-signal sentence catalog filtered by canonical-key coverage, density, "
            "sentence shape, and normalized quality scores."
        ),
    ),
    ArtifactId.CLEANED_EXAMPLE_SENTENCES: ArtifactSpec(
        id=ArtifactId.CLEANED_EXAMPLE_SENTENCES,
        filename="10_cleaned_example_sentences.csv",
        format=ArtifactFormat.CSV,
        stage="10",
        description="Ollama-curated source sentences kept, lightly fixed, rebuilt, or deleted with minimal-edit bias.",
    ),
    ArtifactId.CLEANED_DICTIONARY_CANDIDATES: ArtifactSpec(
        id=ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        filename="11_cleaned_dictionary_candidates.jsonl",
        format=ArtifactFormat.JSONL,
        stage="11",
        description="Ollama-filtered learner-dictionary candidates with low-signal names and debris removed.",
    ),
    ArtifactId.LINKED_DICTIONARY_CANDIDATES: ArtifactSpec(
        id=ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        filename="12_dictionary_candidates_with_cleaned_examples.jsonl",
        format=ArtifactFormat.JSONL,
        stage="12",
        description="Dictionary candidates whose examples are replaced with unique cleaned sentences from stage 10 when available.",
    ),
    ArtifactId.GENERATED_EXAMPLE_DICTIONARY: ArtifactSpec(
        id=ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        filename="13_vocabulary_with_generated_examples.jsonl",
        format=ArtifactFormat.JSONL,
        stage="13",
        description="Selected vocabulary with new Ollama-written examples for rows that still lacked a cleaned replacement.",
    ),
    ArtifactId.GENERATED_EXAMPLE_ATTEMPTS: ArtifactSpec(
        id=ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        filename="13_generated_example_attempts.jsonl",
        format=ArtifactFormat.JSONL,
        sidecar_of=ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ),
    ArtifactId.GENERATED_EXAMPLE_FAILURES: ArtifactSpec(
        id=ArtifactId.GENERATED_EXAMPLE_FAILURES,
        filename="13_generated_example_failures.jsonl",
        format=ArtifactFormat.JSONL,
        sidecar_of=ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
    ),
    ArtifactId.MEANING_DICTIONARY_CANDIDATES: ArtifactSpec(
        id=ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        filename="14_vocabulary_with_meanings.jsonl",
        format=ArtifactFormat.JSONL,
        stage="14",
        description="Confirmed vocabulary enriched with reference-dictionary meanings when canonical keys match.",
    ),
    ArtifactId.FINAL_DICTIONARY: ArtifactSpec(
        id=ArtifactId.FINAL_DICTIONARY,
        filename="15_final_dictionary.jsonl",
        format=ArtifactFormat.JSONL,
        stage="15",
        description="Final dictionary with Ollama-written meanings for rows that still had empty meanings.",
    ),
}


def artifact_spec(artifact_id: ArtifactId) -> ArtifactSpec:
    return _ARTIFACT_SPECS[artifact_id]


def artifact_filename(artifact_id: ArtifactId) -> str:
    return artifact_spec(artifact_id).filename


def artifact_path(output_dir: Path, artifact_id: ArtifactId) -> Path:
    return output_dir / artifact_filename(artifact_id)


def artifact_event_name(artifact_id: ArtifactId) -> str:
    spec = artifact_spec(artifact_id)
    return spec.event_name or Path(spec.filename).stem


def artifact_exists(output_dir: Path, artifact_id: ArtifactId) -> bool:
    return artifact_path(output_dir, artifact_id).is_file()


def required_artifact_path(output_dir: Path, artifact_id: ArtifactId, *, target: str) -> Path:
    path = artifact_path(output_dir, artifact_id)
    if not path.is_file():
        filename = artifact_filename(artifact_id)
        raise ValueError(
            f"Cannot build {target} because {filename} is missing in {output_dir}. "
            "Run the deterministic pipeline first."
        )
    return path


def existing_artifact_ids(output_dir: Path) -> list[ArtifactId]:
    return [
        artifact_id
        for artifact_id in (*BASE_ARTIFACT_IDS, *OPTIONAL_ARTIFACT_IDS)
        if artifact_exists(output_dir, artifact_id)
    ]


def all_output_artifact_ids() -> list[ArtifactId]:
    return [*BASE_ARTIFACT_IDS, *OPTIONAL_ARTIFACT_IDS]


def legacy_output_filenames() -> list[str]:
    return list(LEGACY_OUTPUT_FILES)


def output_artifact_ids(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_generated_example_attempts: bool | None = None,
    include_generated_example_failures: bool | None = None,
    include_meaning_dictionary_candidates: bool = False,
    include_generated_meaning_dictionary_candidates: bool = False,
) -> list[ArtifactId]:
    return [
        *BASE_ARTIFACT_IDS,
        *([ArtifactId.CLEANED_EXAMPLE_SENTENCES] if include_cleaned_example_sentences else []),
        *([ArtifactId.CLEANED_DICTIONARY_CANDIDATES] if include_cleaned_dictionary_candidates else []),
        *([ArtifactId.LINKED_DICTIONARY_CANDIDATES] if include_linked_dictionary_candidates else []),
        *([ArtifactId.GENERATED_EXAMPLE_DICTIONARY] if include_generated_dictionary_candidates else []),
        *(
            [ArtifactId.GENERATED_EXAMPLE_ATTEMPTS]
            if include_generated_dictionary_candidates and include_generated_example_attempts is not False
            else []
        ),
        *(
            [ArtifactId.GENERATED_EXAMPLE_FAILURES]
            if include_generated_dictionary_candidates and include_generated_example_failures is not False
            else []
        ),
        *([ArtifactId.MEANING_DICTIONARY_CANDIDATES] if include_meaning_dictionary_candidates else []),
        *([ArtifactId.FINAL_DICTIONARY] if include_generated_meaning_dictionary_candidates else []),
    ]


def output_filenames(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_generated_example_attempts: bool | None = None,
    include_generated_example_failures: bool | None = None,
    include_meaning_dictionary_candidates: bool = False,
    include_generated_meaning_dictionary_candidates: bool = False,
) -> list[str]:
    return [
        artifact_filename(artifact_id)
        for artifact_id in output_artifact_ids(
            include_cleaned_example_sentences=include_cleaned_example_sentences,
            include_cleaned_dictionary_candidates=include_cleaned_dictionary_candidates,
            include_linked_dictionary_candidates=include_linked_dictionary_candidates,
            include_generated_dictionary_candidates=include_generated_dictionary_candidates,
            include_generated_example_attempts=include_generated_example_attempts,
            include_generated_example_failures=include_generated_example_failures,
            include_meaning_dictionary_candidates=include_meaning_dictionary_candidates,
            include_generated_meaning_dictionary_candidates=include_generated_meaning_dictionary_candidates,
        )
    ]


def pipeline_stage_rows(
    *,
    include_cleaned_example_sentences: bool = False,
    include_cleaned_dictionary_candidates: bool = False,
    include_linked_dictionary_candidates: bool = False,
    include_generated_dictionary_candidates: bool = False,
    include_meaning_dictionary_candidates: bool = False,
    include_generated_meaning_dictionary_candidates: bool = False,
) -> list[dict[str, str]]:
    artifact_ids = [
        *BASE_ARTIFACT_IDS,
        *([ArtifactId.CLEANED_EXAMPLE_SENTENCES] if include_cleaned_example_sentences else []),
        *([ArtifactId.CLEANED_DICTIONARY_CANDIDATES] if include_cleaned_dictionary_candidates else []),
        *([ArtifactId.LINKED_DICTIONARY_CANDIDATES] if include_linked_dictionary_candidates else []),
        *([ArtifactId.GENERATED_EXAMPLE_DICTIONARY] if include_generated_dictionary_candidates else []),
        *([ArtifactId.MEANING_DICTIONARY_CANDIDATES] if include_meaning_dictionary_candidates else []),
        *([ArtifactId.FINAL_DICTIONARY] if include_generated_meaning_dictionary_candidates else []),
    ]
    rows: list[dict[str, str]] = []
    for artifact_id in artifact_ids:
        spec = artifact_spec(artifact_id)
        if spec.is_pipeline_stage:
            rows.append(
                {
                    "stage": spec.stage or "",
                    "file": spec.filename,
                    "description": spec.description,
                }
            )
    return rows


def optional_artifacts_from(first_artifact_id: ArtifactId) -> list[ArtifactId]:
    if first_artifact_id not in OPTIONAL_ARTIFACT_IDS:
        raise ValueError(f"Unknown optional output artifact: {first_artifact_id}")
    start_index = OPTIONAL_ARTIFACT_IDS.index(first_artifact_id)
    return list(OPTIONAL_ARTIFACT_IDS[start_index:])
