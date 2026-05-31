from __future__ import annotations

import argparse
import importlib.metadata
import multiprocessing
import os
import queue
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from wordbyword._json_io import jsonable
from wordbyword._json_io import read_json_object_or_empty as read_json_object_or_empty_file
from wordbyword._json_io import write_json_atomic as write_json_atomic_file

_PIPELINE_CACHE: dict[tuple[tuple[str, Any], ...], Any] = {}
PADDLEOCR_VL_MODEL_VERSION = "1.6"
PADDLEOCR_VL_PIPELINE_VERSION = f"v{PADDLEOCR_VL_MODEL_VERSION}"
PADDLEOCR_VL_PARSER_NAME = f"paddleocr-vl-{PADDLEOCR_VL_MODEL_VERSION}"


@dataclass(frozen=True)
class PaddleOCRVLSettings:
    device: str | None = None
    engine: str | None = None
    vl_rec_backend: str | None = None
    vl_rec_server_url: str | None = None
    vl_rec_api_model_name: str | None = None
    vl_rec_api_key: str | None = None
    use_doc_orientation_classify: bool | None = None
    use_doc_unwarping: bool | None = None
    use_layout_detection: bool | None = None
    use_chart_recognition: bool | None = None
    use_seal_recognition: bool | None = None
    use_ocr_for_image_block: bool | None = None
    layout_shape_mode: str | None = "auto"
    format_block_content: bool | None = True
    temperature: float | None = None
    max_new_tokens: int | None = None
    isolated_process: bool = True
    sidecar_env: str | None = None
    sidecar_python: str | None = None
    sidecar_timeout_seconds: float | None = None
    sidecar_poll_seconds: float | None = None


@dataclass(frozen=True)
class PaddleOCRVLPageResult:
    page: int
    input_image_path: str
    markdown: str
    raw_json: dict[str, Any]
    markdown_asset_paths: list[str]


@dataclass(frozen=True)
class PaddleOCRVLDocumentResult:
    parser_version: str
    settings: PaddleOCRVLSettings
    pages: list[PaddleOCRVLPageResult]


@dataclass(frozen=True)
class PaddleOCRVLProgress:
    phase: Literal["load", "ocr"]
    completed_pages: int
    total_pages: int
    detail: str


PaddleOCRVLProgressCallback = Callable[[PaddleOCRVLProgress], None]
PaddleOCRVLPageResultCallback = Callable[[PaddleOCRVLPageResult], None]


class PaddleOCRVLRuntimeError(RuntimeError):
    """Raised when PaddleOCR-VL cannot be loaded or run."""


class PaddleOCRVLInterruptedError(PaddleOCRVLRuntimeError):
    """Raised when PaddleOCR-VL inference is interrupted and its worker is stopped."""


def run_paddleocr_vl(
    *,
    page_image_paths: list[Path],
    assets_dir: Path,
    settings: PaddleOCRVLSettings,
    progress: PaddleOCRVLProgressCallback | None = None,
    page_result_callback: PaddleOCRVLPageResultCallback | None = None,
) -> PaddleOCRVLDocumentResult:
    if _uses_sidecar(settings):
        return _run_paddleocr_vl_sidecar(
            page_image_paths=page_image_paths,
            assets_dir=assets_dir,
            settings=settings,
            progress=progress,
            page_result_callback=page_result_callback,
        )
    if settings.isolated_process:
        return _run_paddleocr_vl_subprocess(
            page_image_paths=page_image_paths,
            assets_dir=assets_dir,
            settings=settings,
            progress=progress,
            page_result_callback=page_result_callback,
        )
    return _run_paddleocr_vl_in_process(
        page_image_paths=page_image_paths,
        assets_dir=assets_dir,
        settings=settings,
        progress=progress,
        page_result_callback=page_result_callback,
    )


