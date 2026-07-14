"""Hermetic tests for GatewayChild routing, run_step, config, and SharedGateway."""

from __future__ import annotations

import ast
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import planner.minds as minds
from planner.chat.contracts import (
    ChatActivityObservation,
    CommandCatalog,
    GatewayStatus,
    HumanChatCompletion,
    HumanChatObservation,
    HumanChatOutputDelta,
)
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.errors import ErrorCode, PlannerError
from planner.minds.config import (
    boot_smoke_check,
    hermes_src_root,
    provision_planner_home_skills,
    resolve_hermes_python,
    resolve_planner_home,
)
from planner.minds.contracts import RunResult
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import ChildProcess, GatewayChild, GatewayError
from planner.minds.runner import run_step
from planner.minds.sessions import LiveSessionManager
from planner.minds.shared_gateway import (
    BUSY_CODE,
    CHAT_SOURCE,
    SESSION_COLS,
    EntityRoutingGateway,
    SharedGateway,
    SharedGatewayBusy,
)
from planner.minds.smoke import _check_distinct_sessions_demux
from planner.tickets.contracts import EmployeeSessionHistory
from planner.worker_context.contracts import (
    PendingWorkerContext,
    PreparedWorkerPrompt,
    WorkerContextReceipt,
)

LIVE_SID = "ab12cd34"
OTHER_SID = "ff00ff00"
WINNER_SID = "ee11ee11"
STORED_KEY = "20260706_120000_abcdef"
OTHER_KEY = "20260706_120000_999999"
WINNER_KEY = "20260706_120000_winner"
HERMES_PY = "/x/hermes-agent/venv/bin/python"


def _identity_binder(session_key: str) -> str:
    return session_key


def human_observations(
    gateway: Any,
    session_key: str | None,
    entity_id: str,
    text: str,
    mode: str,
    bind_session_key=_identity_binder,
    image_paths: tuple[Path, ...] = (),
):
    return gateway.run_human_turn(
        session_key,
        entity_id,
        text,
        mode,
        bind_session_key,
        image_paths,
    )


def create_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={"session_id": sid, "stored_session_id": key})


def resume_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={"session_id": sid, "resumed": key})


def complete_ev(
    sid: str = LIVE_SID, text: str = "hello", status: str = "complete"
) -> dict[str, Any]:
    return ev(
        "message.complete",
        sid,
        {"text": text, "usage": {"input": 1, "output": 2}, "status": status},
    )


def submit_reply(
    *events_after: dict[str, Any], events_before: tuple[dict[str, Any], ...] = ()
) -> Reply:
    return Reply(
        result={"status": "streaming"},
        events_before=events_before,
        events_after=tuple(events_after),
    )


def gw(fake: FakeGateway) -> GatewayChild:
    return GatewayChild(HERMES_PY, {}, spawn=fake.spawn)


def run(
    fake: FakeGateway,
    session_key: str | None = None,
    *,
    role: str = "planner-worker",
    on_event: Any = None,
    **kw: Any,
) -> RunResult:
    return run_step(
        session_key,
        role,
        "do the step",
        on_event,
        home="/tmp/planner-home",
        hermes_python=HERMES_PY,
        spawn=fake.spawn,
        base_env={},
        **kw,
    )


class Spawner:
    def __init__(self, children: list[FakeGateway]) -> None:
        self.children = children
        self.i = 0
        self.lock = threading.Lock()

    def spawn(self, argv: list[str], env: dict[str, str]) -> ChildProcess:
        with self.lock:
            child = self.children[self.i]
            self.i += 1
        return child.spawn(argv, env)


class NonExitingChild:
    def __init__(self) -> None:
        self._ready_sent = False
        self.release_readers = threading.Event()
        self.wait_timeouts: list[float | None] = []

    def send(self, line: str) -> None:
        del line

    def read_stdout(self) -> str | None:
        if not self._ready_sent:
            self._ready_sent = True
            return json.dumps(ev("gateway.ready"))
        self.release_readers.wait()
        return None

    def read_stderr(self) -> str | None:
        self.release_readers.wait()
        return None

    def close_stdin(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int | None:
        self.wait_timeouts.append(timeout)
        threading.Event().wait(timeout)
        return None


def shared(fake: FakeGateway, worker_context=None) -> SharedGateway:
    return SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
        worker_context=worker_context,
    )


class RecordingWorkerContext:
    def __init__(self) -> None:
        self.pending: dict[str, tuple[PendingWorkerContext, ...]] = {}
        self.prepare_calls: list[tuple[str, str]] = []
        self.acknowledgements: list[tuple[str, tuple[tuple[str, int], ...]]] = []

    def set(self, entity_id: str, *items: PendingWorkerContext) -> None:
        self.pending[entity_id] = items

    def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt:
        self.prepare_calls.append((worker_entity_id, prompt_text))
        items = self.pending.get(worker_entity_id, ())
        if not items:
            return PreparedWorkerPrompt(prompt_text, ())
        context_lines = "\n".join(f"- {item.text}" for item in items)
        return PreparedWorkerPrompt(
            f"{prompt_text}\n\n[Pending worker context]\n{context_lines}"
            "\n[/Pending worker context]",
            tuple(WorkerContextReceipt(item.context_key, item.revision) for item in items),
        )

    def acknowledge(self, worker_entity_id: str, receipts) -> None:
        pairs = tuple((receipt.context_key, receipt.revision) for receipt in receipts)
        self.acknowledgements.append((worker_entity_id, pairs))
        current = self.pending.get(worker_entity_id, ())
        self.pending[worker_entity_id] = tuple(
            item for item in current if (item.context_key, item.revision) not in pairs
        )


class RecordingChatGateway:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def status(self) -> GatewayStatus:
        self.calls.append(("status", ""))
        return GatewayStatus(available=True)

    def read_employee_session_history(
        self, employee_session_id: str, ticket_id: str
    ) -> EmployeeSessionHistory:
        self.calls.append(("read_employee_session_history", ticket_id))
        return EmployeeSessionHistory(
            messages=(), employee_session_id=employee_session_id
        )

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key=_identity_binder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ):
        del session_key, mode, image_paths, require_existing_session
        self.calls.append(("run_human_turn", entity_id))
        bind_session_key(f"{self.name}-key")
        yield HumanChatCompletion(f"{self.name}: {text}", "assistant")

    def catalog(self) -> CommandCatalog:
        self.calls.append(("catalog", ""))
        return CommandCatalog(categories=(), skills=(), canon={}, sub={})

    def shutdown(self, *, deadline: float | None = None) -> None:
        del deadline
        self.closed = True


def test_entity_routing_gateway_streams_each_entity_through_its_role_gateway() -> None:
    worker = RecordingChatGateway("worker")
    chief = RecordingChatGateway("chief")
    gateway = EntityRoutingGateway(
        worker,  # type: ignore[arg-type]
        {CHIEF_OF_STAFF_ENTITY_ID: chief},  # type: ignore[dict-item]
    )

    chief_chunks = list(
        human_observations(
            gateway, None, CHIEF_OF_STAFF_ENTITY_ID, "hello", "message"
        )
    )
    ticket_chunks = list(human_observations(gateway, None, "t_demo", "hello", "message"))
    gateway.status_for_entity(CHIEF_OF_STAFF_ENTITY_ID)

    assert chief_chunks[-1] == HumanChatCompletion("chief: hello", "assistant")
    assert ticket_chunks[-1] == HumanChatCompletion("worker: hello", "assistant")
    assert chief.calls == [("run_human_turn", CHIEF_OF_STAFF_ENTITY_ID), ("status", "")]
    assert worker.calls == [("run_human_turn", "t_demo")]


def test_shared_gateway_streams_structured_display_safe_activity() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                submit_reply(
                    ev("reasoning.delta", LIVE_SID, {"text": "private reasoning"}),
                    ev(
                        "tool.start",
                        LIVE_SID,
                        {
                            "tool_id": "call-web-1",
                            "name": "web_search",
                            "args_text": "api_key=secret",
                        },
                    ),
                    ev(
                        "tool.delta",
                        LIVE_SID,
                        {"tool_id": "call-web-1", "result": "private delta"},
                    ),
                    ev(
                        "tool.end",
                        LIVE_SID,
                        {
                            "tool_id": "call-web-1",
                            "name": "web_search",
                            "result": "private output",
                        },
                    ),
                    complete_ev(text="done"),
                )
            ],
        }
    )
    gateway = shared(fake)
    try:
        chunks = list(human_observations(gateway, None, "t_demo", "hello", "message"))
    finally:
        gateway.shutdown()

    activities = [chunk for chunk in chunks if isinstance(chunk, ChatActivityObservation)]
    assert activities == [
        ChatActivityObservation(
            category="thinking",
            label="Thinking",
            lifecycle_state="running",
            action_identity="thinking",
        ),
        ChatActivityObservation(
            category="tool",
            label="Using web_search",
            lifecycle_state="running",
            action_identity="tool:call-web-1",
        ),
        ChatActivityObservation(
            category="tool",
            label="Used web_search",
            lifecycle_state="complete",
            action_identity="tool:call-web-1",
        ),
    ]
    serialized = repr(activities)
    assert "private reasoning" not in serialized
    assert "api_key" not in serialized
    assert "private delta" not in serialized
    assert "private output" not in serialized


