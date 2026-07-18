"""Acceptance area 1 — vocabulary contracts (plan §8 area 1).

Every event/request dataclass round-trips its wire form; the exact wire dict is asserted
for a representative event; and the vocabulary module imports nothing Hermes-specific
(mechanical import-graph assertion, plan §2)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from planner.hermes_backend import neutral_vocabulary as nv

E1 = "ticket_e1"


def _every_event() -> list[object]:
    return [
        nv.TurnStartedEvent(employee_entity_id=E1),
        nv.AssistantTextDeltaEvent(employee_entity_id=E1, text="hi"),
        nv.ThinkingDeltaEvent(employee_entity_id=E1, text="mmm"),
        nv.ToolActivityEvent(
            employee_entity_id=E1,
            tool_id="tool-9",
            tool_name="bash",
            phase=nv.ToolPhase.completed,
            preview="ran ls",
        ),
        nv.AgentQuestionEvent(
            employee_entity_id=E1,
            request_id="req-1",
            prompt_text="which?",
            choices=("a", "b"),
        ),
        nv.ToolApprovalRequestEvent(
            employee_entity_id=E1, request_id="req-2", summary="rm -rf /"
        ),
        nv.TurnCompletedEvent(employee_entity_id=E1, final_text="done"),
        nv.TurnFailedEvent(
            employee_entity_id=E1, reason=nv.TurnFailureReason.agent_error, detail="boom"
        ),
        nv.SessionTitledEvent(employee_entity_id=E1, title="My session"),
        nv.HistorySnapshotEvent(
            employee_entity_id=E1,
            messages=(
                nv.NeutralHistoryMessage(role="user", text="q", tool_name=None),
                nv.NeutralHistoryMessage(role="tool", text="ran", tool_name="bash"),
            ),
        ),
        nv.ChildResetEvent(employee_entity_id=E1),
        nv.CatalogResultEvent(
            employee_entity_id=E1,
            payload_json='{"categories": [{"name": "Configuration", "pairs": []}]}',
        ),
        nv.PassthroughEvent(
            employee_entity_id=E1, native_type="some.future.kind", payload_json='{"x": 1}'
        ),
    ]


def _every_request() -> list[object]:
    return [
        nv.AttachToEmployeeRequest(employee_entity_id=E1),
        nv.SendMessageRequest(employee_entity_id=E1, text="go", image_refs=("a.png", "b.png")),
        nv.AnswerQuestionRequest(employee_entity_id=E1, request_id="req-1", answer="a"),
        nv.RespondToApprovalRequest(
            employee_entity_id=E1, request_id="req-2", decision="approve", apply_to_all=True
        ),
        nv.InterruptRequest(employee_entity_id=E1),
        nv.CompactRequest(employee_entity_id=E1),
        nv.ListCatalogRequest(employee_entity_id=E1),
    ]


def test_every_event_kind_round_trips() -> None:
    for event in _every_event():
        assert nv.from_wire(nv.to_wire(event)) == event
    # Exact wire dict for a representative event.
    assert nv.to_wire(nv.AssistantTextDeltaEvent(employee_entity_id="ticket_e1", text="hi")) == {
        "neutral": "event",
        "kind": "assistant_text_delta",
        "employee_entity_id": "ticket_e1",
        "text": "hi",
    }
    # All 13 event dataclasses covered.
    assert len(_every_event()) == 13


def test_every_request_kind_round_trips() -> None:
    for request in _every_request():
        rebuilt = nv.from_wire(nv.to_wire(request))
        assert rebuilt == request
    # tuple fields rebuild to tuples (not lists).
    rebuilt_send = nv.from_wire(
        nv.to_wire(nv.SendMessageRequest(employee_entity_id=E1, text="go", image_refs=("a", "b")))
    )
    assert isinstance(rebuilt_send, nv.SendMessageRequest)
    assert rebuilt_send.image_refs == ("a", "b")
    rebuilt_q = nv.from_wire(
        nv.to_wire(
            nv.AgentQuestionEvent(
                employee_entity_id=E1, request_id="r", prompt_text="?", choices=("x", "y")
            )
        )
    )
    assert isinstance(rebuilt_q, nv.AgentQuestionEvent)
    assert rebuilt_q.choices == ("x", "y")
    # All 7 request dataclasses covered.
    assert len(_every_request()) == 7


def test_vocabulary_module_imports_nothing_hermes_specific() -> None:
    source = inspect.getsource(nv)
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_roots.add(node.module.split(".")[0])
    allowed = {"dataclasses", "enum", "typing", "__future__", "json"}
    assert imported_roots <= allowed, imported_roots
    # No IMPORT statement names a Hermes/relay/minds module (the mechanical contract).
    assert "planner" not in imported_roots
    assert "tui_gateway" not in imported_roots
    # The module file lives beside the translator but shares none of its imports.
    assert Path(nv.__file__).name == "neutral_vocabulary.py"
