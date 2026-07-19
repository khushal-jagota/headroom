"""C1 mutation proofs at the reader level (plan §11.2).

`RawFrameChildTransport.request()` gained the folded request/reply. These two tests pin the two
load-bearing C1 properties so a regression fails deterministically:
- the request id is handed to `on_request_id` PRE-SEND (before the frame is enqueued);
- for a response frame the reader runs sink1 (`on_deliver`) BEFORE sink2 (its own responder
  settle), the deliver-before-observe order the relay's FIFO ordering depends on.
"""

from __future__ import annotations

import itertools
import threading
from time import monotonic as _monotonic

from planner.hermes_backend import raw_frame_transport as _rft
from planner.hermes_backend.raw_frame_transport import RawFrameChildTransport
from planner.minds.fake import FakeGateway, Reply
from planner.minds.gateway import SHUTDOWN_GRACE_DEFAULT

HERMES_PY = "/x/hermes-agent/venv/bin/python"


def _create_reply() -> Reply:
    return Reply(result={"session_id": "s", "stored_session_id": "k"})


def _reader(fake, on_deliver, allocate_request_id, on_fold=None):
    return RawFrameChildTransport(
        hermes_python=HERMES_PY,
        env={},
        on_frame=on_deliver,
        on_dead=lambda: None,
        spawn=fake.spawn,
        allocate_request_id=allocate_request_id,
        on_fold=on_fold,
    )


def test_request_hands_out_id_pre_send() -> None:
    """C1: `on_request_id(rid)` fires BEFORE the frame is enqueued, so a step submission can
    register its pending ACK id before any response can be observed. A mutation that enqueues
    before calling `on_request_id` flips the recorded order and fails this test."""
    fake = FakeGateway({"session.create": [_create_reply()]})
    order: list[tuple[str, int]] = []
    reader = _reader(fake, on_deliver=lambda f: None, allocate_request_id=lambda: 7)
    reader.start_reading()
    reader.wait_ready()

    real_enqueue = reader.enqueue_frame

    def spy_enqueue(frame):
        order.append(("enqueue", frame["id"]))
        real_enqueue(frame)

    reader.enqueue_frame = spy_enqueue  # type: ignore[method-assign]

    def on_id(rid: int) -> None:
        order.append(("on_request_id", rid))

    reader.request("session.create", {"source": "x", "cols": 1}, timeout=2.0, on_request_id=on_id)

    assert order[0] == ("on_request_id", 7)
    assert order[1] == ("enqueue", 7)
    reader.shutdown(deadline=_monotonic() + SHUTDOWN_GRACE_DEFAULT)


def test_deliver_runs_before_own_responder_settles() -> None:
    """C1/C6 ordering: for a response frame the reader runs the full sink1 (`on_deliver`) → sink2
    (its own request/reply responder settle) → sink3 (`on_fold`) order. All three run on the single
    reader thread, so tokening each is deterministic: any mutation that reorders the three sinks
    (settle-before-deliver, or fold-before-settle) flips the tokens and fails this test."""
    fake = FakeGateway({"session.create": [_create_reply()]})
    ticket = itertools.count(1)
    tokens: dict[str, int] = {}
    delivered: list[dict] = []

    def on_deliver(frame):
        delivered.append(frame)
        if frame.get("id") == 3 and "result" in frame and "deliver" not in tokens:
            tokens["deliver"] = next(ticket)

    fold_seen = threading.Event()

    def on_fold(frame):
        if frame.get("id") == 3 and "result" in frame and "fold" not in tokens:
            tokens["fold"] = next(ticket)
            fold_seen.set()

    real_observe = _rft._PoolSessionResponder.observe

    def patched_observe(self, frame):
        if (
            "observe" not in tokens
            and not self.done.is_set()
            and frame.get("id") == self._request_id
            and "result" in frame
        ):
            tokens["observe"] = next(ticket)
        return real_observe(self, frame)

    _rft._PoolSessionResponder.observe = patched_observe  # type: ignore[method-assign]
    try:
        reader = _reader(
            fake, on_deliver=on_deliver, allocate_request_id=lambda: 3, on_fold=on_fold
        )
        reader.start_reading()
        reader.wait_ready()
        reader.request("session.create", {"source": "x", "cols": 1}, timeout=2.0)
        # sink3 (on_fold) runs on the reader thread AFTER request() unblocks at sink2, so wait for
        # it before asserting order (deliver/observe are already set once request() returns).
        assert fold_seen.wait(2.0)
        reader.shutdown(deadline=_monotonic() + SHUTDOWN_GRACE_DEFAULT)
    finally:
        _rft._PoolSessionResponder.observe = real_observe  # type: ignore[method-assign]

    assert tokens.get("deliver") is not None
    assert tokens.get("observe") is not None
    assert tokens.get("fold") is not None
    # Full sink1 → sink2 → sink3 order on the single reader thread (a fold-before-settle mutation
    # would flip observe/fold; a settle-before-deliver mutation would flip deliver/observe).
    assert tokens["deliver"] < tokens["observe"] < tokens["fold"]
    # The response frame also reached sink1 (on_deliver) — the relay-fanout path is preserved.
    assert any(f.get("id") == 3 and "result" in f for f in delivered)
