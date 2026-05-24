from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TypeVar

ArchiveErrorT = TypeVar("ArchiveErrorT", bound=Exception)
NaturalSortToken = tuple[int, int | str]

NATURAL_SORT_TOKEN_PATTERN = re.compile(r"(\d+)")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_first_seen_timestamp(path: Path) -> datetime:
    stat = path.stat()
    timestamp = getattr(stat, "st_birthtime", None)
    if timestamp is None:
        timestamp = min(stat.st_ctime, stat.st_mtime)
    return datetime.fromtimestamp(float(timestamp)).astimezone()


def safe_ascii_slug(value: object, *, fallback: str, max_chars: int) -> str:
    text = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return fallback
    if len(slug) <= max_chars:
        return slug
    shortened = slug[:max_chars].rstrip("-")
    return shortened or fallback


def natural_sort_tokens(text: str) -> tuple[NaturalSortToken, ...]:
    tokens: list[NaturalSortToken] = []
    for token in NATURAL_SORT_TOKEN_PATTERN.split(text.lower()):
        if not token:
            continue
        if token.isdigit():
            tokens.append((1, int(token)))
            continue
        normalized = re.sub(r"[\s_.-]+", " ", token).strip()
        if normalized:
            tokens.append((0, normalized))
    return tuple(tokens)


def natural_filename_sort_key(path: Path) -> tuple[NaturalSortToken, ...]:
    return (*natural_sort_tokens(path.stem), (0, path.suffix.lower()))


def natural_path_parts_sort_key(path: Path) -> tuple[NaturalSortToken, ...]:
    tokens: list[NaturalSortToken] = []
    for part in path.parts:
        tokens.extend(natural_sort_tokens(part))
    return tuple(tokens)


def ensure_inside_root(
    root: Path,
    path: Path,
    *,
    allow_equal: bool,
    error_type: type[ArchiveErrorT],
    label: str,
) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if allow_equal and resolved_path == resolved_root:
        return
    if resolved_root not in resolved_path.parents:
        raise error_type(f"Refusing to write outside {label}: {path}")


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def duplicate_resolution_timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S%z") or value.isoformat(timespec="seconds").replace(":", "")


def connected_path_components(
    path_groups: Iterable[Sequence[Path]],
    *,
    sort_key: Callable[[Path], object] | None = None,
) -> tuple[tuple[Path, ...], ...]:
    parent: dict[Path, Path] = {}

    def find(path: Path) -> Path:
        parent.setdefault(path, path)
        if parent[path] != path:
            parent[path] = find(parent[path])
        return parent[path]

    def union(left: Path, right: Path) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if sort_key is not None and sort_key(right_root) < sort_key(left_root):
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root

    for paths in path_groups:
        if not paths:
            continue
        first = paths[0]
        parent.setdefault(first, first)
        for path in paths[1:]:
            parent.setdefault(path, path)
            union(first, path)

    components: dict[Path, list[Path]] = {}
    for path in parent:
        components.setdefault(find(path), []).append(path)

    grouped = []
    for paths in components.values():
        if len(paths) < 2:
            continue
        grouped.append(tuple(sorted(paths, key=sort_key) if sort_key is not None else paths))
    if sort_key is not None:
        return tuple(sorted(grouped, key=lambda paths: sort_key(paths[0])))
    return tuple(grouped)
