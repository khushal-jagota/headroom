"""Deployment uncertainty reaches Chief without bypassing installation admission."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryQueued,
    PromptDeliverySuppressed,
    PromptDeliveryUncertain,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.environments.backup import managed_file_roots
from planner.environments.deployment_attention import DeploymentAttentionObserver
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.message_delivery import service as message_delivery
from planner.runtime import conversation_start

SHA = "b" * 40


def _observer(tmp_path: Path, clock: PlannerTestClock, system: InMemoryConversationSystem,
              flags: dict[str, bool]) -> DeploymentAttentionObserver:
    return DeploymentAttentionObserver(
        lifecycle_store=DeploymentLifecycleStore(
            tmp_path / "data/deployment-lifecycle.json", now=clock.now
        ),
        attention_directory=tmp_path / "data/deployment-attention",
        app_sha=SHA, clock=clock,
        connect_database=lambda: sqlite3.connect(":memory:"),
        conversation_system=lambda: system,
        startup_pending=lambda: flags.get("startup", False),
        admission_closed=lambda: flags.get("admission", False),
    )


def _expire(observer: DeploymentAttentionObserver, clock: PlannerTestClock) -> None:
    observer.lifecycle.start("deployment-one", SHA)
    observer.lifecycle.transition("deployment-one", "verifying")
    observer.lifecycle.transition("deployment-one", "app_healthy", serving_sha=SHA)
    clock.set(clock.now() + timedelta(minutes=16))


@pytest.mark.parametrize("gate", ["startup", "admission", "workspace"])
def test_attention_retains_pending_without_any_database_or_agent_work_during_hold(
    tmp_path: Path, fake_clock: PlannerTestClock, monkeypatch: pytest.MonkeyPatch, gate: str,
) -> None:
    system = InMemoryConversationSystem()
    flags = {gate: True}
    if gate == "workspace":
        assert system.workspace_admission.claim(lambda: None, idle=lambda: True, require_idle=True)
    observer = _observer(tmp_path, fake_clock, system, flags)
    _expire(observer, fake_clock)
    observer.connect_database = lambda: pytest.fail("A held installation cannot open message state")
    attention = asyncio.run(observer.poll())
    assert attention is not None and attention.status == "pending"
    # The receipt survives a server restart and the harmless owner's display deadline.
    fake_clock.set(fake_clock.now() + timedelta(hours=1))
    restored = asyncio.run(_observer(tmp_path, fake_clock, system, flags).poll())
    assert restored == attention
    assert observer.store.directory not in managed_file_roots(tmp_path / "data/planner.db").values()


def test_attention_crosses_real_message_door_with_panels_identity_and_deduplicates_lost_receipt(
    tmp_path: Path, fake_clock: PlannerTestClock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = InMemoryConversationSystem()
    flags = {"startup": True}
    observer = _observer(tmp_path, fake_clock, system, flags)
    _expire(observer, fake_clock)
    monkeypatch.setattr(conversation_start, "read_agent_conversation", lambda *_: "c_chief")
    monkeypatch.setattr(conversation_start, "agent_resolve", lambda *_: None)

    async def exercise() -> None:
        await system.start_conversation(ConversationStartRequest(
            conversation_id="c_chief", backend_key=ConversationBackendKey.hermes, model="test-model"
        ))
        pending = await observer.poll()
        assert pending is not None and pending.status == "pending"
        flags["startup"] = False
        started = await observer.poll()
        assert started is not None and started.status == "started"
        writes = system.backend_prompt_writes("c_chief")
        assert len(writes) == 1
        assert writes[0].text == "Panels:\n" + pending.message
        turn = await system.active_turn_reference("c_chief")
        assert turn is not None
        assert not await system.turn_expects_reply(turn, CHIEF_PRINCIPAL)
        observation = next(o for o in system.observations("c_chief")
                           if o.sender_message_id == pending.sender_message_id)
        assert observation.sender is None
        assert observation.recipient == CHIEF_PRINCIPAL
        assert observation.sender_label == "Panels"
        # Model a crash after the conversation accepted the exact message, before its
        # external receipt changed. Retry must recover that existing fate, not re-send.
        observer.store.save(pending)
        recovered = await _observer(tmp_path, fake_clock, system, flags).poll()
        assert recovered is not None and recovered.status == "started"
        assert len(system.backend_prompt_writes("c_chief")) == 1
        assert await observer.poll() == recovered

    asyncio.run(exercise())


@pytest.mark.parametrize("fate,status", [
    (PromptDeliveryQueued(1), "queued"),
    (PromptDeliveryUncertain(), "delivery_uncertain"),
    (PromptDeliverySuppressed("source no longer eligible"), "pending"),
])
def test_attention_retains_the_actual_receipt_without_claiming_backend_delivery(
    tmp_path: Path, fake_clock: PlannerTestClock, monkeypatch: pytest.MonkeyPatch,
    fate: object, status: str,
) -> None:
    system = InMemoryConversationSystem()
    observer = _observer(tmp_path, fake_clock, system, {})
    _expire(observer, fake_clock)
    calls = []

    async def send(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(fate=fate, conversation_id="c_chief")

    monkeypatch.setattr(message_delivery, "send_message", send)
    result = asyncio.run(observer.poll())
    assert result is not None and result.status == status
    assert len(calls) == 1
    assert asyncio.run(observer.poll()) == result
    assert len(calls) == (2 if status == "pending" else 1)


def test_attention_resolution_and_supersession_require_durable_lifecycle_evidence(
    tmp_path: Path, fake_clock: PlannerTestClock,
) -> None:
    system = InMemoryConversationSystem()
    observer = _observer(tmp_path, fake_clock, system, {"startup": True})
    _expire(observer, fake_clock)
    old = asyncio.run(observer.poll())
    assert old is not None
    observer.lifecycle.transition("deployment-one", "succeeded")
    resolved = asyncio.run(observer.poll())
    assert resolved == replace(old, status="resolved")
    observer.lifecycle.start("deployment-two", SHA, expected_deployment_id="deployment-one")
    fake_clock.set(fake_clock.now() + timedelta(minutes=16))
    newer = asyncio.run(observer.poll())
    assert newer is not None and newer.deployment_id == "deployment-two"
    assert newer.sender_message_id != old.sender_message_id
    observer.lifecycle.start("deployment-three", SHA, expected_deployment_id="deployment-two")
    assert asyncio.run(observer.poll()) is None
    superseded = observer.store.read(newer.evidence_identity)
    assert superseded is not None and superseded.status == "superseded"


def test_unavailable_record_retains_attention_without_inventing_a_deployment_identity(
    tmp_path: Path, fake_clock: PlannerTestClock,
) -> None:
    system = InMemoryConversationSystem()
    observer = _observer(tmp_path, fake_clock, system, {"startup": True})
    observer.lifecycle.path.parent.mkdir(parents=True)
    observer.lifecycle.path.write_text("{broken")
    attention = asyncio.run(observer.poll())
    assert attention is not None and attention.deployment_id is None
    assert attention.evidence_identity.startswith("unavailable:")
    assert asyncio.run(observer.poll()) == attention


def test_a_renewed_progress_record_retains_attention_but_does_not_dispatch_a_stale_unknown_claim(
    tmp_path: Path, fake_clock: PlannerTestClock,
) -> None:
    system = InMemoryConversationSystem()
    flags = {"startup": True}
    observer = _observer(tmp_path, fake_clock, system, flags)
    _expire(observer, fake_clock)
    pending = asyncio.run(observer.poll())
    assert pending is not None
    observer.lifecycle.transition("deployment-one", "app_healthy", serving_sha=SHA)
    flags["startup"] = False
    observer.connect_database = lambda: pytest.fail("Fresh progress cannot send an expired claim")
    assert asyncio.run(observer.poll()) == pending
