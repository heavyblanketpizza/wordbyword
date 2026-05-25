from __future__ import annotations

import errno
import json
import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

RUN_LOCK_SCHEMA_VERSION = "wordbyword.run-lock.v1"
RUN_LOCK_DIRNAME = ".wordbyword"
RUN_LOCK_FILENAME = "run.lock"

_HELD_LOCKS: dict[Path, "ActiveCommand"] = {}
_HELD_LOCKS_MUTEX = threading.Lock()


class _LockConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ActiveCommand:
    command_label: str
    pid: int | None
    hostname: str
    workspace_root: str
    started_at: str
    schema_version: str = RUN_LOCK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "command_label": self.command_label,
            "pid": self.pid,
            "hostname": self.hostname,
            "workspace_root": self.workspace_root,
            "started_at": self.started_at,
        }


class CommandAlreadyRunningError(RuntimeError):
    def __init__(self, active_command: ActiveCommand | None, lock_path: Path) -> None:
        self.active_command = active_command
        self.lock_path = lock_path
        super().__init__(f"another command is already running in this workspace: {active_command_summary(active_command)}")


class WorkspaceCommandGuard:
    def __init__(self, *, workspace_root: Path, command_label: str) -> None:
        self.workspace_root = workspace_root.resolve()
        self.command_label = command_label
        self.lock_path = self.workspace_root / RUN_LOCK_DIRNAME / RUN_LOCK_FILENAME
        self.active_command = ActiveCommand(
            command_label=command_label,
            pid=os.getpid(),
            hostname=socket.gethostname(),
            workspace_root=str(self.workspace_root),
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._handle: Any | None = None
        self._registered = False

    def acquire(self) -> WorkspaceCommandGuard:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._register_process_lock()
        try:
            handle = self.lock_path.open("a+", encoding="utf-8")
            try:
                _try_lock_handle(handle)
            except _LockConflict as error:
                active_command = _read_active_command(handle)
                handle.close()
                self._unregister_process_lock()
                raise CommandAlreadyRunningError(active_command, self.lock_path) from error
            except Exception:
                handle.close()
                self._unregister_process_lock()
                raise
            _write_active_command(handle, self.active_command)
            self._handle = handle
        except Exception:
            if self._handle is None:
                self._unregister_process_lock()
            raise
        return self

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        try:
            if handle is not None:
                try:
                    _unlock_handle(handle)
                finally:
                    handle.close()
        finally:
            self._unregister_process_lock()

    def __enter__(self) -> WorkspaceCommandGuard:
        if self._handle is None:
            self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def _register_process_lock(self) -> None:
        with _HELD_LOCKS_MUTEX:
            active_command = _HELD_LOCKS.get(self.lock_path)
            if active_command is not None:
                raise CommandAlreadyRunningError(active_command, self.lock_path)
            _HELD_LOCKS[self.lock_path] = self.active_command
            self._registered = True

    def _unregister_process_lock(self) -> None:
        if not self._registered:
            return
        with _HELD_LOCKS_MUTEX:
            if _HELD_LOCKS.get(self.lock_path) == self.active_command:
                _HELD_LOCKS.pop(self.lock_path, None)
        self._registered = False


def acquire_workspace_command_guard(
    command_label: str,
    *,
    workspace_root: Path | None = None,
) -> WorkspaceCommandGuard:
    root = Path.cwd() if workspace_root is None else workspace_root
    return WorkspaceCommandGuard(workspace_root=root, command_label=command_label).acquire()


def active_command_summary(active_command: ActiveCommand | None) -> str:
    if active_command is None:
        return "unknown command"
    label = active_command.command_label or "unknown command"
    details: list[str] = []
    if active_command.pid is not None:
        details.append(f"pid {active_command.pid}")
    if active_command.hostname:
        details.append(active_command.hostname)
    if active_command.started_at:
        details.append(f"started {active_command.started_at}")
    if not details:
        return label
    return f"{label} ({', '.join(details)})"


def _try_lock_handle(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise _LockConflict() from error
        return

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise _LockConflict() from error
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            raise _LockConflict() from error
        raise


def _unlock_handle(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_active_command(handle: Any, active_command: ActiveCommand) -> None:
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(active_command.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _read_active_command(handle: Any) -> ActiveCommand | None:
    try:
        handle.seek(0)
        payload = json.loads(handle.read() or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _active_command_from_payload(payload)


def _active_command_from_payload(payload: dict[str, Any]) -> ActiveCommand | None:
    try:
        pid_value = payload.get("pid")
        pid = int(pid_value) if pid_value not in {None, ""} else None
    except (TypeError, ValueError):
        pid = None
    command_label = str(payload.get("command_label") or "")
    hostname = str(payload.get("hostname") or "")
    workspace_root = str(payload.get("workspace_root") or "")
    started_at = str(payload.get("started_at") or "")
    schema_version = str(payload.get("schema_version") or "")
    if not any((command_label, pid, hostname, workspace_root, started_at)):
        return None
    return ActiveCommand(
        command_label=command_label,
        pid=pid,
        hostname=hostname,
        workspace_root=workspace_root,
        started_at=started_at,
        schema_version=schema_version or RUN_LOCK_SCHEMA_VERSION,
    )
