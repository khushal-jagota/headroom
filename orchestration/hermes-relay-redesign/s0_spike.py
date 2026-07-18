"""S0 spike: prove the relay design against a live `hermes serve` WS backend.

Phases:
  1. connect  -> expect gateway.ready
  2. create   -> session.create, record ids
  3. prompt   -> prompt.submit, collect every frame until message.complete
  4. persist  -> session.list shows the stored session
  5. drop     -> close the socket entirely
  6. reconnect-> new WS, session.resume the stored id, prompt again, prove
                 history carried (agent repeats the earlier word)
  7. probe    -> second concurrent connection tries session.resume of the same
                 stored session while connection A holds it (failure-mode info)

Artifacts: frames.jsonl (every inbound frame, annotated by phase) and
summary.json (event-type inventory + phase outcomes).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

URL = "ws://127.0.0.1:9911/api/ws?token=s0-spike-token-7f3a"
OUT = Path(__file__).parent
FRAMES = (OUT / "s0-frames.jsonl").open("w", encoding="utf-8")

summary: dict = {"phases": {}, "event_types": {}, "rpc_responses": {}}


def record(phase: str, frame: dict) -> None:
    FRAMES.write(json.dumps({"phase": phase, "t": time.time(), "frame": frame}) + "\n")
    FRAMES.flush()
    params = frame.get("params")
    if isinstance(params, dict) and frame.get("method") == "event":
        kind = str(params.get("type"))
        summary["event_types"][kind] = summary["event_types"].get(kind, 0) + 1


class Client:
    def __init__(self, name: str):
        self.name = name
        self.ws = None
        self.next_id = 1
        self.pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.reader_task = None

    async def connect(self, phase: str) -> dict | None:
        self.ws = await websockets.connect(URL, open_timeout=15)
        self.reader_task = asyncio.create_task(self._reader(phase))
        # gateway.ready should be the first frame
        try:
            ready = await asyncio.wait_for(self.events.get(), timeout=15)
        except TimeoutError:
            return None
        return ready

    async def _reader(self, phase: str) -> None:
        try:
            async for raw in self.ws:
                frame = json.loads(raw)
                record(f"{self.name}:{phase}", frame)
                rid = frame.get("id")
                if rid is not None and ("result" in frame or "error" in frame):
                    fut = self.pending.pop(rid, None)
                    if fut is not None and not fut.done():
                        fut.set_result(frame)
                elif frame.get("method") == "event":
                    await self.events.put(frame.get("params") or {})
        except Exception:
            pass

    async def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        rid = self.next_id
        self.next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        frame_out = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        await self.ws.send(json.dumps(frame_out))
        frame = await asyncio.wait_for(fut, timeout=timeout)
        summary["rpc_responses"].setdefault(method, frame.get("error") and "error" or "ok")
        if "error" in frame:
            return {"__error__": frame["error"]}
        return frame.get("result") or {}

    async def drain_until(
        self, event_type: str, timeout: float = 240
    ) -> tuple[dict | None, list[str]]:
        seen: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, seen
            try:
                ev = await asyncio.wait_for(self.events.get(), timeout=remaining)
            except TimeoutError:
                return None, seen
            seen.append(str(ev.get("type")))
            if ev.get("type") == event_type:
                return ev, seen

    async def close(self):
        if self.reader_task:
            self.reader_task.cancel()
        if self.ws:
            await self.ws.close()


async def main() -> int:
    a = Client("A")

    # Phase 1: connect
    ready = await a.connect("connect")
    ok = bool(ready and ready.get("type") == "gateway.ready")
    summary["phases"]["1-connect"] = {"ok": ok, "ready": ready}
    if not ok:
        print("FAIL: no gateway.ready", ready)
        return 1

    # Phase 2: create
    created = await a.request("session.create", {"source": "s0-spike", "cols": 100})
    live_sid = str(created.get("session_id") or "")
    stored = str(created.get("stored_session_id") or live_sid)
    summary["phases"]["2-create"] = {"ok": bool(live_sid), "live": live_sid, "stored": stored,
                                     "keys": sorted(created.keys())}
    if not live_sid:
        print("FAIL: session.create", created)
        return 1

    # Phase 3: prompt + full frame collection
    prompt_text = "Reply with exactly the single word: marigold"
    sub = await a.request("prompt.submit",
                          {"session_id": live_sid, "text": prompt_text},
                          timeout=120)
    done, seen = await a.drain_until("message.complete", timeout=300)
    summary["phases"]["3-prompt"] = {
        "ok": done is not None,
        "submit_response_keys": sorted(sub.keys()),
        "submit_response": sub,
        "event_sequence": seen,
        "final_text": (done or {}).get("payload", {}).get("text", "")[:200] if done else None,
    }
    if done is None:
        print("FAIL: no message.complete; events seen:", seen)
        return 1

    # Phase 4: persistence
    listed = await a.request("session.list", {"limit": 20})
    ours = [s for s in (listed.get("sessions") or []) if str(s.get("source", "")) == "s0-spike"]
    summary["phases"]["4-list"] = {"ok": bool(ours), "spike_sessions": ours[:3]}

    # Phase 5: hard drop (no session.close — simulate relay crash)
    await a.close()
    summary["phases"]["5-drop"] = {"ok": True}
    await asyncio.sleep(2)

    # Phase 6: reconnect + resume + history proof
    b = Client("B")
    ready_b = await b.connect("reconnect")
    resumed = await b.request("session.resume",
                              {"session_id": stored, "cols": 100, "source": "s0-spike"},
                              timeout=120)
    live_b = str(resumed.get("session_id") or "")
    ok6 = bool(ready_b) and bool(live_b) and "__error__" not in resumed
    summary["phases"]["6-resume"] = {"ok": ok6, "resumed_keys": sorted(resumed.keys()),
                                     "resumed_live": live_b,
                                     "resumed_field": resumed.get("resumed"),
                                     "message_count": len(resumed.get("messages") or [])}
    if ok6:
        recall_text = (
            "What was the single word you replied with before? Answer with just that word."
        )
        await b.request("prompt.submit",
                        {"session_id": live_b, "text": recall_text},
                        timeout=120)
        done2, seen2 = await b.drain_until("message.complete", timeout=300)
        text2 = (done2 or {}).get("payload", {}).get("text", "") if done2 else ""
        summary["phases"]["6b-history"] = {"ok": "marigold" in text2.lower(),
                                           "reply": text2[:200], "events": seen2}

    # Phase 7: concurrent second resume of the same stored session (probe)
    c = Client("C")
    await c.connect("probe")
    probe = await c.request("session.resume",
                            {"session_id": stored, "cols": 100, "source": "s0-spike"},
                            timeout=60)
    summary["phases"]["7-concurrent-resume"] = {
        "response_keys": sorted(probe.keys()),
        "error": probe.get("__error__"),
        "note": "informational: relay design never does this; recording server behavior",
    }
    await c.close()
    await b.close()

    (OUT / "s0-summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