def test_shared_gateway_routes_tool_complete_alias_as_first_activity() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                submit_reply(
                    ev(
                        "tool.complete",
                        LIVE_SID,
                        {
                            "tool_id": "call-web-1",
                            "name": "web_search",
                            "result": "private output",
                        },
                    ),
                    complete_ev(text="done"),
                )
            ],
        }
    )
    gateway = shared(fake)
    try:
        activities = [
            chunk
            for chunk in human_observations(gateway, None, "t_demo", "hello", "message")
            if isinstance(chunk, ChatActivityObservation)
        ]
    finally:
        gateway.shutdown()

    assert activities == [
        ChatActivityObservation(
            category="tool",
            label="Used web_search",
            lifecycle_state="complete",
            action_identity="tool:call-web-1",
        )
    ]
    assert "private output" not in repr(activities)


def test_minds_package_exports_shared_contracts_not_run_step() -> None:
    assert minds.RunResult is RunResult
    assert not hasattr(minds, "run_step")


def test_gateway_responses_still_demux_by_request_id() -> None:
    fake = FakeGateway(
        {
            "first": [Reply()],
            "second": [
                Reply(
                    frames=(
                        {"jsonrpc": "2.0", "id": 2, "result": {"which": "second"}},
                        {"jsonrpc": "2.0", "id": 1, "result": {"which": "first"}},
                    )
                )
            ],
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    results: dict[str, dict[str, Any]] = {}

    def call_first() -> None:
        results["first"] = child.request("first", timeout=10.0)

    t = threading.Thread(target=call_first)
    t.start()
    assert fake.wait_sent(1, 5.0)
    assert child.request("second", timeout=10.0) == {"which": "second"}
    t.join(10.0)
    assert results["first"] == {"which": "first"}
    child.shutdown()


def test_production_has_one_consequence_owned_session_event_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    planner_root = root / "src/planner"
    allowed_ingress_claimers = {
        Path("minds/sessions/service.py"),
        Path("minds/runner.py"),
        Path("minds/smoke.py"),
    }
    removed_calls = {
        "open_session_events",
        "_submit_and_drain",
        "_stream_prompt",
    }
    violations: list[str] = []

    for path in planner_root.rglob("*.py"):
        relative = path.relative_to(planner_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != "LiveSession":
                continue
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    member.name in {"submit", "next_observation"}
                ):
                    violations.append(f"{relative}:{member.lineno}: LiveSession.{member.name}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in removed_calls
            ):
                violations.append(f"{relative}:{node.lineno}: def {node.name}")
            if not isinstance(node, ast.Call):
                continue
            called_name: str | None = None
            if isinstance(node.func, ast.Attribute):
                called_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                called_name = node.func.id
            if called_name in removed_calls:
                violations.append(f"{relative}:{node.lineno}: {called_name}")
            if (
                called_name == "claim_session_event_ingress"
                and relative not in allowed_ingress_claimers
            ):
                violations.append(
                    f"{relative}:{node.lineno}: claim_session_event_ingress"
                )

    assert violations == []


def test_gateway_child_wide_session_ingress_receives_events_once_in_stdout_order() -> None:
    fake = FakeGateway(
        {
            "go": [
                Reply(
                    result={},
                    events_before=(
                        complete_ev(LIVE_SID, text="first"),
                        ev("notice", None, {"process": True}),
                    ),
                    events_after=(complete_ev(OTHER_SID, text="second"),),
                )
            ]
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    ingress = child.claim_session_event_ingress()

    assert child.request("go", timeout=5.0) == {}
    assert ingress.next_event(timeout=5.0)["payload"]["text"] == "first"
    assert ingress.next_event(timeout=5.0)["payload"]["text"] == "second"
    assert child.next_process_event(timeout=5.0)["payload"] == {"process": True}
    with pytest.raises(RuntimeError, match="already claimed"):
        child.claim_session_event_ingress()

    ingress.close()
    child.shutdown()


def test_gateway_begin_request_writes_before_response_wait() -> None:
    fake = FakeGateway(
        {
            "first": [Reply()],
            "second": [Reply(result={"accepted": True})],
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)

    first = child.begin_request("first")
    second = child.begin_request("second")

    assert fake.sent_methods() == ["first", "second"]
    assert second.wait(timeout=5.0) == {"accepted": True}
    with pytest.raises(GatewayError, match="no response to first"):
        first.wait(timeout=0.01)
    child.shutdown()


def test_gateway_child_death_wakes_single_session_ingress_claimant() -> None:
    fake = FakeGateway({"boom": [Reply(die=True)]}, stderr_lines=("traceback: kaboom",))
    child = gw(fake)
    child.wait_ready(5.0)
    ingress = child.claim_session_event_ingress()
    with pytest.raises(GatewayError, match="died"):
        child.request("boom", timeout=5.0)
    assert ingress.next_event(timeout=5.0) is None
    child.shutdown()
    assert child.stderr_tail() == ["traceback: kaboom"]
    assert child.alive is False


def test_concurrency_smoke_rejects_cross_session_delivery_from_single_feed() -> None:
    unexpected_sid = "unexpected-session"
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(complete_ev(unexpected_sid, text="wrong"),),
                ),
                Reply(result={"status": "streaming"}),
            ]
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    ingress = child.claim_session_event_ingress()

    try:
        assert _check_distinct_sessions_demux(
            child,
            ingress,
            LIVE_SID,
            OTHER_SID,
        ) is False
    finally:
        ingress.close()
        child.shutdown()

    assert fake.sent_methods() == ["prompt.submit", "prompt.submit"]


def test_gateway_wait_ready_timeout() -> None:
    fake = FakeGateway({}, ready=False)
    child = gw(fake)
    with pytest.raises(GatewayError, match="ready"):
        child.wait_ready(timeout=0.2)
    child.shutdown()


def test_run_step_create_happy_path() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                submit_reply(
                    ev("message.start", LIVE_SID),
                    ev("message.delta", LIVE_SID, {"text": "hel"}),
                    complete_ev(text="hello"),
                )
            ],
        }
    )
    seen: list[dict[str, Any]] = []
    res = run(fake, on_event=seen.append)
    assert res.status == "complete"
    assert res.text == "hello"
    assert res.usage == {"input": 1, "output": 2}
    assert res.session_key == STORED_KEY
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    assert fake.env is not None
    assert fake.env["HERMES_TUI_SKILLS"] == "planner-worker"
    assert fake.env["HERMES_HOME"] == "/tmp/planner-home"
    assert fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"
    assert [e["type"] for e in seen] == ["message.start", "message.delta", "message.complete"]
    assert fake.closed is True


def test_run_step_resume_path_issues_resume_not_create() -> None:
    fake = FakeGateway(
        {
            "session.resume": [resume_reply("ffff0000", "20260707_090000_tip999")],
            "prompt.submit": [submit_reply(complete_ev(sid="ffff0000", text="resumed"))],
        }
    )
    res = run(fake, session_key=STORED_KEY)
    assert fake.sent_methods() == ["session.resume", "prompt.submit"]
    assert res.session_key == "20260707_090000_tip999"
    assert res.status == "complete"


def test_shared_gateway_employee_session_history_resumes_and_preserves_full_trace() -> None:
    fake = FakeGateway(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": "20260708_090000_rotated",
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "human asks\n\n[Pending worker context]\n- Ticket changed."
                                    "\n[/Pending worker context]"
                                ),
                                "created_at": 10,
                            },
                            {
                                "role": "system",
                                "content": [{"text": "worker prompt"}, {"text": "context"}],
                                "timestamp": "11.7",
                            },
                            {
                                "author": "assistant",
                                "message": {"content": "worker replies"},
                                "time": 12.2,
                            },
                            {"role": "tool", "output": "tool output", "created_at": 13},
                        ],
                    }
                )
            ]
        }
    )
    gateway = shared(fake)
    try:
        history = gateway.read_employee_session_history(STORED_KEY, "t_demo")
    finally:
        gateway.shutdown()

    assert fake.sent_methods() == ["session.resume"]
    assert fake.sent[0]["params"] == {
        "session_id": STORED_KEY,
        "cols": SESSION_COLS,
        "lazy": True,
        "source": CHAT_SOURCE,
    }
    assert history.employee_session_id == "20260708_090000_rotated"
    assert [(msg.role, msg.text, msg.created_at) for msg in history.messages] == [
        (
            "user",
            "human asks\n\n[Pending worker context]\n- Ticket changed."
            "\n[/Pending worker context]",
            10,
        ),
        ("system", "worker prompt\ncontext", 11),
        ("assistant", "worker replies", 12),
        ("tool", "tool output", 13),
    ]


def test_run_step_event_racing_submit_response() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                Reply(result={"status": "streaming"}, events_before=(complete_ev(text="fast"),))
            ],
        }
    )
    res = run(fake)
    assert res.status == "complete"
    assert res.text == "fast"


def test_run_step_busy_4009() -> None:
    fake = FakeGateway(
        {"session.create": [create_reply()], "prompt.submit": [Reply(error=(4009, "busy"))]}
    )
    res = run(fake)
    assert res.status == "errored"
    assert "4009" in (res.error or "")
    assert res.session_key == STORED_KEY


