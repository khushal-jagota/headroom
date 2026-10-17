from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.resident.contracts import (
    Compatibility,
    ExecutorSourceClosure,
    PreparedResidentPackage,
    ResidentError,
    ResidentPackage,
    ResidentPaths,
)
from planner.environments.resident.logic.update import fresh_health, safe_quiescence
from planner.environments.resident.update import _update_lock, update_resident
from planner.executor.contracts import (
    ConnectionState,
    ExecutorHealth,
    ExecutorIdentity,
    Quiescence,
    QuiescenceReceipt,
    QuiesceRequest,
)

OLD = "a" * 40
NEW = "b" * 40
READY = Quiescence(True, 0, 0, 0, 0, True)


def health(revision: str, incarnation: str, *, ready: bool = True) -> ExecutorHealth:
    return ExecutorHealth(
        ExecutorIdentity(1, revision, 1, incarnation, 1),
        123,
        ConnectionState.ready if ready else ConnectionState.reconciling,
        ready,
        100,
        None,
    )


class Control:
    def __init__(
        self, observations: list[ExecutorHealth | None], quiescences: list[Quiescence] | None = None
    ) -> None:
        self.observations = observations
        self.quiescences = quiescences or [READY]
        self.quiesced: list[ExecutorIdentity] = []
        self.compatibility: Compatibility = "compatible"

    def observe(self) -> ExecutorHealth | None:
        return self.observations.pop(0)

    def quiesce(self, expected: QuiesceRequest) -> QuiescenceReceipt:
        self.quiesced.append(expected.expected_identity)
        return QuiescenceReceipt(
            expected.expected_identity, expected.expected_process_id, self.quiescences.pop(0), True
        )

    def compatible(self, package: ResidentPackage) -> Compatibility:
        return self.compatibility


class Service:
    def __init__(self) -> None:
        self.stopped: list[QuiescenceReceipt] = []
        self.started: list[PreparedResidentPackage] = []

    def stop(self, expected: QuiescenceReceipt) -> None:
        self.stopped.append(expected)

    def start(self, package: PreparedResidentPackage) -> None:
        self.started.append(package)


@pytest.fixture
def packages(
    tmp_path: Path,
) -> tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage]:
    paths = ResidentPaths(tmp_path / "Home User", tmp_path / "deployment", tmp_path / "data")
    paths.app.mkdir(parents=True)
    paths.executor_root.mkdir()
    (paths.executor_root / "pending-ask").write_text("retain")
    (paths.app / "source").write_text("old")
    candidate_path = paths.app.parent / "staged"
    candidate_path.mkdir()
    (candidate_path / "source").write_text("new")
    closure = ExecutorSourceClosure(("main.py",), "main", "requirements.txt")
    old = ResidentPackage(OLD, 1, 1, "Darwin", "arm64", "3.12", closure)
    previous = PreparedResidentPackage(old, paths.app, paths.runtimes / OLD)
    candidate = PreparedResidentPackage(
        replace(old, application_revision=NEW), candidate_path, paths.runtimes / NEW
    )
    return paths, candidate, previous


def validate(package: PreparedResidentPackage) -> None:
    if not (package.app / "source").is_file():
        raise ResidentError("missing source")


