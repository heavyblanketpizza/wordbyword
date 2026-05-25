from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wordbyword.execution_guard import (
    CommandAlreadyRunningError,
    acquire_workspace_command_guard,
)


class ExecutionGuardTests(unittest.TestCase):
    def test_second_guard_for_workspace_fails_fast_with_active_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            with acquire_workspace_command_guard("first command", workspace_root=root):
                with self.assertRaises(CommandAlreadyRunningError) as context:
                    acquire_workspace_command_guard("second command", workspace_root=root)

            active = context.exception.active_command
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.command_label, "first command")
            self.assertIsInstance(active.pid, int)
            self.assertTrue(active.hostname)
            self.assertTrue(active.started_at)
            self.assertIn("first command", str(context.exception))

    def test_released_guard_allows_later_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            guard = acquire_workspace_command_guard("first command", workspace_root=root)
            guard.release()

            with acquire_workspace_command_guard("second command", workspace_root=root) as second_guard:
                self.assertEqual(second_guard.active_command.command_label, "second command")

    def test_guard_writes_lock_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            with acquire_workspace_command_guard("metadata command", workspace_root=root):
                payload = json.loads((root / ".wordbyword" / "run.lock").read_text(encoding="utf-8"))

            self.assertEqual(payload["schema_version"], "wordbyword.run-lock.v1")
            self.assertEqual(payload["command_label"], "metadata command")
            self.assertEqual(payload["workspace_root"], str(root.resolve()))
            self.assertIsInstance(payload["pid"], int)
            self.assertTrue(payload["hostname"])
            self.assertTrue(payload["started_at"])

    def test_invalid_lock_metadata_still_reports_safe_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            lock_path = root / ".wordbyword" / "run.lock"
            lock_path.parent.mkdir()
            lock_path.write_text("{not json", encoding="utf-8")

            with patch("wordbyword.execution_guard._try_lock_handle", side_effect=_conflict):
                with self.assertRaises(CommandAlreadyRunningError) as context:
                    acquire_workspace_command_guard("blocked command", workspace_root=root)

            self.assertIsNone(context.exception.active_command)
            self.assertIn("unknown command", str(context.exception))


def _conflict(_handle: object) -> None:
    from wordbyword.execution_guard import _LockConflict

    raise _LockConflict()