def _run_paddleocr_vl_in_process(
    *,
    page_image_paths: list[Path],
    assets_dir: Path,
    settings: PaddleOCRVLSettings,
    progress: PaddleOCRVLProgressCallback | None = None,
    page_result_callback: PaddleOCRVLPageResultCallback | None = None,
) -> PaddleOCRVLDocumentResult:
    total_pages = len(page_image_paths)
    _emit_progress(progress, phase="load", completed_pages=0, total_pages=total_pages, detail="loading PaddleOCR-VL pipeline")
    pipeline = _cached_pipeline(settings)
    _emit_progress(progress, phase="load", completed_pages=0, total_pages=total_pages, detail="PaddleOCR-VL pipeline ready")

    predict_kwargs = _paddle_predict_kwargs(settings)
    page_results: list[PaddleOCRVLPageResult] = []
    for index, image_path in enumerate(page_image_paths, start=1):
        _emit_progress(
            progress,
            phase="ocr",
            completed_pages=index - 1,
            total_pages=total_pages,
            detail=f"recognizing page {index:,}/{total_pages:,}",
        )
        raw_results = list(pipeline.predict(input=[image_path.as_posix()], **predict_kwargs))
        if len(raw_results) != 1:
            raise PaddleOCRVLRuntimeError(
                "PaddleOCR-VL returned "
                f"{len(raw_results)} result(s) for page image {image_path.name}."
            )
        page_result = _page_result(
            result=raw_results[0],
            page=index,
            input_image_path=image_path,
            assets_dir=assets_dir,
        )
        page_results.append(page_result)
        if page_result_callback is not None:
            page_result_callback(page_result)
        _emit_progress(
            progress,
            phase="ocr",
            completed_pages=index,
            total_pages=total_pages,
            detail=f"recognized page {index:,}/{total_pages:,}",
        )

    return PaddleOCRVLDocumentResult(
        parser_version=paddleocr_vl_version(),
        settings=settings,
        pages=page_results,
    )


def _run_paddleocr_vl_subprocess(
    *,
    page_image_paths: list[Path],
    assets_dir: Path,
    settings: PaddleOCRVLSettings,
    progress: PaddleOCRVLProgressCallback | None,
    page_result_callback: PaddleOCRVLPageResultCallback | None = None,
) -> PaddleOCRVLDocumentResult:
    context = multiprocessing.get_context("spawn")
    message_queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    process = context.Process(
        target=_paddleocr_vl_worker,
        args=(
            [path.as_posix() for path in page_image_paths],
            assets_dir.as_posix(),
            settings,
            message_queue,
        ),
    )
    process.start()

    result_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    try:
        while result_payload is None and error_payload is None:
            try:
                message = message_queue.get(timeout=0.25)
            except queue.Empty:
                if process.is_alive():
                    continue
                break

            message_type = message.get("type")
            if message_type == "progress":
                paddle_progress = _progress_from_payload(message.get("progress", {}))
                _emit_progress(
                    progress,
                    phase=paddle_progress.phase,
                    completed_pages=paddle_progress.completed_pages,
                    total_pages=paddle_progress.total_pages,
                    detail=paddle_progress.detail,
                )
                continue
            if message_type == "page_result":
                if page_result_callback is not None:
                    page_result_callback(_page_result_from_payload(message.get("page_result", {})))
                continue
            if message_type == "result":
                result_payload = _dict_or_empty(message.get("result"))
                continue
            if message_type == "error":
                error_payload = _dict_or_empty(message.get("error"))
                continue

        process.join(timeout=5.0)
        if process.is_alive():
            _terminate_process(process)

        if result_payload is not None:
            return _document_result_from_payload(result_payload)
        if error_payload is not None:
            _raise_worker_error(error_payload)

        exitcode = process.exitcode
        raise PaddleOCRVLRuntimeError(
            "PaddleOCR-VL worker stopped without returning a result"
            + (f" (exit code {exitcode})." if exitcode is not None else ".")
        )
    except KeyboardInterrupt:
        _terminate_process(process)
        raise PaddleOCRVLInterruptedError(
            "PaddleOCR-VL parsing was interrupted; the inference worker was terminated."
        ) from None
    finally:
        message_queue.close()
        message_queue.join_thread()


