"""ScriptedRelayChild: a STATEFUL, NON-BLOCKING fake Hermes child for TEST-MODE relay
composition (plan §6). NO real Hermes.

It implements the `ChildProcess` protocol (`minds/gateway`) so the pool spawns it through
the SAME `SpawnFn` seam a real child uses, and drives the REAL pool + relay + neutral pane
end to end under Playwright — but every session/turn is scripted here, deterministically.

Why stateful (F12): static method->reply scripts cannot serve history / refresh / new-
conversation / child-reset. This child keeps a SHARED session store (module-level, keyed by
stored session id) that SURVIVES respawn, so re-attach after a child reset restores history.

Why non-blocking (F13): the transport has ONE serial writer calling `send`; a blocking `send`
would deadlock a held-turn `clarify.respond`, and a synchronous full-stream completion would
leave NO running turn to interrupt/steer. So `send` NEVER blocks — it parses the frame,
enqueues the ACK + any immediate frames, and for a prompt opens a HELD turn whose stream is
released token-by-token by THIS child's OWN pacer thread with a small fixed inter-token delay
(a scripted-child tunable, never a `time.sleep` in a transport writer). The stream can also be
released early (interrupt) or gated (clarify) by subsequent frames on the child's own stdin.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from planner.minds.gateway import ChildProcess, JsonDict

# Fixed inter-token pacing (the child's own reader paces its output; NOT a transport sleep).
_INTER_TOKEN_DELAY_SECONDS = 0.02
# The cue substrings that steer the scripted turn (matched in the prompt text).
_CLARIFY_CUE = "ask me"
# A HOLD cue opens a turn that streams start+thinking then WAITS (still running) until an
# interrupt arrives — so the interrupt scenario acts on a genuinely RUNNING turn (F13).
_HOLD_CUE = "hold open"
# The scripted 4009 compact-failure cue: a prompt carrying it flags ITS session so a later
# session.compress on that session fails with 4009 (a native rejection that must not be masked).
_COMPACT_FAIL_CUE = "compact-fails"
# A RESET cue kills the child so the relay synthesizes a child_reset; the respawn shares the
# store, so re-attach restores history.
_RESET_CUE = "reset-child"

# S3 §1.3 — the prompt.submit ACK carries a DISPOSITION `status` field matching real Hermes
# (sessions/service.py:556 `disposition = str(result.get("status") ...)`, values
# streaming/queued/steered). A normal step ACKs "streaming"; cues drive the other two so the
# PoolStepGateway's A1 terminal-ownership paths are e2e-observable.
_QUEUED_CUE = "__queued__"  # ACK "queued"; the child streams the interrupted PREDECESSOR's
#                             frames (a distinctive delta THEN its terminal) BEFORE OUR turn, so
#                             the skip-one path AND the drop-predecessor-frames path are both
#                             observable (a leaked predecessor delta would corrupt the owned turn).
_STEERED_CUE = "__steered__"  # ACK "steered"; delivered into the active execution — NO owned
#                               terminal is emitted (A1 returns errored immediately).
# A PREDECESSOR-BEFORE-ACK cue emits a predecessor turn's terminal BEFORE replying to the submit
# with disposition "streaming" — modelling a competing turn whose frame lands on the child stdout
# thread ahead of OUR prompt.submit ACK (Codex Finding 1). The pool must DROP that pre-ACK frame
# (it is a predecessor's, never ours) and settle on OUR turn's terminal streamed after the ACK.
_PREDECESSOR_BEFORE_ACK_CUE = "__predecessor_before_ack__"
# The distinctive predecessor text a queued/pre-ACK predecessor streams, so a test can assert it
# NEVER reaches the owned turn's on_event stream.
_PREDECESSOR_DELTA_TEXT = "PREDECESSOR draft"
# A BUSY cue makes the NEXT prompt.submit return a native 4009 RPC error (not a disposition), so
# the PoolStepGateway maps it to SharedGatewayBusy (mirrors shared_gateway.py:836-837).
_BUSY_SUBMIT_CUE = "__busy_submit__"

# One fixed catalog payload in the EXACT native `commands.catalog` shape (as
# shared_gateway._build_catalog reads it): top-level `pairs` (list of [command, description])
# + `skill_count` (skills are the LAST skill_count pairs) + `categories[].pairs`. Here one
# runnable command (`/help`) precedes one SKILL (`$summarize`) so the picker's skills-only
# extraction (last skill_count entries) is exercised against real structure, not an invented one.
_CATALOG_PAYLOAD: JsonDict = {
    "pairs": [
        ["/help", "Show help"],
        ["$summarize", "Summarize the conversation so far."],
    ],
    "skill_count": 1,
    "categories": [
        {"name": "Commands", "pairs": [["/help", "Show help"]]},
        {"name": "Skills", "pairs": [["$summarize", "Summarize the conversation so far."]]},
    ],
}


@dataclass
class _StoredSession:
    """One durable session's state, keyed by stored id in the shared store."""

    stored_id: str
    live_id: str
    messages: list[JsonDict] = field(default_factory=list)
    live: bool = True
    # Set by a prompt carrying the compact-fail cue: the next session.compress on this session
    # returns a 4009 (a native rejection surfaced as a failure line, not masked).
    compress_fails: bool = False


