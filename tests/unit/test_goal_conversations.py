"""Goal ownership reaches the backend, history, navigation and ordinary message routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from click.testing import CliRunner
from tests.unit.test_conversation_system import _FakeBackendChild, _Harness

from planner.cli import http
from planner.cli.main import main
from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.authctx import RequestContext, _classify
from planner.core.clock import TestClock as Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    JsonDict,
    Principal,
    PrincipalKind,
)
from planner.core.errors import PlannerError
from planner.goals.actions import create_goal
from planner.goals.conversation import goal_agent_key, resolve_goal
from planner.goals.data import read_goal
from planner.message_delivery.service import send_message
from planner.notifications.data import queue_deliveries
from planner.projects.data import create_project
from planner.runtime.conversation_start import read_agent_conversation, reset_agent_conversation
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides


class GoalHarness(_Harness):
    def __init__(self, path: Path) -> None:
        self.starts: list[ResolvedConversationStart] = []
        super().__init__(path)

    def _make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:
        self.starts.append(resolved_start)
        return super()._make_child(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )


def test_goal_backend_identity_and_reset_preserve_independent_owner_history(
    tmp_db: Connection,
    fake_clock: Clock,
) -> None:
    project = create_project(tmp_db, name="Goal conversations", now=1)
    goals = [
        create_goal(
            tmp_db, {"project_id": project.id, "title": title}, principal=OWNER_PRINCIPAL, now=1
        )
        for title in ("Ship", "Read")
    ]
    path = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    harness = GoalHarness(path)

    async def exercise() -> None:
        try:
            # A read does not create a conversation or start a model turn.
            resolve_goal(tmp_db, goals[0].id)
            assert read_agent_conversation(tmp_db, goal_agent_key(goals[0].id)) is None
            ids = []
            for index, goal in enumerate(goals):
                principal = Principal(PrincipalKind.goal, goal.id)
                result = await send_message(
                    harness.system,
                    tmp_db,
                    fake_clock,
                    RequestContext(OWNER_PRINCIPAL),
                    principal,
                    "Discuss this goal",
                    created_conversation_id=f"goal-conv-{index}",
                    runs_under=ConversationStartOverrides(model="opus"),
                )
                assert result.conversation_id is not None
                ids.append(result.conversation_id)
                await harness.settle()
                start = harness.starts[-1]
                assert start.role_materials is not None
                assert dict(start.role_materials.identity_environment_variables) == {
                    "PLAN_ACTOR": "goal",
                    "PLAN_GOAL_ID": goal.id,
                }
                assert "panels goal read " + goal.id in start.role_materials.role_text
                assert (
                    "Completion and active state are independent" in start.role_materials.role_text
                )
                assert "Discuss this goal" in harness.backend(ids[-1]).writes[-1].text
                assert (
                    "You are the agent for goal " + goal.id
                    in harness.backend(ids[-1]).writes[-1].text
                )
                assert read_goal(tmp_db, goal.id).employee_launch_model == "opus"
                await send_message(
                    harness.system,
                    tmp_db,
                    fake_clock,
                    RequestContext(principal),
                    OWNER_PRINCIPAL,
                    "Goal response",
                )
                await harness.complete_turn(ids[-1])
            assert set(await harness.store.conversation_ids_holding_unread_owner_message()) == set(
                ids
            )
            first = await harness.store.read_conversation(ids[0])
            assert first is not None
            await harness.store.advance_owner_read_through_sequence(ids[0], first.latest_sequence)
            assert await harness.store.conversation_ids_holding_unread_owner_message() == frozenset(
                {ids[1]}
            )
            # Goal messages have their own notification identity and resolvable route.
            queue_deliveries(tmp_db, now=20)
            assert {
                row[0]
                for row in tmp_db.execute(
                    "SELECT subject_id FROM notification_attention_edges WHERE subject_kind='goal'"
                )
            } == {goal.id for goal in goals}
            await reset_agent_conversation(harness.system, tmp_db, goal_agent_key(goals[0].id))
            assert read_agent_conversation(tmp_db, goal_agent_key(goals[0].id)) is None
            assert tuple(
                tmp_db.execute(
                    "SELECT owner_kind,owner_id,conversation_id FROM conversation_history_catalog "
                    "WHERE conversation_id=?",
                    (ids[0],),
                ).fetchone()
            ) == ("goal", goals[0].id, ids[0])
            addresses = [
                json.loads(row[0])
                for row in tmp_db.execute(
                    "SELECT value FROM navigation_records WHERE kind='address' "
                    "AND json_extract(value,'$.owner_kind')='goal'"
                )
            ]
            assert {(a["owner_id"], a["current"]) for a in addresses} == {
                (goals[0].id, False),
                (goals[1].id, True),
            }
            with pytest.raises(PlannerError, match="not in that conversation"):
                await send_message(
                    harness.system,
                    tmp_db,
                    fake_clock,
                    RequestContext(OWNER_PRINCIPAL),
                    Principal(PrincipalKind.goal, goals[0].id),
                    "stale",
                    conversation_id=ids[0],
                )
            # Chief receives a truthful Goal sender and an exact goal reply target.
            await send_message(
                harness.system,
                tmp_db,
                fake_clock,
                RequestContext(Principal(PrincipalKind.goal, goals[1].id)),
                CHIEF_PRINCIPAL,
                "Please arrange this work",
            )
            await harness.settle()
            chief_id = read_agent_conversation(tmp_db, "chief_of_staff")
            assert chief_id is not None
            wire = harness.backend(chief_id).writes[-1].text
            assert f"--goal {goals[1].id}" in wire
            assert (await harness.recorded_prompts(chief_id))[-1][1] == f"Goal {goals[1].id}"
        finally:
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), timeout=15))


def test_goal_cli_identity_and_address_are_distinct_from_ticket_and_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLAN_ACTOR", "goal")
    monkeypatch.setenv("PLAN_GOAL_ID", "goal_one")
    monkeypatch.delenv("PLAN_MANAGER_ID", raising=False)
    monkeypatch.delenv("PLAN_SPRINT_ITEM_ID", raising=False)
    assert http._headers() == {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": "goal_one"}
    assert _classify("goal", goal_id="goal_one").principal == Principal(
        PrincipalKind.goal, "goal_one"
    )
    sent = []

    def send(method: str, path: str, **kwargs: Any) -> JsonDict:
        sent.append((method, path, kwargs["json_body"]))
        return {"fate": "started"}

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(main, ["send-message", "--goal", "goal_two", "--message", "hello"])
    assert result.exit_code == 0, result.output
    assert sent[0][2]["target"] == {"kind": "goal", "id": "goal_two"}
