"""Inputs for the Mac executor LaunchAgent; no installation lifecycle."""

from dataclasses import dataclass
from pathlib import Path

from planner.executor.contracts import SshAuthenticationMode


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
    ssh_host: str
    application_revision: str
    panels_server_url: str
    ssh_port: int
    ssh_user: str
    ssh_identity_file: Path | None
    ssh_known_hosts_file: Path
    ssh_auth_mode: SshAuthenticationMode
    remote_command: str = "panels executor relay"
    backend_path: str | None = None
    tailscale_binary: Path | None = None


@dataclass(frozen=True)
class ServiceConfiguration:
    paths: MacExecutorPaths
    user_id: int
    launch: ExecutorLaunchOptions
    label: str = "com.panels.executor"