def test_run_step_rejects_cross_session_delivery() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev(OTHER_SID, text="wrong"))],
        }
    )

    result = run(fake)

    assert result.status == "errored"
    assert "received an event for session" in (result.error or "")
    assert OTHER_SID in (result.error or "")


def test_shared_gateway_start_reuses_one_child_and_sets_worker_env() -> None:
    fake = FakeGateway({})
    gateway = shared(fake)
    gateway.start()
    gateway.start()
    assert fake.env is not None
    assert fake.env["HERMES_HOME"] == "/tmp/planner-home"
    assert fake.env["HERMES_TUI_SKILLS"] == "planner-worker"
    assert fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"
    gateway.shutdown()
    assert fake.closed is True


def test_shared_gateway_respawns_after_child_death() -> None:
    fake1 = FakeGateway({})
    fake2 = FakeGateway({})
    spawner = Spawner([fake1, fake2])
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=spawner.spawn,
        base_env={},
    )
    gateway.start()
    fake1.kill()
    time.sleep(0.05)
    gateway.start()
    assert spawner.i == 2
    gateway.shutdown()
    assert fake2.closed is True


def test_shared_gateway_delivers_and_acknowledges_context_for_human_message_streams() -> None:
    context = RecordingWorkerContext()
    context.set(
        "t_sync",
        PendingWorkerContext("alpha", "Alpha changed.", 2),
        PendingWorkerContext("beta", "Beta changed.", 4),
    )
    context.set("t_stream", PendingWorkerContext("ticket_changed", "Ticket changed.", 7))
    fake = FakeGateway(
        {
            "session.create": [
                create_reply(LIVE_SID, STORED_KEY),
                create_reply(OTHER_SID, OTHER_KEY),
            ],
            "prompt.submit": [
                submit_reply(complete_ev(LIVE_SID, text="sync reply")),
                submit_reply(complete_ev(OTHER_SID, text="stream reply")),
            ],
        }
    )
    gateway = shared(fake, context)
    try:
        first = list(human_observations(gateway, None, "t_sync", "original sync", "message"))
        streamed = list(human_observations(gateway, None, "t_stream", "original stream", "message"))
    finally:
        gateway.shutdown()

    submits = [
        frame["params"]["text"]
        for frame in fake.sent
        if frame["method"] == "prompt.submit"
    ]
    assert submits == [
        "original sync\n\n[Pending worker context]\n- Alpha changed.\n- Beta changed."
        "\n[/Pending worker context]",
        "original stream\n\n[Pending worker context]\n- Ticket changed."
        "\n[/Pending worker context]",
    ]
    assert first[-1].text == "sync reply"
    assert streamed[-1].text == "stream reply"
    assert context.prepare_calls == [
        ("t_sync", "original sync"),
        ("t_stream", "original stream"),
    ]
    assert context.acknowledgements == [
        ("t_sync", (("alpha", 2), ("beta", 4))),
        ("t_stream", (("ticket_changed", 7),)),
    ]


@pytest.mark.parametrize("disposition", ["streaming", "queued", "steered"])
@pytest.mark.parametrize("input_path", ["message", "command", "image"])
def test_human_input_paths_ack_exact_context_after_native_acceptance(
    disposition: str,
    input_path: str,
) -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 12))
    script: dict[str, list[Reply]] = {
        "session.create": [create_reply()],
        "prompt.submit": [
            Reply(
                result={"status": disposition},
                events_after=(complete_ev(text="reply"),),
            )
        ],
    }
    if input_path == "command":
        script["slash.exec"] = [
            Reply(result={"type": "skill", "message": "command model text"})
        ]
    if input_path == "image":
        script["image.attach"] = [Reply(result={"attached": True})]
    fake = FakeGateway(script)
    gateway = shared(fake, context)

    try:
        if input_path == "command":
            chunks = list(human_observations(gateway, None, "t_demo", "/skill", "command"))
            expected_visible_model_text = "command model text"
        elif input_path == "image":
            chunks = list(
                human_observations(gateway,
                    None,
                    "t_demo",
                    "describe it",
                    "message",
                    image_paths=(Path("/tmp/chat-image.png"),),
                )
            )
            expected_visible_model_text = "describe it"
        else:
            chunks = list(human_observations(gateway, None, "t_demo", "human text", "message"))
            expected_visible_model_text = "human text"
    finally:
        gateway.shutdown()

    assert chunks[-1].text == "reply"
    submit = next(frame for frame in fake.sent if frame["method"] == "prompt.submit")
    assert submit["params"]["text"] == (
        f"{expected_visible_model_text}\n\n[Pending worker context]\n"
        "- Ticket changed.\n[/Pending worker context]"
    )
    assert context.prepare_calls == [("t_demo", expected_visible_model_text)]
    assert context.acknowledgements == [
        ("t_demo", (("ticket_changed", 12),))
    ]


@pytest.mark.parametrize("with_image", [False, True])
def test_unknown_human_submit_retains_context_without_retry_or_image_detach(
    with_image: bool,
) -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 13))
    script: dict[str, list[Reply]] = {
        "session.create": [create_reply()],
        "prompt.submit": [Reply()],
    }
    if with_image:
        script["image.attach"] = [Reply(result={"attached": True})]
    fake = FakeGateway(script)
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
        worker_context=context,
        request_timeout=0.01,
    )

    try:
        with pytest.raises(PlannerError) as caught:
            list(
                human_observations(gateway,
                    None,
                    "t_demo",
                    "human text",
                    "message",
                    image_paths=(Path("/tmp/chat-image.png"),) if with_image else (),
                )
            )
    finally:
        gateway.shutdown()

    assert caught.value.code == ErrorCode.gateway_offline
    assert fake.sent_methods().count("prompt.submit") == 1
    assert "image.detach" not in fake.sent_methods()
    assert context.acknowledgements == []
    assert context.pending["t_demo"]


def test_shared_gateway_delivers_context_for_model_backed_command_streams_only() -> None:
    context = RecordingWorkerContext()
    for entity_id in ("t_sync_command", "t_stream_command", "t_pure_command"):
        context.set(entity_id, PendingWorkerContext("ticket_changed", "Ticket changed.", 1))
    fake = FakeGateway(
        {
            "session.create": [
                create_reply(LIVE_SID, STORED_KEY),
                create_reply(OTHER_SID, OTHER_KEY),
                create_reply("pure-sid", "pure-key"),
            ],
            "slash.exec": [
                Reply(result={"type": "skill", "message": "sync command prompt"}),
                Reply(result={"type": "send", "message": "stream command prompt"}),
                Reply(result={"output": "pure output"}),
            ],
            "prompt.submit": [
                submit_reply(complete_ev(LIVE_SID, text="sync command reply")),
                submit_reply(complete_ev(OTHER_SID, text="stream command reply")),
            ],
        }
    )
    gateway = shared(fake, context)
    try:
        first = list(human_observations(gateway, None, "t_sync_command", "/skill", "command"))
        streamed = list(human_observations(gateway, None, "t_stream_command", "/skill", "command"))
        pure = list(human_observations(gateway, None, "t_pure_command", "/status", "command"))
    finally:
        gateway.shutdown()

    submits = [
        frame["params"]["text"]
        for frame in fake.sent
        if frame["method"] == "prompt.submit"
    ]
    assert submits == [
        "sync command prompt\n\n[Pending worker context]\n- Ticket changed."
        "\n[/Pending worker context]",
        "stream command prompt\n\n[Pending worker context]\n- Ticket changed."
        "\n[/Pending worker context]",
    ]
    assert first[-1].role == "assistant"
    assert streamed[-1].role == "assistant"
    assert pure[-1].text == "pure output"
    assert context.prepare_calls == [
        ("t_sync_command", "sync command prompt"),
        ("t_stream_command", "stream command prompt"),
    ]
    assert context.pending["t_pure_command"]


@pytest.mark.parametrize("prior_session_key", [STORED_KEY, None])
def test_literal_new_stream_starts_one_bound_fresh_session_reused_by_next_message(
    prior_session_key: str | None,
) -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply(OTHER_SID, OTHER_KEY)],
            "prompt.submit": [
                submit_reply(complete_ev(OTHER_SID, text="reply from new session"))
            ],
        }
    )
    gateway = shared(fake)
    callback_keys: list[str] = []

    def record_bound_key(session_key: str) -> str:
        live_session = gateway.live_session(session_key)
        assert live_session is not None
        assert live_session.live_session_id == OTHER_SID
        callback_keys.append(session_key)
        return session_key

    try:
        chunks = list(
            human_observations(gateway,
                prior_session_key,
                "t_demo",
                "/new",
                "command",
                bind_session_key=record_bound_key,
            )
        )
        next_chunks = list(
            human_observations(gateway, OTHER_KEY, "t_demo", "hello new session", "message")
        )
    finally:
        gateway.shutdown()

    assert chunks == [
        HumanChatOutputDelta("New session started."),
        HumanChatCompletion("New session started.", "system"),
    ]
    assert callback_keys == [OTHER_KEY]
    assert next_chunks[-1].text == "reply from new session"
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    assert fake.sent[0]["params"] == {"source": CHAT_SOURCE, "cols": SESSION_COLS}
    assert fake.sent[1]["params"] == {
        "session_id": OTHER_SID,
        "text": "hello new session",
    }


