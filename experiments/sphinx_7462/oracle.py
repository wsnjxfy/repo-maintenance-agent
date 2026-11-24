"""Deterministic reference-patch policy for Sphinx 7462 calibration."""

from pathlib import Path

from agent_core.agent import Action


class ReferencePatchPolicy:
    def __init__(self, patch_path):
        self.patch = Path(patch_path).read_text(encoding="utf-8")

    def next_action(self, history):
        if not history:
            return Action("apply_patch", {"patch": self.patch})
        return Action("finish", {"reason": "reference patch applied"})
