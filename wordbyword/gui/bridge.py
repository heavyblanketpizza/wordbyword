"""One-request JSON bridge between the native desktop app and WordByWord.

No server, shell, or third-party bridge dependency is involved.  Each process
owns one workspace and streams newline-delimited packets on stdout.  Incidental
Python and native-library output goes to stderr, never into the JSON protocol.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, TextIO

sys.dont_write_bytecode = True

from wordbyword.gui.workspace import (  # noqa: E402
    import_files,
    migration_preview,
    preview,
    safe_data_path,
    snapshot,
    target_grade,
    workspace_root,
)

MAX_REQUEST_BYTES = 262_144
MAX_ARGUMENTS = 128
MAX_STRING_LENGTH = 8_192
PacketSink = Callable[[dict[str, object]], None]

# Several recovery flags intentionally form one command.  All other command
# selectors must be exclusive, including combinations the legacy parser accepts.
COMMAND_SELECTORS = (
    ("migrate_grade_workspaces",), ("build_dictionary",), ("prep_dictionary",),
    ("clean_example_sentences", "clean_dictionary_candidates", "generate_missing_examples", "retry_generated_examples"),
    ("parse_source_material",), ("parse_source_materials",),
    ("rewrite_whole_source_material",), ("rewrite_whole_source_materials",),
    ("rewrite_vocabulary_guided_source_material",), ("rewrite_vocabulary_guided_source_materials",),
    ("print_class_material",), ("print_class_materials",), ("build_class_printouts",),
    ("generate_after_class_reading_report",), ("resolve_archive_duplicates",),
    ("resolve_dictionary_source_duplicates",), ("rename_class_session_archive",),
    ("rename_dictionary_source_archive",), ("build_usage_signals",), ("update_live_lexicon",),
    ("show_distribution",), ("link_cleaned_examples",), ("add_reference_meanings",),
    ("generate_missing_meanings",),
)


class BridgeError(ValueError):
    """Invalid desktop request or CLI argument combination."""


def _redact(text: str) -> str:
    for name, value in os.environ.items():
        if len(value) >= 8 and re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD)", name, re.IGNORECASE):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^\s&,]+", r"\1[redacted]", text)
    return text


def _safe_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item) for item in value]
    return value


def _error_message(error: BaseException) -> str:
    text = str(error).strip() or type(error).__name__
    return _redact(text)[:2_000]


def _string(value: object, name: str, *, optional: bool = False) -> str:
    if optional and value is None:
        return ""
    if not isinstance(value, str) or not value or len(value) > MAX_STRING_LENGTH or "\x00" in value:
        raise BridgeError(f"{name} must be a nonempty string of at most {MAX_STRING_LENGTH} characters.")
    return value


def _strings(value: object, name: str, *, maximum: int) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise BridgeError(f"{name} must contain between 1 and {maximum} strings.")
    return [_string(item, name) for item in value]


def read_request(stream: Any) -> dict[str, object]:
    payload = stream.read(MAX_REQUEST_BYTES + 1)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise BridgeError("The desktop request is too large.")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BridgeError("Duplicate JSON keys are not allowed.")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        del value
        raise BridgeError("Nonfinite numbers are not valid in desktop requests.")

    try:
        request = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BridgeError("Send one valid JSON object to the desktop bridge.") from error
    if not isinstance(request, dict):
        raise BridgeError("The desktop request must be a JSON object.")
    return request


@contextlib.contextmanager
def _working_directory(root: Path) -> Iterator[None]:
    original = Path.cwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(original)


def build_request(arguments: list[str]) -> Any:
    from wordbyword.application.commands import registered_command_ids
    from wordbyword.cli.options import build_parser, validate_args
    from wordbyword.cli.request_builder import build_cli_request

    if any(re.search(r"(?i)(api[-_]?key|access[-_]?token|password|secret)", argument.split("=", 1)[0])
           for argument in arguments if argument.startswith("--")):
        raise BridgeError("Supply provider credentials through the environment, not desktop arguments.")
    for index, argument in enumerate(arguments):
        if argument.startswith("--target-grade="):
            target_grade(argument.split("=", 1)[1])
        elif argument == "--target-grade" and index + 1 < len(arguments):
            target_grade(arguments[index + 1])
    parser = build_parser()
    parser.allow_abbrev = False

    def exit_parser(status: int = 0, message: str | None = None) -> None:
        del status
        raise BridgeError((message or "Choose an explicit project command.").strip())

    parser.exit = exit_parser  # type: ignore[method-assign]
    args = parser.parse_args(arguments)
    selected = sum(
        any((value := getattr(args, name, None)) is not None and value is not False for name in group)
        for group in COMMAND_SELECTORS
    )
    if selected != 1:
        raise BridgeError("Run exactly one project command at a time.")
    validate_args(parser, args)
    request = build_cli_request(args)
    if request is None or request.command_id not in registered_command_ids():
        raise BridgeError("Choose a registered WordByWord command.")
    return request


def _validate_request_paths(root: Path, request: Any) -> None:
    for field in fields(request):
        value = getattr(request, field.name)
        if isinstance(value, Path) and field.name != "font_path":
            safe_data_path(root, value)
    vocabulary_source = getattr(request, "vocabulary_source", None)
    if vocabulary_source is not None:
        safe_data_path(root, vocabulary_source.path)
    source_id = getattr(request, "source_id", None)
    if source_id is not None and (source_id.startswith(".") or "/" in source_id or "\\" in source_id):
        raise BridgeError("A source ID must be a plain name, not a file path.")
    base_url = getattr(request, "base_url", None)
    if base_url:
        from urllib.parse import urlsplit

        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            raise BridgeError("Provider URLs must use HTTP(S) without embedded credentials.")


def _run(root: Path, arguments: list[str], emit: PacketSink) -> int:
    from wordbyword.application.commands import CommandId, command_spec
    from wordbyword.application.use_cases import run_request
    from wordbyword.execution_guard import acquire_workspace_command_guard

    request = build_request(arguments)
    _validate_request_paths(root, request)
    specification = command_spec(request.command_id)
    guard = (
        acquire_workspace_command_guard(specification.lock_label, workspace_root=root)
        if specification.lock_label else contextlib.nullcontext()
    )
    with guard:
        if request.command_id == CommandId.MIGRATE_GRADE_WORKSPACES and request.apply:
            plan = migration_preview(root)
            if not plan["can_apply"]:
                raise BridgeError("There is no safe, fully reviewable migration to apply. Review the migration plan first.")
        elif specification.lock_label and getattr(request, "target_grade", None):
            if snapshot(root, request.target_grade)["legacy_layout"]:
                raise BridgeError("Review and apply workspace migration before running commands for this grade.")

        def progress(command_event: Any) -> None:
            emit({"type": "event", "data": command_event.to_dict()})

        result = run_request(request, emit_event=progress)
    emit({"type": "result", "data": asdict(result)})
    return result.exit_code


def dispatch_request(root: Path, request: dict[str, object], emit: PacketSink) -> int:
    """Handle one request; errors are packets, and only explicit mutations lock."""
    try:
        if "operation" in request and "op" in request and request["operation"] != request["op"]:
            raise BridgeError("The desktop operation selectors disagree.")
        operation = _string(request.get("operation", request.get("op")), "operation")
        grade = request.get("grade", "grade1")
        with _working_directory(root):
            if operation == "snapshot":
                emit({"type": "snapshot", "data": snapshot(root, grade)})
            elif operation == "preview":
                emit({"type": "preview", "data": preview(root, _string(request.get("path"), "path"), grade)})
            elif operation == "migration_plan":
                emit({"type": "preview", "data": migration_preview(root)})
            elif operation == "run":
                arguments = _strings(request.get("arguments"), "arguments", maximum=MAX_ARGUMENTS)
                return _run(root, arguments, emit)
            elif operation == "import":
                from wordbyword.execution_guard import acquire_workspace_command_guard
                from wordbyword.gui.workspace import MAX_IMPORT_FILES

                destination = _string(request.get("destination"), "destination")
                files = _strings(request.get("files"), "files", maximum=MAX_IMPORT_FILES)
                with acquire_workspace_command_guard("desktop file import", workspace_root=root):
                    artifacts = import_files(root, grade, destination, files)
                emit({"type": "result", "data": {
                    "exit_code": 0, "status": "complete", "detail": f"Imported {len(artifacts)} file(s).",
                    "artifacts": artifacts, "error": "",
                }})
            else:
                raise BridgeError("Unknown desktop operation.")
        return 0
    except KeyboardInterrupt:
        emit({"type": "result", "data": {
            "exit_code": 130, "status": "cancelled", "detail": "Stopped. Completed files have been preserved.",
            "artifacts": {}, "error": "interrupted",
        }})
        return 130
    except Exception as error:
        emit({"type": "error", "message": _error_message(error)})
        return 2


@contextlib.contextmanager
def _protocol_stdout() -> Iterator[TextIO]:
    """Reserve a duplicate of stdout before redirecting native stdout to stderr."""
    sys.stdout.flush()
    descriptor = sys.stdout.fileno()
    protocol = os.fdopen(os.dup(descriptor), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), descriptor)
    try:
        yield protocol
    finally:
        sys.stdout.flush()
        # Keep fd 1 redirected through interpreter shutdown: native libraries
        # and atexit handlers can still write after main() has returned.
        protocol.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WordByWord desktop JSON bridge")
    parser.add_argument("--workspace", required=True)
    options = parser.parse_args(argv)
    with _protocol_stdout() as output:
        mutex = threading.Lock()

        def emit(packet: dict[str, object]) -> None:
            line = json.dumps(_safe_payload(packet), ensure_ascii=False, allow_nan=False)
            with mutex:
                output.write(line + "\n")
                output.flush()

        try:
            root = workspace_root(options.workspace)
            request = read_request(sys.stdin.buffer)
        except KeyboardInterrupt:
            emit({"type": "result", "data": {
                "exit_code": 130, "status": "cancelled", "detail": "Stopped before execution.",
                "artifacts": {}, "error": "interrupted",
            }})
            return 130
        except Exception as error:
            emit({"type": "error", "message": _error_message(error)})
            return 2
        return dispatch_request(root, request, emit)


if __name__ == "__main__":
    raise SystemExit(main())
