"""Local proofs for the durable swap, with no Mac or permission operations."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import aiohttp
import pytest
from click.testing import CliRunner
from pydantic import TypeAdapter
from tests.unit.test_executor_native_runtime import ScriptedChild, acknowledge_pending
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.conversation.backends.contracts import (
    BackendStreamEnded,
    BackgroundTaskEnded,
    BackgroundTaskStarted,
    TurnEnded,
)
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import ConversationTurnEnding
from planner.environments.mac_runtime import extract_runtime
from planner.execution_identity import TurnToken
from planner.executor import runtime_bootstrap
from planner.executor.cli import executor_group
from planner.executor.link import Link
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.mac_updates import MacUpdateController, advance_generation, handoff_identity
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    EnvelopeKind,
    StopRun,
    WritePrompt,
)
from planner.executor.runtime_bootstrap import atomic_record, read_record
from planner.executor.update_configuration import (
    MAC_RUNTIME_CHILD_ENV,
    MAC_RUNTIME_HANDOFF_ENV,
    MAC_RUNTIME_READY_ENV,
)
from planner.executor.update_contracts import (
    MacRuntimeManifest,
    MacRuntimeSelection,
    MacUpdateCheck,
    MacUpdatePrefetch,
    MacUpdateReport,
    MacUpdateTarget,
    mac_prefetch_archive_path,
)


def runtime_archive(tmp_path: Path, *, unsafe: bool = False) -> tuple[Path, str, int]:
    archive = tmp_path / "runtime.tar.gz"
    files = {
        "mac-runtime.json": json.dumps(asdict(MacRuntimeManifest(1, "b" * 40, "darwin", "arm64"))),
        "python/bin/python3": "#!/bin/sh\nexit 0\n",
    }
    if unsafe:
        files["../escaped"] = "do not extract"
    with tarfile.open(archive, "w:gz") as packed:
        for name, value in files.items():
            data = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755 if name.endswith("python3") else 0o644
            packed.addfile(member, io.BytesIO(data))
    data = archive.read_bytes()
    return archive, hashlib.sha256(data).hexdigest(), len(data)


def test_executor_run_validates_required_options_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_bootstrap, "supervise", lambda _: pytest.fail("invalid input reached supervisor"),
    )
    result = CliRunner().invoke(executor_group, ["run", "--executor-id=mac"])
    assert result.exit_code == 2
    assert "Missing option" in result.output


@pytest.mark.parametrize("risk", ["digest", "source", "traversal"])
def test_runtime_rejects_unapproved_bytes_source_and_paths(tmp_path: Path, risk: str) -> None:
    archive, digest, size = runtime_archive(tmp_path, unsafe=risk == "traversal")
    with pytest.raises(ValueError):
        extract_runtime(
            archive, tmp_path / "releases",
            expected_sha256="0" * 64 if risk == "digest" else digest,
            expected_size=size, expected_revision="c" * 40 if risk == "source" else "b" * 40,
        )
    assert not (tmp_path / "escaped").exists()
    assert not list((tmp_path / "releases").glob("b*"))


def test_prebuilt_install_never_touches_saved_executor_data(tmp_path: Path) -> None:
    archive, digest, size = runtime_archive(tmp_path)
    spool = tmp_path / "spool"
    spool.mkdir()
    saved = spool / "saved-result"
    saved.write_bytes(b"unsent output")
    runtime = extract_runtime(
        archive, tmp_path / "updates/releases", expected_sha256=digest,
        expected_size=size, expected_revision="b" * 40,
    )
    assert runtime.joinpath("python/bin/python3").is_file()
    assert saved.read_bytes() == b"unsent output"


def test_update_now_stops_before_locked_provider_and_preserves_saved_fifo(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        runner, spool, native, start = native_registry
        await runner.run(start)
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def blocked(*args: object, **kwargs: object) -> object:
            entered.set()
            await stopped.wait()
            raise ConnectionError("provider stopped")

        async def stop() -> None:
            stopped.set()

        monkeypatch.setattr(native, "write_prompt", blocked)
        monkeypatch.setattr(native, "stop", stop)
        command = ChildCommand(
            "blocked_prompt", start.child, ChildOperation.write_prompt,
            TurnToken("c_one", 1), WritePrompt(
                content=(), sender_content=(), sender_label="owner", mode=PromptDeliveryMode.queue,
            ),
        )
        task = asyncio.create_task(runner.run(command))
        await entered.wait()
        assert not await runner.prepare_update("idle")
        assert not runner.admission_stopped
        # This completes only if stop runs before the command's held child lock.
        assert await asyncio.wait_for(runner.prepare_update("now"), timeout=1)
        await task
        assert stopped.is_set()
        pending = spool.pending()
        assert pending and pending[-1].kind is EnvelopeKind.child_stopped
        assert runner.children() == ()
        assert runner.admission_stopped and runner.connection.admission_stopped
        # Maintenance still waits for delivery receipts; runtime swaps do not.
        assert not (await runner.quiesce()).ready
        runner.resume_after_cancelled_update()
        assert not runner.admission_stopped and not runner.connection.admission_stopped

    asyncio.run(exercise())


def test_handoff_keeps_only_planned_identity_and_monotonic_generations(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _, _, _ = native_registry
    original = runner.connection.identity
    path = tmp_path / "handoff.json"
    atomic_record(path, {
        "executor_incarnation": original.executor_incarnation,
        "connection_generation": 12,
    })
    monkeypatch.setenv(MAC_RUNTIME_HANDOFF_ENV, str(path))
    inherited = handoff_identity(original)
    advanced = advance_generation(inherited)
    assert advanced.connection_generation == 13
    assert handoff_identity(original).connection_generation == 13
    assert advanced.executor_incarnation == original.executor_incarnation
    monkeypatch.delenv(MAC_RUNTIME_HANDOFF_ENV)
    assert handoff_identity(original) is original


def test_idle_update_closes_admission_before_awaited_native_stop(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        runner, _, native, start = native_registry
        await runner.run(start)
        entered, release = asyncio.Event(), asyncio.Event()

        async def stop() -> None:
            entered.set()
            await release.wait()

        monkeypatch.setattr(native, "stop", stop)
        preparing = asyncio.create_task(runner.prepare_update("idle"))
        await entered.wait()
        late = replace(
            start, command_id="late_start", child=replace(start.child, child_id=str(uuid4())),
        )
        result = await runner.run(late)
        assert result.result["error_type"] == "_CommandAdmissionRefused"
        assert native.starts == 1
        release.set()
        assert await preparing

    asyncio.run(exercise())


@pytest.mark.parametrize("task_ending", ["task_ended", "stream_ended", "update_now"])
def test_idle_update_waits_for_background_work_after_turn_ends(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch, task_ending: str,
) -> None:
    async def exercise() -> None:
        runner, _, native, start = native_registry
        await runner.run(start)
        child = runner._children[start.child.child_id]
        turn = TurnToken(start.child.conversation_id, 1)
        child.active_turn = turn
        stops: list[bool] = []

        async def stop() -> None:
            stops.append(True)

        monkeypatch.setattr(native, "stop", stop)
        assert native.sink is not None
        await native.sink.report(
            None, BackgroundTaskStarted(task_id="long_test", task_description="Long test run"),
        )
        await native.sink.report(turn, TurnEnded(ending=ConversationTurnEnding.completed))
        assert runner._quiescence_counts()[0] == 0
        assert not await runner.prepare_update("idle")
        assert stops == []
        assert not runner.admission_stopped and not runner.connection.admission_stopped
        if task_ending == "update_now":
            assert await runner.prepare_update("now")
        else:
            await native.sink.report(
                None, BackgroundTaskEnded(task_id="long_test", task_status="completed")
                if task_ending == "task_ended" else BackendStreamEnded(),
            )
            assert await runner.prepare_update("idle")
        assert stops == [True]

    asyncio.run(exercise())


@pytest.mark.parametrize("confirms", [True, False])
def test_fixed_supervisor_confirms_or_restores_without_touching_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirms: bool,
) -> None:
    root = tmp_path / "executor/updates"
    root.mkdir(parents=True)
    spool = root.parent / "spool/saved"
    spool.parent.mkdir()
    spool.write_text("unsent")
    runtime = tmp_path / "prebuilt"
    runtime.mkdir()
    selection = MacRuntimeSelection(
        "request_one", "b" * 40, str(runtime), None, "a" * 40,
        "00000000-0000-0000-0000-000000000001", 3, "c" * 64,
    )
    atomic_record(root / "pending.json", asdict(selection))
    # Pending belongs to an active child, not a previous supervisor crash.
    pending = (root / "pending.json").read_bytes()
    (root / "pending.json").unlink()
    calls: list[str | None] = []

    def launch(
        arguments: list[str], selected: str | None, revision: str, *,
        ready: Path | None = None, handoff: Path | None = None,
    ) -> subprocess.Popen[bytes]:
        calls.append(selected)
        if selected is None and len(calls) == 1:
            (root / "pending.json").write_bytes(pending)
            program = "raise SystemExit(75)"
        elif selected is not None:
            assert ready is not None
            program = (
                "import pathlib,time; "
                f"pathlib.Path({str(ready.parent)!r}).mkdir(parents=True,exist_ok=True); "
            )
            if confirms:
                marker = json.dumps({"request_id": "request_one", "application_revision": "b" * 40})
            else:
                marker = json.dumps({
                    "request_id": "stale_request", "application_revision": "b" * 40,
                })
            program += f"pathlib.Path({str(ready)!r}).write_text({marker!r}); "
            program += "time.sleep(2)"
        else:
            program = "raise SystemExit(0)"
        return subprocess.Popen([sys.executable, "-c", program], start_new_session=True)

    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(root.parent))
    monkeypatch.setattr(runtime_bootstrap, "_launch", launch)
    monkeypatch.setattr(runtime_bootstrap, "MAC_RUNTIME_READY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(runtime_bootstrap, "MAC_UPDATE_POLL_SECONDS", 0.01)
    args = ["executor", "run", "--application-revision=" + "a" * 40]
    assert runtime_bootstrap.supervise(args) == 0
    assert spool.read_text() == "unsent"
    if confirms:
        assert read_record(root / "selection.json") == asdict(selection)
        assert calls == [None, str(runtime)]
    else:
        failure = read_record(root / "failure.json")
        assert failure is not None and failure["request_id"] == "request_one"
        assert calls == [None, str(runtime), None]
    assert not (root / "pending.json").exists()


@pytest.mark.parametrize("replay_pending", [True, False])
def test_confirmed_selection_inherits_identity_only_until_old_receipts_arrive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay_pending: bool,
) -> None:
    root = tmp_path / "updates"
    root.mkdir()
    atomic_record(root / "selection.json", {
        "request_id": "confirmed", "runtime_path": str(tmp_path / "selected"),
        "application_revision": "b" * 40,
    })
    handoff = root / "handoff.json"
    atomic_record(handoff, {"replay_pending": replay_pending})
    inherited: list[Path | None] = []

    def launch(
        arguments: list[str], selected: str | None, revision: str, *,
        ready: Path | None = None, handoff: Path | None = None,
    ) -> subprocess.Popen[bytes]:
        inherited.append(handoff)
        return subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])

    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime_bootstrap, "_launch", launch)
    assert runtime_bootstrap.supervise([
        "executor", "run", "--application-revision", "a" * 40,
    ]) == 0
    assert inherited == [handoff if replay_pending else None]


def test_recovery_check_reports_target_revision_and_resumes_cancelled_request(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        runner, _, _, _ = native_registry
        controller = MacUpdateController(
            paths=runner.paths, runner=runner, connection=runner.connection,
            server_url="https://fixture.example", executor_id="mac",
            session_factory=aiohttp.ClientSession,
            request_exit=lambda: pytest.fail("target is not active"),
        )
        target: MacUpdateTarget | None = MacUpdateTarget(
            "request_one", "b" * 40, "now", False, "c" * 64, 1,
            "/api/executor/update/request_one/archive",
        )
        reports: list[dict[str, object]] = []

        async def response(
            session: aiohttp.ClientSession, method: str, path: str,
            payload: dict[str, object] | None = None,
        ) -> object:
            if method == "GET":
                return asdict(MacUpdateCheck(1, target))
            assert payload is not None
            reports.append(payload)
            return {"confirmed": False}

        monkeypatch.setattr(controller, "_json", response)
        await controller.check()
        assert reports[-1]["phase"] == "ready_to_restart"
        assert reports[-1]["application_revision"] == "b" * 40
        assert reports[-1]["identity"] == asdict(runner.connection.identity)
        assert runner.admission_stopped
        target = MacUpdateTarget(
            "previous_request", runner.connection.identity.application_revision,
            "now", True, "c" * 64, 1, "/api/executor/update/previous_request/archive",
        )
        await controller.check()
        assert not runner.admission_stopped
        assert controller.prepared_request is None

    asyncio.run(exercise())


def test_marker_requires_server_confirmation_and_replay_custody_survives_selection(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        runner, spool, _, start = native_registry
        await runner.run(start)
        await runner.prepare_update("now")
        handoff = runner.paths.root / "updates/handoff.json"
        atomic_record(handoff, {
            "executor_incarnation": runner.incarnation, "connection_generation": 1,
            "replay_pending": True,
        })
        marker = runner.paths.root / "updates/ready/unique-attempt.json"
        monkeypatch.setenv(MAC_RUNTIME_HANDOFF_ENV, str(handoff))
        monkeypatch.setenv(MAC_RUNTIME_READY_ENV, str(marker))
        monkeypatch.setattr(runner.connection, "ready_link", lambda: cast(Link, object()))
        controller = MacUpdateController(
            paths=runner.paths, runner=runner, connection=runner.connection,
            server_url="https://fixture.example", executor_id="mac",
            session_factory=aiohttp.ClientSession, request_exit=lambda: None,
        )
        confirmed = False
        target = MacUpdateTarget(
            "request_one", runner.connection.identity.application_revision,
            "now", True, "c" * 64, 1, "/api/executor/update/request_one/archive",
        )

        async def response(
            session: aiohttp.ClientSession, method: str, path: str,
            payload: dict[str, object] | None = None,
        ) -> object:
            if method == "GET":
                return asdict(MacUpdateCheck(1, target))
            assert payload is not None and payload["identity"] == asdict(runner.connection.identity)
            return {"confirmed": confirmed}

        monkeypatch.setattr(controller, "_json", response)
        await controller.check()
        assert not marker.exists()
        confirmed = True
        await controller.check()
        assert marker.exists()
        assert runner.admission_stopped
        assert (read_record(handoff) or {})["replay_pending"] is True
        acknowledge_pending(spool)
        controller.release_handoff_custody()
        assert (read_record(handoff) or {})["replay_pending"] is False
        assert runner.admission_stopped
        atomic_record(controller.root / "selection.json", {"request_id": target.request_id})
        controller.release_runtime_admission()
        assert not runner.admission_stopped

    asyncio.run(exercise())


@pytest.mark.parametrize("installs", [True, False])
def test_active_target_creates_durable_handoff_or_retains_failed_attempt(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch, installs: bool,
) -> None:
    async def exercise() -> None:
        runner, _, _, _ = native_registry
        exits: list[bool] = []
        downloads: list[bool] = []
        controller = MacUpdateController(
            paths=runner.paths, runner=runner, connection=runner.connection,
            server_url="https://fixture.example", executor_id="mac",
            session_factory=aiohttp.ClientSession, request_exit=lambda: exits.append(True),
        )
        target = MacUpdateTarget(
            "request_one", "b" * 40, "now", True, "c" * 64, 1,
            "/api/executor/update/request_one/archive",
        )

        async def response(
            session: aiohttp.ClientSession, method: str, path: str,
            payload: dict[str, object] | None = None,
        ) -> object:
            return asdict(MacUpdateCheck(1, target)) if method == "GET" else {"confirmed": False}

        async def download(session: aiohttp.ClientSession, target: MacUpdateTarget) -> Path:
            downloads.append(True)
            if not installs:
                raise ValueError("fixture digest failure")
            return runner.paths.root / "updates/releases/approved-runtime"

        monkeypatch.setenv(MAC_RUNTIME_CHILD_ENV, "1")
        monkeypatch.setattr(controller, "_json", response)
        monkeypatch.setattr(controller, "_download", download)
        await controller.check()
        root = runner.paths.root / "updates"
        if installs:
            assert exits == [True]
            assert (read_record(root / "pending.json") or {})["request_id"] == target.request_id
            handoff = read_record(root / "handoff.json") or {}
            assert handoff["executor_incarnation"] == runner.incarnation
            assert handoff["replay_pending"] is True
        else:
            assert exits == []
            assert (read_record(root / "failure.json") or {})["request_id"] == target.request_id
            await controller.check()
            assert downloads == [True]
            assert not runner.admission_stopped

    asyncio.run(exercise())


def test_prefetch_runs_beside_work_and_activation_reuses_its_verified_runtime(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Risk: prefetch stops work, blocks the poll, loops on bad bytes, or activation refetches."""

    async def exercise() -> None:
        runner, _, _, _ = native_registry
        exits: list[bool] = []
        controller = MacUpdateController(
            paths=runner.paths, runner=runner, connection=runner.connection,
            server_url="https://fixture.example", executor_id="mac",
            session_factory=aiohttp.ClientSession, request_exit=lambda: exits.append(True),
        )
        archive, sha, size = runtime_archive(tmp_path)
        damaged = MacUpdatePrefetch("b" * 40, "d" * 64, size, mac_prefetch_archive_path("d" * 64))
        prefetch = damaged
        target: MacUpdateTarget | None = None
        phases: list[object] = []
        fetched: list[str] = []
        arrived = asyncio.Event()

        async def response(
            session: aiohttp.ClientSession, method: str, path: str,
            payload: dict[str, object] | None = None,
        ) -> object:
            if method == "GET":
                return asdict(MacUpdateCheck(1, target, prefetch))
            assert payload is not None
            phases.append(payload["phase"])
            return {"confirmed": False}

        async def fetch(
            session: aiohttp.ClientSession, archive_path: str, *, revision: str,
            archive_sha256: str, archive_size: int, download_name: str,
        ) -> Path:
            fetched.append(archive_path)
            if archive_sha256 == damaged.archive_sha256:
                raise ValueError("fixture digest failure")
            await arrived.wait()
            return extract_runtime(
                archive, controller.root / "releases", expected_sha256=archive_sha256,
                expected_size=archive_size, expected_revision=revision,
            )

        monkeypatch.setenv(MAC_RUNTIME_CHILD_ENV, "1")
        monkeypatch.setattr(controller, "_json", response)
        monkeypatch.setattr(controller, "_fetch", fetch)
        for _ in range(2):
            await asyncio.wait_for(controller.check(), 1)
            await asyncio.sleep(0)
        assert fetched == [damaged.archive_path]
        prefetch = MacUpdatePrefetch("b" * 40, sha, size, mac_prefetch_archive_path(sha))
        fetched.clear()
        await asyncio.wait_for(controller.check(), 1)
        await asyncio.sleep(0)
        assert fetched == [prefetch.archive_path]
        assert phases == [] and not runner.admission_stopped
        target = MacUpdateTarget(
            "request_one", "b" * 40, "now", True, sha, size,
            "/api/executor/update/request_one/archive",
        )
        activation = asyncio.create_task(controller.check())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not activation.done()
        arrived.set()
        await asyncio.wait_for(activation, 1)
        assert fetched == [mac_prefetch_archive_path(sha)]
        assert exits == [True]
        pending = read_record(controller.root / "pending.json") or {}
        assert pending["runtime_path"] == str(controller.root / "releases" / f"{'b' * 40}-{sha}")

    asyncio.run(exercise())