def test_new_with_arguments_keeps_generic_command_stream_path() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "slash.exec": [Reply(result={"type": "exec", "output": "generic output"})],
        }
    )
    gateway = shared(fake)

    try:
        chunks = list(human_observations(gateway, None, "t_demo", "/new title", "command"))
        assert chunks[-1] == HumanChatCompletion("generic output", "system")
    finally:
        gateway.shutdown()

    assert fake.sent_methods() == ["session.create", "slash.exec"]


def test_shared_gateway_retains_context_when_prompt_submit_is_busy_or_errors() -> None:
    for error in ((4009, "busy"), (4999, "failed")):
        context = RecordingWorkerContext()
        context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 3))
        fake = FakeGateway(
            {
                "session.create": [create_reply()],
                "prompt.submit": [Reply(error=error)],
            }
        )
        gateway = shared(fake, context)
        try:
            if error[0] == 4009:
                with pytest.raises(SharedGatewayBusy, match="session busy"):
                    gateway.run_ticket_step(None, "t_demo", "worker prompt")
            else:
                result = gateway.run_ticket_step(None, "t_demo", "worker prompt")
                assert result.status == "errored"
        finally:
            gateway.shutdown()
        assert context.acknowledgements == []
        assert context.pending["t_demo"]


def test_shared_gateway_strict_existing_session_does_not_create_submit_or_consume_context() -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 3))
    fake = FakeGateway(
        {
            "session.resume": [Reply(error=(4007, "stored session not found"))],
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev())],
        }
    )
    gateway = shared(fake, context)
    try:
        result = gateway.run_ticket_step(
            STORED_KEY,
            "t_demo",
            "revision guidance",
            require_existing_session=True,
        )
    finally:
        gateway.shutdown()

    assert result.status == "errored"
    assert result.session_key == STORED_KEY
    assert fake.sent_methods() == ["session.resume"]
    assert context.prepare_calls == []
    assert context.acknowledgements == []
    assert context.pending["t_demo"]


def test_shared_gateway_strict_human_resume_does_not_create_bind_prepare_or_submit() -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 3))
    fake = FakeGateway(
        {
            "session.resume": [Reply(error=(4007, "stored session not found"))],
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev())],
        }
    )
    gateway = shared(fake, context)
    bound: list[str] = []
    try:
        with pytest.raises(PlannerError) as caught:
            list(
                gateway.run_human_turn(
                    STORED_KEY,
                    "t_demo",
                    "Panels restarted",
                    "message",
                    lambda candidate: bound.append(candidate) or candidate,
                    require_existing_session=True,
                )
            )
    finally:
        gateway.shutdown()

    assert caught.value.code is ErrorCode.gateway_offline
    assert fake.sent_methods() == ["session.resume"]
    assert bound == []
    assert context.prepare_calls == []
    assert context.acknowledgements == []
    assert context.pending["t_demo"]


def test_shared_gateway_default_resume_not_found_still_creates_and_submits() -> None:
    fake = FakeGateway(
        {
            "session.resume": [Reply(error=(4007, "stored session not found"))],
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev())],
        }
    )
    gateway = shared(fake)
    try:
        result = gateway.run_ticket_step(STORED_KEY, "t_demo", "automatic step")
    finally:
        gateway.shutdown()

    assert result.status == "complete"
    assert result.session_key == STORED_KEY
    assert fake.sent_methods() == ["session.resume", "session.create", "prompt.submit"]


def test_employee_queued_submission_owns_only_its_next_execution() -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 18))
    fake = ManualEventFake(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "running": True,
                    }
                )
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "queued"},
                    events_after=(
                        ev("message.delta", LIVE_SID, {"text": "old delta"}),
                        ev("tool.start", LIVE_SID, {"name": "old tool"}),
                        complete_ev(
                            LIVE_SID,
                            text="prior partial",
                            status="interrupted",
                        ),
                    ),
                )
            ],
        }
    )
    gateway = shared(fake, context)
    results: list[RunResult] = []
    observed: list[dict[str, Any]] = []
    run_thread = threading.Thread(
        target=lambda: results.append(
            gateway.run_ticket_step(
                STORED_KEY,
                "t_demo",
                "employee prompt",
                observed.append,
            )
        )
    )

    try:
        run_thread.start()
        assert fake.wait_sent(2, 5.0)
        time.sleep(0.05)
        assert run_thread.is_alive()
        assert observed == []
        assert context.acknowledgements == []

        fake.emit(ev("message.start", LIVE_SID))
        fake.emit(ev("message.delta", LIVE_SID, {"text": "employee"}))
        fake.emit(complete_ev(LIVE_SID, text="employee reply"))
        run_thread.join(5.0)
    finally:
        gateway.shutdown()

    assert not run_thread.is_alive()
    assert [(result.status, result.text) for result in results] == [
        ("complete", "employee reply")
    ]
    assert [event["type"] for event in observed] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert context.acknowledgements == [
        ("t_demo", (("ticket_changed", 18),))
    ]


def test_employee_streaming_submission_keeps_events_before_receipt() -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 19))
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_before=(
                        ev("message.start", LIVE_SID),
                        ev("message.delta", LIVE_SID, {"text": "fast"}),
                        complete_ev(LIVE_SID, text="fast reply"),
                    ),
                )
            ],
        }
    )
    gateway = shared(fake, context)
    observed: list[dict[str, Any]] = []

    try:
        result = gateway.run_ticket_step(
            None,
            "t_demo",
            "employee prompt",
            observed.append,
        )
    finally:
        gateway.shutdown()

    assert (result.status, result.text) == ("complete", "fast reply")
    assert [event["type"] for event in observed] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert context.acknowledgements == [
        ("t_demo", (("ticket_changed", 19),))
    ]


def test_employee_steered_submission_is_delivered_without_owning_a_terminal() -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 20))
    fake = ManualEventFake(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "running": True,
                    }
                )
            ],
            "prompt.submit": [Reply(result={"status": "steered"})],
        }
    )
    gateway = shared(fake, context)
    results: list[RunResult] = []
    observed: list[dict[str, Any]] = []
    run_thread = threading.Thread(
        target=lambda: results.append(
            gateway.run_ticket_step(
                STORED_KEY,
                "t_demo",
                "employee prompt",
                observed.append,
            )
        )
    )

    try:
        run_thread.start()
        assert fake.wait_sent(2, 5.0)
        run_thread.join(0.2)
        assert not run_thread.is_alive()

        fake.emit(complete_ev(LIVE_SID, text="unrelated active reply"))
        time.sleep(0.05)
    finally:
        gateway.shutdown()

    assert results == [
        RunResult(
            "errored",
            "",
            None,
            STORED_KEY,
            "Hermes delivered the employee prompt by steering the active execution; "
            "no independent employee execution was created",
        )
    ]
    assert observed == []
    assert context.acknowledgements == [
        ("t_demo", (("ticket_changed", 20),))
    ]


def test_employee_queued_context_is_acknowledged_only_at_owned_start() -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 21))
    fake = ManualEventFake(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "running": True,
                    }
                )
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "queued"},
                    events_after=(complete_ev(LIVE_SID, text="prior reply"),),
                )
            ],
        }
    )
    gateway = shared(fake, context)
    results: list[RunResult] = []
    run_thread = threading.Thread(
        target=lambda: results.append(
            gateway.run_ticket_step(STORED_KEY, "t_demo", "employee prompt")
        )
    )

    try:
        run_thread.start()
        assert fake.wait_sent(2, 5.0)
        time.sleep(0.05)
        assert context.acknowledgements == []

        fake.emit(ev("message.start", LIVE_SID))
        deadline = time.monotonic() + 5.0
        while not context.acknowledgements and time.monotonic() < deadline:
            time.sleep(0.01)
        assert context.acknowledgements == [
            ("t_demo", (("ticket_changed", 21),))
        ]
        fake.emit(complete_ev(LIVE_SID, text="employee reply"))
        run_thread.join(5.0)
    finally:
        gateway.shutdown()

    assert not run_thread.is_alive()
    assert [(result.status, result.text) for result in results] == [
        ("complete", "employee reply")
    ]


def test_employee_queued_child_death_before_owned_start_retains_context() -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 22))
    fake = FakeGateway(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "running": True,
                    }
                )
            ],
            "prompt.submit": [Reply(result={"status": "queued"}, die=True)],
        }
    )
    gateway = shared(fake, context)

    try:
        result = gateway.run_ticket_step(
            STORED_KEY,
            "t_demo",
            "employee prompt",
        )
    finally:
        gateway.shutdown()

    assert result.status == "errored"
    assert context.acknowledgements == []
    assert context.pending["t_demo"]
    assert fake.sent_methods().count("prompt.submit") == 1


