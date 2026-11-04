"""The Codex adapter contains Workers, without containing shared account probes.

The unit lifecycle itself has separate coverage. These cases reuse the scripted Codex
wire to prove the adapter selects containment from explicit identity and publishes OOM.
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

from planner.conversation.contracts import ConversationRoleMaterials
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_content import text_message_content
from planner.conversation.worker_unit import WorkerUnit, WorkerUnitError


class _ObservedUnit:
    def __init__(self, environment: Mapping[str, str], *, oom: bool) -> None:
        self.environment = dict(environment)
        self.environment["TMPDIR"] = environment["TEST_DISK_TMP"]
        self.oom = oom
        self.finished = False
        self.stopped = False

    def argv(self, original: Sequence[str]) -> tuple[str, ...]:
        return tuple(original)

    async def finish(self) -> str | None:
        self.finished = True
        return "Worker stopped because it exceeded its memory limit." if self.oom else None

    async def stop(self) -> None:
        self.stopped = True


def test_only_explicit_worker_identity_selects_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    units: list[_ObservedUnit] = []

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerUnit:
        assert working_directory == tmp_path
        unit = _ObservedUnit(environment, oom=False)
        units.append(unit)
        return cast(WorkerUnit, unit)

    monkeypatch.setattr(WorkerUnit, "create", create)
    monkeypatch.setenv("PLAN_TICKET_ID", "inherited-parent-ticket")
    monkeypatch.setenv("TEST_DISK_TMP", str(tmp_path / "disk-temp"))

    async def exercise() -> None:
        # Shared probes and non-Worker conversations inherit the server's environment.
        # Ambient Worker identity must not opt them into containment.
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
            assert scripted.launched()["environment"]["TMPDIR"] == str(tmp_path / "disk-temp")
            assert not units[0].stopped and not units[0].finished
            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("second turn"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert len(units) == 1
        assert units[0].stopped

    _run(exercise)


def test_a_worker_oom_reason_reaches_the_failed_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    units: list[_ObservedUnit] = []

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerUnit:
        unit = _ObservedUnit(environment, oom=True)
        units.append(unit)
        return cast(WorkerUnit, unit)

    monkeypatch.setattr(WorkerUnit, "create", create)
    monkeypatch.setenv("TEST_DISK_TMP", str(tmp_path))

    async def exercise() -> None:
        start = replace(
            _resolved_start(tmp_path),
            role_materials=ConversationRoleMaterials(
                role_text="Worker",
                identity_environment_variables=(("PLAN_TICKET_ID", "this-worker"),),
            ),
        )
        script = {"turns": [{"actions": [{"do": "sleep", "seconds": 0.05}, {"do": "die"}]}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.child.start(start, vendor_session_cursor=None)
            await scripted.write_prompt(1, text_message_content("use memory"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert scripted.sink.endings == [ConversationTurnEnding.failed]
            assert scripted.sink.error_summaries == [
                "Worker stopped because it exceeded its memory limit."
            ]
            assert units[0].finished

    _run(exercise)


def test_worker_launch_failure_cleans_up_its_prepared_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.conversation.backends.contracts import BackendSpawnFailed

    units: list[_ObservedUnit] = []

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerUnit:
        unit = _ObservedUnit(environment, oom=False)
        units.append(unit)
        return cast(WorkerUnit, unit)

    def missing_executable(self: _ObservedUnit, original: Sequence[str]) -> tuple[str, ...]:
        return (str(tmp_path / "missing-systemd-run"),)

    monkeypatch.setattr(WorkerUnit, "create", create)
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
            with pytest.raises(BackendSpawnFailed, match="missing-systemd-run"):
                await scripted.child.start(start, vendor_session_cursor=None)
        assert len(units) == 1
        assert units[0].stopped

    _run(exercise)


def test_failed_unit_stop_keeps_the_handle_for_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    units: list[_ObservedUnit] = []
    stop_calls = 0

    async def create(environment: Mapping[str, str], working_directory: Path) -> WorkerUnit:
        unit = _ObservedUnit(environment, oom=False)
        units.append(unit)
        return cast(WorkerUnit, unit)

    original_stop = _ObservedUnit.stop

    async def retry_stop(unit: _ObservedUnit) -> None:
        nonlocal stop_calls
        stop_calls += 1
        if stop_calls == 1:
            raise WorkerUnitError("service manager unavailable")
        await original_stop(unit)

    monkeypatch.setattr(WorkerUnit, "create", create)
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
            with pytest.raises(WorkerUnitError, match="service manager unavailable"):
                await scripted.child.stop()
            assert not units[0].stopped
        assert stop_calls == 2
        assert units[0].stopped

    _run(exercise)
