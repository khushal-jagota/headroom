"""Saved Manager settings preserve identity and reach the next message without a reset."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.in_memory_conversation_system import InMemoryConversationSystem

from planner.conversation.contracts import (
    ConversationBackendKey as Backend,
)
from planner.conversation.contracts import (
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
)
from planner.conversation.message_content import text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import PlannerError
from planner.managers import api, data
from planner.managers.contracts import ManagerLaunchConfiguration
from planner.message_delivery.service import send_message
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides


def test_defaults_are_prospective_and_chief_can_save_without_a_turn(tmp_db: Connection) -> None:
    clock = FakeClock(datetime(2026, 11, 2, tzinfo=UTC))
    first = data.create_manager(tmp_db, title="Existing", project_id="project_vylo", clock=clock)
    choice = {
        "employee_backend": "codex",
        "employee_launch_model": "gpt-6.1-terra",
        "employee_launch_reasoning_effort": "high",
    }
    result = asyncio.run(
        api.put_manager_launch_defaults(choice, tmp_db, RequestContext(CHIEF_PRINCIPAL))
    )
    assert result == choice
    second = data.create_manager(tmp_db, title="New", project_id="project_vylo", clock=clock)
    assert first.manager_launch_configuration.employee_launch_model == "gpt-6.1-sol"
    assert second.manager_launch_configuration.employee_launch_model == "gpt-6.1-terra"
    assert (
        data.read_manager(tmp_db, first.id).item.manager_launch_configuration
        == first.manager_launch_configuration
    )
    assert conversation_start.read_agent_conversation(tmp_db, first.manager_agent_key) is None
    denied = RequestContext(Principal(PrincipalKind.manager, first.id))
    with pytest.raises(PlannerError):
        asyncio.run(api.put_manager_launch_defaults(choice, tmp_db, denied))


def test_saved_model_and_null_effort_reach_reused_conversation(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda conn: tmp_path)
    clock = FakeClock(datetime(2026, 11, 2, tzinfo=UTC))
    item = data.create_manager(tmp_db, title="Continuity", project_id="project_vylo", clock=clock)
    system = InMemoryConversationSystem()
    owner = RequestContext(OWNER_PRINCIPAL)
    recipient = Principal(PrincipalKind.manager, item.id)

    async def exercise() -> None:
        first = await send_message(system, tmp_db, clock, owner, recipient, "First")
        assert first.conversation_id is not None
        before = system.observations(first.conversation_id)
        saved = {
            "employee_backend": "codex",
            "employee_launch_model": "exact-custom-model",
            "employee_launch_reasoning_effort": None,
        }
        assert (
            await api.put_manager_launch_configuration(
                item.id, saved, tmp_db, RequestContext(CHIEF_PRINCIPAL), clock
            )
            == saved
        )
        assert (
            conversation_start.read_agent_conversation(tmp_db, item.manager_agent_key)
            == first.conversation_id
        )
        assert system.observations(first.conversation_id) == before
        steered = await send_message(
            system,
            tmp_db,
            clock,
            owner,
            recipient,
            "Steer current work",
            mode=PromptDeliveryMode.steer,
        )
        assert isinstance(steered.fate, PromptDeliveryInjected)
        assert system.backend_prompt_writes(first.conversation_id)[-1].text.endswith(
            "Steer current work"
        )
        assert system.backend_model(first.conversation_id) == "gpt-6.1-sol"
        other_backend = {**saved, "employee_backend": "claude"}
        with pytest.raises(PlannerError, match="cannot change backend"):
            await api.put_manager_launch_configuration(item.id, other_backend, tmp_db, owner, clock)
        # The stand-in's first turn remains active. The changed choice waits in its queue.
        second = await send_message(system, tmp_db, clock, owner, recipient, "Next")
        assert second.conversation_id == first.conversation_id
        assert system.backend_model(first.conversation_id) == "gpt-6.1-sol"
        assert system.backend_reasoning_effort(first.conversation_id) == "medium"
        system.complete_running_turn(first.conversation_id)
        assert system.backend_model(first.conversation_id) == "exact-custom-model"
        assert system.backend_reasoning_effort(first.conversation_id) is None
        # An owner can choose another model while this turn remains active. Its
        # accepted queued intent must outlive that one held prompt.
        queued = await send_message(
            system,
            tmp_db,
            clock,
            owner,
            recipient,
            "Picker change",
            runs_under=ConversationStartOverrides(model="second-custom-model"),
        )
        assert isinstance(queued.fate, PromptDeliveryQueued)
        latest = data.read_manager(tmp_db, item.id).item
        assert latest.launch_configuration_is_explicit
        assert latest.manager_launch_configuration.employee_launch_model == "second-custom-model"
        assert system.backend_model(first.conversation_id) == "exact-custom-model"
        system.complete_running_turn(first.conversation_id)
        assert system.backend_model(first.conversation_id) == "second-custom-model"
        system.complete_running_turn(first.conversation_id)
        # Wakes and review notices use this same helper, with no caller overrides.
        notice = await conversation_start.send_to_agent_conversation(
            system,
            tmp_db,
            latest.manager_agent_key,
            text_message_content("Next wake"),
            conversation_start.manager_resolve(tmp_db, latest),
            conversation_id=first.conversation_id,
            sender_label="Panels",
            reply_requested=False,
            required_manager_id=item.id,
        )
        assert notice.conversation_id == first.conversation_id
        assert system.backend_model(first.conversation_id) == "second-custom-model"
        system.complete_running_turn(first.conversation_id)
        await api.put_manager_launch_configuration(
            item.id,
            {**saved, "employee_launch_model": "idle-steer-model"},
            tmp_db,
            RequestContext(CHIEF_PRINCIPAL),
            clock,
        )
        idle_steer = await send_message(
            system,
            tmp_db,
            clock,
            owner,
            recipient,
            "Steer while idle",
            mode=PromptDeliveryMode.steer,
        )
        assert isinstance(idle_steer.fate, PromptDeliveryStarted)
        assert system.backend_model(first.conversation_id) == "idle-steer-model"
        system.complete_running_turn(first.conversation_id)
        latest = data.read_manager(tmp_db, item.id).item

    asyncio.run(exercise())


def test_legacy_alias_is_actionable_and_disabled_choice_is_refused(tmp_db: Connection) -> None:
    from planner.conversation.backend_state import write_model_enablement
    from planner.managers.launch_settings import validate_launch_configuration

    with pytest.raises(PlannerError, match="model settings"):
        validate_launch_configuration(
            tmp_db, ManagerLaunchConfiguration(Backend.codex, "sol", "medium")
        )
    write_model_enablement(tmp_db, Backend.codex, "disabled-model", enabled=False)
    with pytest.raises(PlannerError, match="disabled"):
        validate_launch_configuration(
            tmp_db, ManagerLaunchConfiguration(Backend.codex, "disabled-model", None)
        )
