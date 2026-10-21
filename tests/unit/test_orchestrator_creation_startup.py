"""Creation hands the exact goal to the supervisor and retains recovery evidence."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.unit.test_conversation_api import _FakeBackend, _FakeBackendChild
from tests.unit.test_sprint_item_supervisors import _app

from planner.conversation.backends.contracts import BackendEventSink, BackendSpawnFailed
from planner.conversation.contracts import (
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    ExplicitReplyMissingEventPayload,
    PromptEventPayload,
)
from planner.conversation.message_content import message_content_text
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.views.storage import read_page
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect
from planner.managers import data as managers_data
from planner.managers.contracts import MANAGER_LAUNCH_DEFAULTS


class _StartupHarness:
    """Scripted children under the application's sole conversation owner."""

    def __init__(self) -> None:
        self.backends: dict[str, _FakeBackend] = {}

    def backend(self, conversation_id: str) -> _FakeBackend:
        return self.backends.setdefault(conversation_id, _FakeBackend(conversation_id))

    def make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:

        backend = self.backend(resolved_start.conversation_id)
        if backend.spawn_fails:
            raise BackendSpawnFailed(resolved_start.conversation_id)
        return _FakeBackendChild(backend, event_sink)


@pytest.mark.parametrize("actor", ["owner", "chief"])
def test_creation_delivers_exact_goal_as_panels_with_supervisor_defaults(
    tmp_path: Path, actor: str
) -> None:
    harness = _StartupHarness()
    app, db_path = _app(
        tmp_path,
        backend_child_factories={key: harness.make_child for key in ConversationBackendKey},
    )
    goal = "  Take the next step.\n\nKeep these spaces.  \n"
    with TestClient(app) as client:
        runtime = app.state.conversation
        store = runtime.store
        response = client.post(
            "/api/managers",
            json={"title": "Kickoff", "project_id": "project_vylo", "goal": goal},
            headers={"X-Plan-Actor": "chief"} if actor == "chief" else {},
        )
        assert response.status_code == 200, response.text
        item = response.json()
        startup = item["startup"]
        assert startup["fate"] == "started"
        conversation_id = startup["conversation_id"]
        assert client.portal is not None
        events = client.portal.call(store.read_events_after, conversation_id, 0)
        prompts = [
            event.payload for event in events if isinstance(event.payload, PromptEventPayload)
        ]
        assert len(prompts) == 1
        prompt = prompts[0]
        assert message_content_text(prompt.content) == goal
        assert prompt.sender is None
        assert prompt.sender_label == "Panels"
        assert prompt.recipient == Principal(PrincipalKind.manager, item["id"])
        assert prompt.mode is PromptDeliveryMode.queue
        assert prompt.sender_message_id
        record = client.portal.call(store.read_conversation, conversation_id)
        assert record is not None
        defaults = MANAGER_LAUNCH_DEFAULTS
        with closing(connect(str(db_path))) as conn:
            saved = managers_data.read_manager(conn, item["id"]).item.manager_launch_configuration
        assert saved == defaults
        # The configured alias and its concrete catalogue resolution are distinct.
        assert (record.backend_key, record.model, record.reasoning_effort) == (
            defaults.employee_backend,
            "gpt-5.6-sol",
            defaults.employee_launch_reasoning_effort,
        )
        identity = dict(record.identity_environment_variables)
        assert identity["PLAN_ACTOR"] == "manager"
        assert identity["PLAN_MANAGER_ID"] == item["id"]
        wire = harness.backend(conversation_id).written_texts
        assert len(wire) == 1
        assert wire[0].endswith(goal)
        assert not wire[0].startswith("[Authenticated Panels reply requirement]")
        assert item["goal"] == goal
        backend = harness.backend(conversation_id)
        assert backend.sink is not None and backend.live_turn_token is not None

        async def complete_turn() -> None:
            assert backend.sink is not None and backend.live_turn_token is not None
            await backend.sink.turn_ended(
                backend.live_turn_token,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )

        client.portal.call(complete_turn)
        client.portal.call(runtime.system.wait_until_quiescent)
        events = client.portal.call(store.read_events_after, conversation_id, 0)
        assert not any(isinstance(e.payload, ExplicitReplyMissingEventPayload) for e in events)
        with closing(connect(str(db_path))) as conn:
            full = read_page(conn, conversation_id, "full")
            focus = read_page(conn, conversation_id, "focus")
        rows = [
            row for row in full["items"] if row["kind"] == "row" and row["row_kind"] == "prompt"
        ]
        assert len(rows) == 1
        assert rows[0]["body"]["kind"] == "inline"
        body = rows[0]["body"]["value"]
        assert body["kind"] == "prompt"
        assert body["sender_label"] == "Panels"
        assert body["content"] == [{"piece": "text", "text": goal}]
        assert focus["items"] == []
