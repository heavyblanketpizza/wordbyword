from __future__ import annotations

import importlib.util
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wordbyword.classroom_printout_studio.source_parsing.materials import sha256_file
from wordbyword.classroom_printout_studio.source_parsing.paddleocr_vl import PaddleOCRVLSettings
from wordbyword.dictionary_build.llm import (
    DEFAULT_PROVIDER_MODELS,
    ProviderName,
    SupportsCompletion,
    build_provider_client,
    normalize_provider_name,
    provider_model_from_env,
)
from wordbyword.dictionary_build.artifacts import (
    ArtifactId,
    artifact_event_name as _artifact_event_name,
    artifact_filename as _artifact_filename,
    artifact_path,
)
from wordbyword.dictionary_build.pipeline import PipelineUsageError
from wordbyword.grade_profiles import (
    CLASS_PARSED_SOURCE_ROOT,
    CLASS_SESSION_ARCHIVE_ROOT,
    CLASS_SOURCE_MATERIAL_ROOT,
    DEFAULT_TARGET_GRADE,
    DICTIONARY_SOURCE_ROOT,
    GradeWorkspace,
    default_after_class_report_output_root,
    default_live_lexicon_path,
    default_print_output_root,
    default_supplemental_lexicon_path,
    default_unmatched_usage_output_path,
    default_usage_signal_output_root,
    default_vocabulary_guided_rewrite_output_root,
    default_whole_rewrite_output_root,
    grade_workspace,
    normalize_target_grade,
)

PADDLEOCR_VL_SIDECAR_ENV_VAR = "WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV"
PADDLEOCR_VL_SIDECAR_PYTHON_ENV_VAR = "WORDBYWORD_PADDLEOCR_VL_SIDECAR_PYTHON"
DEFAULT_PADDLEOCR_VL_SIDECAR_ENVS = (".venv-paddleocr-vl", ".venv-paddleocr")


@dataclass(frozen=True)
class RewriteVocabularySource:
    path: Path
    kind: str
    sha256: str


@dataclass(frozen=True)
class SourceParseOptions:
    output_root: Path
    parse_pdf: bool
    render_dpi: int
    paddleocr_vl_settings: PaddleOCRVLSettings | None


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: ProviderName
    model: str
    base_url: str | None


def artifact_event_name(artifact_id: ArtifactId) -> str:
    return _artifact_event_name(artifact_id)


def artifact_filename(artifact_id: ArtifactId) -> str:
    return _artifact_filename(artifact_id)


def llm_runtime_config(
    *,
    provider: object = "ollama",
    model: object = "",
    base_url: object = None,
) -> LLMRuntimeConfig:
    normalized = normalize_provider_name(str(provider or "ollama"))
    model_text = str(model or "").strip()
    if not model_text:
        model_text = provider_model_from_env(normalized) or DEFAULT_PROVIDER_MODELS[normalized]
    base_url_text = str(base_url or "").strip() or None
    return LLMRuntimeConfig(provider=normalized, model=model_text, base_url=base_url_text)


def llm_runtime_config_from_args(args: object) -> LLMRuntimeConfig:
    provider = getattr(args, "llm_provider", "ollama")
    normalized = normalize_provider_name(str(provider or "ollama"))
    explicit_model = getattr(args, "llm_model", None)
    explicit_base_url = getattr(args, "llm_base_url", None)
    if normalized == "ollama":
        model = explicit_model or getattr(args, "ollama_model", "")
        base_url = explicit_base_url or getattr(args, "ollama_base_url", None)
    else:
        model = explicit_model or ""
        base_url = explicit_base_url
    return llm_runtime_config(provider=normalized, model=model, base_url=base_url)


