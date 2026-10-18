"""Explicit two-host coordination; no retry queue or distributed rollback."""

from __future__ import annotations

from collections.abc import Callable

from planner.environments.resident.contracts import (
    HostDeploymentAdapter,
    HostDeploymentResult,
    HostName,
    ResidentError,
    TwoHostDeploymentResult,
)
from planner.environments.resident.logic.deployment import valid_revision, validated_host_result


def deploy_two_hosts(
    requested_revision: str,
    *,
    vps: HostDeploymentAdapter,
    mac: HostDeploymentAdapter,
    record: Callable[[TwoHostDeploymentResult], None],
) -> TwoHostDeploymentResult:
    """Invoke each host once and persist the exact observed result.

    Recording failure propagates: callers must not claim a recorded deployment.
    A successful host stays installed when its peer fails or is unreachable.
    """
    if not valid_revision(requested_revision):
        raise ResidentError("Deployment requires a complete lowercase Git revision")

    def invoke(host: HostName, adapter: HostDeploymentAdapter) -> HostDeploymentResult:
        try:
            result = adapter.deploy(requested_revision)
        except Exception as exc:
            return HostDeploymentResult(host, requested_revision, "failed", None, str(exc))
        return validated_host_result(host, requested_revision, result)

    vps_result = invoke("vps", vps)
    mac_result = invoke("mac", mac)
    result = TwoHostDeploymentResult(
        requested_revision,
        vps_result,
        mac_result,
        vps_result.disposition == "verified" and mac_result.disposition == "verified",
    )
    record(result)
    return result
