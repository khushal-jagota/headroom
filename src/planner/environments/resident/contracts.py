"""Resident-owned contracts; these are not executor wire or IPC schemas.

An executor-owned adapter must supply control observations. Until it exists,
there is no production control transport and no safe positive update readiness.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from planner.executor.contracts import ExecutorHealth, QuiescenceReceipt, QuiesceRequest


class ResidentError(RuntimeError):
    """Preparation or deployment could not satisfy its safety contract."""


NativeFailureKind = Literal[
    "nonzero_exit", "timeout", "process_start", "output_limit", "cleanup_deadline"
]
NativeBuildPhase = Literal[
    "python_probe", "node_probe", "python_build", "backend_npm_install", "runtime_verification"
]
NativeBuildOperation = Literal[
    "python_probe",
    "node_probe",
    "create_venv",
    "install_build_tools",
    "download_source",
    "build_source_wheel",
    "download_dependencies",
    "build_application_wheel",
    "install_wheels",
    "check_dependencies",
    "npm_ci",
    "import_entrypoint",
    "installed_files",
]


@dataclass(frozen=True)
class NativeBuildDiagnostic:
    phase: NativeBuildPhase
    operation: NativeBuildOperation
    failure_kind: NativeFailureKind
    exit_status: int | None = None


class NativeCommandFailure(ResidentError):
    """Safe process facts only; never includes captured output or command arguments."""

    def __init__(self, kind: NativeFailureKind, exit_status: int | None = None) -> None:
        super().__init__("native command failed")
        self.kind = kind
        self.exit_status = exit_status


class NativeBuildFailure(ResidentError):
    """A fixed builder operation with safe process failure facts."""

    def __init__(self, diagnostic: NativeBuildDiagnostic) -> None:
        super().__init__("native runtime build failed")
        self.diagnostic = diagnostic


Compatibility = Literal["compatible", "incompatible", "unknown"]
HostName = Literal["vps", "mac"]
HostDisposition = Literal["verified", "unreachable", "deferred", "failed"]


@dataclass(frozen=True)
class ExecutorCompatibilityVersions:
    """Wire and persistent-journal versions remain separate compatibility facts."""

    protocol_version: int
    spool_format_version: int


@dataclass(frozen=True)
class InstalledExecutorCompatibility:
    """Both optional fields are absent only after positive first-install proof.

    Unknown, unreadable, or conflicting installation evidence raises ResidentError.
    """

    host: HostName
    application_revision: str | None
    versions: ExecutorCompatibilityVersions | None


@dataclass(frozen=True)
class ResidentPaths:
    """Code is replaceable; data and native versioned runtimes never move."""

    home: Path
    deployment_root: Path
    executor_root: Path

    @property
    def app(self) -> Path:
        return self.deployment_root / "current" / "app"

    @property
    def runtimes(self) -> Path:
        return self.deployment_root / "runtimes"


@dataclass(frozen=True)
class ExecutorSourceClosure:
    """Standard package inputs from one commit, including source directory roots.

    Setuptools owns Python discovery and package data. These paths select build
    inputs, not a parallel inventory of importable Python files. Dependency locks
    and backend manifests are committed inputs, never existing environments.
    """

    files: tuple[str, ...]
    entrypoint_module: str
    python_requirements: str
    node_package_roots: tuple[str, ...] = ()
    build_requirements: str = "requirements-build.txt"


@dataclass(frozen=True)
class ResidentPackage:
    application_revision: str
    protocol_version: int
    spool_format_version: int
    target_os: str
    target_architecture: str
    python_version: str
    closure: ExecutorSourceClosure


@dataclass(frozen=True)
class PreparedResidentPackage:
    """Source payload plus runtime built at its permanent native-host path."""

    package: ResidentPackage
    app: Path
    runtime: Path
    runtime_digest: str = ""


class ExecutorControl(Protocol):
    """Implemented against the eventual executor-owned local control contract."""

    def compatible(self, package: ResidentPackage) -> Compatibility: ...

    def quiesce(self, request: QuiesceRequest) -> QuiescenceReceipt: ...

    def observe(self) -> ExecutorHealth | None: ...


class ResidentService(Protocol):
    def stop(self, expected: QuiescenceReceipt) -> None:
        """Confirm receipt process exited; refuse ambiguity or any replacement process.

        Never kill or restart another incarnation. No code mutation is safe
        until the original process exit and launch restart inhibition are proven.
        """
        ...

    def start(self, prepared: PreparedResidentPackage) -> None: ...


@dataclass(frozen=True)
class ExecutorLaunchOptions:
    """Explicit transport arguments for the installed executor command."""

    ssh_host: str
    application_revision: str
    panels_server_url: str
    ssh_port: int
    ssh_user: str
    ssh_identity_file: Path
    ssh_known_hosts_file: Path
    remote_command: str = "panels executor relay"
    backend_path: str | None = None


@dataclass(frozen=True)
class ServiceConfiguration:
    paths: ResidentPaths
    user_id: int
    label: str = "com.panels.executor"
    launch: ExecutorLaunchOptions | None = None


@dataclass(frozen=True)
class ResidentUpdateResult:
    requested_revision: str
    disposition: HostDisposition
    observed_revision: str | None
    detail: str
    rolled_back: bool = False


@dataclass(frozen=True)
class HostDeploymentResult:
    host: HostName
    requested_revision: str
    disposition: HostDisposition
    observed_revision: str | None
    detail: str


class HostDeploymentAdapter(Protocol):
    """Only explicit invocation requests deployment; unreachable does not queue it."""

    def deploy(self, requested_revision: str) -> HostDeploymentResult: ...


@dataclass(frozen=True)
class TwoHostDeploymentResult:
    requested_revision: str
    vps: HostDeploymentResult
    mac: HostDeploymentResult
    verified: bool


@dataclass(frozen=True)
class ResidentDeploymentRecord:
    version: int
    deployment_id: str
    result: TwoHostDeploymentResult


@dataclass(frozen=True)
class VpsExecutorLaunchConfiguration:
    """Explicit stable launch settings, applied after the ambient scrub."""

    mode: Literal["local", "remote"]
    socket_path: Path | None = None
    version: int = 1
