"""Inputs for the Mac executor LaunchAgent; no installation lifecycle."""

from dataclasses import dataclass
from pathlib import Path


class MacExecutorConfigurationError(RuntimeError):
    """The explicit LaunchAgent inputs are invalid."""


@dataclass(frozen=True)
class MacExecutorPaths:
    home: Path
    source: Path
    venv: Path
    executor_root: Path


@dataclass(frozen=True)
class ExecutorLaunchOptions:
    executor_id: str
    application_revision: str
    panels_server_url: str
    backend_path: str | None = None


@dataclass(frozen=True)
class ServiceConfiguration:
    paths: MacExecutorPaths
    user_id: int
    launch: ExecutorLaunchOptions
    label: str = "com.panels.executor"