class _SharedSessionStore:
    """Sessions surviving child respawn: keyed by stored id. Guarded by its own lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_stored: dict[str, _StoredSession] = {}
        self._counter = 0

    def _next_ids(self) -> tuple[str, str]:
        self._counter += 1
        n = self._counter
        return f"live-{n}", f"stored-{n}"

    def create(self) -> _StoredSession:
        with self._lock:
            live_id, stored_id = self._next_ids()
            session = _StoredSession(stored_id=stored_id, live_id=live_id)
            self._by_stored[stored_id] = session
            return session

    def seed(self, stored_id: str, messages: list[JsonDict]) -> _StoredSession:
        """Seed a known stored id (the Chief's durable key) so adoption resumes it with
        prior messages. Idempotent — a later resume finds the same live id."""
        with self._lock:
            existing = self._by_stored.get(stored_id)
            if existing is not None:
                return existing
            self._counter += 1
            live_id = f"live-seed-{self._counter}"
            session = _StoredSession(
                stored_id=stored_id, live_id=live_id, messages=list(messages)
            )
            self._by_stored[stored_id] = session
            return session

    def resume(self, stored_id: str) -> _StoredSession:
        with self._lock:
            existing = self._by_stored.get(stored_id)
            if existing is not None:
                existing.live = True
                return existing
            # An unknown stored id (a never-seen adopted key): materialize it empty so a
            # resume of it behaves like a fresh empty session rather than an error.
            self._counter += 1
            live_id = f"live-resume-{self._counter}"
            session = _StoredSession(stored_id=stored_id, live_id=live_id)
            self._by_stored[stored_id] = session
            return session

    def by_live(self, live_id: str) -> _StoredSession | None:
        with self._lock:
            for session in self._by_stored.values():
                if session.live_id == live_id:
                    return session
            return None

    def close(self, live_id: str) -> None:
        with self._lock:
            for session in self._by_stored.values():
                if session.live_id == live_id:
                    session.live = False
                    return

    def live_sessions(self) -> list[_StoredSession]:
        with self._lock:
            return [s for s in self._by_stored.values() if s.live]


# A well-known durable Chief key the flag-on e2e seeds into agent_chat_sessions.chat_session_key
# (via conftest seed_db). Composition adopts it and the first spawn `session.resume`s it; the
# scripted store seeds it (below) with prior messages so the "history on load" scenario renders
# a durable transcript that was NOT written by Panels DB.
SEEDED_CHIEF_SESSION_KEY = "chief-durable-seed"
_SEEDED_CHIEF_MESSAGES: list[JsonDict] = [
    {"role": "user", "content": "earlier question"},
    {"role": "assistant", "content": "earlier answer"},
]


def _new_store() -> _SharedSessionStore:
    store = _SharedSessionStore()
    store.seed(SEEDED_CHIEF_SESSION_KEY, list(_SEEDED_CHIEF_MESSAGES))
    return store


# The process-wide store: one per Python process (the test-mode server subprocess), so every
# respawned child shares it and re-attach after a reset restores history. Pre-seeds the Chief's
# durable key so a flag-on e2e that seeds the same key resumes prior history.
_PROCESS_STORE = _new_store()


def reset_process_store() -> None:
    """Reset the module store (used only by in-process unit tests that want isolation)."""
    global _PROCESS_STORE
    _PROCESS_STORE = _SharedSessionStore()


def seed_scripted_session(stored_id: str, messages: list[JsonDict] | None = None) -> None:
    """Seed a durable stored id (the Chief's `chat_session_key`) with optional prior
    messages so a flag-on e2e can assert history renders on load."""
    _PROCESS_STORE.seed(stored_id, list(messages or []))


def _text_message(role: str, text: str) -> JsonDict:
    return {"role": role, "content": text}


def _event(type_: str, session_id: str, payload: JsonDict | None = None) -> JsonDict:
    params: JsonDict = {"type": type_, "session_id": session_id}
    if payload is not None:
        params["payload"] = payload
    return {"jsonrpc": "2.0", "method": "event", "params": params}


class ScriptedRelayChild(ChildProcess):
    """One scripted child. Non-blocking `send`; a background pacer streams held turns."""

    def __init__(self, store: _SharedSessionStore) -> None:
        self._store = store
        self._out: queue.Queue[str | None] = queue.Queue()
        # A separate job queue drives the pacer thread (held-turn beats) so `send` never
        # blocks and never sleeps.
        self._jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._dead = threading.Event()
        self._closed = threading.Event()
        # Held-turn state: the live session id of the currently-streaming turn (if any) and
        # a flag the interrupt/clarify frames flip. Guarded by `_turn_lock`.
        self._turn_lock = threading.Lock()
        self._running_live_id: str | None = None
        self._interrupted = False
        self._clarify_pending: dict[str, Any] | None = None
        # A gate a HELD-open turn waits on until interrupt (or death) releases it.
        self._interrupt_gate = threading.Event()
        # assertion surface (in-process tests)
        self.attached_image_paths: list[str] = []

        self._out.put(json.dumps(_event("gateway.ready", "")))
        self._pacer = threading.Thread(
            target=self._pacer_loop, name="scripted-relay-pacer", daemon=True
        )
        self._pacer.start()

    # --- ChildProcess protocol --------------------------------------------

    def send(self, line: str) -> None:
        if self._closed.is_set() or self._dead.is_set():
            raise BrokenPipeError("scripted relay child stdin closed")
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(frame, dict):
            return
        method = frame.get("method")
        rid = frame.get("id")
        params = frame.get("params")
        params = params if isinstance(params, dict) else {}
        self._dispatch(str(method), rid, params)

    def read_stdout(self) -> str | None:
        item = self._out.get()
        if item is None:
            self._out.put(None)
            return None
        return item

    def read_stderr(self) -> str | None:
        # No stderr; block until death, then EOF (mirrors a live child that never logs).
        self._dead.wait()
        return None

    def close_stdin(self) -> None:
        self._closed.set()
        self._die()

    def kill(self) -> None:
        self._closed.set()
        self._die()

    def wait(self, timeout: float | None = None) -> int | None:
        return 0 if self._dead.wait(timeout) else None

    # --- dispatch (non-blocking) ------------------------------------------

    def _dispatch(self, method: str, rid: Any, params: JsonDict) -> None:
        if method == "session.create":
            session = self._store.create()
            self._reply_result(
                rid, {"session_id": session.live_id, "stored_session_id": session.stored_id}
            )
            return
        if method == "session.resume":
            stored_id = str(params.get("session_id", ""))
            session = self._store.resume(stored_id)
            self._reply_result(
                rid, {"session_id": session.live_id, "resumed": session.stored_id}
            )
            return
        if method == "session.active_list":
            sessions = [{"id": s.live_id} for s in self._store.live_sessions()]
            self._reply_result(rid, {"sessions": sessions})
            return
        if method == "session.history":
            live_id = str(params.get("session_id", ""))
            history_session = self._store.by_live(live_id)
            messages = list(history_session.messages) if history_session is not None else []
            self._reply_result(rid, {"count": len(messages), "messages": messages})
            return
        if method == "session.close":
            self._store.close(str(params.get("session_id", "")))
            self._reply_result(rid, {"closed": True})
            return
        if method == "commands.catalog":
            self._reply_result(rid, _CATALOG_PAYLOAD)
            return
        if method == "session.compress":
            live_id = str(params.get("session_id", ""))
            compress_session = self._store.by_live(live_id)
            if compress_session is not None and compress_session.compress_fails:
                self._reply_error(rid, 4009, "session busy: already running")
                return
            self._reply_result(rid, {"compressed": True})
            return
        if method == "session.interrupt":
            self._reply_result(rid, {"interrupted": True})
            self._request_interrupt()
            return
        if method == "clarify.respond":
            self._reply_result(rid, {"accepted": True})
            self._release_clarify()
            return
        if method == "approval.respond":
            self._reply_result(rid, {"accepted": True})
            return
        if method == "image.attach":
            self._attach_image(rid, params)
            return
        if method == "prompt.submit":
            self._submit_prompt(rid, params)
            return
        # Any other method: a structured unknown-method error (never a silent drop).
        self._reply_error(rid, -32601, f"unknown method: {method}")

    def _attach_image(self, rid: Any, params: JsonDict) -> None:
        # F7: VALIDATE the resolved path is an ABSOLUTE, openable filesystem path — an
        # ACK-anything fake would let the images scenario pass falsely.
        import os

        path = params.get("path")
        if not isinstance(path, str) or not os.path.isabs(path) or not os.path.exists(path):
            self._reply_error(rid, -32602, f"image path not absolute/openable: {path!r}")
            return
        self.attached_image_paths.append(path)
        self._reply_result(rid, {"attached": True})

    # --- prompt.submit disposition + held-turn state machine --------------

    def _submit_prompt(self, rid: Any, params: JsonDict) -> None:
        """Reply to a prompt.submit with the DISPOSITION `status` (streaming/queued/steered)
        matching real Hermes (sessions/service.py:556), then drive the scripted turn to match.

        - `__busy_submit__` → a native 4009 RPC error (no turn), so the gateway maps it to
          SharedGatewayBusy (shared_gateway.py:836-837). NO disposition, NO turn begins.
        - `__steered__` → ACK "steered"; delivered into the active execution — NO owned terminal
          is emitted (A1 returns errored without watching a terminal).
        - `__queued__` → ACK "queued"; the child streams the interrupted PREDECESSOR's frames (a
          distinctive delta THEN its terminal) BEFORE our own completion, so both the skip-one
          terminal path AND the drop-predecessor-frames path are observable.
        - `__predecessor_before_ack__` → a predecessor turn's terminal is emitted BEFORE the ACK,
          then ACK "streaming", then OUR turn — so the pool's pre-ACK-drop path is exercised.
        - otherwise → ACK "streaming"; a normal held turn that streams + completes."""
        live_id = str(params.get("session_id", ""))
        text = str(params.get("text", ""))
        if _BUSY_SUBMIT_CUE in text:
            # A native busy rejection (4009); mirrors a real submit racing a running turn.
            self._reply_error(rid, 4009, "session busy: already running")
            return
        if _STEERED_CUE in text:
            self._reply_result(rid, {"status": "steered"})
            # Delivered by steering the active execution: no independent turn to stream.
            return
        if _QUEUED_CUE in text:
            self._reply_result(rid, {"status": "queued"})
            self._begin_queued_turn(live_id, text)
            return
        if _PREDECESSOR_BEFORE_ACK_CUE in text:
            # A competing predecessor turn's terminal lands on the child stdout thread BEFORE our
            # prompt.submit ACK: emit it first, THEN the ACK, THEN our turn. The pool must drop the
            # pre-ACK terminal (a predecessor's) and settle on our post-ACK terminal (Finding 1).
            self._emit(
                _event(
                    "message.complete",
                    live_id,
                    {"text": _PREDECESSOR_DELTA_TEXT, "status": "complete"},
                )
            )
            self._reply_result(rid, {"status": "streaming"})
            self._begin_turn(live_id, text)
            return
        self._reply_result(rid, {"status": "streaming"})
        self._begin_turn(live_id, text)

    def _begin_queued_turn(self, live_id: str, text: str) -> None:
        """A queued submission: stream the INTERRUPTED predecessor's frames first (a distinctive
        delta THEN its terminal), then stream + complete OUR turn — so the pool's skip-one
        terminal-ownership path is exercised AND a leaked predecessor delta would corrupt the owned
        turn (the predecessor's frames must never reach on_event; Codex Finding 2)."""
        with self._turn_lock:
            self._running_live_id = live_id
            self._interrupted = False
            self._clarify_pending = None

        def run() -> None:
            if self._dead.is_set():
                return
            # The predecessor's own delta (the human turn the queued step displaced): distinctive
            # text that must NOT reach the owned turn's on_event stream.
            self._emit(
                _event("message.delta", live_id, {"text": _PREDECESSOR_DELTA_TEXT})
            )
            self._paced_sleep()
            # The predecessor's interrupted terminal (the human turn the queued step displaced).
            self._emit(
                _event("message.complete", live_id, {"text": "", "status": "interrupted"})
            )
            self._paced_sleep()
            # Now OUR turn streams and completes; the pool skips the predecessor's frames above and
            # owns this.
            self._finish_turn(live_id, text)

        self._jobs.put(run)

    def _begin_turn(self, live_id: str, text: str) -> None:
        # A RESET cue kills the child (the relay then synthesizes child_reset). The child dies
        # AFTER the prompt.submit ACK already went out, so the pane sees a running turn cut off.
        if _RESET_CUE in text:
            self._jobs.put(self._die)
            return
        # A compact-fail cue flags THIS session so a later session.compress on it returns 4009.
        if _COMPACT_FAIL_CUE in text:
            session = self._store.by_live(live_id)
            if session is not None:
                session.compress_fails = True
        with self._turn_lock:
            self._running_live_id = live_id
            self._interrupted = False
            self._clarify_pending = None
        self._jobs.put(lambda: self._stream_turn(live_id, text))

    def _request_interrupt(self) -> None:
        with self._turn_lock:
            self._interrupted = True
        # Release a HELD-open turn waiting on the interrupt gate.
        self._interrupt_gate.set()

    def _release_clarify(self) -> None:
        with self._turn_lock:
            pending = self._clarify_pending
            self._clarify_pending = None
        if pending is not None:
            self._jobs.put(
                lambda: self._finish_turn(pending["live_id"], pending["text"])
            )

    def _pacer_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception:  # noqa: BLE001 — a scripted job must never crash the child
                pass

    def _paced_sleep(self) -> None:
        # The child's OWN reader paces output; this is a scripted-child tunable, not a
        # transport-writer sleep. Wake early on death.
        self._dead.wait(_INTER_TOKEN_DELAY_SECONDS)

    def _stream_turn(self, live_id: str, text: str) -> None:
        if self._dead.is_set():
            return
        self._interrupt_gate.clear()
        self._emit(_event("message.start", live_id))
        self._paced_sleep()
        self._emit(_event("thinking.delta", live_id, {"text": "considering"}))
        self._paced_sleep()
        # A HOLD cue keeps the turn RUNNING (one delta out) until interrupt arrives, so the
        # interrupt scenario acts on a genuinely running turn -> completes as interrupted.
        if _HOLD_CUE in text:
            self._emit(_event("message.delta", live_id, {"text": "streaming"}))
            # Wait (running) until interrupt or death releases the gate.
            while not self._interrupt_gate.wait(0.05):
                if self._dead.is_set():
                    return
            self._emit(
                _event("message.complete", live_id, {"text": "", "status": "interrupted"})
            )
            self._clear_running(live_id)
            return
        # A clarify cue holds the turn until clarify.respond arrives.
        if _CLARIFY_CUE in text:
            with self._turn_lock:
                self._clarify_pending = {"live_id": live_id, "text": text}
            self._emit(
                _event(
                    "clarify.request",
                    live_id,
                    {
                        "request_id": "clarify-1",
                        "question": "Which option do you want?",
                        "choices": ["a", "b"],
                    },
                )
            )
            return  # the turn is HELD; _release_clarify enqueues _finish_turn
        self._finish_turn(live_id, text)

    def _finish_turn(self, live_id: str, text: str) -> None:
        echo = f"echo: {text}"
        streamed: list[str] = []
        for token in _tokenize(echo):
            if self._dead.is_set():
                return
            with self._turn_lock:
                interrupted = self._interrupted
            if interrupted:
                self._emit(
                    _event("message.complete", live_id, {"text": "", "status": "interrupted"})
                )
                self._clear_running(live_id)
                return
            streamed.append(token)
            self._emit(_event("message.delta", live_id, {"text": token}))
            self._paced_sleep()
        final_text = "".join(streamed)
        self._emit(_event("message.complete", live_id, {"text": final_text, "status": "complete"}))
        self._emit(_event("session.title", live_id, {"title": "Scripted conversation"}))
        # Persist the human + assistant messages so a later session.history (refresh) returns
        # them (the durable transcript survives respawn via the shared store).
        session = self._store.by_live(live_id)
        if session is not None:
            with self._store._lock:  # noqa: SLF001 — the store's own message append
                session.messages.append(_text_message("user", text))
                session.messages.append(_text_message("assistant", final_text))
        self._clear_running(live_id)

    def _clear_running(self, live_id: str) -> None:
        with self._turn_lock:
            if self._running_live_id == live_id:
                self._running_live_id = None
                self._interrupted = False

    # --- reply / emit / death ---------------------------------------------

    def _reply_result(self, rid: Any, result: JsonDict) -> None:
        self._emit({"jsonrpc": "2.0", "id": rid, "result": result})

    def _reply_error(self, rid: Any, code: int, message: str) -> None:
        self._emit({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def _emit(self, frame: JsonDict) -> None:
        if self._dead.is_set():
            return
        self._out.put(json.dumps(frame))

    def _die(self) -> None:
        if self._dead.is_set():
            return
        self._dead.set()
        self._interrupt_gate.set()  # wake any held-open turn so its pacer thread can exit
        self._jobs.put(None)
        self._out.put(None)


def _tokenize(text: str) -> list[str]:
    """Split into small chunks so the pane sees the assistant text grow token-by-token
    (F13 — the streamed-delta scenario asserts a partial prefix existed)."""
    words = text.split(" ")
    tokens: list[str] = []
    for index, word in enumerate(words):
        tokens.append(word if index == 0 else " " + word)
    return tokens or [text]


def scripted_relay_spawn(argv: list[str], env: dict[str, str]) -> ChildProcess:
    """The `SpawnFn` the test-mode composition injects. Every spawn shares the process-wide
    session store, so a respawn after a child reset restores history."""
    _ = (argv, env)
    return ScriptedRelayChild(_PROCESS_STORE)
