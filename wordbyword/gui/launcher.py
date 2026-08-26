from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


NATIVE_ROOT = Path(__file__).with_name("native")
MODULE_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ID = "com.wordbyword.studio"


def source_fingerprint(source_root: Path = NATIVE_ROOT) -> str:
    digest = hashlib.sha256()
    paths = [source_root / "Package.swift", *sorted((source_root / "Sources").rglob("*.swift"))]
    for path in paths:
        digest.update(path.relative_to(source_root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_atomic(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".studio-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_executable(source: Path, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".studio-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def build_application(workspace: Path, *, rebuild: bool = False) -> Path:
    cache = workspace / ".wordbyword" / "desktop"
    bundle = cache / "WordByWord.app"
    executable = bundle / "Contents" / "MacOS" / "WordByWord"
    stamp = cache / "source.sha256"
    resources = bundle / "Contents" / "Resources"
    cache_paths = [
        cache.parent, cache, cache / "build", stamp, bundle, bundle / "Contents",
        executable.parent, executable, resources, resources / "runtime.json",
        bundle / "Contents" / "Info.plist",
    ]
    if any(path.is_symlink() for path in cache_paths):
        raise RuntimeError("The desktop app cache cannot use symbolic links. Choose a workspace with a local .wordbyword folder.")
    fingerprint = source_fingerprint()
    cached_fingerprint = stamp.read_text().strip() if stamp.is_file() else ""
    if rebuild or not executable.is_file() or cached_fingerprint != fingerprint:
        swift = shutil.which("swift")
        if swift is None:
            raise RuntimeError("Install Apple's Xcode Command Line Tools to build the native GUI (xcode-select --install).")
        cache.mkdir(parents=True, exist_ok=True)
        build_args = [
            swift, "build", "--package-path", str(NATIVE_ROOT),
            "--scratch-path", str(cache / "build"), "--configuration", "release",
        ]
        print("Building WordByWord for macOS. This is only needed on the first launch or after an update.", flush=True)
        subprocess.run(build_args, check=True)
        binary_dir = Path(subprocess.check_output([*build_args, "--show-bin-path"], text=True).strip())
        executable.parent.mkdir(parents=True, exist_ok=True)
        _copy_executable(binary_dir / "WordByWord", executable)
        info = {
            "CFBundleExecutable": "WordByWord",
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": "WordByWord",
            "CFBundleDisplayName": "WordByWord",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
        }
        _write_atomic(bundle / "Contents" / "Info.plist", plistlib.dumps(info))
        _write_atomic(stamp, (fingerprint + "\n").encode())
    resources.mkdir(parents=True, exist_ok=True)
    # Keep the virtualenv executable path: resolving its symlink loses the environment.
    runtime = {"python": os.path.abspath(sys.executable), "workspace": str(workspace),
               "module_root": str(MODULE_ROOT)}
    _write_atomic(resources / "runtime.json", (json.dumps(runtime, indent=2) + "\n").encode("utf-8"))
    return bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open WordByWord's native monochrome macOS studio.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Existing WordByWord data workspace.")
    parser.add_argument("--build-only", action="store_true", help="Build the app bundle without opening a window.")
    parser.add_argument("--rebuild", action="store_true", help="Recompile the app even if its sources have not changed.")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("The native GUI requires macOS 14 or later. The wordbyword CLI remains available on this platform.",
              file=sys.stderr)
        return 2
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        print(f"Workspace does not exist: {workspace}", file=sys.stderr)
        return 2
    try:
        bundle = build_application(workspace, rebuild=args.rebuild)
        if args.build_only:
            print(bundle)
            return 0
        return subprocess.run(
            [str(bundle / "Contents" / "MacOS" / "WordByWord"),
             "--workspace", str(workspace), "--python", os.path.abspath(sys.executable)],
            cwd=workspace,
            check=False,
        ).returncode
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Could not open WordByWord: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
