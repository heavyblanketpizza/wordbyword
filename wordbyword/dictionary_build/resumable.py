from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from kiwipiepy import Kiwi

from wordbyword.dictionary_build.analyzer import (
    SURFACE_WORD_RE,
    KoreanAnalyzer,
    is_number_only,
    is_vocabulary_token,
)
from wordbyword.dictionary_build.artifacts import (
    ArtifactFormat,
    ArtifactId,
    artifact_event_name,
    artifact_filename,
    artifact_path,
    artifact_spec,
    legacy_output_filenames,
)
from wordbyword.dictionary_build.candidates import build_candidate_outputs
from wordbyword.dictionary_build.example_cleanup import CleanupProgressCallback
from wordbyword.dictionary_build.example_sentences import example_sentence_rows
from wordbyword.dictionary_build.llm import (
    SupportsCompletion,
    ollama_client,
    unload_model_if_supported,
)
from wordbyword.dictionary_build.models import CountEntry, PageText
from wordbyword.dictionary_build.output_files import (
    candidate_term_fields,
    example_sentence_fields,
    page_fields,
    page_rows,
    parsed_document_count,
    read_jsonl,
    read_rows,
    rejected_candidate_fields,
    sentence_fields,
    token_fields,
    write_jsonl,
    write_rows,
    write_surface_words,
    write_vocabulary,
)
from wordbyword.dictionary_build.output_metadata import refresh_saved_output_metadata
from wordbyword.dictionary_build.pipeline import PipelineResult, load_pipeline_result_from_output
from wordbyword.dictionary_build.readers import discover_documents, document_id_for, read_document
from wordbyword.dictionary_build.saved_outputs import (
    write_cleanup_outputs_from_saved,
    write_generated_meaning_dictionary_candidates_from_saved,
    write_linked_dictionary_candidates_from_saved,
    write_meaning_dictionary_candidates_from_saved,
)
from wordbyword.dictionary_build.source_archive import validate_managed_dictionary_source_archive
from wordbyword.dictionary_build.output_metadata import RENAMED_SUMMARY_KEYS
from wordbyword.dictionary_build.artifacts import output_filenames, pipeline_stage_rows
from wordbyword.grade_profiles import DEFAULT_TARGET_GRADE, normalize_target_grade

ArtifactCallback = Callable[[str, Sequence[Mapping[str, object]]], None]
ProgressCallback = Callable[[str, str], None]
CheckpointStatusCallback = Callable[["CheckpointStatus"], None]

CACHE_SCHEMA_VERSION = 1
DETERMINISTIC_CACHE_VERSION = "deterministic-v1"
LLM_CACHE_VERSION = "llm-v1"


class CheckpointAction(StrEnum):
    SKIP = "SKIP"
    RUN = "RUN"
    REBUILD = "REBUILD"


@dataclass(frozen=True)
class CheckpointSpec:
    key: str
    label: str
    artifact_ids: tuple[ArtifactId, ...]
    dependency_keys: tuple[str, ...] = ()
    stage_version: str = DETERMINISTIC_CACHE_VERSION


@dataclass(frozen=True)
class CheckpointStatus:
    key: str
    label: str
    action: CheckpointAction
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str
    adopted: bool = False


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass
class BuildState:
    target_grade: str
    documents: list[Path]
    source_fingerprints: list[SourceFingerprint]
    managed_source_metadata_by_sha256: dict[str, Mapping[str, object]]
    source_digest: str
    options: dict[str, object]
    options_digest: str
    cache: dict[str, Any]
    checkpoint_digests: dict[str, str]
    pages: list[PageText] | None = None
    sentences: list[dict[str, object]] | None = None
    tokens: list[dict[str, object]] | None = None
    vocabulary: dict[tuple[str, str], CountEntry] | None = None
    surface_words: dict[str, CountEntry] | None = None
    document_totals: Counter[str] | None = None
    candidate_terms: list[dict[str, object]] | None = None


@dataclass(frozen=True)
class ResumableDictionaryBuildResult:
    pipeline_result: PipelineResult
    statuses: tuple[CheckpointStatus, ...]


class ResumableBuildError(RuntimeError):
    pass


class UnresolvedGeneratedExampleFailures(ResumableBuildError):
    def __init__(self, count: int, path: Path) -> None:
        super().__init__(f"stage 13 left {count:,} unresolved generated-example failure(s)")
        self.count = count
        self.path = path