class LazyCompletionClient:
    def __init__(
        self,
        *,
        provider: str = "ollama",
        model: str,
        base_url: str | None,
        timeout_seconds: float,
        max_output_tokens: int,
        client_factory: Callable[..., SupportsCompletion] | None = None,
        client_factory_accepts_provider: bool = False,
    ) -> None:
        self.provider = normalize_provider_name(provider)
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.client_factory = client_factory
        self.client_factory_accepts_provider = client_factory_accepts_provider
        self.client: SupportsCompletion | None = None

    def complete(self, *args: object, **kwargs: object) -> object:
        if self.client is None:
            if self.client_factory is None:
                self.client = build_provider_client(
                    self.provider,
                    model=self.model,
                    base_url=self.base_url,
                    timeout_seconds=self.timeout_seconds,
                    max_output_tokens=self.max_output_tokens,
                )
            else:
                client_kwargs = {
                    "model": self.model,
                    "base_url": self.base_url,
                    "timeout_seconds": self.timeout_seconds,
                    "max_output_tokens": self.max_output_tokens,
                }
                if self.client_factory_accepts_provider:
                    self.client = self.client_factory(self.provider, **client_kwargs)
                else:
                    self.client = self.client_factory(**client_kwargs)
        return self.client.complete(*args, **kwargs)  # type: ignore[arg-type]


LazyOllamaCompletionClient = LazyCompletionClient


def target_grade_from_value(value: object = DEFAULT_TARGET_GRADE) -> str:
    return normalize_target_grade(value or DEFAULT_TARGET_GRADE)


def workspace_for_target_grade(value: object = DEFAULT_TARGET_GRADE) -> GradeWorkspace:
    return grade_workspace(target_grade_from_value(value))


def option_was_explicit(source: object, dest: str) -> bool:
    return bool(getattr(source, f"{dest}_explicit", False))


def target_grade_from_args(args: object) -> str:
    return target_grade_from_value(getattr(args, "target_grade", DEFAULT_TARGET_GRADE))


def workspace_from_args(args: object) -> GradeWorkspace:
    return workspace_for_target_grade(target_grade_from_args(args))


def resolved_dictionary_input_dir(args: object) -> Path:
    return getattr(args, "input") if option_was_explicit(args, "input") else workspace_from_args(args).dictionary_input_dir


def resolved_dictionary_output_dir(args: object) -> Path:
    return getattr(args, "output") if option_was_explicit(args, "output") else workspace_from_args(args).dictionary_output_dir


def resolved_reference_dictionary_path(args: object) -> Path:
    return getattr(args, "reference_dictionary")


def resolved_canonical_dictionary_path(args: object) -> Path:
    if option_was_explicit(args, "canonical_dictionary"):
        return getattr(args, "canonical_dictionary")
    return artifact_path(workspace_from_args(args).dictionary_output_dir, ArtifactId.FINAL_DICTIONARY)


def resolved_class_source_dir(args: object, source_dir: Path) -> Path:
    source_dir_was_explicit = option_was_explicit(args, "parse_source_materials") or option_was_explicit(
        args, "build_class_printouts"
    )
    if source_dir == CLASS_SOURCE_MATERIAL_ROOT and not source_dir_was_explicit:
        return workspace_from_args(args).class_source_dir
    return source_dir


def resolved_parsed_source_root(args: object, parsed_root: Path) -> Path:
    return workspace_from_args(args).source_output_root if parsed_root == CLASS_PARSED_SOURCE_ROOT else parsed_root


def resolved_archive_root(args: object, archive_root: Path) -> Path:
    return workspace_from_args(args).archive_root if archive_root == CLASS_SESSION_ARCHIVE_ROOT else archive_root


def resolved_usage_signal_input_root(args: object) -> Path:
    if option_was_explicit(args, "usage_signal_input_root"):
        return getattr(args, "usage_signal_input_root")
    return workspace_from_args(args).archive_root


def resolved_dictionary_source_root(args: object, source_root: Path) -> Path:
    if source_root == DICTIONARY_SOURCE_ROOT:
        return workspace_from_args(args).dictionary_input_dir
    return source_root


def resolved_usage_signal_output_root(args: object) -> Path:
    if not option_was_explicit(args, "usage_signal_output_root"):
        return default_usage_signal_output_root(target_grade_from_args(args), explicit_target=True)
    return getattr(args, "usage_signal_output_root")


def resolved_live_lexicon_path(args: object) -> Path:
    if not option_was_explicit(args, "live_lexicon"):
        return default_live_lexicon_path(target_grade_from_args(args))
    return getattr(args, "live_lexicon")


