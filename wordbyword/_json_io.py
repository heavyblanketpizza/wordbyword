from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_object(
    path: Path,
    *,
    error_type: type[Exception] = ValueError,
    not_object_message: str | None = None,
) -> dict[str, Any]:
    parsed = read_json(path, error_type=error_type)
    if not isinstance(parsed, dict):
        raise error_type(not_object_message or f"Expected JSON object in {path}.")
    return parsed


def read_json_object_or_empty(
    path: Path,
    *,
    error_type: type[Exception] = ValueError,
) -> dict[str, Any]:
    parsed = read_json(path, error_type=error_type)
    return parsed if isinstance(parsed, dict) else {}


def read_json_list(
    path: Path,
    *,
    error_type: type[Exception] = ValueError,
    not_list_message: str | None = None,
) -> list[Any]:
    parsed = read_json(path, error_type=error_type)
    if not isinstance(parsed, list):
        raise error_type(not_list_message or f"Expected JSON list in {path}.")
    return parsed


def read_json(path: Path, *, error_type: type[Exception] = ValueError) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise error_type(f"Invalid JSON in {path}: {error.msg}") from error


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    write_json(temporary_path, payload)
    temporary_path.replace(path)


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "tolist"):
        return jsonable(value.tolist())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