def _run_paddleocr_vl_sidecar(
    *,
    page_image_paths: list[Path],
    assets_dir: Path,
    settings: PaddleOCRVLSettings,
    progress: PaddleOCRVLProgressCallback | None,
    page_result_callback: PaddleOCRVLPageResultCallback | None = None,
) -> PaddleOCRVLDocumentResult:
    total_pages = len(page_image_paths)
    _emit_progress(
        progress,
        phase="load",
        completed_pages=0,
        total_pages=total_pages,
        detail="activating PaddleOCR-VL sidecar environment",
    )

    with tempfile.TemporaryDirectory(prefix="wordbyword-paddleocr-vl-") as workspace:
        work_dir = Path(workspace)
        result_dir = work_dir / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        settings_path = work_dir / "settings.json"
        job_path = work_dir / "job.json"
        stdout_path = work_dir / "worker.stdout.log"
        stderr_path = work_dir / "worker.stderr.log"
        error_path = result_dir / "error.json"
        complete_path = result_dir / "complete.json"

        _write_json_atomic(settings_path, _sidecar_settings_payload(settings))
        _write_json_atomic(
            job_path,
            {
                "pages": [
                    {"page": index, "image_path": image_path.resolve().as_posix()}
                    for index, image_path in enumerate(page_image_paths, start=1)
                ]
            },
        )

        process: subprocess.Popen[bytes] | None = None
        results_by_page: dict[int, PaddleOCRVLPageResult] = {}
        pending_pages = set(range(1, total_pages + 1))
        parser_version = ""
        deadline = (
            time.monotonic() + float(settings.sidecar_timeout_seconds)
            if settings.sidecar_timeout_seconds is not None
            else None
        )
        poll_seconds = (
            max(float(settings.sidecar_poll_seconds), 0.05)
            if settings.sidecar_poll_seconds is not None
            else 0.25
        )

        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    _sidecar_worker_command(
                        settings=settings,
                        settings_path=settings_path,
                        job_path=job_path,
                        result_dir=result_dir,
                        assets_dir=assets_dir,
                    ),
                    stdout=stdout,
                    stderr=stderr,
                    env=_sidecar_process_env(settings),
                )
                _emit_progress(progress, phase="load", completed_pages=0, total_pages=total_pages, detail="PaddleOCR-VL sidecar started")
                _emit_progress(progress, phase="ocr", completed_pages=0, total_pages=total_pages, detail=f"recognizing page 1/{max(total_pages, 1)}")

                while True:
                    _collect_sidecar_page_results(
                        result_dir=result_dir,
                        pending_pages=pending_pages,
                        results_by_page=results_by_page,
                        progress=progress,
                        total_pages=total_pages,
                        page_result_callback=page_result_callback,
                    )
                    if complete_path.is_file():
                        complete_payload = _read_json_file(complete_path)
                        parser_version = str(complete_payload.get("parser_version") or "")
                    if error_path.is_file():
                        break
                    if deadline is not None and time.monotonic() > deadline:
                        _terminate_sidecar_process(process)
                        raise PaddleOCRVLRuntimeError(
                            "PaddleOCR-VL sidecar timed out after "
                            f"{settings.sidecar_timeout_seconds} second(s)."
                        )
                    return_code = process.poll()
                    if not pending_pages and (complete_path.is_file() or return_code is not None):
                        break
                    if return_code is not None:
                        break
                    time.sleep(poll_seconds)

                if not pending_pages and process.poll() is None:
                    _terminate_sidecar_process(process)
        finally:
            if process is not None and process.poll() is None:
                _terminate_sidecar_process(process)

        _collect_sidecar_page_results(
            result_dir=result_dir,
            pending_pages=pending_pages,
            results_by_page=results_by_page,
            progress=progress,
            total_pages=total_pages,
            page_result_callback=page_result_callback,
        )
        error_message = _sidecar_error_message(error_path)
        return_code = process.poll() if process is not None else None
        if return_code not in (0, None) and not complete_path.is_file() and not error_message:
            error_message = f"Sidecar process exited with code {return_code}."
        if pending_pages or error_message:
            raise PaddleOCRVLRuntimeError(
                "PaddleOCR-VL sidecar did not return all page results "
                f"({len(results_by_page)}/{total_pages})."
                f"{_sidecar_failure_details(stderr_path=stderr_path, error_message=error_message)}"
            )

        return PaddleOCRVLDocumentResult(
            parser_version=parser_version,
            settings=settings,
            pages=[results_by_page[page] for page in sorted(results_by_page)],
        )