def test_employee_unknown_submit_settles_once_without_claiming_later_terminal() -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 23))
    fake = ManualEventFake(
        {
            "session.create": [create_reply()],
            "prompt.submit": [Reply()],
        }
    )
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
        worker_context=context,
        request_timeout=0.01,
    )
    observed: list[dict[str, Any]] = []

    try:
        result = gateway.run_ticket_step(
            None,
            "t_demo",
            "employee prompt",
            observed.append,
        )
        fake.emit(complete_ev(LIVE_SID, text="later unrelated reply"))
        time.sleep(0.05)
    finally:
        gateway.shutdown()

    assert result.status == "errored"
    assert "outcome is unknown" in (result.error or "")
    assert "not retried" in (result.error or "")
    assert fake.sent_methods().count("prompt.submit") == 1
    assert context.acknowledgements == []
    assert context.pending["t_demo"]
    assert observed == []


def test_second_employee_attempt_is_busy_without_a_second_prompt_write() -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    running_snapshot = {
        "session_id": LIVE_SID,
        "resumed": STORED_KEY,
        "running": True,
    }
    fake = ManualEventFake(
        {
            "session.resume": [
                Reply(result=running_snapshot),
                Reply(result=running_snapshot),
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "queued"},
                    events_after=(complete_ev(LIVE_SID, text="prior reply"),),
                ),
                Reply(error=(BUSY_CODE, "busy")),
            ],
        }
    )
    gateway = shared(fake)
    first_results: list[RunResult] = []
    first = threading.Thread(
        target=lambda: first_results.append(
            gateway.run_ticket_step(STORED_KEY, "t_demo", "first employee prompt")
        )
    )

    try:
        first.start()
        assert fake.wait_sent(2, 5.0)
        time.sleep(0.05)

        with pytest.raises(SharedGatewayBusy):
            gateway.run_ticket_step(
                STORED_KEY,
                "t_demo",
                "second employee prompt",
            )
        assert fake.sent_methods().count("prompt.submit") == 1

        fake.emit(ev("message.start", LIVE_SID))
        fake.emit(complete_ev(LIVE_SID, text="first employee reply"))
        first.join(5.0)
    finally:
        gateway.shutdown()

    assert not first.is_alive()
    assert [(result.status, result.text) for result in first_results] == [
        ("complete", "first employee reply")
    ]


def test_shared_gateway_run_reuses_child_for_multiple_sessions() -> None:
    fake = FakeGateway(
        {
            "session.create": [
                create_reply(LIVE_SID, STORED_KEY),
                create_reply(OTHER_SID, OTHER_KEY),
            ],
            "prompt.submit": [
                submit_reply(complete_ev(LIVE_SID, text="one")),
                submit_reply(complete_ev(OTHER_SID, text="two")),
            ],
        }
    )
    gateway = shared(fake)
    one = gateway.run_ticket_step(None, "t_one", "one")
    two = gateway.run_ticket_step(None, "t_two", "two")
    assert one.text == "one"
    assert two.text == "two"
    assert fake.sent_methods() == [
        "session.create",
        "prompt.submit",
        "session.create",
        "prompt.submit",
    ]


def test_shared_gateway_interrupt_resolves_stored_key_to_live_session() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "prompt.submit": [Reply(result={"status": "streaming"})],
            "session.interrupt": [
                Reply(result={}, events_after=(complete_ev(status="interrupted"),))
            ],
        }
    )
    gateway = shared(fake)
    results: list[RunResult] = []
    turn = threading.Thread(
        target=lambda: results.append(gateway.run_ticket_step(None, "t_demo", "in flight"))
    )

    try:
        turn.start()
        assert fake.wait_sent(2, 5.0)
        gateway.interrupt(STORED_KEY, "t_demo")
        turn.join(5.0)
    finally:
        gateway.shutdown()

    interrupt = next(frame for frame in fake.sent if frame["method"] == "session.interrupt")
    assert interrupt["params"] == {"session_id": LIVE_SID}
    assert not turn.is_alive()
    assert [result.status for result in results] == ["interrupted"]
    assert results[0].session_key == STORED_KEY


def test_deadline_interrupt_does_not_spawn_missing_child() -> None:
    fake = FakeGateway({})
    gateway = shared(fake)

    with pytest.raises(PlannerError):
        gateway.interrupt(STORED_KEY, "t_demo", deadline=time.monotonic() + 1.0)

    assert fake.argv is None
    assert fake.sent == []


def test_deadline_interrupt_does_not_resume_missing_live_session() -> None:
    fake = FakeGateway({})
    gateway = shared(fake)
    gateway.start()
    try:
        with pytest.raises(PlannerError):
            gateway.interrupt(STORED_KEY, "t_demo", deadline=time.monotonic() + 1.0)
        assert fake.sent == []
    finally:
        gateway.shutdown()


@pytest.mark.parametrize("second_disposition", ["streaming", "queued"])
def test_human_stop_then_immediate_send_ignores_old_interrupted_completion(
    second_disposition: str,
) -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(ev("message.start", LIVE_SID),),
                ),
                Reply(
                    result={"status": second_disposition},
                    events_after=(
                        complete_ev(LIVE_SID, text="old partial", status="interrupted"),
                        ev("message.start", LIVE_SID),
                        ev("message.delta", LIVE_SID, {"text": "new"}),
                        complete_ev(LIVE_SID, text="new reply"),
                    ),
                ),
            ],
            "session.interrupt": [Reply(result={"interrupted": True})],
        }
    )
    gateway = shared(fake)
    first_chunks: list[Any] = []
    first = threading.Thread(
        target=lambda: first_chunks.extend(
            human_observations(gateway, None, "t_demo", "first", "message")
        )
    )

    try:
        first.start()
        assert fake.wait_sent(2, 5.0)
        gateway.interrupt(STORED_KEY, "t_demo")
        second_chunks = list(
            human_observations(gateway, STORED_KEY, "t_demo", "second", "message")
        )
        first.join(5.0)
    finally:
        gateway.shutdown()

    assert not first.is_alive()
    assert isinstance(first_chunks[-1], HumanChatCompletion)
    assert first_chunks[-1].text == "old partial"
    assert isinstance(second_chunks[-1], HumanChatCompletion)
    assert second_chunks[-1].text == "new reply"
    assert [
        chunk.text for chunk in second_chunks if isinstance(chunk, HumanChatOutputDelta)
    ] == ["new"]
    assert fake.sent_methods() == [
        "session.create",
        "prompt.submit",
        "session.interrupt",
        "prompt.submit",
    ]


def test_completed_released_human_session_reopens_through_resume() -> None:
    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "session.resume": [resume_reply(OTHER_SID, STORED_KEY)],
            "prompt.submit": [
                submit_reply(complete_ev(LIVE_SID, text="first reply")),
                submit_reply(complete_ev(OTHER_SID, text="second reply")),
            ],
        }
    )
    gateway = shared(fake)

    try:
        first = list(human_observations(gateway, None, "t_demo", "first", "message"))
        second = list(human_observations(gateway, STORED_KEY, "t_demo", "second", "message"))
    finally:
        gateway.shutdown()

    assert first[-1].text == "first reply"
    assert second[-1].text == "second reply"
    assert fake.sent_methods() == [
        "session.create",
        "prompt.submit",
        "session.resume",
        "prompt.submit",
    ]
    assert fake.sent[-1]["params"]["session_id"] == OTHER_SID


@pytest.mark.parametrize("disposition", ["streaming", "queued", "unknown"])
def test_shared_gateway_shutdown_settles_pending_employee_once_without_retry(
    disposition: str,
) -> None:
    submit = Reply() if disposition == "unknown" else Reply(result={"status": disposition})
    fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [submit],
        }
    )
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
        request_timeout=30.0,
    )
    results: list[RunResult] = []
    caller = threading.Thread(
        target=lambda: results.append(
            gateway.run_ticket_step(None, "t_shutdown", "employee prompt")
        )
    )

    caller.start()
    assert fake.wait_sent(2, 5.0)
    gateway.shutdown()
    caller.join(5.0)

    assert caller.is_alive() is False
    assert len(results) == 1
    assert results[0].status == "errored"
    assert fake.sent_methods().count("prompt.submit") == 1


def test_shared_gateway_child_shutdown_spends_one_absolute_deadline() -> None:
    child = NonExitingChild()
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=lambda _argv, _env: child,
        base_env={},
    )
    gateway.start()
    deadline = time.monotonic() + 0.05
    started = time.monotonic()

    gateway.shutdown(deadline=deadline)

    elapsed = time.monotonic() - started
    child.release_readers.set()
    assert elapsed < 0.12
    assert len(child.wait_timeouts) == 2
    assert child.wait_timeouts[0] is not None and child.wait_timeouts[0] > 0.0
    assert child.wait_timeouts[1] is not None and child.wait_timeouts[1] < 0.01


def test_live_session_shutdown_deadline_failure_signals_waiters_and_clears_closing() -> None:
    fake = FakeGateway({})
    child = GatewayChild(HERMES_PY, {}, spawn=fake.spawn)
    child.wait_ready()
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    state = session._state
    state.command_lock.acquire()
    first_errors: list[GatewayError] = []

    try:
        def first_shutdown() -> None:
            try:
                manager.shutdown(deadline=time.monotonic() + 0.05)
            except GatewayError as exc:
                first_errors.append(exc)

        first = threading.Thread(target=first_shutdown)
        first.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with manager._lock:
                closing = manager._closing
            if closing:
                break
            time.sleep(0.01)

        with pytest.raises(GatewayError, match="command lock"):
            manager.shutdown(deadline=time.monotonic() + 1.0)

        first.join(1.0)
        assert not first.is_alive()
        assert len(first_errors) == 1
        with manager._lock:
            assert manager._closing is False
            assert manager._shutdown_error is first_errors[0]
        assert manager._shutdown_complete.is_set()
    finally:
        state.command_lock.release()
        try:
            manager.shutdown()
        except GatewayError:
            pass
        child.shutdown()


