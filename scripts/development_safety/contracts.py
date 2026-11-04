"""Shared refusal contract for the dependency scanner and guarded launchers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Diagnostic:
    classification: Literal[
        "shared deployment storage detected", "cannot establish dependency safety"
    ]
    examined: Path
    resolved: Path | None
    protected: Path | None
    signal: str
    detail: str


class SafetyRefusal(RuntimeError):
    """A command cannot start because its dependency safety is not established."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.classification}\n"
            f"Examined path: {diagnostic.examined}\n"
            f"Resolved path: {diagnostic.resolved or 'unavailable'}\n"
            f"Protected path: {diagnostic.protected or 'unavailable'}\n"
            f"Signal: {diagnostic.signal}\n{diagnostic.detail}\n"
            "If sharing is real, use a fresh worktree with scripts/dev setup. "
            "Preserve suspect dependencies until their links and mounts "
            "receive deliberate repair.\n"
            "If this is a detector error, preserve this diagnostic, fix the detector, "
            "and add a regression check. Restore missing evidence before retry. "
            "No bypass is supported."
        )
