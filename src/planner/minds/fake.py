"""Scriptable fake gateway child (test double). Implements the ChildProcess
protocol so runner/queue/gateway tests exercise the real frame router with
no subprocess and no model calls."""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from planner.minds.gateway import ChildProcess, JsonDict


def ev(type_: str, session_id: str | None = None, payload: JsonDict | None = None) -> JsonDict:
    """Build an event frame exactly as server.py _emit does (server.py:803-807):
    the payload key is OMITTED when payload is None; session_id omitted when None
    (gateway.ready is the only such event — entry.py:316-322)."""
    params: JsonDict = {"type": type_}
    if session_id is not None:
        params["session_id"] = session_id
    if payload is not None:
        params["payload"] = payload
    return {"jsonrpc": "2.0", "method": "event", "params": params}


@dataclass(frozen=True)
class Reply:
    result: JsonDict | None = None  # -> {"jsonrpc":"2.0","id":<rid>,"result":...}
    error: tuple[int, str] | None = None  # -> {"jsonrpc":"2.0","id":<rid>,"error":{...}}
    events_before: tuple[JsonDict, ...] = ()  # event frames written BEFORE the response
    events_after: tuple[JsonDict, ...] = ()  # event frames written AFTER the response
    frames: tuple[JsonDict | str, ...] = ()  # raw frames (dict) or raw lines (str), written last
    die: bool = False  # then: stdout EOF (child death)

    def __post_init__(self) -> None:
        if self.result is not None and self.error is not None:
            raise ValueError("Reply cannot carry both result and error")


class FakeGateway:
    """One scriptable fake child. Build one per child; run_step spawns exactly one."""

    def __init__(
        self,
        script: Mapping[str, Sequence[Reply]],
        *,
        ready: bool = True,
        stderr_lines: Sequence[str] = (),
    ) -> None:
        self._script: dict[str, deque[Reply]] = {
            method: deque(replies) for method, replies in script.items()
        }
        self._out: queue.Queue[str | None] = queue.Queue()
        self._err: queue.Queue[str | None] = queue.Queue()
        for line in stderr_lines:
            self._err.put(line)
        if ready:
            self._out.put(json.dumps(ev("gateway.ready", None, {"skin": "fake"})))
        self._sent_cond = threading.Condition()
        self._exited = threading.Event()
        self._dead = threading.Event()
        # assertion surface
        self.sent: list[JsonDict] = []
        self.argv: list[str] | None = None
        self.env: dict[str, str] | None = None
        self.closed: bool = False

    # the spawn hook to inject: GatewayChild(..., spawn=fake.spawn)
    def spawn(self, argv: list[str], env: dict[str, str]) -> ChildProcess:
        self.argv = list(argv)
        self.env = dict(env)
        return self

    # --- ChildProcess protocol --------------------------------------------

    def send(self, line: str) -> None:
        if self.closed or self._dead.is_set():
            raise BrokenPipeError("fake stdin closed")
        frame: JsonDict = json.loads(line)
        with self._sent_cond:
            self.sent.append(frame)
            self._sent_cond.notify_all()
        method = frame.get("method")
        rid = frame.get("id")
        replies = self._script.get(method) if isinstance(method, str) else None
        if replies is None or len(replies) == 0:
            # unscripted or exhausted → fail loud but structured (mirrors server.py:910)
            self._out.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32601, "message": f"unknown method: {method}"},
                    }
                )
            )
            return
        reply = replies.popleft()
        for frame_before in reply.events_before:
            self._out.put(json.dumps(frame_before))
        if reply.result is not None:
            self._out.put(json.dumps({"jsonrpc": "2.0", "id": rid, "result": reply.result}))
        elif reply.error is not None:
            code, message = reply.error
            self._out.put(
                json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
                )
            )
        for frame_after in reply.events_after:
            self._out.put(json.dumps(frame_after))
        for raw in reply.frames:
            self._out.put(raw if isinstance(raw, str) else json.dumps(raw))
        if reply.die:
            self._die()

    def read_stdout(self) -> str | None:
        item = self._out.get()
        if item is None:
            self._out.put(None)
            return None
        return item

    def read_stderr(self) -> str | None:
        item = self._err.get()
        if item is None:
            self._err.put(None)
            return None
        return item

    def close_stdin(self) -> None:
        self.closed = True
        if not self._dead.is_set():
            self._die()  # real gateway exits on stdin EOF (entry.py:344)

    def kill(self) -> None:
        self.closed = True
        self._die()

    def wait(self, timeout: float | None = None) -> int | None:
        return 0 if self._exited.wait(timeout) else None

    # --- internal ----------------------------------------------------------

    def _die(self) -> None:
        if self._dead.is_set():
            return
        self._dead.set()
        self._exited.set()
        self._err.put(None)
        self._out.put(None)

    # --- assertion helpers -------------------------------------------------

    def sent_methods(self) -> list[str]:
        return [str(frame.get("method")) for frame in self.sent]

    def wait_sent(self, count: int, timeout: float = 5.0) -> bool:
        with self._sent_cond:
            return self._sent_cond.wait_for(lambda: len(self.sent) >= count, timeout)