def test_role_gateway_child_death_does_not_stop_sibling_role() -> None:
    worker_fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [Reply(result={"status": "streaming"})],
        }
    )
    chief_fake = FakeGateway(
        {
            "session.create": [create_reply(OTHER_SID, OTHER_KEY)],
            "prompt.submit": [submit_reply(complete_ev(OTHER_SID, text="chief reply"))],
        }
    )
    worker = shared(worker_fake)
    chief = SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-chief-of-staff",
        spawn=chief_fake.spawn,
        base_env={},
    )
    worker_results: list[RunResult] = []
    worker_call = threading.Thread(
        target=lambda: worker_results.append(
            worker.run_ticket_step(None, "t_worker", "employee prompt")
        )
    )

    try:
        worker_call.start()
        assert worker_fake.wait_sent(2, 5.0)
        worker_fake.kill()
        worker_call.join(5.0)
        chief_result = list(
            human_observations(chief, None, "chief-of-staff", "still there?", "message")
        )
    finally:
        worker.shutdown()
        chief.shutdown()

    assert worker_call.is_alive() is False
    assert len(worker_results) == 1
    assert worker_results[0].status == "errored"
    assert worker_fake.sent_methods().count("prompt.submit") == 1
    assert chief_result[-1].text == "chief reply"
    assert chief_fake.sent_methods() == ["session.create", "prompt.submit"]


def test_new_gateway_resumes_stored_session_without_replaying_prior_input() -> None:
    first_fake = FakeGateway(
        {
            "session.create": [create_reply()],
            "prompt.submit": [submit_reply(complete_ev(text="first reply"))],
        }
    )
    first_gateway = shared(first_fake)
    list(human_observations(first_gateway, None, "t_demo", "first input", "message"))
    first_gateway.shutdown()

    second_fake = FakeGateway(
        {
            "session.resume": [resume_reply(OTHER_SID, STORED_KEY)],
            "prompt.submit": [submit_reply(complete_ev(OTHER_SID, text="second reply"))],
        }
    )
    second_gateway = shared(second_fake)
    try:
        second_result = list(human_observations(second_gateway,
            STORED_KEY,
            "t_demo",
            "second input",
            "message",
        ))
    finally:
        second_gateway.shutdown()

    assert second_result[-1].text == "second reply"
    assert second_fake.sent_methods() == ["session.resume", "prompt.submit"]
    assert [
        frame["params"]["text"]
        for frame in second_fake.sent
        if frame["method"] == "prompt.submit"
    ] == ["second input"]


def test_two_employee_sessions_share_one_child_without_cross_settlement() -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    fake = ManualEventFake(
        {
            "session.create": [
                create_reply(LIVE_SID, STORED_KEY),
                create_reply(OTHER_SID, OTHER_KEY),
            ],
            "prompt.submit": [
                Reply(result={"status": "streaming"}),
                Reply(result={"status": "streaming"}),
            ],
        }
    )
    gateway = shared(fake)
    results: dict[str, RunResult] = {}
    observed: dict[str, list[dict[str, Any]]] = {"one": [], "two": []}
    one = threading.Thread(
        target=lambda: results.setdefault(
            "one",
            gateway.run_ticket_step(None, "t_one", "one", observed["one"].append),
        )
    )
    two = threading.Thread(
        target=lambda: results.setdefault(
            "two",
            gateway.run_ticket_step(None, "t_two", "two", observed["two"].append),
        )
    )

    try:
        one.start()
        assert fake.wait_sent(2, 5.0)
        two.start()
        assert fake.wait_sent(4, 5.0)
        fake.emit(ev("message.start", OTHER_SID))
        fake.emit(ev("message.start", LIVE_SID))
        fake.emit(complete_ev(OTHER_SID, text="two reply"))
        fake.emit(complete_ev(LIVE_SID, text="one reply"))
        one.join(5.0)
        two.join(5.0)
    finally:
        gateway.shutdown()

    assert one.is_alive() is False
    assert two.is_alive() is False
    assert results["one"].text == "one reply"
    assert results["two"].text == "two reply"
    assert {event["session_id"] for event in observed["one"]} == {LIVE_SID}
    assert {event["session_id"] for event in observed["two"]} == {OTHER_SID}
    assert fake.sent_methods().count("prompt.submit") == 2


def test_initial_binding_winner_is_the_actual_hermes_session() -> None:
    image_path = Path("/tmp/winner-chat-image.png")
    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "session.resume": [resume_reply(WINNER_SID, WINNER_KEY)],
            "image.attach": [Reply(result={"attached": True})],
            "prompt.submit": [
                submit_reply(complete_ev(WINNER_SID, text="winner reply"))
            ],
        }
    )
    gateway = shared(fake)
    callback_keys: list[str] = []

    def choose_database_winner(candidate: str) -> str:
        callback_keys.append(candidate)
        return WINNER_KEY if candidate == STORED_KEY else candidate

    try:
        observations = list(
            human_observations(
                gateway,
                None,
                "t_demo",
                "describe",
                "message",
                bind_session_key=choose_database_winner,
                image_paths=(image_path,),
            )
        )
    finally:
        gateway.shutdown()

    assert observations[-1] == HumanChatCompletion("winner reply", "assistant")
    assert callback_keys == [STORED_KEY, WINNER_KEY, WINNER_KEY]
    assert fake.sent_methods() == [
        "session.create",
        "session.resume",
        "image.attach",
        "prompt.submit",
    ]
    assert fake.sent[2]["params"] == {
        "session_id": WINNER_SID,
        "path": str(image_path),
    }
    assert fake.sent[3]["params"] == {
        "session_id": WINNER_SID,
        "text": "describe",
    }


@pytest.mark.parametrize("operation", ["message", "image", "command", "alias"])
def test_dormant_retry_uses_database_winner_as_actual_hermes_session(
    operation: str,
) -> None:
    script: dict[str, list[Reply]] = {
        "session.create": [create_reply(LIVE_SID, STORED_KEY)],
        "session.resume": [
            resume_reply(OTHER_SID, OTHER_KEY),
            resume_reply(WINNER_SID, WINNER_KEY),
        ],
        "prompt.submit": [
            Reply(
                result={"status": "streaming"},
                events_after=(ev("message.start", LIVE_SID),),
            ),
            submit_reply(complete_ev(WINNER_SID, text="second reply")),
        ],
        "session.interrupt": [
            Reply(
                result={"interrupted": True},
                events_after=(
                    complete_ev(LIVE_SID, text="first partial", status="interrupted"),
                ),
            )
        ],
    }
    if operation == "command":
        script["slash.exec"] = [
            Reply(result={"type": "skill", "message": "second command"})
        ]
    elif operation == "alias":
        script["slash.exec"] = [
            Reply(result={"type": "alias", "target": "/skill base"})
        ]
        script["command.dispatch"] = [
            Reply(result={"type": "skill", "message": "second alias command"})
        ]
    elif operation == "image":
        script["image.attach"] = [Reply(result={"attached": True})]
    fake = FakeGateway(script)
    gateway = shared(fake)
    first_chunks: list[Any] = []
    second_session_keys: list[str] = []
    initial_binding_seen = threading.Event()
    allow_second_operation = threading.Event()

    def bind_second_session(candidate: str) -> str:
        second_session_keys.append(candidate)
        if candidate == STORED_KEY and len(second_session_keys) == 1:
            initial_binding_seen.set()
            assert allow_second_operation.wait(5.0)
        return WINNER_KEY if candidate == OTHER_KEY else candidate

    first = threading.Thread(
        target=lambda: first_chunks.extend(
            human_observations(gateway, None, "t_demo", "first", "message")
        )
    )
    second_chunks: list[HumanChatObservation] = []
    second = threading.Thread(
        target=lambda: second_chunks.extend(
            human_observations(
                gateway,
                STORED_KEY,
                "t_demo",
                (
                    "/skill"
                    if operation == "command"
                    else "/alias arg"
                    if operation == "alias"
                    else "second"
                ),
                "command" if operation in ("command", "alias") else "message",
                bind_session_key=bind_second_session,
                image_paths=(Path("/tmp/dormant-winner.png"),)
                if operation == "image"
                else (),
            )
        )
    )

    try:
        first.start()
        assert fake.wait_sent(2, 5.0)
        second.start()
        assert initial_binding_seen.wait(5.0)
        gateway.interrupt(STORED_KEY, "t_demo")
        first.join(5.0)
        assert not first.is_alive()
        assert gateway.live_session(STORED_KEY) is None
        allow_second_operation.set()
        second.join(5.0)
    finally:
        allow_second_operation.set()
        gateway.shutdown()

    assert first_chunks[-1].text == "first partial"
    assert not second.is_alive()
    assert second_chunks[-1].text == "second reply"
    assert second_session_keys == [STORED_KEY, OTHER_KEY, WINNER_KEY]
    expected_methods = [
        "session.create",
        "prompt.submit",
        "session.interrupt",
        "session.resume",
        "session.resume",
    ]
    if operation in ("command", "alias"):
        expected_methods.append("slash.exec")
    if operation == "alias":
        expected_methods.append("command.dispatch")
    if operation == "image":
        expected_methods.append("image.attach")
    expected_methods.append("prompt.submit")
    assert fake.sent_methods() == expected_methods
    human_write_methods = {"slash.exec", "command.dispatch", "image.attach", "prompt.submit"}
    second_write_frames = [
        frame
        for frame in fake.sent[5:]
        if frame["method"] in human_write_methods
    ]
    assert second_write_frames
    assert {frame["params"]["session_id"] for frame in second_write_frames} == {
        WINNER_SID
    }


