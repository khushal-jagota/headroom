"""The Codex adapter supervises Workers, without containing shared account probes.

The supervisor lifecycle itself has separate coverage. These cases reuse the scripted Codex
wire to prove the adapter selects supervision from explicit identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.support.conversation_codex_app_server_bench import (
    _resolved_start,
    _run,
    _scripted_child,
)

from planner.conversation import worker_scratch
from planner.conversation.contracts import ConversationRoleMaterials
from planner.conversation.message_content import text_message_content
from planner.worker_supervision import WorkerCleanupError, WorkerSupervisor
from planner.worker_supervision.contracts import CleanupResult


class _ObservedUnit:
    def __init__(self, environment: Mapping[str, str]) -> None:
        self.environment = dict(environment)
        self.stopped = False
        self.path = Path(environment["TMPDIR"]) / "process-record"

    def argv(self, original: Sequence[str]) -> tuple[str, ...]:
        return tuple(original)

    async def started(self, pid: int) -> None:
        pass

    def cancel_unspawned(self) -> None:
        pass

    async def stop(self, *, native_stop=None) -> CleanupResult:  # type: ignore[no-untyped-def]
        if native_stop is not None:
            await native_stop()
        self.stopped = True
        return CleanupResult(True, "")


def test_only_explicit_worker_identity_selects_supervision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    units: list[_ObservedUnit] = []
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "user-bash-env"))

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerSupervisor:
        assert working_directory == tmp_path
        unit = _ObservedUnit(environment)
        units.append(unit)
        return cast(WorkerSupervisor, unit)

    monkeypatch.setattr(WorkerSupervisor, "create", create)
    monkeypatch.setenv("PLAN_TICKET_ID", "inherited-parent-ticket")
    monkeypatch.setenv("TEST_DISK_TMP", str(tmp_path / "disk-temp"))

    async def exercise() -> None:
        # Shared probes and non-Worker conversations inherit the server's environment.
        # Ambient Worker identity must not opt them into supervision.
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            assert units == []
        (tmp_path / "transcript.jsonl").unlink()
        start = replace(
            _resolved_start(tmp_path),
            role_materials=ConversationRoleMaterials(
                role_text="Worker",
                identity_environment_variables=(("PLAN_TICKET_ID", "this-worker"),),
            ),
        )
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.child.start(start, vendor_session_cursor=None)
            await scripted.write_prompt(1, text_message_content("first turn"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert len(units) == 1
            assert units[0].environment["PLAN_TICKET_ID"] == "this-worker"
            scratch = Path(units[0].environment["TMPDIR"])
            assert scratch.parent == tmp_path / "runs"
            assert scratch.is_dir()
            assert scripted.launched()["environment"]["TMPDIR"] == str(scratch)
            assert units[0].environment["BASH_ENV"] == str(tmp_path / "user-bash-env")
            assert not units[0].stopped
            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("second turn"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert len(units) == 1
        assert units[0].stopped
        assert not list((tmp_path / "runs").glob("this-worker-*"))

    _run(exercise)


def test_worker_launch_failure_cleans_up_its_prepared_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.backends.contracts import BackendSpawnFailed

    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    units: list[_ObservedUnit] = []

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerSupervisor:
        unit = _ObservedUnit(environment)
        units.append(unit)
        return cast(WorkerSupervisor, unit)

    def missing_executable(self: _ObservedUnit, original: Sequence[str]) -> tuple[str, ...]:
        return (str(tmp_path / "missing-launcher"),)

    monkeypatch.setattr(WorkerSupervisor, "create", create)
    monkeypatch.setattr(_ObservedUnit, "argv", missing_executable)
    monkeypatch.setenv("TEST_DISK_TMP", str(tmp_path))

    async def exercise() -> None:
        start = replace(
            _resolved_start(tmp_path),
            role_materials=ConversationRoleMaterials(
                role_text="Worker",
                identity_environment_variables=(("PLAN_TICKET_ID", "this-worker"),),
            ),
        )
        async with _scripted_child(tmp_path, script={}) as scripted:
            with pytest.raises(BackendSpawnFailed, match="missing-launcher"):
                await scripted.child.start(start, vendor_session_cursor=None)
        assert len(units) == 1
        assert units[0].stopped
        assert not list((tmp_path / "runs").glob("this-worker-*"))

    _run(exercise)


def test_failed_unit_stop_keeps_the_handle_for_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: tmp_path / "runs")
    units: list[_ObservedUnit] = []
    stop_calls = 0

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerSupervisor:
        unit = _ObservedUnit(environment)
        units.append(unit)
        return cast(WorkerSupervisor, unit)

    original_stop = _ObservedUnit.stop

    async def retry_stop(unit: _ObservedUnit, *, native_stop=None) -> CleanupResult:  # type: ignore[no-untyped-def]
        nonlocal stop_calls
        stop_calls += 1
        if stop_calls == 1:
            raise WorkerCleanupError("cleanup not proven")
        return await original_stop(unit, native_stop=native_stop)

    monkeypatch.setattr(WorkerSupervisor, "create", create)
    monkeypatch.setattr(_ObservedUnit, "stop", retry_stop)
    monkeypatch.setenv("TEST_DISK_TMP", str(tmp_path))

    async def exercise() -> None:
        start = replace(
            _resolved_start(tmp_path),
            role_materials=ConversationRoleMaterials(
                role_text="Worker",
                identity_environment_variables=(("PLAN_TICKET_ID", "this-worker"),),
            ),
        )
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.child.start(start, vendor_session_cursor=None)
            with pytest.raises(WorkerCleanupError, match="cleanup not proven"):
                await scripted.child.stop()
            assert not units[0].stopped
        assert stop_calls == 2
        assert units[0].stopped
        assert not list((tmp_path / "runs").glob("this-worker-*"))

    _run(exercise)
