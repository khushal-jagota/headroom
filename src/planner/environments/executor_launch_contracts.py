"""Explicit VPS executor launch settings, independent of Mac installation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class ExecutorLaunchConfigurationError(RuntimeError):
    """An explicit executor launch configuration is invalid or inaccessible."""


@dataclass(frozen=True)
class VpsExecutorLaunchConfiguration:
    mode: Literal["local", "remote"]
    socket_path: Path | None = None
    version: int = 1