def test_refused_work_protocol_recovers_through_actual_server_update_contract(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.test_mac_updates import MacProvider, attempt, mac_service, write

    async def exercise() -> None:
        runner, _, _, _ = native_registry
        runner.connection.identity = replace(
            runner.connection.identity,
            protocol_version=runner.connection.identity.protocol_version - 2,
        )
        runner.connection.refusal = {"reason": "Mac needs update"}
        provider = MacProvider()
        published = provider.publish()
        server = mac_service(tmp_path / "server", provider, published.manifest.app_sha)
        server.prepare(published)
        write(server, attempt(published, "done"))
        exits: list[bool] = []
        phases: list[str] = []
        controller = MacUpdateController(
            paths=runner.paths, runner=runner, connection=runner.connection,
            server_url="https://fixture.example", executor_id="mac",
            session_factory=aiohttp.ClientSession, request_exit=lambda: exits.append(True),
        )

        async def response(
            session: aiohttp.ClientSession, method: str, path: str,
            payload: dict[str, object] | None = None,
        ) -> object:
            if method == "GET":
                return asdict(server.check())
            report = TypeAdapter(MacUpdateReport).validate_python(payload)
            phases.append(report.phase)
            server.report(report, {"connection_state": "disconnected"})
            return {"confirmed": False}

        async def download(session: aiohttp.ClientSession, target: MacUpdateTarget) -> Path:
            return extract_runtime(
                server.archive(target.request_id), runner.paths.root / "updates/releases",
                expected_sha256=target.archive_sha256, expected_size=target.archive_size,
                expected_revision=target.application_revision,
            )

        monkeypatch.setenv(MAC_RUNTIME_CHILD_ENV, "1")
        monkeypatch.setattr(controller, "_json", response)
        monkeypatch.setattr(controller, "_download", download)
        await controller.check()
        assert exits == [True]
        assert phases == ["downloading"]
        pending = read_record(runner.paths.root / "updates/pending.json") or {}
        assert pending["application_revision"] == published.manifest.app_sha
        assert runner.connection.ready_link() is None

    asyncio.run(exercise())


def test_next_update_does_not_join_retirement_until_saved_replay_releases_custody(
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        runner, spool, _, start = native_registry
        await runner.run(start)
        await runner.prepare_update("now")
        runner.begin_planned_replay()
        handoff = runner.paths.root / "updates/handoff.json"
        atomic_record(handoff, {
            "executor_incarnation": runner.incarnation, "connection_generation": 1,
            "replay_pending": True,
        })
        monkeypatch.setenv(MAC_RUNTIME_HANDOFF_ENV, str(handoff))
        monkeypatch.setattr(runner.connection, "ready_link", lambda: cast(Link, object()))
        stopping = asyncio.create_task(runner.run(ChildCommand(
            "restart_stop", start.child, ChildOperation.stop, None, StopRun(),
        )))
        await asyncio.sleep(0)
        controller = MacUpdateController(
            paths=runner.paths, runner=runner, connection=runner.connection,
            server_url="https://fixture.example", executor_id="mac",
            session_factory=aiohttp.ClientSession, request_exit=lambda: None,
        )
        target = MacUpdateTarget(
            "next_request", "c" * 40, "now", False, "d" * 64, 1,
            "/api/executor/update/next_request/archive",
        )
        phases: list[object] = []

        async def response(
            session: aiohttp.ClientSession, method: str, path: str,
            payload: dict[str, object] | None = None,
        ) -> object:
            if method == "GET":
                return asdict(MacUpdateCheck(1, target))
            assert payload is not None
            phases.append(payload["phase"])
            return {"confirmed": False}

        monkeypatch.setattr(controller, "_json", response)
        await asyncio.wait_for(controller.check(), 1)
        assert phases == ["waiting"] and not stopping.done()
        acknowledge_pending(spool)
        await asyncio.wait_for(controller.check(), 1)
        assert phases == ["waiting", "ready_to_restart"]
        assert stopping.done()
        assert (read_record(handoff) or {})["replay_pending"] is False

    asyncio.run(exercise())