def resolved_supplemental_lexicon_path(args: object) -> Path:
    if not option_was_explicit(args, "supplemental_lexicon"):
        return default_supplemental_lexicon_path(target_grade_from_args(args))
    return getattr(args, "supplemental_lexicon")


def resolved_usage_candidates_path(args: object) -> Path:
    if not option_was_explicit(args, "usage_candidates"):
        return artifact_path(resolved_usage_signal_output_root(args), ArtifactId.DICTIONARY_CANDIDATES)
    return getattr(args, "usage_candidates")


def resolved_unmatched_usage_output_path(args: object) -> Path:
    if not option_was_explicit(args, "unmatched_usage_output"):
        return default_unmatched_usage_output_path(target_grade_from_args(args), explicit_target=True)
    return getattr(args, "unmatched_usage_output")


def resolved_after_class_report_output_root(args: object) -> Path:
    if option_was_explicit(args, "after_class_report_output_root"):
        return getattr(args, "after_class_report_output_root")
    return default_after_class_report_output_root(target_grade_from_args(args))


def resolved_whole_rewrite_output_root(args: object) -> Path:
    rewrite_output_root = getattr(args, "rewrite_output_root", None)
    if rewrite_output_root is not None:
        return rewrite_output_root
    return default_whole_rewrite_output_root(target_grade_from_args(args))


def resolved_vocabulary_rewrite_output_root(args: object) -> Path:
    rewrite_output_root = getattr(args, "rewrite_output_root", None)
    if rewrite_output_root is not None:
        return rewrite_output_root
    return default_vocabulary_guided_rewrite_output_root(target_grade_from_args(args))


def resolved_print_output_root(args: object) -> Path:
    if not option_was_explicit(args, "print_output_root"):
        return default_print_output_root(target_grade_from_args(args))
    return getattr(args, "print_output_root")


def resolved_print_class_materials_root(args: object) -> Path:
    root = getattr(args, "print_class_materials")
    if root == default_vocabulary_guided_rewrite_output_root(DEFAULT_TARGET_GRADE):
        return default_vocabulary_guided_rewrite_output_root(target_grade_from_args(args))
    return root


def file_sha256_if_present(path: Path) -> str:
    return sha256_file(path) if path.is_file() else ""


def rewrite_vocabulary_source(
    *,
    explicit_path: Path | None,
    target_grade: str,
    dictionary_output_dir: Path,
    live_lexicon_path_factory: Callable[[object], Path] | None = None,
    file_sha256: Callable[[Path], str] | None = None,
) -> RewriteVocabularySource:
    resolve_live_lexicon_path = default_live_lexicon_path if live_lexicon_path_factory is None else live_lexicon_path_factory
    resolve_sha256 = file_sha256_if_present if file_sha256 is None else file_sha256
    if explicit_path is not None:
        return RewriteVocabularySource(path=explicit_path, kind="explicit", sha256=resolve_sha256(explicit_path))
    live_lexicon_path = resolve_live_lexicon_path(target_grade)
    if live_lexicon_path.is_file():
        return RewriteVocabularySource(
            path=live_lexicon_path,
            kind="live_lexicon",
            sha256=resolve_sha256(live_lexicon_path),
        )
    dictionary_path = artifact_path(dictionary_output_dir, ArtifactId.FINAL_DICTIONARY)
    return RewriteVocabularySource(
        path=dictionary_path,
        kind="dictionary_seed",
        sha256=resolve_sha256(dictionary_path),
    )


def source_parse_options_from_args(
    args: object,
    *,
    discover_sidecar_env: Callable[[], Path | None] | None = None,
    validate_sidecar: Callable[..., None] | None = None,
    current_python_available: Callable[[], bool] | None = None,
) -> SourceParseOptions:
    parse_pdf = not bool(getattr(args, "skip_pdf_parse", False))
    output_root = (
        getattr(args, "source_output_root")
        if option_was_explicit(args, "source_output_root")
        else workspace_from_args(args).source_output_root
    )
    return SourceParseOptions(
        output_root=output_root,
        parse_pdf=parse_pdf,
        render_dpi=int(getattr(args, "pdf_render_dpi", 144) or 144),
        paddleocr_vl_settings=None
        if not parse_pdf
        else paddleocr_vl_settings_from_args(
            args,
            discover_sidecar_env=discover_sidecar_env,
            validate_sidecar=validate_sidecar,
            current_python_available=current_python_available,
        ),
    )


