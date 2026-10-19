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
from planner.conversation.events import PromptEventPayload
from planner.conversation.message_content import message_content_text
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.sprints import data as sprints_data
from planner.sprints.contracts import SPRINT_ITEM_SUPERVISOR_LAUNCH_DEFAULTS


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
def test_creation_delivers_exact_goal_as_creator_with_supervisor_defaults(
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
            "/api/items",
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
        assert prompt.sender == (OWNER_PRINCIPAL if actor == "owner" else CHIEF_PRINCIPAL)
        assert prompt.sender_label == actor.replace("chief", "Chief")
        assert prompt.recipient == Principal(PrincipalKind.sprint_item, item["id"])
        assert prompt.mode is PromptDeliveryMode.queue
        assert prompt.sender_message_id
        record = client.portal.call(store.read_conversation, conversation_id)
        assert record is not None
        defaults = SPRINT_ITEM_SUPERVISOR_LAUNCH_DEFAULTS
        with closing(connect(str(db_path))) as conn:
            saved = sprints_data.read_item(conn, item["id"]).item.supervisor_launch_configuration
        assert saved == defaults
        # The configured alias and its concrete catalogue resolution are distinct.
        assert (record.backend_key, record.model, record.reasoning_effort) == (
            defaults.employee_backend,
            "gpt-5.6-sol",
            defaults.employee_launch_reasoning_effort,
        )
        identity = dict(record.identity_environment_variables)
        assert identity["PLAN_ACTOR"] == "sprint_item_supervisor"
        assert identity["PLAN_SPRINT_ITEM_ID"] == item["id"]
        wire = harness.backend(conversation_id).written_texts
        assert len(wire) == 1
        assert wire[0].endswith(goal)
        assert wire[0].startswith("[Authenticated Panels reply requirement]")
        assert item["goal"] == goal
