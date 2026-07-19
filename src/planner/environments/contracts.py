"""Framework-free contracts for isolated Panels runtime environments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

EnvironmentKind = Literal["live", "staging", "preview"]
ExpectedLinuxAccount = Literal["panels-live", "panels-worker"]
MIN_TCP_PORT: Final = 1
MAX_TCP_PORT: Final = 65535


class EnvironmentValidationError(ValueError):
    """Raised when an environment contract would be unsafe or ambiguous."""


def validate_tcp_port(port: int, *, label: str = "port") -> int:
    if port < MIN_TCP_PORT or port > MAX_TCP_PORT:
        raise EnvironmentValidationError(
            f"{label} must be between {MIN_TCP_PORT} and {MAX_TCP_PORT}"
        )
    return port


@dataclass(frozen=True)
class EnvironmentPortRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        validate_tcp_port(self.start, label="preview port range start")
        validate_tcp_port(self.end, label="preview port range end")
        if self.start > self.end:
            raise EnvironmentValidationError("preview port range start must be before end")


@dataclass(frozen=True)
class EnvironmentDefaults:
    live_port: int = 8767
    staging_port: int = 8768
    preview_ports: EnvironmentPortRange = field(
        default_factory=lambda: EnvironmentPortRange(9000, 9999)
    )

    def __post_init__(self) -> None:
        validate_tcp_port(self.live_port, label="live default port")
        validate_tcp_port(self.staging_port, label="staging default port")
        if self.live_port == self.staging_port:
            raise EnvironmentValidationError(
                "default live and staging ports must be different"
            )
        if self.preview_ports.start <= self.live_port <= self.preview_ports.end:
            raise EnvironmentValidationError(
                "default live port must not overlap preview port range"
            )
        if self.preview_ports.start <= self.staging_port <= self.preview_ports.end:
            raise EnvironmentValidationError(
                "default staging port must not overlap preview port range"
            )


@dataclass(frozen=True)
class EnvironmentCredentialPolicy:
    allowed_keys: frozenset[str] = frozenset()
    production_only_keys: frozenset[str] = frozenset()


DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY: Final = EnvironmentCredentialPolicy(
    allowed_keys=frozenset(
        {
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
        }
    ),
)


@dataclass(frozen=True)
class ResolvedEnvironmentInstance:
    kind: EnvironmentKind
    instance_id: str
    environment_root: Path
    instance_root: Path
    db_path: Path
    managed_files_root: Path
    hermes_home: Path
    logs_dir: Path
    dispatcher_lock_path: Path
    server_control_socket_path: Path
    port: int
    credentials_env_file: Path | None
    allowed_repository_roots: tuple[Path, ...]
    expected_linux_account: ExpectedLinuxAccount
    fixture_version: str | None
    prepared: bool
    running: bool


@dataclass(frozen=True)
class EnvironmentManifest:
    kind: EnvironmentKind
    instance_id: str
    environment_root: Path
    instance_root: Path
    db_path: Path
    managed_files_root: Path
    hermes_home: Path
    logs_dir: Path
    dispatcher_lock_path: Path
    server_control_socket_path: Path
    port: int
    credentials_env_file: Path | None
    expected_linux_account: ExpectedLinuxAccount
    fixture_version: str | None
    prepared_at: int | None
    repository_roots: tuple[Path, ...]