def paddleocr_vl_settings_from_args(
    args: object,
    *,
    discover_sidecar_env: Callable[[], Path | None] | None = None,
    validate_sidecar: Callable[..., None] | None = None,
    current_python_available: Callable[[], bool] | None = None,
) -> PaddleOCRVLSettings | None:
    sidecar_env_path = getattr(args, "paddleocr_vl_sidecar_env", None)
    sidecar_python_path = getattr(args, "paddleocr_vl_sidecar_python", None)
    sidecar_env = sidecar_env_path.as_posix() if sidecar_env_path is not None else None
    sidecar_python = sidecar_python_path.as_posix() if sidecar_python_path is not None else None
    return build_paddleocr_vl_settings(
        device=getattr(args, "paddleocr_vl_device", None),
        engine=getattr(args, "paddleocr_vl_engine", None),
        vl_rec_backend=getattr(args, "paddleocr_vl_rec_backend", None),
        vl_rec_server_url=getattr(args, "paddleocr_vl_rec_server_url", None),
        vl_rec_api_model_name=getattr(args, "paddleocr_vl_rec_api_model_name", None),
        vl_rec_api_key=getattr(args, "paddleocr_vl_rec_api_key", None),
        use_doc_orientation_classify=getattr(args, "paddleocr_vl_doc_orientation", False) or None,
        use_doc_unwarping=getattr(args, "paddleocr_vl_doc_unwarping", False) or None,
        use_layout_detection=False if getattr(args, "paddleocr_vl_no_layout_detection", False) else None,
        use_chart_recognition=getattr(args, "paddleocr_vl_chart_recognition", False) or None,
        use_seal_recognition=getattr(args, "paddleocr_vl_seal_recognition", False) or None,
        use_ocr_for_image_block=False if getattr(args, "paddleocr_vl_no_ocr_for_image_block", False) else None,
        max_new_tokens=getattr(args, "paddleocr_vl_max_new_tokens", None),
        sidecar_env=sidecar_env,
        sidecar_python=sidecar_python,
        sidecar_timeout_seconds=getattr(args, "paddleocr_vl_sidecar_timeout_seconds", None),
        discover_sidecar_env=discover_sidecar_env,
        validate_sidecar=validate_sidecar,
        current_python_available=current_python_available,
    )