CHECKPOINTS: tuple[CheckpointSpec, ...] = (
    CheckpointSpec("01", "extracted pages", (ArtifactId.EXTRACTED_PAGES,)),
    CheckpointSpec(
        "02-05",
        "analysis tables",
        (
            ArtifactId.SENTENCES,
            ArtifactId.TOKENS,
            ArtifactId.SURFACE_WORDS,
            ArtifactId.VOCABULARY_RAW,
        ),
        dependency_keys=("01",),
    ),
    CheckpointSpec(
        "06-08",
        "candidate tables",
        (
            ArtifactId.CANDIDATE_TERMS,
            ArtifactId.REJECTED_TERMS,
            ArtifactId.DICTIONARY_CANDIDATES,
        ),
        dependency_keys=("02-05",),
    ),
    CheckpointSpec(
        "09",
        "example sentence catalog",
        (ArtifactId.EXAMPLE_SENTENCES,),
        dependency_keys=("02-05", "06-08"),
    ),
    CheckpointSpec(
        "10",
        "cleaned examples",
        (ArtifactId.CLEANED_EXAMPLE_SENTENCES,),
        dependency_keys=("06-08", "09"),
        stage_version=LLM_CACHE_VERSION,
    ),
    CheckpointSpec(
        "11",
        "cleaned dictionary candidates",
        (ArtifactId.CLEANED_DICTIONARY_CANDIDATES,),
        dependency_keys=("06-08", "10"),
        stage_version=LLM_CACHE_VERSION,
    ),
    CheckpointSpec(
        "12",
        "linked cleaned examples",
        (ArtifactId.LINKED_DICTIONARY_CANDIDATES,),
        dependency_keys=("10", "11"),
    ),
    CheckpointSpec(
        "13",
        "generated examples",
        (ArtifactId.GENERATED_EXAMPLE_DICTIONARY,),
        dependency_keys=("06-08", "12"),
        stage_version=LLM_CACHE_VERSION,
    ),
    CheckpointSpec(
        "14",
        "reference meanings",
        (ArtifactId.MEANING_DICTIONARY_CANDIDATES,),
        dependency_keys=("13",),
    ),
    CheckpointSpec(
        "15",
        "final dictionary",
        (ArtifactId.FINAL_DICTIONARY,),
        dependency_keys=("14",),
        stage_version=LLM_CACHE_VERSION,
    ),
)

CHECKPOINT_BY_KEY = {checkpoint.key: checkpoint for checkpoint in CHECKPOINTS}

