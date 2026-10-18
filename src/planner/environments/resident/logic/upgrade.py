"""Protocol transitions require an explicit migration outside resident deployment."""

from planner.environments.resident.contracts import (
    ExecutorCompatibilityVersions,
    InstalledExecutorCompatibility,
    ResidentError,
)


def require_supported_upgrade(
    candidate: ExecutorCompatibilityVersions,
    installed: tuple[InstalledExecutorCompatibility, ...],
    supported: ExecutorCompatibilityVersions,
) -> None:
    """Require exact protocol and spool agreement, including deployment tooling."""
    for versions in (candidate, supported, *(host.versions for host in installed)):
        if versions is not None and any(
            type(value) is not int or value <= 0
            for value in (versions.protocol_version, versions.spool_format_version)
        ):
            raise ResidentError("Executor compatibility versions are unknown")
    if candidate != supported:
        raise ResidentError("Requested executor protocol/spool differs from deployment tooling")
    for host in installed:
        if (host.application_revision is None) != (host.versions is None):
            raise ResidentError(f"{host.host} installed executor identity is unknown")
        if host.versions is not None and host.versions != candidate:
            raise ResidentError(f"{host.host} installed executor protocol/spool transition refused")