def build_paddleocr_vl_settings(
    *,
    device: str | None = None,
    engine: str | None = None,
    vl_rec_backend: str | None = None,
    vl_rec_server_url: str | None = None,
    vl_rec_api_model_name: str | None = None,
    vl_rec_api_key: str | None = None,
    use_doc_orientation_classify: bool | None = None,
    use_doc_unwarping: bool | None = None,
    use_layout_detection: bool | None = None,
    use_chart_recognition: bool | None = None,
    use_seal_recognition: bool | None = None,
    use_ocr_for_image_block: bool | None = None,
    max_new_tokens: int | None = None,
    sidecar_env: str | None = None,
    sidecar_python: str | None = None,
    sidecar_timeout_seconds: float | None = None,
    discover_sidecar_env: Callable[[], Path | None] | None = None,
    validate_sidecar: Callable[..., None] | None = None,
    current_python_available: Callable[[], bool] | None = None,
) -> PaddleOCRVLSettings:
    discover = discover_paddleocr_vl_sidecar_env if discover_sidecar_env is None else discover_sidecar_env
    validate = validate_paddleocr_vl_sidecar if validate_sidecar is None else validate_sidecar
    current_available = current_python_has_paddleocr if current_python_available is None else current_python_available
    if sidecar_env is None and sidecar_python is None:
        sidecar_python = os.environ.get(PADDLEOCR_VL_SIDECAR_PYTHON_ENV_VAR) or None
        sidecar_env = os.environ.get(PADDLEOCR_VL_SIDECAR_ENV_VAR) or None
    if sidecar_env is None and sidecar_python is None:
        discovered_sidecar_env = discover()
        sidecar_env = discovered_sidecar_env.as_posix() if discovered_sidecar_env is not None else None

    if sidecar_env is not None or sidecar_python is not None:
        validate(sidecar_env=sidecar_env, sidecar_python=sidecar_python)
    elif not current_available():
        raise PipelineUsageError(
            "PaddleOCR-VL parser is selected, but PaddleOCR-VL is not available. "
            "Create .venv-paddleocr-vl, set WORDBYWORD_PADDLEOCR_VL_SIDECAR_ENV, "
            "or pass --paddleocr-vl-sidecar-env /path/to/env. Use --skip-pdf-parse "
            "only when you intentionally want workspace preparation without PDF parsing."
        )

    return PaddleOCRVLSettings(
        device=device,
        engine=engine,
        vl_rec_backend=vl_rec_backend,
        vl_rec_server_url=vl_rec_server_url,
        vl_rec_api_model_name=vl_rec_api_model_name,
        vl_rec_api_key=vl_rec_api_key,
        use_doc_orientation_classify=use_doc_orientation_classify,
        use_doc_unwarping=use_doc_unwarping,
        use_layout_detection=use_layout_detection,
        use_chart_recognition=use_chart_recognition,
        use_seal_recognition=use_seal_recognition,
        use_ocr_for_image_block=use_ocr_for_image_block,
        max_new_tokens=max_new_tokens,
        sidecar_env=sidecar_env,
        sidecar_python=sidecar_python,
        sidecar_timeout_seconds=sidecar_timeout_seconds,
    )


def discover_paddleocr_vl_sidecar_env(*, roots: Sequence[Path] | None = None) -> Path | None:
    for root in roots or paddleocr_vl_sidecar_search_roots():
        for env_name in DEFAULT_PADDLEOCR_VL_SIDECAR_ENVS:
            candidate = root / env_name
            if candidate.is_dir():
                python_path = paddleocr_vl_sidecar_python_path(sidecar_env=candidate.as_posix(), sidecar_python=None)
                if python_path is not None and paddleocr_vl_python_can_locate_paddleocr(python_path):
                    return candidate
    return None


def paddleocr_vl_sidecar_search_roots() -> tuple[Path, ...]:
    roots = [Path.cwd(), wordbyword_project_root()]
    unique_roots: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique_roots:
            unique_roots.append(resolved)
    return tuple(unique_roots)


def wordbyword_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def current_python_has_paddleocr() -> bool:
    return importlib.util.find_spec("paddleocr") is not None


def validate_paddleocr_vl_sidecar(*, sidecar_env: str | None, sidecar_python: str | None) -> None:
    python_path = paddleocr_vl_sidecar_python_path(sidecar_env=sidecar_env, sidecar_python=sidecar_python)
    label = sidecar_python or sidecar_env or "unknown sidecar"
    if python_path is None or not paddleocr_vl_python_can_locate_paddleocr(python_path):
        raise PipelineUsageError(
            "PaddleOCR-VL sidecar cannot import paddleocr: "
            f"{label}. Install PaddleOCR-VL there or choose a valid sidecar with "
            "--paddleocr-vl-sidecar-env /path/to/env."
        )


def paddleocr_vl_sidecar_python_path(*, sidecar_env: str | None, sidecar_python: str | None) -> Path | None:
    if sidecar_python:
        return Path(sidecar_python).expanduser()
    if not sidecar_env:
        return None
    sidecar_env_path = Path(sidecar_env).expanduser()
    for candidate in (
        sidecar_env_path / "bin" / "python",
        sidecar_env_path / "Scripts" / "python.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def paddleocr_vl_python_can_locate_paddleocr(python_path: Path) -> bool:
    if not python_path.is_file():
        return False
    try:
        result = subprocess.run(
            [
                python_path.as_posix(),
                "-c",
                "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('paddleocr') else 1)",
            ],
            capture_output=True,
            check=False,
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
