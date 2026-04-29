from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "wordbyword.source_material_workspace.v1"
DEFAULT_SOURCE_MATERIAL_ROOT = Path("class-source-materials")
DEFAULT_PARSED_SOURCE_ROOT = DEFAULT_SOURCE_MATERIAL_ROOT / "parsed"
SUPPORTED_SOURCE_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".xhtml",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".markdown",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
}
REMOVED_GENERATED_FILES = {
    "canonical_content.json",
    "canonical_blocks.json",
    "canonical_blocks.jsonl",
}
REMOVED_ASSET_SUBFOLDERS = {
    "drawings",
    "images",
    "pages",
    "tables",
}


@dataclass(frozen=True)
class SourceMaterialBundle:
    source_id: str
    source_path: Path
    output_dir: Path
    manifest_path: Path
    assets_dir: Path
    manifest: dict[str, Any]


@dataclass(frozen=True)
class SourceMaterialBatchResult:
    source_dir: Path
    output_root: Path
    source_paths: list[Path]
    bundles: list[SourceMaterialBundle]


class SourceMaterialParseError(RuntimeError):
    """Raised when a source material workspace cannot be prepared."""


def parse_source_material(
    source_path: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
    source_id: str | None = None,
) -> SourceMaterialBundle:
    source_path = source_path.expanduser()
    if not source_path.is_file():
        raise SourceMaterialParseError(f"Source material does not exist: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SOURCE_EXTENSIONS:
        raise SourceMaterialParseError(
            f"Unsupported source material format: {suffix or source_path.name}"
        )

    resolved_source_id = source_id or source_id_for(source_path)
    output_dir = output_root / resolved_source_id
    assets_dir = output_dir / "assets"
    _prepare_workspace(output_dir, assets_dir)

    manifest = _build_manifest(
        source_path=source_path,
        source_id=resolved_source_id,
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return SourceMaterialBundle(
        source_id=resolved_source_id,
        source_path=source_path,
        output_dir=output_dir,
        manifest_path=manifest_path,
        assets_dir=assets_dir,
        manifest=manifest,
    )


def parse_source_materials(
    source_dir: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
) -> SourceMaterialBatchResult:
    source_paths = discover_source_materials(source_dir, output_root=output_root)
    used_source_ids: set[str] = set()
    bundles = [
        parse_source_material(
            source_path,
            output_root=output_root,
            source_id=_unique_source_id(source_path, source_dir, used_source_ids),
        )
        for source_path in source_paths
    ]
    return SourceMaterialBatchResult(
        source_dir=source_dir,
        output_root=output_root,
        source_paths=source_paths,
        bundles=bundles,
    )


def discover_source_materials(
    source_dir: Path,
    *,
    output_root: Path = DEFAULT_PARSED_SOURCE_ROOT,
) -> list[Path]:
    source_dir = source_dir.expanduser()
    if not source_dir.is_dir():
        raise SourceMaterialParseError(f"Source material directory does not exist: {source_dir}")

    resolved_output_root = output_root.expanduser().resolve()
    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_SOURCE_EXTENSIONS
        and not _is_relative_to(path.resolve(), resolved_output_root)
    )


def source_id_for(source_path: Path) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", source_path.stem.strip()).strip("-._")
    return cleaned.lower() or "source-material"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_workspace(output_dir: Path, assets_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in REMOVED_GENERATED_FILES:
        path = output_dir / filename
        if path.is_file():
            path.unlink()

    assets_dir.mkdir(parents=True, exist_ok=True)
    for folder_name in REMOVED_ASSET_SUBFOLDERS:
        path = assets_dir / folder_name
        if path.is_dir():
            shutil.rmtree(path)


def _build_manifest(*, source_path: Path, source_id: str) -> dict[str, Any]:
    suffix = source_path.suffix.lower()
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "id": source_id,
            "path": source_path.as_posix(),
            "filename": source_path.name,
            "extension": suffix,
            "mime_type": mimetypes.guess_type(source_path.name)[0] or "",
            "sha256": sha256_file(source_path),
            "byte_size": source_path.stat().st_size,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        },
        "parser": {
            "status": "not_run",
            "name": "",
            "reason": (
                "Custom PyMuPDF extraction was removed because its output was not useful. "
                "This workspace is ready for a Docling/Marker/Unstructured parser adapter."
            ),
        },
        "files": {
            "assets": "assets/",
        },
    }


def _unique_source_id(source_path: Path, source_dir: Path, used_source_ids: set[str]) -> str:
    source_id = source_id_for(source_path)
    if source_id not in used_source_ids:
        used_source_ids.add(source_id)
        return source_id

    try:
        relative = source_path.relative_to(source_dir)
    except ValueError:
        relative = Path(source_path.name)
    source_id = source_id_for(Path("-".join(relative.with_suffix("").parts)))
    if source_id not in used_source_ids:
        used_source_ids.add(source_id)
        return source_id

    digest = hashlib.sha256(source_path.as_posix().encode("utf-8")).hexdigest()[:8]
    source_id = f"{source_id}-{digest}"
    used_source_ids.add(source_id)
    return source_id


def _is_relative_to(path: Path, possible_parent: Path) -> bool:
    try:
        path.relative_to(possible_parent)
    except ValueError:
        return False
    return True
