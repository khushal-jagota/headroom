"""Creation hands the exact brief to the supervisor and retains recovery evidence."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.unit.test_conversation_api import _FakeBackend, _FakeBackendChild
from tests.unit.test_sprint_item_supervisors import _app

from planner.conversation.backends.contracts import BackendEventSink, BackendSpawnFailed
from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ConversationBackendKey,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryUncertain,
    ResolvedConversationStart,
)
from planner.conversation.events import PromptDeliveryRefusedEventPayload, PromptEventPayload
from planner.conversation.message_content import message_content_text
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.runtime import conversation_start
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
def test_creation_delivers_exact_brief_as_creator_with_supervisor_defaults(
    tmp_path: Path, actor: str
) -> None:
    harness = _StartupHarness()
    app, db_path = _app(
        tmp_path,
        backend_child_factories={key: harness.make_child for key in ConversationBackendKey},
    )
    brief = "  Take the next step.\n\nKeep these spaces.  \n"
    with TestClient(app) as client:
        runtime = app.state.conversation
        store = runtime.store
        response = client.post(
            "/api/items",
            json={"title": "Kickoff", "project_id": "project_vylo", "body": brief},
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
        assert message_content_text(prompt.content) == brief
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
        assert wire[0].endswith(brief)
        assert wire[0].startswith("[Authenticated Panels reply requirement]")
        assert item["body"] == brief


@pytest.mark.parametrize("brief", ["", " \n\t "])
def test_blank_brief_saves_without_conversation(tmp_path: Path, brief: str) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        result = client.post(
            "/api/items", json={"title": "Quiet", "project_id": "project_vylo", "body": brief}
        )
        assert result.status_code == 200, result.text
        assert result.json()["startup"] is None
        assert result.json()["body"] == brief
        with connect(str(db_path)) as conn:
            item = sprints_data.read_item(conn, result.json()["id"]).item
            assert (
                conversation_start.read_agent_conversation(conn, item.supervisor_agent_key) is None
            )


def test_refused_initial_brief_stays_linked_and_recorded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _StartupHarness()
    app, db_path = _app(
        tmp_path,
        backend_child_factories={key: harness.make_child for key in ConversationBackendKey},
    )
    backend_for = harness.backend

    def refusing_backend(conversation_id: str):  # type: ignore[no-untyped-def]
        backend = backend_for(conversation_id)
        backend.spawn_fails = True
        return backend

    monkeypatch.setattr(harness, "backend", refusing_backend)
    brief = "  A saved brief even when the backend cannot start.\n"
    with TestClient(app) as client:
        runtime = app.state.conversation
        system = runtime.system
        store = runtime.store
        response = client.post(
            "/api/items", json={"title": "Refused", "project_id": "project_vylo", "body": brief}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["startup"]["fate"] == "refused"
        conversation_id = result["startup"]["conversation_id"]
        assert conversation_id
        with connect(str(db_path)) as conn:
            item = sprints_data.read_item(conn, result["id"]).item
            assert (
                conversation_start.read_agent_conversation(conn, item.supervisor_agent_key)
                == conversation_id
            )
        assert client.portal is not None
        events = client.portal.call(store.read_events_after, conversation_id, 0)
        refused = [
            event.payload
            for event in events
            if isinstance(event.payload, PromptDeliveryRefusedEventPayload)
        ]
        assert len(refused) == 1
        assert message_content_text(refused[0].content) == brief
        assert refused[0].sender == OWNER_PRINCIPAL
        assert str(refused[0].refusal_reason) == result["startup"]["refusal_reason"]

        async def replay_refused_brief() -> None:
            refused_prompt = refused[0]
            receipt = await system.send_with_receipt(
                conversation_id,
                refused_prompt.content,
                sender_label=refused_prompt.sender_label,
                sender_message_id=refused_prompt.sender_message_id,
                sender=refused_prompt.sender,
                recipient=refused_prompt.recipient,
                record_refusal=True,
            )
            assert not receipt.newly_accepted

        client.portal.call(replay_refused_brief)
        replayed_events = client.portal.call(store.read_events_after, conversation_id, 0)
        assert replayed_events == events


@pytest.mark.parametrize(
    ("fate", "expected"),
    [
        (PromptDeliveryUncertain(), "uncertain"),
        (PromptDeliveryQueued(2), "queued"),
        (PromptDeliveryInjected(), "injected"),
    ],
)
def test_creation_reports_delivery_fate_without_claiming_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fate: object, expected: str
) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:

        async def send_with_receipt(
            *args: object, **kwargs: object
        ) -> AddressedPromptDeliveryReceipt:
            return AddressedPromptDeliveryReceipt(fate, newly_accepted=True)  # type: ignore[arg-type]

        monkeypatch.setattr(app.state.conversation_system, "send_with_receipt", send_with_receipt)
        response = client.post(
            "/api/items", json={"title": "Receipt", "project_id": "project_vylo", "body": "Go."}
        )
        assert response.status_code == 200, response.text
        startup = response.json()["startup"]
        assert startup["fate"] == expected
        assert startup["conversation_id"]
        if expected == "queued":
            assert startup["queue_position"] == 2


@pytest.mark.parametrize("failure_at", ["start_conversation", "send_with_receipt"])
def test_unexpected_start_error_reports_saved_item_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_at: str
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:

        async def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Backend secret detail must not leak")

        monkeypatch.setattr(app.state.conversation_system, failure_at, fail)
        response = client.post(
            "/api/items", json={"title": "Saved", "project_id": "project_vylo", "body": "Keep me."}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["startup"]["fate"] == "failed"
        assert "Inspect its conversation before you retry" in result["startup"]["error"]
        assert "secret detail" not in response.text
        assert bool(result["startup"]["conversation_id"]) == (failure_at == "send_with_receipt")
        with connect(str(db_path)) as conn:
            assert sprints_data.read_item(conn, result["id"]).item.body == "Keep me."
            assert (
                conn.execute("SELECT COUNT(*) FROM sprint_items WHERE title='Saved'").fetchone()[0]
                == 1
            )
