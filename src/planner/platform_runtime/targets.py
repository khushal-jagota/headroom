"""Artifact targets independent of released manifest serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RuntimeTarget:
    system: str
    architecture: str
    role: Literal["home", "executor"]


LINUX_HOME = RuntimeTarget("linux", "x86_64", "home")
MAC_EXECUTOR = RuntimeTarget("darwin", "arm64", "executor")


def require_runtime_target(actual: RuntimeTarget, expected: RuntimeTarget) -> None:
    """Refuse an artifact for a different OS, architecture, or installation role."""
    if actual != expected:
        raise ValueError(
            f"runtime target {actual.system}/{actual.architecture}/{actual.role} "
            f"does not match {expected.system}/{expected.architecture}/{expected.role}"
        )
