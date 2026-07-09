"""Hermetic tests for GatewayChild routing, run_step, config, and SharedGateway."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

import planner.minds as minds
from planner.chat.contracts import ChatHistory, ChatSendResult, CommandCatalog, GatewayStatus
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
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
from planner.minds.shared_gateway import CHAT_SOURCE, SESSION_COLS, EntityRoutingGateway, SharedGateway

LIVE_SID = "ab12cd34"
OTHER_SID = "ff00ff00"
STORED_KEY = "20260706_120000_abcdef"
OTHER_KEY = "20260706_120000_999999"
HERMES_PY = "/x/hermes-agent/venv/bin/python"


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


def shared(fake: FakeGateway) -> SharedGateway:
    return SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
    )


class RecordingChatGateway:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def status(self) -> GatewayStatus:
        self.calls.append(("status", ""))
        return GatewayStatus(available=True)

    def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
        self.calls.append(("history", entity_id))
        return ChatHistory(messages=(), session_key=session_key)

    def send(self, session_key: str | None, entity_id: str, text: str, on_session_key=None):
        self.calls.append(("send", entity_id))
        return ChatSendResult(reply_text=f"{self.name}: {text}", session_key=f"{self.name}-key")

    def stream(self, session_key: str | None, entity_id: str, text: str, mode: str, on_session_key=None):
        self.calls.append(("stream", entity_id))
        yield from ()

    def catalog(self) -> CommandCatalog:
        self.calls.append(("catalog", ""))
        return CommandCatalog(categories=(), skills=(), canon={}, sub={})

    def run_command(self, session_key: str | None, entity_id: str, command: str, on_session_key=None):
        self.calls.append(("run_command", entity_id))
        return ChatSendResult(reply_text=f"{self.name}: {command}", session_key=f"{self.name}-key")

    def shutdown(self) -> None:
        self.closed = True


def test_entity_routing_gateway_sends_chief_entity_to_chief_gateway() -> None:
    worker = RecordingChatGateway("worker")
    chief = RecordingChatGateway("chief")
    gateway = EntityRoutingGateway(
        worker,  # type: ignore[arg-type]
        {CHIEF_OF_STAFF_ENTITY_ID: chief},  # type: ignore[dict-item]
    )

    chief_result = gateway.send(None, CHIEF_OF_STAFF_ENTITY_ID, "hello")
    ticket_result = gateway.send(None, "t_demo", "hello")
    gateway.status_for_entity(CHIEF_OF_STAFF_ENTITY_ID)

    assert chief_result.reply_text == "chief: hello"
    assert ticket_result.reply_text == "worker: hello"
    assert chief.calls == [("send", CHIEF_OF_STAFF_ENTITY_ID), ("status", "")]
    assert worker.calls == [("send", "t_demo")]


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


def test_gateway_events_demux_by_session_id() -> None:
    fake = FakeGateway(
        {
            "go": [
                Reply(
                    result={},
                    events_after=(
                        complete_ev(OTHER_SID, text="other"),
                        complete_ev(LIVE_SID, text="live"),
                    ),
                )
            ]
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    live = child.open_session_events(LIVE_SID)
    other = child.open_session_events(OTHER_SID)
    assert child.request("go", timeout=5.0) == {}
    assert live.next_event(timeout=5.0)["payload"]["text"] == "live"
    assert other.next_event(timeout=5.0)["payload"]["text"] == "other"
    live.close()
    other.close()
    child.shutdown()


def test_gateway_concurrent_session_drains_do_not_steal_events() -> None:
    fake = FakeGateway(
        {
            "go": [
                Reply(
                    result={},
                    events_after=(
                        complete_ev(OTHER_SID, text="other"),
                        complete_ev(LIVE_SID, text="live"),
                    ),
                )
            ]
        }
    )
    child = gw(fake)
    child.wait_ready(5.0)
    live = child.open_session_events(LIVE_SID)
    other = child.open_session_events(OTHER_SID)
    seen: dict[str, str] = {}

    def drain(name: str, sid_events: Any) -> None:
        event = sid_events.next_event(timeout=5.0)
        seen[name] = str(event["payload"]["text"])

    t1 = threading.Thread(target=drain, args=("live", live))
    t2 = threading.Thread(target=drain, args=("other", other))
    t1.start()
    t2.start()
    child.request("go", timeout=5.0)
    t1.join(5.0)
    t2.join(5.0)
    assert seen == {"live": "live", "other": "other"}
    live.close()
    other.close()
    child.shutdown()


def test_gateway_process_events_do_not_enter_session_drains() -> None:
    fake = FakeGateway({"go": [Reply(result={}, events_after=(ev("notice", None, {"x": 1}),))]})
    child = gw(fake)
    child.wait_ready(5.0)
    live = child.open_session_events(LIVE_SID)
    child.request("go", timeout=5.0)
    assert child.next_process_event(timeout=5.0)["type"] == "notice"
    with pytest.raises(GatewayError, match="no gateway event"):
        live.next_event(timeout=0.1)
    live.close()
    child.shutdown()


def test_gateway_child_death_wakes_all_session_drainers() -> None:
    fake = FakeGateway({"boom": [Reply(die=True)]}, stderr_lines=("traceback: kaboom",))
    child = gw(fake)
    child.wait_ready(5.0)
    live = child.open_session_events(LIVE_SID)
    other = child.open_session_events(OTHER_SID)
    with pytest.raises(GatewayError, match="died"):
        child.request("boom", timeout=5.0)
    assert live.next_event(timeout=5.0) is None
    assert other.next_event(timeout=5.0) is None
    child.shutdown()
    assert child.stderr_tail() == ["traceback: kaboom"]
    assert child.alive is False


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


def test_shared_gateway_history_resumes_and_preserves_full_trace() -> None:
    fake = FakeGateway(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": "20260708_090000_rotated",
                        "messages": [
                            {"role": "user", "content": "human asks", "created_at": 10},
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
        history = gateway.history(STORED_KEY, "t_demo")
    finally:
        gateway.shutdown()

    assert fake.sent_methods() == ["session.resume"]
    assert fake.sent[0]["params"] == {
        "session_id": STORED_KEY,
        "cols": SESSION_COLS,
        "lazy": True,
        "source": CHAT_SOURCE,
    }
    assert history.session_key == "20260708_090000_rotated"
    assert [(msg.role, msg.text, msg.created_at) for msg in history.messages] == [
        ("user", "human asks", 10),
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
    one = gateway.run_ticket_step(None, "one")
    two = gateway.run_ticket_step(None, "two")
    assert one.text == "one"
    assert two.text == "two"
    assert fake.sent_methods() == [
        "session.create",
        "prompt.submit",
        "session.create",
        "prompt.submit",
    ]


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
    assert resolve_planner_home("/explicit", env={"PLAN_HERMES_HOME": "/env"}) == Path("/explicit")
    assert resolve_planner_home(None, env={"PLAN_HERMES_HOME": "/env"}) == Path("/env")
    expanded = resolve_planner_home(None, env={"PLAN_HERMES_HOME": "~/homey"})
    assert expanded == Path("~/homey").expanduser()


def test_provision_planner_home_skills_symlinks_repo_skills(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path)

    panels = tmp_path / "skills" / "panels"
    worker = tmp_path / "skills" / "panels-worker"
    chief = tmp_path / "skills" / "panels-chief-of-staff"
    assert panels.is_symlink()
    assert worker.is_symlink()
    assert chief.is_symlink()
    assert (panels / "SKILL.md").exists()
    assert (worker / "SKILL.md").exists()
    assert (chief / "SKILL.md").exists()


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
