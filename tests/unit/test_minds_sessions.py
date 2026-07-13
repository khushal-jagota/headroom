"""Contract tests for the ordered live Hermes session boundary."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from planner.minds.contracts import InterruptReceipt, SubmissionReceipt, TransportUnknown
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import (
    GatewayChild,
    GatewayError,
    GatewayRequestHandle,
    GatewayRpcError,
)
from planner.minds.sessions import AcceptedSubmission, LiveSessionManager, PendingSubmission
from planner.minds.shared_gateway import SharedGateway

HERMES_PY = "/x/hermes-agent/venv/bin/python"
LIVE_SID = "ab12cd34"
OTHER_SID = "ff00ff00"
STORED_KEY = "20260706_120000_abcdef"
OTHER_KEY = "20260706_120000_999999"


def _child(fake: FakeGateway) -> GatewayChild:
    child = GatewayChild(HERMES_PY, {}, spawn=fake.spawn)
    child.wait_ready(5.0)
    return child


def _shared(fake: FakeGateway) -> SharedGateway:
    return SharedGateway(
        hermes_python=HERMES_PY,
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
    )


def _event(type_: str, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    return ev(type_, sid, payload)


@pytest.mark.parametrize("second_disposition", ["streaming", "queued"])
def test_submission_consequences_isolate_late_interrupted_completion_from_next_prompt(
    second_disposition: str,
) -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(_event("message.start", LIVE_SID, {}),),
                ),
                Reply(result={"status": second_disposition}),
            ],
            "session.interrupt": [Reply(result={"interrupted": True})],
            "late-old-completion": [
                Reply(
                    result={},
                    events_after=(
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "interrupted", "text": "old partial"},
                        ),
                    ),
                )
            ],
            "next-lifecycle": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "new"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "new reply"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)

    first = session.submit_consequence("first", timeout=5.0)
    assert isinstance(first, AcceptedSubmission)
    assert first.consequence.next_observation(5.0).event_type == "message.start"
    assert session.interrupt(timeout=5.0) == InterruptReceipt({"interrupted": True})
    second = session.submit_consequence("second", timeout=5.0)
    assert isinstance(second, AcceptedSubmission)
    assert second.receipt == SubmissionReceipt(second_disposition)  # type: ignore[arg-type]

    child.request("late-old-completion", timeout=5.0)
    old_terminal = first.consequence.next_observation(5.0)
    assert old_terminal.event_type == "message.complete"
    assert old_terminal.payload["text"] == "old partial"
    with pytest.raises(GatewayError, match="no Hermes observation"):
        second.consequence.next_observation(0.01)

    child.request("next-lifecycle", timeout=5.0)
    second_observations = [second.consequence.next_observation(5.0) for _ in range(3)]
    assert [item.event_type for item in second_observations] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert second_observations[-1].payload["text"] == "new reply"
    first.consequence.release()
    second.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_same_session_image_attach_and_prompt_writes_remain_adjacent() -> None:
    fake = FakeGateway(
        {
            "image.attach": [
                Reply(result={"attached": True}),
                Reply(result={"attached": True}),
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.complete", LIVE_SID, {"status": "complete"}),
                    ),
                ),
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.complete", LIVE_SID, {"status": "complete"}),
                    ),
                ),
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    outcomes: list[AcceptedSubmission | TransportUnknown] = []

    def submit(text: str, image_path: str) -> None:
        outcomes.append(
            session.submit_consequence(
                text,
                timeout=5.0,
                image_paths=(Path(image_path),),
            )
        )

    first = threading.Thread(target=submit, args=("first", "/tmp/first.png"))
    second = threading.Thread(target=submit, args=("second", "/tmp/second.png"))
    first.start()
    second.start()
    first.join(5.0)
    second.join(5.0)

    assert fake.sent_methods() == [
        "image.attach",
        "prompt.submit",
        "image.attach",
        "prompt.submit",
    ]
    for outcome in outcomes:
        assert isinstance(outcome, AcceptedSubmission)
        outcome.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_steered_consequence_keeps_current_lifecycle_events_that_race_its_receipt() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(_event("message.start", LIVE_SID, {}),),
                ),
                Reply(
                    result={"status": "steered"},
                    events_before=(
                        _event("message.delta", LIVE_SID, {"text": "shared"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "shared reply"},
                        ),
                    ),
                ),
            ]
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)

    first = session.submit_consequence("first", timeout=5.0)
    assert isinstance(first, AcceptedSubmission)
    assert first.consequence.next_observation(5.0).event_type == "message.start"
    steered = session.submit_consequence("steer", timeout=5.0)
    assert isinstance(steered, AcceptedSubmission)
    assert steered.receipt == SubmissionReceipt("steered")

    assert [first.consequence.next_observation(5.0).event_type for _ in range(2)] == [
        "message.delta",
        "message.complete",
    ]
    steered_observations = [
        steered.consequence.next_observation(5.0) for _ in range(2)
    ]
    assert [item.event_type for item in steered_observations] == [
        "message.delta",
        "message.complete",
    ]
    assert steered_observations[-1].payload["text"] == "shared reply"
    first.consequence.release()
    steered.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_streaming_consequence_claims_owned_lifecycle_that_precedes_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_streaming_response = threading.Event()
    streaming_response_withheld = threading.Event()

    class WithheldStreamingResponseGateway(FakeGateway):
        def read_stdout(self) -> str | None:
            line = super().read_stdout()
            if line is None:
                return None
            frame = json.loads(line)
            result = frame.get("result")
            if isinstance(result, dict) and result.get("status") == "streaming":
                streaming_response_withheld.set()
                assert allow_streaming_response.wait(5.0)
            return line

    fake = WithheldStreamingResponseGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_before=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "owned"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "owned reply"},
                        ),
                    ),
                )
            ]
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    terminal_routed = threading.Event()
    original_route = manager._route_consequence_observation

    def observe_route(state, observation, *, was_running):
        result = original_route(state, observation, was_running=was_running)
        if observation.event_type == "message.complete":
            terminal_routed.set()
        return result

    monkeypatch.setattr(manager, "_route_consequence_observation", observe_route)
    session = manager.bind(
        STORED_KEY,
        LIVE_SID,
        snapshot={"running": True},
    )

    accepted_results: list[AcceptedSubmission | TransportUnknown] = []
    submitter = threading.Thread(
        target=lambda: accepted_results.append(
            session.submit_consequence("new streaming work", timeout=5.0)
        )
    )
    submitter.start()
    assert streaming_response_withheld.wait(5.0)
    assert terminal_routed.wait(5.0)
    allow_streaming_response.set()
    submitter.join(5.0)

    assert submitter.is_alive() is False
    accepted = accepted_results[0]
    assert isinstance(accepted, AcceptedSubmission)
    assert accepted.receipt == SubmissionReceipt("streaming")
    observations = [accepted.consequence.next_observation(5.0) for _ in range(3)]
    assert [observation.event_type for observation in observations] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert observations[-1].payload["text"] == "owned reply"
    accepted.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_unknown_submission_never_owns_a_later_accepted_lifecycle() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(),
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "second"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "second reply"},
                        ),
                    ),
                ),
            ]
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)

    assert isinstance(session.submit_consequence("unknown", timeout=0.01), TransportUnknown)
    second = session.submit_consequence("second", timeout=5.0)
    assert isinstance(second, AcceptedSubmission)
    observations = [second.consequence.next_observation(5.0) for _ in range(3)]

    assert [item.event_type for item in observations] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert observations[-1].payload["text"] == "second reply"
    assert fake.sent_methods().count("prompt.submit") == 2
    second.consequence.release()
    assert session.pending_consequence_count == 1
    assert session.detach_if_dormant() is False
    manager.shutdown()
    child.shutdown()


@pytest.mark.parametrize("disposition", ["steered", "queued"])
def test_pre_receipt_terminal_on_resumed_running_session_uses_native_disposition(
    disposition: str,
) -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": disposition},
                    events_before=(
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "current lifecycle"},
                        ),
                    ),
                )
            ],
            "next-lifecycle": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "queued lifecycle"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID, snapshot={"running": True})

    accepted = session.submit_consequence("follow up", timeout=5.0)
    assert isinstance(accepted, AcceptedSubmission)
    if disposition == "steered":
        terminal = accepted.consequence.next_observation(5.0)
        assert terminal.payload["text"] == "current lifecycle"
    else:
        with pytest.raises(GatewayError, match="no Hermes observation"):
            accepted.consequence.next_observation(0.01)
        child.request("next-lifecycle", timeout=5.0)
        queued = [accepted.consequence.next_observation(5.0) for _ in range(2)]
        assert queued[-1].payload["text"] == "queued lifecycle"
    accepted.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_queued_consequence_ignores_preexisting_lifecycle_activity() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "queued"},
                    events_after=(
                        _event("message.delta", LIVE_SID, {"text": "old delta"}),
                        _event("tool.start", LIVE_SID, {"name": "old tool"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "interrupted", "text": "old partial"},
                        ),
                    ),
                )
            ],
            "next-lifecycle": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "employee"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "employee reply"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID, snapshot={"running": True})

    accepted = session.submit_consequence("employee prompt", timeout=5.0)
    assert isinstance(accepted, AcceptedSubmission)
    assert accepted.receipt == SubmissionReceipt("queued")
    with pytest.raises(GatewayError, match="no Hermes observation"):
        accepted.consequence.next_observation(0.01)

    child.request("next-lifecycle", timeout=5.0)
    observations = [accepted.consequence.next_observation(5.0) for _ in range(3)]
    assert [item.event_type for item in observations] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert observations[-1].payload["text"] == "employee reply"
    accepted.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_multiple_queued_submissions_share_hermes_merged_next_lifecycle() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(_event("message.start", LIVE_SID, {}),),
                ),
                Reply(result={"status": "queued"}),
                Reply(result={"status": "queued"}),
            ],
            "old-terminal": [
                Reply(
                    result={},
                    events_after=(
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "first"},
                        ),
                    ),
                )
            ],
            "merged-next": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "B + C"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "merged reply"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    first = session.submit_consequence("A", timeout=5.0)
    second = session.submit_consequence("B", timeout=5.0)
    third = session.submit_consequence("C", timeout=5.0)
    assert isinstance(first, AcceptedSubmission)
    assert isinstance(second, AcceptedSubmission)
    assert isinstance(third, AcceptedSubmission)
    assert first.consequence.next_observation(5.0).event_type == "message.start"

    child.request("old-terminal", timeout=5.0)
    assert first.consequence.next_observation(5.0).payload["text"] == "first"
    child.request("merged-next", timeout=5.0)
    second_events = [second.consequence.next_observation(5.0) for _ in range(3)]
    third_events = [third.consequence.next_observation(5.0) for _ in range(3)]

    assert [item.event_type for item in second_events] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert [item.event_type for item in third_events] == [
        "message.start",
        "message.delta",
        "message.complete",
    ]
    assert second_events[-1].payload["text"] == "merged reply"
    assert third_events[-1].payload["text"] == "merged reply"
    first.consequence.release()
    second.consequence.release()
    third.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_unknown_current_lifecycle_is_a_barrier_for_later_queued_submission() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [Reply(), Reply(result={"status": "queued"})],
            "unknown-lifecycle": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "unknown A"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "unknown A reply"},
                        ),
                    ),
                )
            ],
            "queued-lifecycle": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "queued B"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "queued B reply"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)

    assert isinstance(session.submit_consequence("A", timeout=0.01), TransportUnknown)
    queued = session.submit_consequence("B", timeout=5.0)
    assert isinstance(queued, AcceptedSubmission)
    assert queued.receipt == SubmissionReceipt("queued")

    child.request("unknown-lifecycle", timeout=5.0)
    with pytest.raises(GatewayError, match="no Hermes observation"):
        queued.consequence.next_observation(0.01)
    child.request("queued-lifecycle", timeout=5.0)
    observations = [queued.consequence.next_observation(5.0) for _ in range(3)]
    assert observations[-1].payload["text"] == "queued B reply"
    assert fake.sent_methods().count("prompt.submit") == 2
    queued.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_merged_queue_routing_uses_response_frames_before_waiter_scheduling() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(_event("message.start", LIVE_SID, {}),),
                ),
                Reply(result={"status": "queued"}),
                Reply(result={"status": "queued"}),
            ],
            "old-terminal": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.complete", LIVE_SID, {"status": "complete"}),
                    ),
                )
            ],
            "merged-next": [
                Reply(
                    result={},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "merged"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "merged reply"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    first = session.submit_consequence("A", timeout=5.0)
    assert isinstance(first, AcceptedSubmission)
    assert first.consequence.next_observation(5.0).event_type == "message.start"
    with session.ordered_operation() as operation:
        pending_b = operation.begin_submission("B")
    with session.ordered_operation() as operation:
        pending_c = operation.begin_submission("C")
    assert isinstance(pending_b, PendingSubmission)
    assert isinstance(pending_c, PendingSubmission)
    assert fake.wait_sent(3, 5.0)

    child.request("old-terminal", timeout=5.0)
    assert first.consequence.next_observation(5.0).event_type == "message.complete"
    child.request("merged-next", timeout=5.0)
    accepted_b = pending_b.wait(5.0)
    accepted_c = pending_c.wait(5.0)
    assert isinstance(accepted_b, AcceptedSubmission)
    assert isinstance(accepted_c, AcceptedSubmission)
    events_b = [accepted_b.consequence.next_observation(5.0) for _ in range(3)]
    events_c = [accepted_c.consequence.next_observation(5.0) for _ in range(3)]

    assert events_b[-1].payload["text"] == "merged reply"
    assert events_c[-1].payload["text"] == "merged reply"
    first.consequence.release()
    accepted_b.consequence.release()
    accepted_c.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_shutdown_cancels_compound_operation_before_waiting_for_its_lane() -> None:
    fake = FakeGateway({"slash.exec": [Reply()]})
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    operation_finished = threading.Event()

    def run_operation() -> None:
        try:
            with session.ordered_operation() as operation:
                operation.request("slash.exec", timeout=30.0)
                operation.begin_submission("must not write")
        except GatewayError:
            pass
        finally:
            operation_finished.set()

    operation_thread = threading.Thread(target=run_operation)
    operation_thread.start()
    assert fake.wait_sent(1, 5.0)
    shutdown_thread = threading.Thread(target=manager.shutdown)
    shutdown_thread.start()
    shutdown_thread.join(0.5)
    shutdown_completed_before_forced_child_death = not shutdown_thread.is_alive()
    if not operation_finished.wait(0.5):
        fake.kill()
    operation_thread.join(5.0)
    shutdown_thread.join(5.0)

    assert shutdown_completed_before_forced_child_death is True
    assert operation_finished.is_set()
    assert fake.sent_methods() == ["slash.exec"]
    assert manager.router_alive is False
    child.shutdown()


def test_manager_shutdown_reports_a_router_that_did_not_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGateway({})
    child = _child(fake)
    manager = LiveSessionManager(child)
    real_router = manager._router

    class ReportedAliveRouter:
        def join(self, timeout: float | None = None) -> None:
            real_router.join(timeout)

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(manager, "_router", ReportedAliveRouter())

    with pytest.raises(GatewayError, match="router did not terminate"):
        manager.shutdown()

    assert real_router.is_alive() is False
    child.shutdown()


def test_unknown_barrier_does_not_swallow_known_active_terminal() -> None:
    fake = FakeGateway(
        {
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(_event("message.start", LIVE_SID, {}),),
                ),
                Reply(),
            ],
            "finish-known": [
                Reply(
                    result={},
                    events_after=(
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "known A reply"},
                        ),
                    ),
                )
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    known = session.submit_consequence("A", timeout=5.0)
    assert isinstance(known, AcceptedSubmission)
    assert known.consequence.next_observation(5.0).event_type == "message.start"

    assert isinstance(session.submit_consequence("B", timeout=0.01), TransportUnknown)
    child.request("finish-known", timeout=5.0)

    terminal = known.consequence.next_observation(5.0)
    assert terminal.event_type == "message.complete"
    assert terminal.payload["text"] == "known A reply"
    known.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_unknown_barrier_buffers_pre_receipt_steered_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_steered_response = threading.Event()
    steered_response_withheld = threading.Event()

    class WithheldSteeredResponseGateway(FakeGateway):
        def read_stdout(self) -> str | None:
            line = super().read_stdout()
            if line is None:
                return None
            frame = json.loads(line)
            result = frame.get("result")
            if isinstance(result, dict) and result.get("status") == "steered":
                steered_response_withheld.set()
                assert allow_steered_response.wait(5.0)
            return line

    fake = WithheldSteeredResponseGateway(
        {
            "prompt.submit": [
                Reply(),
                Reply(
                    result={"status": "steered"},
                    events_before=(
                        _event("message.delta", LIVE_SID, {"text": "shared"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "shared reply"},
                        ),
                    ),
                ),
            ]
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    terminal_routed = threading.Event()
    original_route = manager._route_consequence_observation

    def observe_route(state, observation, *, was_running):
        result = original_route(state, observation, was_running=was_running)
        if observation.event_type == "message.complete":
            terminal_routed.set()
        return result

    monkeypatch.setattr(manager, "_route_consequence_observation", observe_route)
    session = manager.bind(STORED_KEY, LIVE_SID)
    assert isinstance(session.submit_consequence("A", timeout=0.01), TransportUnknown)
    with session.ordered_operation() as operation:
        pending = operation.begin_submission("B")
    assert isinstance(pending, PendingSubmission)
    assert steered_response_withheld.wait(5.0)
    assert terminal_routed.wait(5.0)

    allow_steered_response.set()
    accepted = pending.wait(5.0)
    assert isinstance(accepted, AcceptedSubmission)
    observations = [accepted.consequence.next_observation(5.0) for _ in range(2)]
    assert observations[-1].payload["text"] == "shared reply"
    accepted.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_ready_rpc_error_is_removed_before_later_lifecycle_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_prompt_wait_entered = threading.Event()
    allow_first_prompt_wait = threading.Event()
    original_wait = GatewayRequestHandle.wait
    first_prompt_wait_claimed = False

    def gated_wait(
        handle: GatewayRequestHandle,
        timeout: float,
    ) -> dict[str, Any]:
        nonlocal first_prompt_wait_claimed
        if handle._method == "prompt.submit" and not first_prompt_wait_claimed:
            first_prompt_wait_claimed = True
            first_prompt_wait_entered.set()
            assert allow_first_prompt_wait.wait(5.0)
        return original_wait(handle, timeout)

    monkeypatch.setattr(GatewayRequestHandle, "wait", gated_wait)
    image_path = Path("/tmp/rejected-image.png")
    fake = FakeGateway(
        {
            "image.attach": [Reply(result={"attached": True})],
            "image.detach": [Reply(error=(4020, "detach failed"))],
            "prompt.submit": [
                Reply(error=(4019, "rejected")),
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        _event("message.start", LIVE_SID, {}),
                        _event("message.delta", LIVE_SID, {"text": "B"}),
                        _event(
                            "message.complete",
                            LIVE_SID,
                            {"status": "complete", "text": "B reply"},
                        ),
                    ),
                ),
            ],
            "observe-rejection": [
                Reply(
                    result={},
                    events_after=(
                        _event("session.info", LIVE_SID, {"running": False}),
                    ),
                )
            ],
            "session.interrupt": [Reply(result={"interrupted": True})],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session_info_routed = threading.Event()
    original_route = manager._route_consequence_observation

    def observe_route(state, observation, *, was_running):
        result = original_route(state, observation, was_running=was_running)
        if observation.event_type == "session.info":
            session_info_routed.set()
        return result

    monkeypatch.setattr(manager, "_route_consequence_observation", observe_route)
    session = manager.bind(STORED_KEY, LIVE_SID)
    rejected_errors: list[GatewayRpcError] = []

    def submit_rejected() -> None:
        try:
            session.submit_consequence("A", timeout=5.0, image_paths=(image_path,))
        except GatewayRpcError as exc:
            rejected_errors.append(exc)

    rejected_thread = threading.Thread(target=submit_rejected)
    rejected_thread.start()
    assert first_prompt_wait_entered.wait(5.0)
    child.request("observe-rejection", timeout=5.0)
    assert session_info_routed.wait(5.0)
    accepted_b_results: list[AcceptedSubmission | TransportUnknown] = []

    def submit_b() -> None:
        accepted_b_results.append(session.submit_consequence("B", timeout=5.0))

    submit_b_thread = threading.Thread(target=submit_b)
    submit_b_thread.start()
    assert fake.wait_sent(4, 0.05) is False
    assert fake.sent_methods() == [
        "image.attach",
        "prompt.submit",
        "observe-rejection",
    ]
    assert session.interrupt(timeout=5.0) == InterruptReceipt({"interrupted": True})
    assert fake.sent_methods().count("prompt.submit") == 1
    assert "image.detach" not in fake.sent_methods()

    allow_first_prompt_wait.set()
    rejected_thread.join(5.0)
    submit_b_thread.join(5.0)
    assert len(accepted_b_results) == 1
    accepted_b = accepted_b_results[0]
    assert isinstance(accepted_b, AcceptedSubmission)
    observations_b = [accepted_b.consequence.next_observation(5.0) for _ in range(3)]
    assert observations_b[-1].payload["text"] == "B reply"
    assert len(rejected_errors) == 1
    assert rejected_errors[0].code == 4019
    assert fake.sent_methods().count("image.detach") == 1
    assert fake.sent_methods() == [
        "image.attach",
        "prompt.submit",
        "observe-rejection",
        "session.interrupt",
        "image.detach",
        "prompt.submit",
    ]
    accepted_b.consequence.release()
    manager.shutdown()
    child.shutdown()


def test_invalid_prompt_disposition_detaches_all_images_before_next_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_prompt_wait_entered = threading.Event()
    allow_first_prompt_wait = threading.Event()
    original_wait = GatewayRequestHandle.wait
    first_prompt_wait_claimed = False

    def gated_wait(
        handle: GatewayRequestHandle,
        timeout: float,
    ) -> dict[str, Any]:
        nonlocal first_prompt_wait_claimed
        if handle._method == "prompt.submit" and not first_prompt_wait_claimed:
            first_prompt_wait_claimed = True
            first_prompt_wait_entered.set()
            assert allow_first_prompt_wait.wait(5.0)
        return original_wait(handle, timeout)

    monkeypatch.setattr(GatewayRequestHandle, "wait", gated_wait)
    image_paths = (Path("/tmp/first-invalid.png"), Path("/tmp/second-invalid.png"))
    fake = FakeGateway(
        {
            "image.attach": [
                Reply(result={"attached": True}),
                Reply(result={"attached": True}),
            ],
            "image.detach": [
                Reply(error=(4020, "first detach failed")),
                Reply(result={"detached": True}),
            ],
            "prompt.submit": [
                Reply(result={"status": "invalid"}),
                Reply(result={"status": "streaming"}),
            ],
        }
    )
    child = _child(fake)
    manager = LiveSessionManager(child)
    session = manager.bind(STORED_KEY, LIVE_SID)
    first_errors: list[GatewayError] = []
    second_results: list[AcceptedSubmission | TransportUnknown] = []

    def submit_invalid() -> None:
        try:
            session.submit_consequence("invalid", timeout=5.0, image_paths=image_paths)
        except GatewayError as exc:
            first_errors.append(exc)

    def submit_next() -> None:
        second_results.append(session.submit_consequence("next", timeout=5.0))

    first_thread = threading.Thread(target=submit_invalid)
    first_thread.start()
    assert first_prompt_wait_entered.wait(5.0)
    second_thread = threading.Thread(target=submit_next)
    second_thread.start()
    assert fake.wait_sent(4, 0.05) is False

    allow_first_prompt_wait.set()
    first_thread.join(5.0)
    second_thread.join(5.0)

    assert len(first_errors) == 1
    assert str(first_errors[0]) == "unexpected prompt.submit disposition: 'invalid'"
    assert len(second_results) == 1
    assert isinstance(second_results[0], AcceptedSubmission)
    second_results[0].consequence.release()
    assert fake.sent_methods() == [
        "image.attach",
        "image.attach",
        "prompt.submit",
        "image.detach",
        "image.detach",
        "prompt.submit",
    ]
    assert fake.sent[3]["params"] == {"session_id": LIVE_SID, "path": str(image_paths[0])}
    assert fake.sent[4]["params"] == {"session_id": LIVE_SID, "path": str(image_paths[1])}
    manager.shutdown()
    child.shutdown()
