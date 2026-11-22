"""Protocols shared across layers.

This module must not import project code: ``contracts`` is the bottom of the
dependency graph, so every layer can depend on it without creating cycles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, TypedDict


class ToolSpec(TypedDict):
    args: dict[str, str]
    doc: str
    label: str
    phase: str
    mutating: bool
    executes_code: bool
    handler: str


class Policy(Protocol):
    def next_action(self, history: list[dict]) -> Any:
        ...


class Environment(Protocol):
    """Primitives a tool handler may rely on. Path safety is the env's job."""

    def is_readable(self, path: str) -> bool:
        ...

    def is_editable(self, path: str) -> bool:
        ...

    def read_text(self, path: str) -> str:
        ...

    def write_text(self, path: str, content: str) -> None:
        ...

    def read_bytes(self, path: str) -> bytes:
        ...

    def write_bytes(self, path: str, data: bytes) -> None:
        ...

    def exists(self, path: str) -> bool:
        ...

    def delete(self, path: str) -> None:
        ...

    def list_files(self, subdir: str = ".") -> list[str]:
        ...

    def run(self, command: list[str], *, cwd: str = ".", timeout: float,
            env_extra: dict | None = None) -> dict:
        ...

    def manifest(self) -> dict[str, str]:
        ...

    def export_patch(self, baseline: Path, destination: Path) -> None:
        ...
