from dataclasses import replace

import pytest

from planner.environments.resident.contracts import (
    HostDeploymentResult,
    ResidentError,
    TwoHostDeploymentResult,
)
from planner.environments.resident.deployment import deploy_two_hosts

REVISION = "a" * 40


class Adapter:
    def __init__(self, result: HostDeploymentResult | Exception) -> None:
        self.result = result
        self.requests: list[str] = []

    def deploy(self, requested_revision: str) -> HostDeploymentResult:
        self.requests.append(requested_revision)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_both_exact_hosts_verified_and_result_recorded() -> None:
    vps = Adapter(HostDeploymentResult("vps", REVISION, "verified", REVISION, "ready"))
    mac = Adapter(HostDeploymentResult("mac", REVISION, "verified", REVISION, "ready"))
    recorded: list[TwoHostDeploymentResult] = []
    result = deploy_two_hosts(REVISION, vps=vps, mac=mac, record=recorded.append)
    assert result.verified
    assert recorded == [result]
    assert vps.requests == mac.requests == [REVISION]


def test_unreachable_mac_retains_old_observation_and_never_queues() -> None:
    vps = Adapter(HostDeploymentResult("vps", REVISION, "verified", REVISION, "ready"))
    mac = Adapter(HostDeploymentResult("mac", REVISION, "unreachable", "b" * 40, "retained"))
    result = deploy_two_hosts(REVISION, vps=vps, mac=mac, record=lambda result: None)
    assert not result.verified
    assert result.vps.disposition == "verified"
    assert result.mac.observed_revision == "b" * 40
    assert mac.requests == [REVISION]


@pytest.mark.parametrize("failure", ["wrong_host", "wrong_request", "wrong_observation"])
def test_misbound_host_result_fails_closed(failure: str) -> None:
    good = HostDeploymentResult("mac", REVISION, "verified", REVISION, "ready")
    if failure == "wrong_host":
        wrong = replace(good, host="vps")
    elif failure == "wrong_request":
        wrong = replace(good, requested_revision="b" * 40)
    else:
        wrong = replace(good, observed_revision="b" * 40)
    result = deploy_two_hosts(
        REVISION,
        vps=Adapter(replace(good, host="vps")),
        mac=Adapter(wrong),
        record=lambda result: None,
    )
    assert not result.verified
    assert result.mac.disposition == "failed"


def test_exception_does_not_undo_successful_peer() -> None:
    mac = Adapter(HostDeploymentResult("mac", REVISION, "verified", REVISION, "ready"))
    result = deploy_two_hosts(
        REVISION,
        vps=Adapter(TimeoutError("transport deadline")),
        mac=mac,
        record=lambda result: None,
    )
    assert result.vps.disposition == "failed" and not result.verified
    assert "deadline" in result.vps.detail
    assert result.mac.disposition == "verified" and mac.requests == [REVISION]


def test_record_failure_is_not_success() -> None:
    def fail(result: TwoHostDeploymentResult) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        deploy_two_hosts(
            REVISION,
            vps=Adapter(HostDeploymentResult("vps", REVISION, "verified", REVISION, "")),
            mac=Adapter(HostDeploymentResult("mac", REVISION, "verified", REVISION, "")),
            record=fail,
        )


@pytest.mark.parametrize("revision", ["main", "abc123", "A" * 40, "a" * 39])
def test_exact_revision_required_before_either_host_runs(revision: str) -> None:
    adapter = Adapter(RuntimeError("must not run"))
    with pytest.raises(ResidentError, match="complete lowercase"):
        deploy_two_hosts(revision, vps=adapter, mac=adapter, record=lambda result: None)
    assert not adapter.requests