def test_stream_retry_persists_rotated_key_after_prepare_time_detach() -> None:
    second_prepare_entered = threading.Event()
    allow_second_prepare = threading.Event()

    class BlockingSecondPrepareContext(RecordingWorkerContext):
        def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt:
            if prompt_text == "second":
                second_prepare_entered.set()
                assert allow_second_prepare.wait(5.0)
            return super().prepare(worker_entity_id, prompt_text)

    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "session.resume": [resume_reply(OTHER_SID, OTHER_KEY)],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(ev("message.start", LIVE_SID),),
                ),
                submit_reply(complete_ev(OTHER_SID, text="second reply")),
            ],
            "session.interrupt": [
                Reply(
                    result={"interrupted": True},
                    events_after=(
                        complete_ev(LIVE_SID, text="first partial", status="interrupted"),
                    ),
                )
            ],
        }
    )
    gateway = shared(fake, BlockingSecondPrepareContext())
    first_chunks: list[Any] = []
    second_results: list[HumanChatObservation] = []
    second_session_keys: list[str] = []

    def bind_second_session(candidate: str) -> str:
        second_session_keys.append(candidate)
        return candidate
    first = threading.Thread(
        target=lambda: first_chunks.extend(
            human_observations(gateway, None, "t_demo", "first", "message")
        )
    )
    second = threading.Thread(
        target=lambda: second_results.extend(
            human_observations(gateway,
                STORED_KEY,
                "t_demo",
                "second",
                "message",
                bind_session_key=bind_second_session,
            )
        )
    )

    try:
        first.start()
        assert fake.wait_sent(2, 5.0)
        second.start()
        assert second_prepare_entered.wait(5.0)
        gateway.interrupt(STORED_KEY, "t_demo")
        first.join(5.0)
        assert not first.is_alive()
        assert gateway.live_session(STORED_KEY) is None
        allow_second_prepare.set()
        second.join(5.0)
    finally:
        allow_second_prepare.set()
        gateway.shutdown()

    assert not second.is_alive()
    assert second_results[-1] == HumanChatCompletion("second reply", "assistant")
    assert second_session_keys == [STORED_KEY, OTHER_KEY]
    assert fake.sent_methods() == [
        "session.create",
        "prompt.submit",
        "session.interrupt",
        "session.resume",
        "prompt.submit",
    ]


@pytest.mark.parametrize("alias", [False, True])
def test_model_command_to_derived_prompt_write_is_one_ordered_operation(
    alias: bool,
) -> None:
    prepare_entered = threading.Event()
    allow_prepare = threading.Event()

    class BlockingCommandContext(RecordingWorkerContext):
        def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt:
            if prompt_text == "command model text":
                prepare_entered.set()
                assert allow_prepare.wait(5.0)
            return super().prepare(worker_entity_id, prompt_text)

    context = BlockingCommandContext()
    slash_result = (
        {"type": "alias", "target": "/skill base"}
        if alias
        else {"type": "skill", "message": "command model text"}
    )
    script: dict[str, list[Reply]] = {
        "session.create": [create_reply(LIVE_SID, STORED_KEY)],
        "slash.exec": [Reply(result=slash_result)],
        "prompt.submit": [
            submit_reply(complete_ev(LIVE_SID, text="command reply")),
            submit_reply(complete_ev(LIVE_SID, text="message reply")),
        ],
    }
    if alias:
        script["command.dispatch"] = [
            Reply(result={"type": "skill", "message": "command model text"})
        ]
    fake = FakeGateway(script)
    gateway = shared(fake, context)
    command_results: list[Any] = []
    message_results: list[Any] = []

    def run_command_call() -> None:
        command_results.extend(
            human_observations(gateway, None, "t_demo", "/alias arg", "command")
        )

    command_thread = threading.Thread(target=run_command_call)
    message_thread = threading.Thread(
        target=lambda: message_results.extend(
            human_observations(gateway, STORED_KEY, "t_demo", "human B", "message")
        )
    )
    try:
        command_thread.start()
        assert prepare_entered.wait(5.0)
        message_thread.start()
        interleaved_before_command_prompt = False
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            if any(frame["method"] == "prompt.submit" for frame in fake.sent):
                interleaved_before_command_prompt = True
                break
            threading.Event().wait(0.01)
        allow_prepare.set()
        command_thread.join(5.0)
        message_thread.join(5.0)
    finally:
        allow_prepare.set()
        gateway.shutdown()

    assert interleaved_before_command_prompt is False
    assert not command_thread.is_alive()
    assert not message_thread.is_alive()
    prompt_texts = [
        frame["params"]["text"]
        for frame in fake.sent
        if frame["method"] == "prompt.submit"
    ]
    assert prompt_texts == ["command model text", "human B"]
    assert command_results
    assert message_results[-1].text == "message reply"


def test_shared_gateway_interrupt_maps_live_session_not_found_to_not_found() -> None:
    fake = FakeGateway({"session.interrupt": [Reply(error=(4001, "session not found"))]})
    gateway = shared(fake)

    try:
        with pytest.raises(PlannerError) as caught:
            gateway.interrupt(STORED_KEY, "t_demo")
    finally:
        gateway.shutdown()

    assert caught.value.code == ErrorCode.not_found


def test_shared_gateway_attaches_images_on_live_session_before_prompt_submit() -> None:
    image_paths = (Path("/tmp/first-chat-image.png"), Path("/tmp/second-chat-image.png"))
    fake = FakeGateway(
        {
            "session.resume": [resume_reply(LIVE_SID, STORED_KEY)],
            "image.attach": [
                Reply(result={"attached": True}),
                Reply(result={"attached": True}),
            ],
            "prompt.submit": [submit_reply(complete_ev(LIVE_SID, text="seen"))],
        }
    )
    gateway = shared(fake)

    try:
        chunks = list(
            human_observations(gateway,
                STORED_KEY,
                "t_demo",
                "describe it",
                "message",
                image_paths=image_paths,
            )
        )
    finally:
        gateway.shutdown()

    assert chunks[-1].text == "seen"
    assert fake.sent_methods() == [
        "session.resume",
        "image.attach",
        "image.attach",
        "prompt.submit",
    ]
    assert fake.sent[1]["params"] == {"session_id": LIVE_SID, "path": str(image_paths[0])}
    assert fake.sent[2]["params"] == {"session_id": LIVE_SID, "path": str(image_paths[1])}
    assert fake.sent[3]["params"] == {"session_id": LIVE_SID, "text": "describe it"}


def test_shared_gateway_attach_failure_does_not_submit_prompt() -> None:
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 9))
    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "image.attach": [
                Reply(result={"attached": True}),
                Reply(error=(4000, "bad image")),
            ],
            "image.detach": [Reply(result={"detached": True})],
            "prompt.submit": [submit_reply(complete_ev(LIVE_SID, text="must not run"))],
        }
    )
    gateway = shared(fake, context)

    try:
        with pytest.raises(PlannerError) as caught:
            list(
                human_observations(gateway,
                    None,
                    "t_demo",
                    "describe it",
                    "message",
                    image_paths=(Path("/tmp/first-chat-image.png"), Path("/tmp/bad-image.png")),
                )
            )
    finally:
        gateway.shutdown()

    assert caught.value.code == ErrorCode.gateway_offline
    assert fake.sent_methods() == [
        "session.create",
        "image.attach",
        "image.attach",
        "image.detach",
    ]
    assert context.acknowledgements == []
    assert context.pending["t_demo"]


