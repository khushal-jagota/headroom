"""A reachable host alone never establishes an exact-revision deployment."""

import re

from planner.environments.resident.contracts import HostDeploymentResult, HostName


def validated_host_result(
    host: HostName, requested_revision: str, result: HostDeploymentResult
) -> HostDeploymentResult:
    if result.host != host or result.requested_revision != requested_revision:
        return HostDeploymentResult(
            host, requested_revision, "failed", None, "Host or requested revision mismatch"
        )
    if result.disposition == "verified" and result.observed_revision != requested_revision:
        return HostDeploymentResult(
            host,
            requested_revision,
            "failed",
            result.observed_revision,
            "Requested revision was not observed",
        )
    if result.disposition not in {"verified", "unreachable", "deferred", "failed"}:
        return HostDeploymentResult(
            host,
            requested_revision,
            "failed",
            result.observed_revision,
            "Unknown host disposition",
        )
    return result


def valid_revision(revision: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", revision) is not None
