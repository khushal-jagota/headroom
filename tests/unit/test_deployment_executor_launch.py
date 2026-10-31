from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_deployment import _app
from tests.unit.test_deployment import synthetic_runtime_admission as synthetic_runtime_admission

from planner.environments.deployment import DeploymentResult, deploy_app
from planner.environments.executor_launch_contracts import (
    ExecutorLaunchConfigurationError,
    VpsExecutorLaunchConfiguration,
)
from planner.environments.vps_launch import read_vps_launch, write_vps_launch

SHA_A = "a" * 40
SHA_B = "b" * 40
REMOTE = VpsExecutorLaunchConfiguration("remote", "owner-mac")
LOCAL = VpsExecutorLaunchConfiguration("local")


class Service:
    def __init__(self, path: Path, *, fail_candidate: bool = False) -> None:
        self.path = path
        self.seen: list[VpsExecutorLaunchConfiguration | None] = []
        self.stops: list[VpsExecutorLaunchConfiguration | None] = []
        self.fail_candidate = fail_candidate

    def stop(self) -> None:
        self.stops.append(read_vps_launch(self.path))

    def restart(self) -> None:
        self.seen.append(read_vps_launch(self.path))
        if self.fail_candidate and len(self.seen) == 1:
            raise OSError("candidate start refused")


class Health:
    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        return True


def deploy(
    tmp_path: Path,
    *,
    prior: VpsExecutorLaunchConfiguration | None = None,
    desired: VpsExecutorLaunchConfiguration | None = REMOTE,
    same_sha: bool = False,
    first: bool = False,
    fail_candidate: bool = False,
) -> tuple[DeploymentResult, Service]:
    current = tmp_path / "current"
    path = current / "data" / "executor-launch.json"
    if not first:
        _app(current / "app", SHA_A, "old")
        path.parent.mkdir(parents=True, mode=0o700)
        (path.parent / "planning.db").write_text("database")
        if prior is not None:
            write_vps_launch(path, prior)
    candidate = _app(tmp_path / "candidate", SHA_A if same_sha else SHA_B, "new")
    service = Service(path, fail_candidate=fail_candidate)

    def backup(_: str) -> Path:
        assert service.stops == [prior]
        assert read_vps_launch(path) == prior
        return tmp_path / "snapshot"

    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=path.parent / "planning.db",
        backup=backup,
        restore=lambda _: None,
        service=service,
        health=Health(),
        executor_launch_configuration=desired,
    )
    return result, service


@pytest.mark.parametrize("same_sha", [False, True])
@pytest.mark.parametrize("prior,desired", [(None, REMOTE), (REMOTE, LOCAL), (LOCAL, REMOTE)])
def test_explicit_mode_is_applied_only_after_stop(
    tmp_path: Path,
    same_sha: bool,
    prior: VpsExecutorLaunchConfiguration | None,
    desired: VpsExecutorLaunchConfiguration,
) -> None:
    result, service = deploy(tmp_path, prior=prior, desired=desired, same_sha=same_sha)
    assert result.status == "succeeded"
    assert service.stops == [prior]
    assert service.seen == [desired]


@pytest.mark.parametrize("prior", [None, LOCAL, REMOTE])
def test_candidate_failure_restores_config_before_fallback(
    tmp_path: Path,
    prior: VpsExecutorLaunchConfiguration | None,
) -> None:
    desired = LOCAL if prior == REMOTE else REMOTE
    result, service = deploy(tmp_path, prior=prior, desired=desired, fail_candidate=True)
    assert result.status == "rolled_back"
    assert service.stops == [prior, desired]
    assert service.seen == [desired, prior]
    assert read_vps_launch(service.path) == prior


@pytest.mark.parametrize("first_failure", [False, True])
def test_first_install_applies_config_and_restores_absence_on_failure(
    tmp_path: Path,
    first_failure: bool,
) -> None:
    result, service = deploy(tmp_path, first=True, fail_candidate=first_failure)
    assert result.status == ("initial_failed" if first_failure else "succeeded")
    assert service.seen == [REMOTE]
    assert read_vps_launch(service.path) == (None if first_failure else REMOTE)
    assert service.stops == ([REMOTE] if first_failure else [])


@pytest.mark.parametrize("unsafe", ["malformed", "public", "symlink"])
def test_invalid_prior_refuses_before_service_disruption(tmp_path: Path, unsafe: str) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    path = current / "data" / "executor-launch.json"
    write_vps_launch(path, REMOTE)
    if unsafe == "malformed":
        path.write_text("{}")
    elif unsafe == "public":
        path.chmod(0o644)
    else:
        path.unlink()
        path.symlink_to(tmp_path / "missing")
    service = Service(path)
    with pytest.raises(ExecutorLaunchConfigurationError):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=path.parent / "planning.db",
            backup=lambda _: tmp_path,
            restore=lambda _: None,
            service=service,
            health=Health(),
            executor_launch_configuration=LOCAL,
        )
    assert service.stops == []
    assert service.seen == []


def test_unsafe_desired_target_directory_refuses_before_stop(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    path = current / "data" / "executor-launch.json"
    path.parent.mkdir()
    path.parent.chmod(0o770)
    service = Service(path)
    with pytest.raises(ExecutorLaunchConfigurationError, match="owner controlled"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=path.parent / "planning.db",
            backup=lambda _: tmp_path,
            restore=lambda _: None,
            service=service,
            health=Health(),
            executor_launch_configuration=REMOTE,
        )
    assert service.stops == []
    assert service.seen == []