def test_shared_gateway_detaches_image_when_prompt_submit_fails_before_next_turn() -> None:
    image_paths = (Path("/tmp/first-chat-image.png"), Path("/tmp/second-chat-image.png"))
    context = RecordingWorkerContext()
    context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 10))
    fake = FakeGateway(
        {
            "session.create": [create_reply(LIVE_SID, STORED_KEY)],
            "session.resume": [resume_reply(LIVE_SID, STORED_KEY)],
            "image.attach": [
                Reply(result={"attached": True}),
                Reply(result={"attached": True}),
            ],
            "image.detach": [
                Reply(result={"detached": True}),
                Reply(result={"detached": True}),
            ],
            "prompt.submit": [
                Reply(error=(4000, "submit failed")),
                submit_reply(complete_ev(LIVE_SID, text="clean next turn")),
            ],
        }
    )
    gateway = shared(fake, context)

    try:
        with pytest.raises(PlannerError) as caught:
            list(
                human_observations(gateway,
                    None,
                    "t_demo",
                    "first turn",
                    "message",
                    image_paths=image_paths,
                )
            )
        next_chunks = list(
            human_observations(
                gateway, STORED_KEY, "t_demo", "next turn", "message"
            )
        )
    finally:
        gateway.shutdown()

    assert caught.value.code == ErrorCode.gateway_offline
    assert next_chunks[-1].text == "clean next turn"
    assert fake.sent_methods() == [
        "session.create",
        "image.attach",
        "image.attach",
        "prompt.submit",
        "image.detach",
        "image.detach",
        "prompt.submit",
    ]
    assert fake.sent[4]["params"] == {"session_id": LIVE_SID, "path": str(image_paths[0])}
    assert fake.sent[5]["params"] == {"session_id": LIVE_SID, "path": str(image_paths[1])}
    submit_texts = [
        frame["params"]["text"]
        for frame in fake.sent
        if frame["method"] == "prompt.submit"
    ]
    expected_suffix = (
        "\n\n[Pending worker context]\n- Ticket changed.\n[/Pending worker context]"
    )
    assert submit_texts == [f"first turn{expected_suffix}", f"next turn{expected_suffix}"]
    assert context.acknowledgements == [("t_demo", (("ticket_changed", 10),))]
    assert context.pending["t_demo"] == ()


def test_resolve_hermes_python_precedence() -> None:
    default = Path("~/.hermes/hermes-agent/venv/bin/python").expanduser()
    assert resolve_hermes_python(None, env={}) == default
    explicit = resolve_hermes_python("/opt/py", env={"PLAN_HERMES_PYTHON": "/env/py"})
    assert explicit == Path("/opt/py")
    from_env = resolve_hermes_python(None, env={"PLAN_HERMES_PYTHON": "~/envpy"})
    assert from_env == Path("~/envpy").expanduser()


def test_hermes_src_root() -> None:
    assert hermes_src_root(Path("/x/hermes-agent/venv/bin/python")) == Path("/x/hermes-agent")
    assert hermes_src_root(Path("/python")) == Path("/")


def test_resolve_planner_home() -> None:
    assert resolve_planner_home(None, env={}) == Path("data/hermes-home")
    assert resolve_planner_home(None, env={}, default="/database/hermes-home") == Path(
        "/database/hermes-home"
    )
    assert resolve_planner_home(
        None,
        env={"PLAN_HERMES_HOME": "/env"},
        default="/database/hermes-home",
    ) == Path("/env")
    assert resolve_planner_home(
        "/explicit",
        env={"PLAN_HERMES_HOME": "/env"},
        default="/database/hermes-home",
    ) == Path("/explicit")
    expanded = resolve_planner_home(None, env={"PLAN_HERMES_HOME": "~/homey"})
    assert expanded == Path("~/homey").expanduser()


def test_provision_planner_home_skills_symlinks_repo_skills(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path)

    panels = tmp_path / "skills" / "panels"
    worker = tmp_path / "skills" / "panels-worker"
    chief = tmp_path / "skills" / "panels-chief-of-staff"
    sprint_planning = tmp_path / "skills" / "panels-sprint-planning"
    assert panels.is_symlink()
    assert worker.is_symlink()
    assert chief.is_symlink()
    assert sprint_planning.is_symlink()
    assert (panels / "SKILL.md").exists()
    assert (worker / "SKILL.md").exists()
    assert (chief / "SKILL.md").exists()
    assert (sprint_planning / "SKILL.md").exists()


def test_provisioned_sprint_planning_skill_is_review_first_and_panels_native(
    tmp_path: Path,
) -> None:
    provision_planner_home_skills(tmp_path)
    skill = (
        tmp_path / "skills" / "panels-sprint-planning" / "SKILL.md"
    ).read_text(encoding="utf-8")

    review_heading = "## Phase 1 — review the current sprint"
    planning_heading = "## Phase 2 — decide and build the next sprint"
    assert skill.index(review_heading) < skill.index(planning_heading)
    for review_field in (
        "outcomes",
        "solo-reflection",
        "joint-discussion",
        "updates-to-thinking",
        "carry-forward",
    ):
        assert f"panels sprint set <sprint-id> {review_field}" in skill
    for command in (
        "panels sprint list --json",
        "panels ticket list --sprint-item <item-id> --json",
        "panels sprint create",
        "panels sprint item create",
        "panels sprint item set",
    ):
        assert command in skill

    readback_rule = (
        "After every accepted write, read the affected sprint or sprint item back"
    )
    assert readback_rule in skill

    assert "sprint-tracking.md" not in skill
    assert "panels day add-ticket" not in skill
    assert "non-dropped child tickets and open blocking links" in skill
    assert "Do not run sprint planning autonomously from a cron" in skill


def test_panels_rollover_is_provisioned_as_readable_repo_skill(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path)

    rollover = tmp_path / "skills" / "panels-rollover"
    assert rollover.is_symlink()
    skill_text = (rollover / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text.startswith("---\nname: panels-rollover")


def test_panels_rollover_contract_keeps_automatic_ticket_changes_pending_agreement(
    tmp_path: Path,
) -> None:
    provision_planner_home_skills(tmp_path)
    rollover = (tmp_path / "skills" / "panels-rollover" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Write a likely direction into today's overview" in rollover
    assert "do not add tickets to today yet" in rollover
    assert "After the user agrees" in rollover
    assert "**Morning:** prepare the kickoff draft if it is missing" in rollover
    assert "**Afternoon:** act only as a failsafe" in rollover
    assert "otherwise no-op" in rollover
    assert "Scheduled runs never add tickets to today without the user's agreement" in rollover
    assert "`PLAN_ACTOR=chief`" in rollover
    assert "limited to the approved kickoff draft" in rollover
    assert "Never carry `done` or `dropped` tickets forward" in rollover


def test_panels_rollover_replaces_stale_planning_boundary_skill() -> None:
    skills = Path(__file__).resolve().parents[2] / "skills"

    assert not (skills / "planning-boundary.md").exists()
    for role in ("panels", "panels-chief-of-staff"):
        text = (skills / role / "SKILL.md").read_text(encoding="utf-8")
        assert "panels-rollover" in text
        assert "planning-boundary" not in text


def test_provisioned_skills_encode_implementation_and_closeout_lifecycle(
    tmp_path: Path,
) -> None:
    provision_planner_home_skills(tmp_path)
    skills = tmp_path / "skills"
    panels = (skills / "panels" / "SKILL.md").read_text(encoding="utf-8")
    worker = (skills / "panels-worker" / "SKILL.md").read_text(encoding="utf-8")
    coding_worker = (skills / "panels-worker-coding" / "SKILL.md").read_text(encoding="utf-8")
    chief = (skills / "panels-chief-of-staff" / "SKILL.md").read_text(encoding="utf-8")

    # No provisioned role prompt may still name the retired lifecycle states.
    for text in (panels, worker, coding_worker, chief):
        assert "in_progress" not in text
        assert "needs_review" not in text

    # t_tt05: the coding stage catalogue moved OUT of the base worker skill INTO the
    # coding specialist. The visible six-stage sequence, its five gated fields, the
    # Implementation/Closeout responsibilities, and the closeout duties now live there.
    sequence = "Success → Approach → Plan → Implementation → Closeout → Done"
    assert sequence in coding_worker
    for field in ("success", "approach", "plan", "implementation", "closeout"):
        assert field in coding_worker
    assert "needs_implementation" in coding_worker
    assert "needs_closeout" in coding_worker
    assert "reviewable" in coding_worker
    for closeout_duty in ("merge", "deploy", "follow-up", "bookkeeping"):
        assert closeout_duty in coding_worker

    # The base worker skill is type-agnostic: the extracted coding content is gone,
    # and the self-routing anchors are present. (Narrowed honestly per Codex F5: the
    # retained "Keep proposal shapes predictable" bullet still names the coding stages,
    # so this targets the EXTRACTED ids, not "zero coding words".)
    assert sequence not in worker
    assert "needs_implementation" not in worker
    assert "needs_closeout" not in worker
    assert "panels worker my-ticket" in worker
    assert "skill_view" in worker
    assert "you handle the one current step only." in worker
    assert "Never invoke " in worker and "panels chief" in worker

    # panels lists the five canonical outputs, not the retired `result` field.
    assert "plan, result" not in panels
    assert "implementation" in panels
    assert "closeout" in panels
    assert sequence in panels

    # Chief keeps its planning-draft boundary on the new field names only.
    assert "`result`" not in chief
    assert "implementation" in chief
    assert "closeout" in chief


def test_boot_smoke_check_with_fake() -> None:
    fake = FakeGateway({})
    boot_smoke_check(Path(HERMES_PY), spawn=fake.spawn, env={})
    assert fake.closed is True
    assert fake.env is not None
    assert fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"
    assert fake.argv == [HERMES_PY, "-m", "tui_gateway.entry"]


def test_boot_smoke_check_ready_timeout() -> None:
    fake = FakeGateway({}, ready=False)
    with pytest.raises(GatewayError):
        boot_smoke_check(Path(HERMES_PY), spawn=fake.spawn, ready_timeout=0.2, env={})
    assert fake.closed is True