CHECKPOINT_ARTIFACT_IDS_FROM: dict[str, tuple[ArtifactId, ...]] = {
    "01": (
        ArtifactId.EXTRACTED_PAGES,
        ArtifactId.SENTENCES,
        ArtifactId.TOKENS,
        ArtifactId.SURFACE_WORDS,
        ArtifactId.VOCABULARY_RAW,
        ArtifactId.CANDIDATE_TERMS,
        ArtifactId.REJECTED_TERMS,
        ArtifactId.DICTIONARY_CANDIDATES,
        ArtifactId.EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "02-05": (
        ArtifactId.SENTENCES,
        ArtifactId.TOKENS,
        ArtifactId.SURFACE_WORDS,
        ArtifactId.VOCABULARY_RAW,
        ArtifactId.CANDIDATE_TERMS,
        ArtifactId.REJECTED_TERMS,
        ArtifactId.DICTIONARY_CANDIDATES,
        ArtifactId.EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "06-08": (
        ArtifactId.CANDIDATE_TERMS,
        ArtifactId.REJECTED_TERMS,
        ArtifactId.DICTIONARY_CANDIDATES,
        ArtifactId.EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "09": (
        ArtifactId.EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "10": (
        ArtifactId.CLEANED_EXAMPLE_SENTENCES,
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "11": (
        ArtifactId.CLEANED_DICTIONARY_CANDIDATES,
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "12": (
        ArtifactId.LINKED_DICTIONARY_CANDIDATES,
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "13": (
        ArtifactId.GENERATED_EXAMPLE_DICTIONARY,
        ArtifactId.GENERATED_EXAMPLE_ATTEMPTS,
        ArtifactId.GENERATED_EXAMPLE_FAILURES,
        ArtifactId.MEANING_DICTIONARY_CANDIDATES,
        ArtifactId.FINAL_DICTIONARY,
    ),
    "14": (ArtifactId.MEANING_DICTIONARY_CANDIDATES, ArtifactId.FINAL_DICTIONARY),
    "15": (ArtifactId.FINAL_DICTIONARY,),
}


def build_resumable_dictionary(
    *,
    target_grade: str = DEFAULT_TARGET_GRADE,
    input_dir: Path,
    output_dir: Path,
    reference_dictionary_path: Path,
    include_punctuation: bool,
    include_numbers: bool,
    model: str,
    base_url: str | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
    cleanup_progress: CleanupProgressCallback | None = None,
    dictionary_cleanup_progress: CleanupProgressCallback | None = None,
    generated_example_progress: CleanupProgressCallback | None = None,
    generated_meaning_progress: CleanupProgressCallback | None = None,
    artifact_callback: ArtifactCallback | None = None,
    checkpoint_status: CheckpointStatusCallback | None = None,
    target_checkpoint: str = "15",
    enforce_managed_sources: bool = True,
) -> ResumableDictionaryBuildResult:
    if target_checkpoint not in CHECKPOINT_BY_KEY:
        valid_targets = ", ".join(checkpoint.key for checkpoint in CHECKPOINTS)
        raise ValueError(f"Unknown dictionary build target checkpoint: {target_checkpoint}. Valid targets: {valid_targets}.")

    resolved_target_grade = normalize_target_grade(target_grade)
    documents = discover_documents(input_dir)
    managed_source_validation = (
        validate_managed_dictionary_source_archive(input_dir, documents=documents)
        if enforce_managed_sources
        else None
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    source_fingerprints = fingerprint_documents(documents, input_dir)
    options = build_options(
        target_grade=resolved_target_grade,
        input_dir=input_dir,
        include_punctuation=include_punctuation,
        include_numbers=include_numbers,
    )
    state = BuildState(
        target_grade=resolved_target_grade,
        documents=documents,
        source_fingerprints=source_fingerprints,
        managed_source_metadata_by_sha256=(
            dict(managed_source_validation.metadata_by_sha256)
            if managed_source_validation is not None
            else {}
        ),
        source_digest=stable_hash([fingerprint.__dict__ for fingerprint in source_fingerprints]),
        options=options,
        options_digest=stable_hash(options),
        cache=load_build_cache(output_dir),
        checkpoint_digests={},
    )
    statuses: list[CheckpointStatus] = []
    dirty_found = force
    shared_cleanup_client: SupportsCompletion | None = None
    generated_meaning_client: SupportsCompletion | None = None

    def get_shared_cleanup_client() -> SupportsCompletion:
        nonlocal shared_cleanup_client
        if shared_cleanup_client is None:
            shared_cleanup_client = ollama_client(
                model=model,
                base_url=base_url,
                timeout_seconds=300.0,
                max_output_tokens=256,
            )
        return shared_cleanup_client

    def get_generated_meaning_client() -> SupportsCompletion:
        nonlocal generated_meaning_client
        if generated_meaning_client is None:
            generated_meaning_client = ollama_client(
                model=model,
                base_url=base_url,
                timeout_seconds=300.0,
                max_output_tokens=4096,
            )
        return generated_meaning_client

    try:
        for checkpoint in checkpoints_through(target_checkpoint):
            validation = validate_checkpoint(
                checkpoint,
                output_dir=output_dir,
                reference_dictionary_path=reference_dictionary_path,
                state=state,
                model=model,
            )
            should_run = dirty_found or not validation.valid
            if should_run:
                action = (
                    CheckpointAction.REBUILD
                    if checkpoint_has_any_artifact(output_dir, checkpoint)
                    else CheckpointAction.RUN
                )
                reason = "forced" if force else validation.reason
                if dirty_found and validation.valid and not force:
                    reason = "upstream checkpoint rebuilt"
                emit_checkpoint_status(
                    statuses,
                    CheckpointStatus(checkpoint.key, checkpoint.label, action, reason),
                    checkpoint_status,
                )
                remove_artifacts_from_checkpoint(output_dir, checkpoint.key)
                run_checkpoint(
                    checkpoint,
                    output_dir=output_dir,
                    input_dir=input_dir,
                    reference_dictionary_path=reference_dictionary_path,
                    include_punctuation=include_punctuation,
                    include_numbers=include_numbers,
                    model=model,
                    progress=progress,
                    cleanup_progress=cleanup_progress,
                    dictionary_cleanup_progress=dictionary_cleanup_progress,
                    generated_example_progress=generated_example_progress,
                    generated_meaning_progress=generated_meaning_progress,
                    artifact_callback=artifact_callback,
                    get_shared_cleanup_client=get_shared_cleanup_client,
                    get_generated_meaning_client=get_generated_meaning_client,
                    state=state,
                )
                refresh_base_summary(output_dir, state)
                refresh_saved_output_metadata(output_dir)
                update_checkpoint_cache(
                    checkpoint,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_dictionary_path,
                    state=state,
                    model=model,
                )
                save_build_cache(output_dir, state)
                dirty_found = True
            else:
                reason = "legacy adopted" if validation.adopted else validation.reason
                emit_checkpoint_status(
                    statuses,
                    CheckpointStatus(checkpoint.key, checkpoint.label, CheckpointAction.SKIP, reason),
                    checkpoint_status,
                )
                update_checkpoint_cache(
                    checkpoint,
                    output_dir=output_dir,
                    reference_dictionary_path=reference_dictionary_path,
                    state=state,
                    model=model,
                )
                if validation.adopted:
                    save_build_cache(output_dir, state)

            if checkpoint.key == "13":
                failure_count = generated_example_failure_count(output_dir)
                if failure_count:
                    raise UnresolvedGeneratedExampleFailures(
                        failure_count,
                        artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_FAILURES),
                    )
    finally:
        unload_client(shared_cleanup_client)
        unload_client(generated_meaning_client)

    remove_artifacts_after_checkpoint(output_dir, target_checkpoint)
    remove_checkpoint_cache_after(state, target_checkpoint)
    refresh_base_summary(output_dir, state)
    refresh_saved_output_metadata(output_dir)
    save_build_cache(output_dir, state)
    return ResumableDictionaryBuildResult(
        pipeline_result=load_pipeline_result_from_output(output_dir),
        statuses=tuple(statuses),
    )


def checkpoints_through(target_checkpoint: str) -> tuple[CheckpointSpec, ...]:
    target_index = checkpoint_index(target_checkpoint)
    return CHECKPOINTS[: target_index + 1]


def checkpoints_after(target_checkpoint: str) -> tuple[CheckpointSpec, ...]:
    target_index = checkpoint_index(target_checkpoint)
    return CHECKPOINTS[target_index + 1 :]


def checkpoint_index(target_checkpoint: str) -> int:
    for index, checkpoint in enumerate(CHECKPOINTS):
        if checkpoint.key == target_checkpoint:
            return index
    valid_targets = ", ".join(checkpoint.key for checkpoint in CHECKPOINTS)
    raise ValueError(f"Unknown dictionary build target checkpoint: {target_checkpoint}. Valid targets: {valid_targets}.")


def run_checkpoint(
    checkpoint: CheckpointSpec,
    *,
    output_dir: Path,
    input_dir: Path,
    reference_dictionary_path: Path,
    include_punctuation: bool,
    include_numbers: bool,
    model: str,
    progress: ProgressCallback | None,
    cleanup_progress: CleanupProgressCallback | None,
    dictionary_cleanup_progress: CleanupProgressCallback | None,
    generated_example_progress: CleanupProgressCallback | None,
    generated_meaning_progress: CleanupProgressCallback | None,
    artifact_callback: ArtifactCallback | None,
    get_shared_cleanup_client: Callable[[], SupportsCompletion],
    get_generated_meaning_client: Callable[[], SupportsCompletion],
    state: BuildState,
) -> None:
    if checkpoint.key == "01":
        report(progress, "scan", f"discovering source documents in {input_dir}")
        report(progress, "extract", f"reading {len(state.documents):,} document(s) into page/text units")
        state.pages = [
            page
            for document in state.documents
            for page in read_document(document, document_id_for(document, input_dir))
        ]
        write_rows(
            artifact_path(output_dir, ArtifactId.EXTRACTED_PAGES),
            page_rows(state.pages),
            fieldnames=page_fields(),
        )
        return

    if checkpoint.key == "02-05":
        pages = state.pages if state.pages is not None else load_pages(output_dir)
        kiwi = Kiwi()
        analyzer = KoreanAnalyzer(
            include_punctuation=include_punctuation,
            include_numbers=include_numbers,
            kiwi=kiwi,
        )
        report(progress, "analyze", f"tokenizing {len(pages):,} page/text unit(s)")
        analysis = analyzer.analyze_pages(pages)
        state.pages = pages
        state.sentences = list(analysis["sentences"])  # type: ignore[arg-type]
        state.tokens = list(analysis["tokens"])  # type: ignore[arg-type]
        state.vocabulary = analysis["vocabulary"]  # type: ignore[assignment]
        state.surface_words = analysis["surface_words"]  # type: ignore[assignment]
        state.document_totals = analysis["document_totals"]  # type: ignore[assignment]
        write_rows(
            artifact_path(output_dir, ArtifactId.SENTENCES),
            state.sentences,
            fieldnames=sentence_fields(),
        )
        write_rows(
            artifact_path(output_dir, ArtifactId.TOKENS),
            state.tokens,
            fieldnames=list(state.tokens[0].keys()) if state.tokens else token_fields(),
        )
        write_surface_words(artifact_path(output_dir, ArtifactId.SURFACE_WORDS), state.surface_words)
        write_vocabulary(artifact_path(output_dir, ArtifactId.VOCABULARY_RAW), state.vocabulary)
        return

    if checkpoint.key == "06-08":
        analysis = ensure_saved_analysis(
            output_dir,
            state=state,
            include_numbers=include_numbers,
        )
        candidate_outputs = build_candidate_outputs(
            vocabulary=analysis["vocabulary"],
            surface_words=analysis["surface_words"],
            total_document_count=saved_document_count(output_dir, state),
            total_token_occurrences=len(analysis["tokens"]),
            kiwi=Kiwi(),
        )
        state.candidate_terms = candidate_outputs.terms
        write_rows(
            artifact_path(output_dir, ArtifactId.CANDIDATE_TERMS),
            candidate_outputs.terms,
            fieldnames=candidate_term_fields(),
        )
        write_rows(
            artifact_path(output_dir, ArtifactId.REJECTED_TERMS),
            candidate_outputs.rejected,
            fieldnames=rejected_candidate_fields(),
        )
        write_jsonl(artifact_path(output_dir, ArtifactId.DICTIONARY_CANDIDATES), candidate_outputs.dictionary_entries)
        return

    if checkpoint.key == "09":
        analysis = ensure_saved_analysis(
            output_dir,
            state=state,
            include_numbers=include_numbers,
        )
        candidate_terms = state.candidate_terms or read_rows(artifact_path(output_dir, ArtifactId.CANDIDATE_TERMS))
        example_rows = example_sentence_rows(
            analysis["sentences"],
            analysis["tokens"],
            candidate_terms,
        )
        write_rows(
            artifact_path(output_dir, ArtifactId.EXAMPLE_SENTENCES),
            example_rows,
            fieldnames=example_sentence_fields(),
        )
        if artifact_callback is not None:
            artifact_callback(artifact_event_name(ArtifactId.EXAMPLE_SENTENCES), example_rows)
        return

    if checkpoint.key == "10":
        write_cleanup_outputs_from_saved(
            output_dir,
            sentence_cleanup_client=get_shared_cleanup_client(),
            sentence_cleanup_progress=cleanup_progress,
            artifact_callback=artifact_callback,
        )
        return

    if checkpoint.key == "11":
        write_cleanup_outputs_from_saved(
            output_dir,
            dictionary_cleanup_client=get_shared_cleanup_client(),
            dictionary_cleanup_progress=dictionary_cleanup_progress,
            artifact_callback=artifact_callback,
        )
        return

    if checkpoint.key == "12":
        write_linked_dictionary_candidates_from_saved(
            output_dir,
            artifact_callback=artifact_callback,
            require_inputs=True,
        )
        return

    if checkpoint.key == "13":
        write_cleanup_outputs_from_saved(
            output_dir,
            generated_example_client=get_shared_cleanup_client(),
            generated_example_progress=generated_example_progress,
            generated_example_retry_decider=None,
            artifact_callback=artifact_callback,
        )
        return

    if checkpoint.key == "14":
        write_meaning_dictionary_candidates_from_saved(
            output_dir,
            reference_dictionary_path=reference_dictionary_path,
            artifact_callback=artifact_callback,
        )
        return

    if checkpoint.key == "15":
        write_generated_meaning_dictionary_candidates_from_saved(
            output_dir,
            generated_meaning_client=get_generated_meaning_client(),
            generated_meaning_progress=generated_meaning_progress,
            artifact_callback=artifact_callback,
        )
        return

    raise ResumableBuildError(f"Unknown checkpoint: {checkpoint.key}")


def validate_checkpoint(
    checkpoint: CheckpointSpec,
    *,
    output_dir: Path,
    reference_dictionary_path: Path,
    state: BuildState,
    model: str,
) -> ValidationResult:
    schema_result = validate_checkpoint_schema(checkpoint, output_dir)
    if not schema_result.valid:
        return schema_result

    cache_entry = checkpoint_cache_entry(state.cache, checkpoint.key)
    expected_dependencies = checkpoint_dependencies(
        checkpoint,
        output_dir=output_dir,
        reference_dictionary_path=reference_dictionary_path,
        state=state,
        model=model,
    )
    artifact_hashes = checkpoint_artifact_hashes(checkpoint, output_dir)
    expected_digest = checkpoint_digest(
        checkpoint,
        dependencies=expected_dependencies,
        artifact_hashes=artifact_hashes,
    )

    if cache_entry:
        if cache_entry.get("stage_version") != checkpoint.stage_version:
            return ValidationResult(False, "cache version changed")
        if cache_entry.get("dependencies") != expected_dependencies:
            return ValidationResult(False, "dependency fingerprint changed")
        if cache_entry.get("artifacts") != artifact_hashes:
            return ValidationResult(False, "artifact hash changed")
        state.checkpoint_digests[checkpoint.key] = expected_digest
        return ValidationResult(True, "cache hit")

    legacy_result = validate_legacy_checkpoint(
        checkpoint,
        output_dir,
        reference_dictionary_path=reference_dictionary_path,
        state=state,
    )
    if legacy_result.valid:
        state.checkpoint_digests[checkpoint.key] = expected_digest
    return legacy_result


def validate_checkpoint_schema(checkpoint: CheckpointSpec, output_dir: Path) -> ValidationResult:
    for artifact_id in checkpoint.artifact_ids:
        path = artifact_path(output_dir, artifact_id)
        if not path.is_file():
            return ValidationResult(False, f"{artifact_filename(artifact_id)} is missing")
        try:
            validate_artifact_schema(artifact_id, path)
        except ValueError as error:
            return ValidationResult(False, str(error))
    if checkpoint.key == "13":
        for sidecar_id in (ArtifactId.GENERATED_EXAMPLE_ATTEMPTS, ArtifactId.GENERATED_EXAMPLE_FAILURES):
            path = artifact_path(output_dir, sidecar_id)
            if path.is_file():
                try:
                    read_jsonl(path)
                except ValueError as error:
                    return ValidationResult(False, str(error))
    return ValidationResult(True, "schema valid")


def validate_artifact_schema(artifact_id: ArtifactId, path: Path) -> None:
    spec = artifact_spec(artifact_id)
    if spec.csv_header:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration as error:
                raise ValueError(f"{path.name} is empty") from error
        if header != list(spec.csv_header):
            raise ValueError(f"{path.name} header does not match the current schema")
        return

    if spec.format == ArtifactFormat.JSONL:
        rows = read_jsonl(path)
        if spec.required_jsonl_keys and not rows:
            raise ValueError(f"{path.name} has no rows")
        required_keys = set(spec.required_jsonl_keys)
        for row_number, row in enumerate(rows, start=1):
            missing_keys = required_keys - set(row.keys())
            if missing_keys:
                joined = ", ".join(sorted(missing_keys))
                raise ValueError(f"{path.name} row {row_number} is missing required key(s): {joined}")
        return


def validate_legacy_checkpoint(
    checkpoint: CheckpointSpec,
    output_dir: Path,
    reference_dictionary_path: Path,
    state: BuildState,
) -> ValidationResult:
    artifact_mtime = min(artifact_path(output_dir, artifact_id).stat().st_mtime_ns for artifact_id in checkpoint.artifact_ids)
    dependency_mtime = legacy_dependency_mtime(checkpoint, output_dir, reference_dictionary_path, state)
    if dependency_mtime is not None and artifact_mtime < dependency_mtime:
        return ValidationResult(False, "legacy artifact is older than its dependency")
    return ValidationResult(True, "legacy artifact adopted", adopted=True)


def legacy_dependency_mtime(
    checkpoint: CheckpointSpec,
    output_dir: Path,
    reference_dictionary_path: Path,
    state: BuildState,
) -> int | None:
    if checkpoint.key == "01":
        if not state.source_fingerprints:
            return None
        return max(fingerprint.mtime_ns for fingerprint in state.source_fingerprints)
    mtimes: list[int] = []
    for dependency_key in checkpoint.dependency_keys:
        for artifact_id in CHECKPOINT_BY_KEY[dependency_key].artifact_ids:
            path = artifact_path(output_dir, artifact_id)
            if path.is_file():
                mtimes.append(path.stat().st_mtime_ns)
    if checkpoint.key == "14" and reference_dictionary_path.is_file():
        mtimes.append(reference_dictionary_path.stat().st_mtime_ns)
    return max(mtimes) if mtimes else None


def checkpoint_dependencies(
    checkpoint: CheckpointSpec,
    *,
    output_dir: Path,
    reference_dictionary_path: Path,
    state: BuildState,
    model: str,
) -> dict[str, str]:
    dependencies: dict[str, str] = {
        "options": state.options_digest,
    }
    if checkpoint.key == "01":
        dependencies["sources"] = state.source_digest
    for dependency_key in checkpoint.dependency_keys:
        dependencies[dependency_key] = state.checkpoint_digests.get(dependency_key, "")
    if checkpoint.stage_version == LLM_CACHE_VERSION:
        dependencies["model"] = model
    if checkpoint.key == "14":
        dependencies["reference_dictionary"] = file_sha256(reference_dictionary_path) if reference_dictionary_path.is_file() else ""
    return dependencies


def update_checkpoint_cache(
    checkpoint: CheckpointSpec,
    *,
    output_dir: Path,
    reference_dictionary_path: Path,
    state: BuildState,
    model: str,
) -> None:
    dependencies = checkpoint_dependencies(
        checkpoint,
        output_dir=output_dir,
        reference_dictionary_path=reference_dictionary_path,
        state=state,
        model=model,
    )
    artifact_hashes = checkpoint_artifact_hashes(checkpoint, output_dir)
    digest = checkpoint_digest(
        checkpoint,
        dependencies=dependencies,
        artifact_hashes=artifact_hashes,
    )
    state.cache.setdefault("checkpoints", {})[checkpoint.key] = {
        "stage_version": checkpoint.stage_version,
        "dependencies": dependencies,
        "artifacts": artifact_hashes,
        "digest": digest,
    }
    state.checkpoint_digests[checkpoint.key] = digest


def checkpoint_cache_entry(cache: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    checkpoints = cache.get("checkpoints", {})
    if not isinstance(checkpoints, Mapping):
        return {}
    entry = checkpoints.get(key, {})
    return entry if isinstance(entry, Mapping) else {}


def checkpoint_artifact_hashes(checkpoint: CheckpointSpec, output_dir: Path) -> dict[str, str]:
    return {
        artifact_filename(artifact_id): file_sha256(artifact_path(output_dir, artifact_id))
        for artifact_id in checkpoint.artifact_ids
    }


def checkpoint_digest(
    checkpoint: CheckpointSpec,
    *,
    dependencies: Mapping[str, str],
    artifact_hashes: Mapping[str, str],
) -> str:
    return stable_hash(
        {
            "key": checkpoint.key,
            "stage_version": checkpoint.stage_version,
            "dependencies": dependencies,
            "artifacts": artifact_hashes,
        }
    )


def fingerprint_documents(documents: Sequence[Path], input_dir: Path) -> list[SourceFingerprint]:
    fingerprints: list[SourceFingerprint] = []
    for document in documents:
        stat = document.stat()
        fingerprints.append(
            SourceFingerprint(
                path=document_id_for(document, input_dir),
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=file_sha256(document),
            )
        )
    return fingerprints


def build_options(
    *,
    target_grade: str,
    input_dir: Path,
    include_punctuation: bool,
    include_numbers: bool,
) -> dict[str, object]:
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "target_grade": target_grade,
        "input_root": input_dir.resolve().as_posix(),
        "include_punctuation": include_punctuation,
        "include_numbers": include_numbers,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "kiwipiepy": package_version("kiwipiepy"),
    }


def load_build_cache(output_dir: Path) -> dict[str, Any]:
    summary_path = artifact_path(output_dir, ArtifactId.SUMMARY)
    if not summary_path.is_file():
        return empty_build_cache()
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_build_cache()
    cache = summary.get("build_cache")
    if not isinstance(cache, dict):
        return empty_build_cache()
    if cache.get("schema_version") != CACHE_SCHEMA_VERSION:
        return empty_build_cache()
    return dict(cache)


def empty_build_cache() -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "checkpoints": {},
    }


def save_build_cache(output_dir: Path, state: BuildState) -> None:
    summary_path = artifact_path(output_dir, ArtifactId.SUMMARY)
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    state.cache["schema_version"] = CACHE_SCHEMA_VERSION
    state.cache["source_documents"] = [
        {
            **fingerprint.__dict__,
            "managed_source": state.managed_source_metadata_by_sha256.get(fingerprint.sha256, {}),
        }
        for fingerprint in state.source_fingerprints
    ]
    state.cache["source_digest"] = state.source_digest
    state.cache["options"] = state.options
    state.cache["options_digest"] = state.options_digest
    summary["build_cache"] = state.cache
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def refresh_base_summary(output_dir: Path, state: BuildState) -> None:
    summary_path = artifact_path(output_dir, ArtifactId.SUMMARY)
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    build_cache = summary.get("build_cache")
    for key in RENAMED_SUMMARY_KEYS:
        summary.pop(key, None)
    pages = read_rows(artifact_path(output_dir, ArtifactId.EXTRACTED_PAGES)) if artifact_path(output_dir, ArtifactId.EXTRACTED_PAGES).is_file() else []
    sentences = read_rows(artifact_path(output_dir, ArtifactId.SENTENCES)) if artifact_path(output_dir, ArtifactId.SENTENCES).is_file() else []
    tokens = read_rows(artifact_path(output_dir, ArtifactId.TOKENS)) if artifact_path(output_dir, ArtifactId.TOKENS).is_file() else []
    surface_words = read_rows(artifact_path(output_dir, ArtifactId.SURFACE_WORDS)) if artifact_path(output_dir, ArtifactId.SURFACE_WORDS).is_file() else []
    vocabulary = read_rows(artifact_path(output_dir, ArtifactId.VOCABULARY_RAW)) if artifact_path(output_dir, ArtifactId.VOCABULARY_RAW).is_file() else []
    candidate_terms = read_rows(artifact_path(output_dir, ArtifactId.CANDIDATE_TERMS)) if artifact_path(output_dir, ArtifactId.CANDIDATE_TERMS).is_file() else []
    rejected_terms = read_rows(artifact_path(output_dir, ArtifactId.REJECTED_TERMS)) if artifact_path(output_dir, ArtifactId.REJECTED_TERMS).is_file() else []
    example_sentences = read_rows(artifact_path(output_dir, ArtifactId.EXAMPLE_SENTENCES)) if artifact_path(output_dir, ArtifactId.EXAMPLE_SENTENCES).is_file() else []
    summary.update(
        {
            "documents": [str(path) for path in state.documents],
            "target_grade": state.target_grade,
            "document_count": len(state.documents),
            "extracted_page_count": len(pages),
            "sentence_count": len(sentences),
            "token_occurrences": len(tokens),
            "unique_vocabulary_terms": len(vocabulary),
            "unique_surface_words": len(surface_words),
            "clean_candidate_terms": len(candidate_terms),
            "rejected_terms": len(rejected_terms),
            "example_sentences": len(example_sentences),
            "tokens_by_document": dict(Counter(str(row.get("document", "")) for row in tokens if row.get("document"))),
            "outputs": output_filenames(),
            "pipeline_stages": pipeline_stage_rows(),
        }
    )
    if isinstance(build_cache, dict):
        summary["build_cache"] = build_cache
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_saved_analysis(
    output_dir: Path,
    *,
    state: BuildState,
    include_numbers: bool,
) -> dict[str, Any]:
    if (
        state.sentences is not None
        and state.tokens is not None
        and state.vocabulary is not None
        and state.surface_words is not None
        and state.document_totals is not None
    ):
        return {
            "sentences": state.sentences,
            "tokens": state.tokens,
            "vocabulary": state.vocabulary,
            "surface_words": state.surface_words,
            "document_totals": state.document_totals,
        }

    sentences = [dict(row) for row in read_rows(artifact_path(output_dir, ArtifactId.SENTENCES))]
    tokens = [dict(row) for row in read_rows(artifact_path(output_dir, ArtifactId.TOKENS))]
    vocabulary: dict[tuple[str, str], CountEntry] = defaultdict(CountEntry)
    surface_words: dict[str, CountEntry] = defaultdict(CountEntry)
    document_totals: Counter[str] = Counter()

    for sentence in sentences:
        sentence_text = str(sentence.get("sentence", ""))
        document = str(sentence.get("document", ""))
        for match in SURFACE_WORD_RE.finditer(sentence_text):
            form = match.group(0)
            if include_numbers or not is_number_only(form):
                surface_words[form].seen(document, sentence_text)

    for token in tokens:
        document = str(token.get("document", ""))
        sentence_text = str(token.get("sentence", ""))
        form = str(token.get("form", ""))
        pos = str(token.get("pos", ""))
        document_totals[document] += 1
        if is_vocabulary_token(form, pos, include_numbers=include_numbers):
            vocabulary[(form, pos)].seen(document, sentence_text)

    state.sentences = sentences
    state.tokens = tokens
    state.vocabulary = vocabulary
    state.surface_words = surface_words
    state.document_totals = document_totals
    return {
        "sentences": sentences,
        "tokens": tokens,
        "vocabulary": vocabulary,
        "surface_words": surface_words,
        "document_totals": document_totals,
    }


def load_pages(output_dir: Path) -> list[PageText]:
    pages: list[PageText] = []
    for row in read_rows(artifact_path(output_dir, ArtifactId.EXTRACTED_PAGES)):
        document = str(row.get("document", ""))
        page_value = str(row.get("page", "") or "")
        pages.append(
            PageText(
                source=Path(document),
                page=int(page_value) if page_value else None,
                text=str(row.get("text", "")),
                raw_text=str(row.get("text", "")),
                document_id=document,
            )
        )
    return pages


def saved_document_count(output_dir: Path, state: BuildState) -> int:
    if state.pages is not None:
        return parsed_document_count(state.pages, state.documents)
    pages_path = artifact_path(output_dir, ArtifactId.EXTRACTED_PAGES)
    if pages_path.is_file():
        documents = {str(row.get("document", "")) for row in read_rows(pages_path) if row.get("document")}
        if documents:
            return len(documents)
    return len(state.documents)


def remove_artifacts_from_checkpoint(output_dir: Path, checkpoint_key: str) -> None:
    for artifact_id in CHECKPOINT_ARTIFACT_IDS_FROM[checkpoint_key]:
        path = artifact_path(output_dir, artifact_id)
        if path.is_file():
            path.unlink()
    if checkpoint_key == "01":
        for filename in legacy_output_filenames():
            path = output_dir / filename
            if path.is_file():
                path.unlink()


def remove_artifacts_after_checkpoint(output_dir: Path, target_checkpoint: str) -> None:
    downstream_checkpoints = checkpoints_after(target_checkpoint)
    if not downstream_checkpoints:
        return
    remove_artifacts_from_checkpoint(output_dir, downstream_checkpoints[0].key)


def remove_checkpoint_cache_after(state: BuildState, target_checkpoint: str) -> None:
    downstream_keys = {checkpoint.key for checkpoint in checkpoints_after(target_checkpoint)}
    checkpoints = state.cache.get("checkpoints", {})
    if isinstance(checkpoints, dict):
        for key in downstream_keys:
            checkpoints.pop(key, None)
    for key in downstream_keys:
        state.checkpoint_digests.pop(key, None)


def checkpoint_has_any_artifact(output_dir: Path, checkpoint: CheckpointSpec) -> bool:
    return any(artifact_path(output_dir, artifact_id).is_file() for artifact_id in checkpoint.artifact_ids)


def generated_example_failure_count(output_dir: Path) -> int:
    failures_path = artifact_path(output_dir, ArtifactId.GENERATED_EXAMPLE_FAILURES)
    if not failures_path.is_file():
        return 0
    return len(read_jsonl(failures_path))


def emit_checkpoint_status(
    statuses: list[CheckpointStatus],
    status: CheckpointStatus,
    callback: CheckpointStatusCallback | None,
) -> None:
    statuses.append(status)
    if callback is not None:
        callback(status)


def report(progress: ProgressCallback | None, phase: str, detail: str) -> None:
    if progress is not None:
        progress(phase, detail)


def unload_client(client: SupportsCompletion | None) -> None:
    if client is None:
        return
    try:
        unload_model_if_supported(client)
    except Exception:
        pass


def stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
