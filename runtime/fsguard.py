"""Pure filesystem helpers: path safety, manifests, diffs and display."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat


IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv"}
RESERVED_NAMES = ({"con", "prn", "aux", "nul"}
                  | {"com" + str(n) for n in range(1, 10)}
                  | {"lpt" + str(n) for n in range(1, 10)})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_path(root: Path, name: str, allow_dot: bool = False) -> Path:
    if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name):
        raise ValueError("Expected a nonempty relative path")
    name = name.replace("\\", "/")
    win = PureWindowsPath(name)
    if win.drive or win.root or Path(name).is_absolute() or ":" in name:
        raise ValueError("Absolute paths and drive paths are not allowed")
    parts = name.split("/")
    if any(p == ".." for p in parts) or any(p.casefold() in IGNORED_DIRS for p in parts):
        raise ValueError("Path is outside the exposed workspace")
    if name == "." and allow_dot:
        return root
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        raise ValueError("A file or subdirectory path is required")
    if any(p.endswith((".", " ")) or p.split(".")[0].casefold() in RESERVED_NAMES for p in clean):
        raise ValueError("Windows path aliases and reserved device names are not exposed")
    target = root.joinpath(*clean)
    cursor = root
    for component in clean:
        cursor = cursor / component
        if cursor.exists() or cursor.is_symlink():
            if is_link(cursor):
                raise ValueError("Links and reparse points are not exposed")
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path escapes the workspace")
    return resolved


def files(root: Path, initial=None) -> dict[str, str]:
    initial = initial or {}
    found = {}
    for directory, dirs, names in os.walk(str(root), followlinks=False):
        folder = Path(directory)
        for name in list(dirs):
            path = folder / name
            if is_link(path):
                raise ValueError("Workspace contains a link or reparse point: " + str(path.relative_to(root)))
        dirs[:] = sorted(d for d in dirs if d.casefold() not in IGNORED_DIRS)
        for name in sorted(names):
            path = folder / name
            rel = path.relative_to(root).as_posix()
            if is_link(path):
                raise ValueError("Workspace contains a link or reparse point: " + rel)
            if rel not in initial and (name.endswith((".pyc", ".pyo")) or name == ".coverage"):
                continue
            if path.is_file():
                found[rel] = sha256_bytes(path.read_bytes())
    return found


def digest(manifest: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def changes(before: dict, after: dict) -> dict:
    return {p: {"before": before.get(p), "after": after.get(p)}
            for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)}


def display(raw: bytes, limit: int) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n[output truncated; full bytes retained in log]\n" + text[-half:], True
