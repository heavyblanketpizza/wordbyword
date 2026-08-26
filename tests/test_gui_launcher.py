from __future__ import annotations

import io
import json
import os
import plistlib
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from wordbyword.gui import launcher


def native_sources(root: Path) -> Path:
    source = root / "Sources" / "WordByWord"
    source.mkdir(parents=True)
    (root / "Package.swift").write_text("// package manifest\n", encoding="utf-8")
    (source / "App.swift").write_text("struct App {}\n", encoding="utf-8")
    return root


class GuiImportTest(unittest.TestCase):
    def test_gui_import_does_not_load_launcher_or_workflow_dependencies(self) -> None:
        script = "\n".join(
            [
                "import json, sys",
                "import wordbyword.gui",
                "names = ['wordbyword.gui.launcher', 'wordbyword.cli', 'kiwipiepy', 'fitz', 'paddleocr']",
                "print(json.dumps([name for name in names if name in sys.modules]))",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        self.assertEqual(json.loads(result.stdout), [])

    def test_module_help_works_on_unsupported_platform_without_building(self) -> None:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["wordbyword.gui", "--help"]),
            patch.object(sys, "platform", "linux"),
            patch.object(launcher, "build_application") as build,
            patch.object(launcher.subprocess, "run") as run,
            redirect_stdout(output),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_module("wordbyword.gui", run_name="__main__")

        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--workspace", output.getvalue())
        self.assertIn("--build-only", output.getvalue())
        build.assert_not_called()
        run.assert_not_called()


class SourceFingerprintTest(unittest.TestCase):
    def test_fingerprint_is_independent_of_installation_path_and_ignores_build_products(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wbw-gui-fingerprint-") as temporary:
            root = Path(temporary)
            first = native_sources(root / "first install")
            second = native_sources(root / "second install")
            before = launcher.source_fingerprint(first)
            generated = first / ".build"
            generated.mkdir()
            (generated / "Generated.swift").write_text("generated cache\n", encoding="utf-8")

            self.assertEqual(before, launcher.source_fingerprint(first))
            self.assertEqual(before, launcher.source_fingerprint(second))

    def test_manifest_source_content_and_source_path_changes_invalidate_fingerprint(self) -> None:
        for change in ("manifest", "source", "rename", "add", "remove"):
            with self.subTest(change=change), tempfile.TemporaryDirectory(prefix="wbw-gui-fingerprint-") as temporary:
                native = native_sources(Path(temporary))
                source = native / "Sources" / "WordByWord" / "App.swift"
                before = launcher.source_fingerprint(native)
                if change == "manifest":
                    (native / "Package.swift").write_text("// revised package\n", encoding="utf-8")
                elif change == "source":
                    source.write_text("struct RevisedApp {}\n", encoding="utf-8")
                elif change == "rename":
                    source.rename(source.with_name("RenamedApp.swift"))
                elif change == "add":
                    source.with_name("Model.swift").write_text("struct Model {}\n", encoding="utf-8")
                else:
                    source.unlink()

                self.assertNotEqual(before, launcher.source_fingerprint(native))


class ApplicationBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="wbw-gui-build-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace with spaces"
        self.workspace.mkdir()
        self.native = native_sources(self.root / "native")
        self.binary_dir = self.root / "compiled"
        self.binary_dir.mkdir()
        self.binary = self.binary_dir / "WordByWord"
        self.binary.write_bytes(b"native executable fixture\n")
        self.binary.chmod(0o755)
        self.cache = self.workspace / ".wordbyword" / "desktop"
        self.bundle = self.cache / "WordByWord.app"

        fingerprint = launcher.source_fingerprint
        self.enterContext(patch.object(launcher, "NATIVE_ROOT", self.native))
        self.enterContext(patch.object(launcher, "source_fingerprint", side_effect=lambda: fingerprint(self.native)))
        self.which = self.enterContext(patch.object(launcher.shutil, "which", return_value="/usr/bin/swift"))
        self.run = self.enterContext(patch.object(launcher.subprocess, "run"))
        self.bin_path = self.enterContext(
            patch.object(launcher.subprocess, "check_output", return_value=str(self.binary_dir) + "\n")
        )
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_build_creates_launchable_bundle_and_preserves_virtualenv_python_symlink(self) -> None:
        actual_python = self.root / "python3.14"
        actual_python.write_bytes(b"python fixture\n")
        venv_python = self.workspace / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(actual_python)
        with patch.object(launcher.sys, "executable", str(venv_python)):
            bundle = launcher.build_application(self.workspace)

        executable = bundle / "Contents" / "MacOS" / "WordByWord"
        self.assertEqual(bundle, self.bundle)
        self.assertEqual(executable.read_bytes(), self.binary.read_bytes())
        self.assertTrue(os.access(executable, os.X_OK))
        info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
        self.assertEqual(info["CFBundleExecutable"], executable.name)
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        runtime = json.loads((bundle / "Contents" / "Resources" / "runtime.json").read_text())
        self.assertEqual(runtime, {"python": str(venv_python), "workspace": str(self.workspace),
                                   "module_root": str(launcher.MODULE_ROOT)})
        self.assertNotEqual(runtime["python"], str(venv_python.resolve()))
        self.assertNotEqual(runtime["module_root"], runtime["workspace"])
        build_args = [
            "/usr/bin/swift", "build", "--package-path", str(self.native),
            "--scratch-path", str(self.cache / "build"), "--configuration", "release",
        ]
        self.run.assert_called_once_with(build_args, check=True)
        self.bin_path.assert_called_once_with([*build_args, "--show-bin-path"], text=True)

    def test_unchanged_cache_does_not_require_swift_and_refreshes_python_runtime(self) -> None:
        launcher.build_application(self.workspace)
        self.which.return_value = None
        another_python = str(self.root / "another environment" / "bin" / "python")
        with patch.object(launcher.sys, "executable", another_python):
            bundle = launcher.build_application(self.workspace)

        self.run.assert_called_once()
        self.which.assert_called_once_with("swift")
        self.bin_path.assert_called_once()
        runtime = json.loads((bundle / "Contents" / "Resources" / "runtime.json").read_text())
        self.assertEqual(runtime["python"], another_python)

    def test_source_change_rebuilds_cached_binary_and_updates_stamp(self) -> None:
        launcher.build_application(self.workspace)
        stamp = self.cache / "source.sha256"
        before = stamp.read_text()
        (self.native / "Sources" / "WordByWord" / "App.swift").write_text("struct UpdatedApp {}\n")

        launcher.build_application(self.workspace)

        self.assertEqual(self.run.call_count, 2)
        self.assertNotEqual(before, stamp.read_text())
        self.assertEqual(stamp.read_text().strip(), launcher.source_fingerprint())

    def test_explicit_rebuild_compiles_even_when_sources_are_unchanged(self) -> None:
        launcher.build_application(self.workspace)
        launcher.build_application(self.workspace, rebuild=True)

        self.assertEqual(self.run.call_count, 2)

    def test_missing_cached_executable_triggers_a_rebuild(self) -> None:
        bundle = launcher.build_application(self.workspace)
        executable = bundle / "Contents" / "MacOS" / "WordByWord"
        executable.unlink()

        launcher.build_application(self.workspace)

        self.assertEqual(self.run.call_count, 2)
        self.assertTrue(executable.is_file())

    def test_missing_swift_has_actionable_error_and_creates_no_cache(self) -> None:
        self.which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "xcode-select --install"):
            launcher.build_application(self.workspace)

        self.run.assert_not_called()
        self.bin_path.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_cache_symlinks_are_rejected_without_touching_their_target(self) -> None:
        external = self.root / "external"
        external.mkdir()
        sentinel = external / "preserved.txt"
        sentinel.write_text("do not change\n")
        for relative in (".wordbyword", ".wordbyword/desktop"):
            with self.subTest(relative=relative):
                link = self.workspace / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(external, target_is_directory=True)
                try:
                    with self.assertRaisesRegex(RuntimeError, "symbolic links"):
                        launcher.build_application(self.workspace)
                    self.assertEqual(sentinel.read_text(), "do not change\n")
                    self.assertEqual(list(external.iterdir()), [sentinel])
                finally:
                    link.unlink()
        self.run.assert_not_called()

    def test_cached_runtime_symlink_is_rejected_and_failed_copy_leaves_no_temporary_files(self) -> None:
        launcher.build_application(self.workspace)
        runtime = self.bundle / "Contents" / "Resources" / "runtime.json"
        external = self.root / "private.json"
        external.write_text("private settings\n")
        runtime.unlink()
        runtime.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "symbolic links"):
            launcher.build_application(self.workspace)
        self.assertEqual(external.read_text(), "private settings\n")
        runtime.unlink()
        executable = self.bundle / "Contents" / "MacOS" / "WordByWord"
        previous = executable.read_bytes()
        with patch.object(launcher.shutil, "copy2", side_effect=OSError("copy interrupted")):
            with self.assertRaisesRegex(OSError, "copy interrupted"):
                launcher.build_application(self.workspace, rebuild=True)
        self.assertEqual(executable.read_bytes(), previous)
        self.assertEqual(list(self.cache.rglob(".studio-*")), [])

    def test_failed_rebuild_preserves_previous_binary_and_cache_stamp(self) -> None:
        bundle = launcher.build_application(self.workspace)
        stamp = self.cache / "source.sha256"
        previous_stamp = stamp.read_text()
        executable = bundle / "Contents" / "MacOS" / "WordByWord"
        previous_binary = executable.read_bytes()
        (self.native / "Package.swift").write_text("// revised manifest\n", encoding="utf-8")
        self.run.side_effect = subprocess.CalledProcessError(1, ["swift", "build"])

        with self.assertRaises(subprocess.CalledProcessError):
            launcher.build_application(self.workspace)

        self.assertEqual(stamp.read_text(), previous_stamp)
        self.assertEqual(executable.read_bytes(), previous_binary)
        self.bin_path.assert_called_once()


class GuiLauncherMainTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="wbw-gui-main-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace with spaces"
        self.workspace.mkdir()
        self.bundle = self.root / "WordByWord.app"
        self.enterContext(patch.object(launcher.sys, "platform", "darwin"))
        self.build = self.enterContext(patch.object(launcher, "build_application", return_value=self.bundle))
        self.run = self.enterContext(patch.object(launcher.subprocess, "run"))
        self.stdout = self.enterContext(redirect_stdout(io.StringIO()))
        self.stderr = self.enterContext(redirect_stderr(io.StringIO()))

    def test_unsupported_platform_returns_usage_error_without_building(self) -> None:
        with patch.object(launcher.sys, "platform", "linux"):
            result = launcher.main(["--workspace", str(self.workspace)])

        self.assertEqual(result, 2)
        self.assertIn("macOS 14", self.stderr.getvalue())
        self.assertIn("CLI remains available", self.stderr.getvalue())
        self.build.assert_not_called()
        self.run.assert_not_called()

    def test_missing_workspace_or_file_is_rejected_without_building(self) -> None:
        regular_file = self.root / "not-a-directory"
        regular_file.write_text("fixture\n", encoding="utf-8")
        for workspace in (self.root / "missing", regular_file):
            with self.subTest(workspace=workspace):
                self.assertEqual(launcher.main(["--workspace", str(workspace)]), 2)

        self.assertIn("Workspace does not exist", self.stderr.getvalue())
        self.build.assert_not_called()
        self.run.assert_not_called()

    def test_build_only_forwards_rebuild_flag_without_launching(self) -> None:
        result = launcher.main(["--workspace", str(self.workspace), "--build-only", "--rebuild"])

        self.assertEqual(result, 0)
        self.build.assert_called_once_with(self.workspace, rebuild=True)
        self.run.assert_not_called()
        self.assertEqual(self.stdout.getvalue().strip(), str(self.bundle))

    def test_launch_preserves_argument_boundaries_virtualenv_path_and_exit_status(self) -> None:
        python = str(self.root / "virtual environment" / "bin" / "python")
        self.run.return_value.returncode = 23
        with patch.object(launcher.sys, "executable", python):
            result = launcher.main(["--workspace", str(self.workspace)])

        self.assertEqual(result, 23)
        self.build.assert_called_once_with(self.workspace, rebuild=False)
        self.run.assert_called_once_with(
            [str(self.bundle / "Contents" / "MacOS" / "WordByWord"),
             "--workspace", str(self.workspace), "--python", python],
            cwd=self.workspace,
            check=False,
        )

    def test_build_failures_are_reported_without_a_traceback_or_launch(self) -> None:
        failures = [
            RuntimeError("missing build tool"),
            OSError("permission denied"),
            subprocess.CalledProcessError(1, ["swift", "build"]),
        ]
        for error in failures:
            with self.subTest(error=type(error).__name__):
                self.build.side_effect = error
                self.assertEqual(launcher.main(["--workspace", str(self.workspace)]), 1)

        self.assertIn("Could not open WordByWord", self.stderr.getvalue())
        self.assertNotIn("Traceback", self.stderr.getvalue())
        self.run.assert_not_called()

    def test_interrupted_launch_returns_standard_interrupt_status(self) -> None:
        self.run.side_effect = KeyboardInterrupt

        self.assertEqual(launcher.main(["--workspace", str(self.workspace)]), 130)
