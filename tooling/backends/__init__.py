"""Backend registry: the layer that decides *where* actions actually run.

A backend owns a workspace, its state and its budget. A toolset only decides
which of the backend's capabilities are exposed to the model.
"""

from typing import Protocol


class Backend(Protocol):
    def dispatch(self, name: str, args: dict) -> dict:
        ...


def build_backend(kind, **kwargs):
    """Create a backend by name. Keep this list short and explicit."""
    if kind == "toy":
        from tooling.backends.toy import Tools
        return Tools(**kwargs)
    if kind == "repo":
        from tooling.backends.repo import ToolRunner
        return ToolRunner(**kwargs)
    raise ValueError(f"Unknown backend: {kind}")