def _collect_sidecar_page_results(
    *,
    result_dir: Path,
    pending_pages: set[int],
    results_by_page: dict[int, PaddleOCRVLPageResult],
    progress: PaddleOCRVLProgressCallback | None,
    total_pages: int,
    page_result_callback: PaddleOCRVLPageResultCallback | None,
) -> None:
    for page in sorted(pending_pages):
        result_path = result_dir / f"page-{page:04d}.json"
        if not result_path.is_file():
            continue
        page_result = _page_result_from_payload(_read_json_file(result_path))
        results_by_page[page] = page_result
        pending_pages.remove(page)
        if page_result_callback is not None:
            page_result_callback(page_result)
        _emit_progress(
            progress,
            phase="ocr",
            completed_pages=len(results_by_page),
            total_pages=total_pages,
            detail=f"recognized page {len(results_by_page):,}/{total_pages:,}",
        )


def _paddleocr_vl_worker(
    page_image_paths: list[str],
    assets_dir: str,
    settings: PaddleOCRVLSettings,
    message_queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    try:
        result = _run_paddleocr_vl_in_process(
            page_image_paths=[Path(path) for path in page_image_paths],
            assets_dir=Path(assets_dir),
            settings=settings,
            progress=lambda progress: message_queue.put(
                {"type": "progress", "progress": asdict(progress)}
            ),
            page_result_callback=lambda page_result: message_queue.put(
                {"type": "page_result", "page_result": _page_result_to_payload(page_result)}
            ),
        )
    except KeyboardInterrupt:
        message_queue.put(
            {
                "type": "error",
                "error": {
                    "error_type": "KeyboardInterrupt",
                    "message": "PaddleOCR-VL worker interrupted.",
                    "traceback": traceback.format_exc(),
                },
            }
        )
        return
    except BaseException as error:
        message_queue.put(
            {
                "type": "error",
                "error": {
                    "error_type": error.__class__.__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                },
            }
        )
        return

    message_queue.put({"type": "result", "result": _document_result_to_payload(result)})


def _terminate_process(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        process.join(timeout=1.0)
        return
    process.terminate()
    process.join(timeout=5.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=5.0)


def _document_result_to_payload(result: PaddleOCRVLDocumentResult) -> dict[str, Any]:
    return {
        "parser_version": result.parser_version,
        "settings": asdict(result.settings),
        "pages": [_page_result_to_payload(page) for page in result.pages],
    }


def _document_result_from_payload(payload: dict[str, Any]) -> PaddleOCRVLDocumentResult:
    settings_payload = _dict_or_empty(payload.get("settings"))
    pages_payload = payload.get("pages")
    return PaddleOCRVLDocumentResult(
        parser_version=str(payload.get("parser_version") or ""),
        settings=PaddleOCRVLSettings(**settings_payload),
        pages=[
            _page_result_from_payload(page)
            for page in pages_payload
            if isinstance(page, dict)
        ]
        if isinstance(pages_payload, list)
        else [],
    )


def _page_result_to_payload(page: PaddleOCRVLPageResult) -> dict[str, Any]:
    return {
        "page": page.page,
        "input_image_path": page.input_image_path,
        "markdown": page.markdown,
        "raw_json": page.raw_json,
        "markdown_asset_paths": page.markdown_asset_paths,
    }


def _page_result_from_payload(payload: Any) -> PaddleOCRVLPageResult:
    page = _dict_or_empty(payload)
    markdown_asset_paths = page.get("markdown_asset_paths", [])
    return PaddleOCRVLPageResult(
        page=int(page.get("page", 0) or 0),
        input_image_path=str(page.get("input_image_path") or ""),
        markdown=str(page.get("markdown") or ""),
        raw_json=_dict_or_empty(page.get("raw_json")),
        markdown_asset_paths=[
            str(path)
            for path in markdown_asset_paths
            if isinstance(path, str)
        ]
        if isinstance(markdown_asset_paths, list)
        else [],
    )


def _progress_from_payload(payload: Any) -> PaddleOCRVLProgress:
    progress_payload = _dict_or_empty(payload)
    phase = progress_payload.get("phase")
    return PaddleOCRVLProgress(
        phase=phase if phase in {"load", "ocr"} else "ocr",
        completed_pages=int(progress_payload.get("completed_pages", 0) or 0),
        total_pages=int(progress_payload.get("total_pages", 0) or 0),
        detail=str(progress_payload.get("detail") or ""),
    )


def _raise_worker_error(payload: dict[str, Any]) -> None:
    error_type = str(payload.get("error_type") or "RuntimeError")
    message = str(payload.get("message") or "PaddleOCR-VL worker failed.")
    detail = str(payload.get("traceback") or "").strip()
    if error_type == "KeyboardInterrupt":
        raise PaddleOCRVLInterruptedError(message)
    if detail:
        message = f"{message}\n{detail}"
    raise PaddleOCRVLRuntimeError(message)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _uses_sidecar(settings: PaddleOCRVLSettings) -> bool:
    return bool(settings.sidecar_env or settings.sidecar_python)


def _sidecar_worker_command(
    *,
    settings: PaddleOCRVLSettings,
    settings_path: Path,
    job_path: Path,
    result_dir: Path,
    assets_dir: Path,
) -> list[str]:
    return [
        _sidecar_python(settings),
        Path(__file__).resolve().as_posix(),
        "--paddleocr-vl-worker",
        "--settings-json",
        settings_path.as_posix(),
        "--job-json",
        job_path.as_posix(),
        "--output-dir",
        result_dir.as_posix(),
        "--assets-dir",
        assets_dir.resolve().as_posix(),
    ]


def _sidecar_python(settings: PaddleOCRVLSettings) -> str:
    if settings.sidecar_python:
        expanded = Path(settings.sidecar_python).expanduser()
        return expanded.as_posix() if expanded.exists() else settings.sidecar_python
    if settings.sidecar_env:
        sidecar_env = Path(settings.sidecar_env).expanduser()
        candidates = [
            sidecar_env / "bin" / "python",
            sidecar_env / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.as_posix()
        raise PaddleOCRVLRuntimeError(
            f"PaddleOCR-VL sidecar Python was not found under {sidecar_env}."
        )
    raise PaddleOCRVLRuntimeError(
        "PaddleOCR-VL sidecar mode requires sidecar_env or sidecar_python."
    )


def _sidecar_process_env(settings: PaddleOCRVLSettings) -> dict[str, str]:
    env = os.environ.copy()
    if settings.sidecar_env:
        sidecar_env = Path(settings.sidecar_env).expanduser()
        bin_dir = sidecar_env / ("Scripts" if sys.platform == "win32" else "bin")
        env["VIRTUAL_ENV"] = sidecar_env.as_posix()
        env["PATH"] = f"{bin_dir.as_posix()}{os.pathsep}{env.get('PATH', '')}"
    import_root = Path(__file__).resolve().parents[3].as_posix()
    python_path_entries = [import_root]
    existing_python_path = env.get("PYTHONPATH", "")
    if existing_python_path:
        python_path_entries.extend(
            entry for entry in existing_python_path.split(os.pathsep) if entry and entry != import_root
        )
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    return env


def _sidecar_settings_payload(settings: PaddleOCRVLSettings) -> dict[str, Any]:
    return {
        key: value
        for key, value in asdict(settings).items()
        if value is not None and not key.startswith("sidecar_") and key != "isolated_process"
    }


def _terminate_sidecar_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def _sidecar_error_message(error_path: Path) -> str:
    if not error_path.is_file():
        return ""
    payload = _read_json_file(error_path)
    return str(payload.get("message") or "")


def _sidecar_failure_details(*, stderr_path: Path, error_message: str) -> str:
    details: list[str] = []
    if error_message:
        details.append(error_message)
    stderr_tail = _read_text_tail(stderr_path)
    if stderr_tail:
        details.append(stderr_tail)
    if not details:
        return ""
    return " " + " ".join(details)


def _emit_progress(
    progress: PaddleOCRVLProgressCallback | None,
    *,
    phase: Literal["load", "ocr"],
    completed_pages: int,
    total_pages: int,
    detail: str,
) -> None:
    if progress is None:
        return
    progress(
        PaddleOCRVLProgress(
            phase=phase,
            completed_pages=completed_pages,
            total_pages=total_pages,
            detail=detail,
        )
    )


def _cached_pipeline(settings: PaddleOCRVLSettings) -> Any:
    init_kwargs = _paddle_init_kwargs(settings)
    cache_key = tuple(sorted(init_kwargs.items()))
    pipeline = _PIPELINE_CACHE.get(cache_key)
    if pipeline is None:
        pipeline_class = _load_paddleocr_vl_class()
        pipeline = pipeline_class(**init_kwargs)
        _PIPELINE_CACHE[cache_key] = pipeline
    return pipeline


def paddleocr_vl_version() -> str:
    try:
        return importlib.metadata.version("paddleocr")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _load_paddleocr_vl_class() -> Any:
    try:
        from paddleocr import PaddleOCRVL
    except ImportError as error:
        raise PaddleOCRVLRuntimeError(
            "PaddleOCR-VL is not installed in this Python environment. "
            'Install PaddlePaddle plus "paddleocr[doc-parser]" in a Python 3.9-3.13 '
            "environment, or configure PaddleOCR-VL to call a VLM inference service."
        ) from error
    return PaddleOCRVL


def _paddle_init_kwargs(settings: PaddleOCRVLSettings) -> dict[str, Any]:
    return _without_none(
        {
            "pipeline_version": PADDLEOCR_VL_PIPELINE_VERSION,
            "device": settings.device,
            "engine": settings.engine,
            "vl_rec_backend": settings.vl_rec_backend,
            "vl_rec_server_url": settings.vl_rec_server_url,
            "vl_rec_api_model_name": settings.vl_rec_api_model_name,
            "vl_rec_api_key": settings.vl_rec_api_key,
            "use_doc_orientation_classify": settings.use_doc_orientation_classify,
            "use_doc_unwarping": settings.use_doc_unwarping,
            "use_layout_detection": settings.use_layout_detection,
            "use_chart_recognition": settings.use_chart_recognition,
            "use_seal_recognition": settings.use_seal_recognition,
            "use_ocr_for_image_block": settings.use_ocr_for_image_block,
            "format_block_content": settings.format_block_content,
        }
    )


def _paddle_predict_kwargs(settings: PaddleOCRVLSettings) -> dict[str, Any]:
    return _without_none(
        {
            "layout_shape_mode": settings.layout_shape_mode,
            "temperature": settings.temperature,
            "max_new_tokens": settings.max_new_tokens,
        }
    )


def _page_result(
    *,
    result: Any,
    page: int,
    input_image_path: Path,
    assets_dir: Path,
) -> PaddleOCRVLPageResult:
    raw_json = jsonable(getattr(result, "json", {}))
    markdown_payload = getattr(result, "markdown", {})
    markdown = _markdown_text(markdown_payload)
    markdown, markdown_asset_paths = _save_markdown_images(
        markdown=markdown,
        markdown_payload=markdown_payload,
        assets_dir=assets_dir,
        page=page,
    )
    if not markdown.strip() and isinstance(raw_json, dict):
        markdown = _text_from_raw_json(raw_json)
    return PaddleOCRVLPageResult(
        page=page,
        input_image_path=f"assets/{input_image_path.name}",
        markdown=markdown.strip(),
        raw_json=raw_json if isinstance(raw_json, dict) else {"value": raw_json},
        markdown_asset_paths=markdown_asset_paths,
    )


def _markdown_text(markdown_payload: Any) -> str:
    if isinstance(markdown_payload, str):
        return markdown_payload
    if not isinstance(markdown_payload, dict):
        return ""

    for key in ("markdown_texts", "text"):
        value = markdown_payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n\n".join(str(item).strip() for item in value if str(item).strip())
    return ""


def _text_from_raw_json(raw_json: dict[str, Any]) -> str:
    payload = raw_json.get("res") if isinstance(raw_json.get("res"), dict) else raw_json
    parsing_results = payload.get("parsing_res_list") if isinstance(payload, dict) else None
    if not isinstance(parsing_results, list):
        return ""
    parts = []
    for item in parsing_results:
        if not isinstance(item, dict):
            continue
        content = item.get("block_content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return "\n\n".join(parts)


def _save_markdown_images(
    *,
    markdown: str,
    markdown_payload: Any,
    assets_dir: Path,
    page: int,
) -> tuple[str, list[str]]:
    if not isinstance(markdown_payload, dict):
        return markdown, []

    markdown_images = markdown_payload.get("markdown_images") or markdown_payload.get("images")
    if not isinstance(markdown_images, dict):
        return markdown, []

    asset_paths: list[str] = []
    rewritten = markdown
    for index, (source_name, image) in enumerate(markdown_images.items()):
        save = getattr(image, "save", None)
        if not callable(save):
            continue
        suffix = Path(str(source_name)).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}:
            suffix = ".png"
        filename = f"paddleocr-vl-page-{page:04d}-image-{index:02d}{suffix}"
        output_path = assets_dir / filename
        save(output_path)
        relative_path = f"assets/{filename}"
        asset_paths.append(relative_path)
        rewritten = rewritten.replace(str(source_name), relative_path)

    return rewritten, asset_paths


def _worker_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run PaddleOCR-VL sidecar worker.")
    parser.add_argument("--paddleocr-vl-worker", action="store_true")
    parser.add_argument("--settings-json", type=Path, required=True)
    parser.add_argument("--job-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.paddleocr_vl_worker:
        parser.error("--paddleocr-vl-worker is required")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings_payload = _read_json_file(args.settings_json)
        job_payload = _read_json_file(args.job_json)
        settings = PaddleOCRVLSettings(**settings_payload, isolated_process=False)
        page_jobs = job_payload.get("pages", [])
        if not isinstance(page_jobs, list):
            raise PaddleOCRVLRuntimeError("PaddleOCR-VL sidecar job file is invalid.")

        pipeline = _cached_pipeline(settings)
        predict_kwargs = _paddle_predict_kwargs(settings)
        for page_job in page_jobs:
            if not isinstance(page_job, dict):
                continue
            page = int(page_job.get("page", 0) or 0)
            image_path = Path(str(page_job.get("image_path") or ""))
            raw_results = list(pipeline.predict(input=[image_path.as_posix()], **predict_kwargs))
            if len(raw_results) != 1:
                raise PaddleOCRVLRuntimeError(
                    "PaddleOCR-VL returned "
                    f"{len(raw_results)} result(s) for page image {image_path.name}."
                )
            page_result = _page_result(
                result=raw_results[0],
                page=page,
                input_image_path=image_path,
                assets_dir=args.assets_dir,
            )
            _write_json_atomic(output_dir / f"page-{page:04d}.json", _page_result_to_payload(page_result))

        _write_json_atomic(output_dir / "complete.json", {"parser_version": paddleocr_vl_version()})
        return 0
    except BaseException as error:
        _write_json_atomic(
            output_dir / "error.json",
            {
                "error_type": error.__class__.__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


def _read_json_file(path: Path) -> dict[str, Any]:
    return read_json_object_or_empty_file(path, error_type=PaddleOCRVLRuntimeError)


def _write_json_atomic(path: Path, payload: Any) -> None:
    write_json_atomic_file(path, payload)


def _read_text_tail(path: Path, *, max_chars: int = 4000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:].strip()


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


if __name__ == "__main__":
    raise SystemExit(_worker_main())