def test_exact_update_preserves_spool_and_native_runtime(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    paths, candidate, previous = packages
    control = Control([health(OLD, "old"), health(NEW, "new")])
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=control,
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "verified"
    assert result.observed_revision == NEW
    assert service.stopped == [QuiescenceReceipt(health(OLD, "old").identity, 123, READY, True)]
    assert service.started[0].runtime == candidate.runtime
    assert service.started[0].app == paths.app
    assert (paths.app / "source").read_text() == "new"
    assert (paths.executor_root / "pending-ask").read_text() == "retain"
    assert next(paths.app.parent.glob("previous-*/source")).read_text() == "old"


@pytest.mark.parametrize(
    "field",
    [
        "active_turn_count",
        "pending_callback_count",
        "uncertain_operation_count",
        "unsent_entry_count",
        "admission_stopped",
        "idle_children_stopped",
    ],
)
def test_owned_work_refuses_cutover(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
    field: str,
) -> None:
    paths, candidate, previous = packages
    change: int | bool = False if field in {"admission_stopped", "idle_children_stopped"} else 1
    control = Control(
        [health(OLD, "old")],
        [
            Quiescence(
                bool(change) if field == "admission_stopped" else True,
                int(change) if field == "active_turn_count" else 0,
                int(change) if field == "pending_callback_count" else 0,
                int(change) if field == "uncertain_operation_count" else 0,
                int(change) if field == "unsent_entry_count" else 0,
                bool(change) if field == "idle_children_stopped" else True,
            )
        ],
    )
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=control,
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "deferred"
    assert not service.stopped
    assert (paths.app / "source").read_text() == "old"


@pytest.mark.parametrize("count", [-1, 1])
def test_only_explicit_zero_is_safe(count: int) -> None:
    assert not safe_quiescence(replace(READY, pending_callback_count=count))


def test_unhealthy_candidate_owns_work_and_must_not_be_killed(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    paths, candidate, previous = packages
    bad = health(NEW, "new", ready=False)
    control = Control([health(OLD, "old"), bad, bad], [READY, replace(READY, active_turn_count=1)])
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=control,
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "failed" and not result.rolled_back
    assert "candidate owns work" in result.detail
    assert len(service.stopped) == 1
    assert (paths.app / "source").read_text() == "new"
    assert next(paths.app.parent.glob("previous-*/source")).read_text() == "old"


def test_rollback_requires_fresh_candidate_quiescence_and_new_restored_incarnation(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    paths, candidate, previous = packages
    bad = health(NEW, "new", ready=False)
    control = Control([health(OLD, "old"), bad, bad, health(OLD, "restored")], [READY, READY])
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=control,
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.rolled_back and result.disposition == "failed"
    assert result.observed_revision == OLD
    assert [value.executor_incarnation for value in control.quiesced] == ["old", "new"]
    assert len(service.stopped) == 2
    assert (paths.app / "source").read_text() == "old"
    assert next(paths.app.parent.glob("failed-*/source")).read_text() == "new"


def test_lost_candidate_health_refuses_rollback(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    paths, candidate, previous = packages
    control = Control([health(OLD, "old"), None, None])
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=control,
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "failed" and result.observed_revision is None
    assert "rollback refused" in result.detail
    assert len(service.stopped) == 1


def test_unknown_rollback_compatibility_refuses_before_mutation(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    paths, candidate, previous = packages
    control = Control([])
    control.compatibility = "unknown"
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=control,
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "deferred" and not service.stopped


def test_update_lock_refuses_concurrent_mutation(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    paths, candidate, previous = packages
    with _update_lock(paths.deployment_root):
        result = update_resident(
            paths,
            candidate,
            previous,
            control=Control([]),
            service=Service(),
            validate_package=validate,
            now_ms=lambda: 100,
        )
    assert result.disposition == "failed" and "lock" in result.detail


@pytest.mark.parametrize(
    "mutation",
    ["stale", "future", "lost", "old_incarnation", "wrong_sha", "wrong_protocol", "wrong_spool"],
)
def test_health_requires_exact_fresh_reconciled_identity(mutation: str) -> None:
    value = health(NEW, "new")
    if mutation == "stale":
        value = replace(value, observed_at_ms=99)
    elif mutation == "future":
        value = replace(value, observed_at_ms=101)
    elif mutation == "lost":
        value = replace(value, lost_at_ms=100)
    else:
        identity = value.identity
        if mutation == "old_incarnation":
            identity = replace(identity, executor_incarnation="old")
        elif mutation == "wrong_sha":
            identity = replace(identity, application_revision=OLD)
        elif mutation == "wrong_protocol":
            identity = replace(identity, protocol_version=2)
        else:
            identity = replace(identity, spool_format_version=2)
        value = replace(value, identity=identity)
    package = ResidentPackage(
        NEW, 1, 1, "Darwin", "arm64", "3.12", ExecutorSourceClosure((), "main", "requirements.txt")
    )
    assert not fresh_health(
        value, package, not_before_ms=100, now_ms=100, previous_incarnation="old"
    )


@pytest.mark.parametrize("mismatch", ["identity", "pid", "exit_refused"])
def test_mismatched_exit_receipt_prevents_stop_and_source_mutation(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
    mismatch: str,
) -> None:
    class MismatchedControl(Control):
        def quiesce(self, expected: QuiesceRequest) -> QuiescenceReceipt:
            receipt = super().quiesce(expected)
            if mismatch == "identity":
                return replace(receipt, identity=replace(receipt.identity, connection_generation=2))
            if mismatch == "pid":
                return replace(receipt, process_id=456)
            return replace(receipt, exit_committed=False)

    paths, candidate, previous = packages
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=MismatchedControl([health(OLD, "old")]),
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "deferred"
    assert not service.stopped and not service.started
    assert (paths.app / "source").read_text() == "old"


def test_identity_bound_stop_refusal_does_not_mutate_source(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
) -> None:
    class RefusingService(Service):
        def stop(self, expected: QuiescenceReceipt) -> None:
            raise ResidentError("replacement process found")

    paths, candidate, previous = packages
    result = update_resident(
        paths,
        candidate,
        previous,
        control=Control([health(OLD, "old")]),
        service=RefusingService(),
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "failed" and "replacement process" in result.detail
    assert (paths.app / "source").read_text() == "old"
    assert (candidate.app / "source").read_text() == "new"


@pytest.mark.parametrize("link", ["ancestor", "lock", "runtime", "data"])
def test_symlink_paths_refused_before_service_stop(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
    link: str,
) -> None:
    paths, candidate, previous = packages
    if link == "ancestor":
        alternate = paths.deployment_root.parent / "redirect"
        alternate.symlink_to(paths.deployment_root, target_is_directory=True)
        paths = replace(paths, deployment_root=alternate)
        previous = replace(previous, app=paths.app)
    elif link == "lock":
        (paths.deployment_root / "update.lock").symlink_to(paths.executor_root / "pending-ask")
    elif link == "runtime":
        paths.runtimes.mkdir()
        candidate.runtime.symlink_to(paths.executor_root, target_is_directory=True)
    else:
        alternate = paths.executor_root.parent / "data-link"
        alternate.symlink_to(paths.executor_root, target_is_directory=True)
        paths = replace(paths, executor_root=alternate)
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=Control([]),
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.disposition == "failed" and not service.stopped
    assert (paths.executor_root / "pending-ask").read_text() == "retain"


def test_partial_candidate_rename_failure_restores_previous_source(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.environments.resident import update

    paths, candidate, previous = packages
    real_rename = update._rename

    def fail_candidate(source: Path, target: Path) -> None:
        if source == candidate.app:
            raise OSError("candidate rename failed")
        real_rename(source, target)

    monkeypatch.setattr(update, "_rename", fail_candidate)
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=Control([health(OLD, "old"), health(OLD, "restored")]),
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert result.rolled_back
    assert (paths.app / "source").read_text() == "old"
    assert (candidate.app / "source").read_text() == "new"


def test_failure_after_candidate_rename_still_requires_own_quiescence(
    packages: tuple[ResidentPaths, PreparedResidentPackage, PreparedResidentPackage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.environments.resident import update

    paths, candidate, previous = packages
    real_rename = update._rename

    def fail_after_rename(source: Path, target: Path) -> None:
        real_rename(source, target)
        if source == candidate.app:
            raise OSError("directory fsync failed")

    monkeypatch.setattr(update, "_rename", fail_after_rename)
    service = Service()
    result = update_resident(
        paths,
        candidate,
        previous,
        control=Control([health(OLD, "old"), None]),
        service=service,
        validate_package=validate,
        now_ms=lambda: 100,
    )
    assert not result.rolled_back
    assert "rollback refused" in result.detail
    assert not service.started
    assert (paths.app / "source").read_text() == "new"
    assert next(paths.app.parent.glob("previous-*/source")).read_text() == "old"
