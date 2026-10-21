"""What the codex adapter has to be true about, against a codex that is scripted.

One obligation is left here, and it is the one no outside observer can see: a resume that
came back with somebody else's thread is refused rather than accepted in silence.
Downstream, a fresh thread and a restored one are indistinguishable — same shape, same
wire, an agent that answers — so a substitute taken quietly is the whole conversation's
memory gone with nothing to show for it. It cannot be provoked against a real codex, which
is why it is scripted here.

The bench it runs on — the scripted app-server, the recording sink, and the child's own
transcript of what it was sent — lives in
``tests/support/conversation_codex_app_server_bench.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_codex_app_server_bench import _run, _scripted_child

from planner.conversation.backends.contracts import (
    BackendSteerAccepted,
    BackendSteerRefused,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import PromptDeliveryMode, PromptDeliveryRefusalReason
from planner.conversation.message_content import text_message_content


def test_a_resume_that_came_back_with_another_thread_is_refused(tmp_path: Path) -> None:
    """The documented codex trap: a resume that quietly hands back a different thread.

    Downstream, a fresh thread and a restored one look identical — same shape, same wire,
    an agent that answers. The only place the difference is visible is here, so this is
    where it has to be caught.
    """

    async def exercise() -> None:
        script = {"resume": {"outcome": "other_thread", "thread_id": "thread-somebody-else"}}
        async with _scripted_child(tmp_path, script=script) as scripted:
            with pytest.raises(SessionLoadFailed) as refused:
                await scripted.start(cursor="thread-earlier")
            assert "thread-somebody-else" in str(refused.value)

    _run(exercise)


def test_a_manager_wake_crosses_the_codex_adapter_with_its_sender_label(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={"turns": [{"actions": []}]}) as scripted:
            await scripted.start(cursor=None)
            content = text_message_content("Review the manager wake.")
            await scripted.child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                content,
                sender_content=content,
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )
            wire = json.dumps(scripted.sent("turn/start"))
            assert "Panels" in wire
            assert "Review the manager wake." in wire

    _run(exercise)


def test_a_catalog_command_steered_into_a_running_turn_never_becomes_prose(
    tmp_path: Path,
) -> None:
    """Codex runs a command through its own RPC, and that RPC starts a new turn.

    So a command cannot join a turn that already runs. The one thing the adapter must
    never do is send it anyway: the model then reads ``/compact`` as a sentence and the
    command silently never runs. The adapter says it cannot take it, and the core holds
    the message for the turn after this one.
    """

    async def exercise() -> None:
        script: dict[str, Any] = {"turns": [{"actions": []}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run for a while"))

            command = text_message_content("/compact")
            outcome = await scripted.child.steer(
                TurnToken(conversation_id="c", turn_number=1),
                command,
                sender_content=command,
                sender_label="owner",
            )

            assert outcome == BackendSteerRefused(
                PromptDeliveryRefusalReason.command_cannot_join_running_turn
            )
            assert scripted.all_sent("turn/steer") == []

    _run(exercise)


def test_an_ordinary_steer_still_carries_its_sender_label(tmp_path: Path) -> None:
    """The guard is for commands alone. Ordinary text keeps the behaviour it has."""

    async def exercise() -> None:
        script: dict[str, Any] = {"turns": [{"actions": []}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run for a while"))

            spoken = text_message_content("stop and summarize")
            outcome = await scripted.child.steer(
                TurnToken(conversation_id="c", turn_number=1),
                spoken,
                sender_content=spoken,
                sender_label="owner",
            )

            assert outcome == BackendSteerAccepted(composed_content_delivered=True)
            steered = scripted.sent("turn/steer")
            assert "owner" in json.dumps(steered["params"]["input"])
            assert "stop and summarize" in json.dumps(steered["params"]["input"])

    _run(exercise)


async def _until(condition: Any) -> None:
    import asyncio

    async def reached() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(reached(), 5)


def _runtime_app(app_id: str, name: str, **state: bool) -> dict[str, Any]:
    return {"id": app_id, "runtimeName": name, "enabled": True, "callable": True, **state}


def test_installed_apps_survive_stalled_metadata_and_use_exact_mention_paths(
    tmp_path: Path,
) -> None:
    """Directory metadata cannot authorize apps or hold up a normal message."""

    async def exercise() -> None:
        script = {
            "installed_apps": {
                "apps": [
                    _runtime_app("callable-id", "Runtime Label"),
                    _runtime_app("disabled-id", "Disabled", enabled=False),
                    _runtime_app("uncallable-id", "Uncallable", callable=False),
                ]
            },
            "app_metadata": "never",
            "turns": [
                {
                    "actions": [
                        {"do": "agent_delta", "item_id": "a", "text": "still responsive"},
                        {"do": "complete"},
                    ]
                }
            ],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            catalog = await scripted.sink.wait_for_catalog(
                lambda entries: any(entry.display_text == "@Runtime Label" for entry in entries)
            )
            assert not any(entry.display_text in {"@Disabled", "@Uncallable"} for entry in catalog)
            entry = next(entry for entry in catalog if entry.display_text == "@Runtime Label")
            await _until(lambda: bool(scripted.all_sent("app/read")))
            metadata_request_id = scripted.sent("app/read")["id"]
            assert metadata_request_id in scripted.child._client._pending
            await scripted.write_prompt(1, text_message_content(entry.insertion_text + "help"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert scripted.sink.deltas == ["still responsive"]
            # The ordinary turn completed while metadata remained unanswered.
            assert metadata_request_id in scripted.child._client._pending
            assert {"type": "mention", "name": "Runtime Label", "path": "app://callable-id"} in (
                scripted.sent("turn/start")["params"]["input"]
            )
            await _until(lambda: bool(scripted.all_sent("app/read")))
            assert scripted.sent("app/installed")["params"] == {
                "threadId": "thread-1",
                "forceRefresh": True,
            }
            assert scripted.sent("app/read")["params"] == {
                "threadId": "thread-1",
                "appIds": ["callable-id"],
                "includeTools": False,
            }
            assert scripted.all_sent("app/list") == []
            coordinator = scripted.child._catalog_coordinator
            assert coordinator._lanes["metadata"].failures == 0
            await scripted.child.stop()
            assert coordinator.closed
            assert metadata_request_id not in scripted.child._client._pending
            assert all(
                lane.task is not None and lane.task.done() for lane in coordinator._lanes.values()
            )

    _run(exercise)


def test_failed_sources_retry_independently_with_capped_backoff_and_compact_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Hints cannot bypass retry limits, and recovery needs no further hint."""

    async def exercise() -> None:
        now = [1000.0]
        failure = {"error": {"message": "<html>secret upstream page</html>" * 100, "code": -32000}}
        script = {
            "installed_apps": [failure] * 8 + [{"apps": [_runtime_app("ok", "Recovered")]}],
            "app_metadata": {"apps": [], "missingAppIds": ["ok"]},
            "turns": [
                {
                    "actions": [
                        *[{"do": "apps_changed"} for _ in range(30)],
                        {"do": "skills_changed"},
                        {"do": "agent_delta", "item_id": "a", "text": "chat works"},
                        {"do": "complete"},
                    ]
                }
            ],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            coordinator = scripted.child._catalog_coordinator
            coordinator._clock = lambda: now[0]
            await scripted.start(cursor=None)
            await _until(lambda: coordinator._lanes["installed"].failures == 1)
            await scripted.write_prompt(1, text_message_content("continue"))
            await scripted.sink.wait_for_the_turn_to_end()
            now[0] += 0.05
            coordinator.wake()
            await _until(lambda: len(scripted.all_sent("skills/list")) == 2)
            assert scripted.sink.deltas == ["chat works"]
            assert len(scripted.all_sent("app/installed")) == 1
            previous_attempt = 1000.0
            for attempt, delay in enumerate((5, 10, 20, 40, 80, 160, 300, 300), start=1):
                lane = coordinator._lanes["installed"]
                assert lane.deadline == previous_attempt + delay
                now[0] = lane.deadline - 0.01
                coordinator.wake()
                # Let each lane consume the wake before the deadline.
                import asyncio

                await asyncio.sleep(0.015)
                assert len(scripted.all_sent("app/installed")) == attempt
                now[0] = lane.deadline
                previous_attempt = now[0]
                coordinator.wake()
                if attempt < 8:
                    await _until(lambda lane=lane, attempt=attempt: lane.failures == attempt + 1)
            await scripted.sink.wait_for_catalog(
                lambda entries: any(entry.display_text == "@Recovered" for entry in entries)
            )
            assert coordinator._lanes["installed"].failures == 0
            failures = [
                record.getMessage() for record in caplog.records if "retry" in record.getMessage()
            ]
            assert len(failures) == 8
            assert all(
                len(line) <= 240 and "secret" not in line and "<html>" not in line
                for line in failures
            )
            await scripted.child.stop()
            count = len(scripted.all_sent("app/installed"))
            now[0] += 1000
            coordinator.wake()
            await asyncio.sleep(0.02)
            assert len(scripted.all_sent("app/installed")) == count

    _run(exercise)


def test_late_metadata_cannot_restore_a_revoked_app_and_a_hint_during_read_is_retained(
    tmp_path: Path,
) -> None:
    """A concurrent revocation survives held metadata and then returns to idle."""

    async def exercise() -> None:
        import asyncio

        script = {
            "installed_apps": [
                {
                    "response": {"apps": [_runtime_app("revoked", "Runtime")]},
                    "notifications": ["app/list/updated"],
                    "delay": 0.05,
                },
                {"apps": []},
            ],
            "app_metadata": {
                "response": {
                    "apps": [{"id": "revoked", "name": "Too Late"}],
                    "missingAppIds": [],
                },
                "delay": 0.25,
            },
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            now = [1000.0]
            coordinator = scripted.child._catalog_coordinator
            coordinator._clock = lambda: now[0]
            await scripted.start(cursor=None)
            await scripted.sink.wait_for_catalog(
                lambda entries: any(entry.display_text == "@Runtime" for entry in entries)
            )
            await _until(lambda: bool(scripted.all_sent("app/read")))
            now[0] += 1
            coordinator.wake()
            await _until(lambda: len(scripted.all_sent("app/installed")) == 2)
            await scripted.sink.wait_for_catalog(
                lambda entries: not any(entry.display_text.startswith("@") for entry in entries)
            )
            await asyncio.sleep(0.3)
            assert not any(
                entry.display_text == "@Too Late"
                for entry in scripted.sink.composer_catalog_reports[-1]
            )
            assert len(scripted.all_sent("app/installed")) == 2
            # The stale response must not recreate the invocation, either.
            with pytest.raises(PromptWriteFailed, match="not in the current Codex catalogue"):
                await scripted.write_prompt(1, text_message_content("@too-late hello"))
            with pytest.raises(PromptWriteFailed, match="not in the current Codex catalogue"):
                await scripted.write_prompt(1, text_message_content("@runtime hello"))
            assert scripted.all_sent("turn/start") == []

    _run(exercise)


def test_metadata_batches_stop_on_failure_and_cache_expires_only_on_a_hint(tmp_path: Path) -> None:
    """Large installed sets cannot cause an outage to multiply metadata requests."""

    async def exercise() -> None:
        import asyncio

        apps = [_runtime_app(f"app-{index:03}", f"Runtime {index}") for index in range(101)]
        metadata = {
            "apps": [{"id": app["id"], "name": app["runtimeName"]} for app in apps],
            "missingAppIds": [],
        }
        async with _scripted_child(
            tmp_path,
            script={
                "installed_apps": {"apps": apps},
                "app_metadata": ["error", metadata],
            },
        ) as scripted:
            now = [1000.0]
            coordinator = scripted.child._catalog_coordinator
            coordinator._clock = lambda: now[0]
            await scripted.start(cursor=None)
            await _until(lambda: coordinator._lanes["metadata"].failures == 1)
            assert len(scripted.all_sent("app/read")) == 1
            now[0] += 5
            coordinator.wake()
            await _until(lambda: len(coordinator.metadata) == 101)
            reads = scripted.all_sent("app/read")
            assert [len(read["params"]["appIds"]) for read in reads] == [100, 100, 1]
            reports = len(scripted.sink.composer_catalog_reports)
            coordinator.hint_apps()
            now[0] += 1
            coordinator.wake()
            await _until(lambda: len(scripted.all_sent("app/installed")) == 2)
            await _until(lambda: not coordinator._lanes["metadata"].pending)
            assert len(scripted.all_sent("app/read")) == 3
            assert len(scripted.sink.composer_catalog_reports) == reports
            now[0] += 901
            coordinator.wake()
            await asyncio.sleep(0.02)
            assert len(scripted.all_sent("app/read")) == 3
            coordinator.hint_apps()
            now[0] += 0.05
            coordinator.wake()
            await _until(lambda: len(scripted.all_sent("app/read")) == 5)
            assert len(scripted.all_sent("app/installed")) == 3

    _run(exercise)


def test_catalog_publication_failures_back_off_and_republish_after_sink_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed event delivery neither spins nor counts as an acknowledged snapshot."""

    async def exercise() -> None:
        import asyncio

        script = {
            "installed_apps": [{"apps": []}, {"apps": [_runtime_app("available", "Available")]}],
            "app_metadata": "never",
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            now = [1000.0]
            coordinator = scripted.child._catalog_coordinator
            coordinator._clock = lambda: now[0]
            await scripted.start(cursor=None)
            await _until(
                lambda: (
                    coordinator.skills is not None
                    and coordinator.plugins is not None
                    and coordinator.installed is not None
                )
            )
            original_publish = scripted.sink.composer_catalog_reported
            attempts = [0]
            recovered = [False]

            async def unavailable_sink(entries: Any) -> None:
                attempts[0] += 1
                if not recovered[0]:
                    raise RuntimeError("event sink unavailable")
                await original_publish(entries)

            monkeypatch.setattr(scripted.sink, "composer_catalog_reported", unavailable_sink)
            coordinator.hint_apps()
            now[0] += 1
            coordinator.wake()
            await _until(lambda: coordinator._lanes["installed"].failures == 1)
            for attempt, delay in enumerate((5, 10, 20, 40, 80, 160, 300, 300), start=1):
                lane = coordinator._lanes["installed"]
                assert lane.deadline == now[0] + delay
                assert attempts[0] == attempt
                coordinator.hint_apps()
                now[0] += delay - 0.01
                coordinator.wake()
                await asyncio.sleep(0.01)
                assert attempts[0] == attempt
                now[0] = lane.deadline
                if attempt == 8:
                    recovered[0] = True
                coordinator.wake()
                if attempt < 8:
                    await _until(lambda lane=lane, attempt=attempt: lane.failures == attempt + 1)
            await scripted.sink.wait_for_catalog(
                lambda entries: any(entry.display_text == "@Available" for entry in entries)
            )
            assert attempts[0] == 9
            assert coordinator._lanes["installed"].failures == 0

    _run(exercise)
