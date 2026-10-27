"""Private, opt-in observer for the retained isolated executor case.

This module never participates in domain decisions. Configuration and the startup
receipt belong to the later isolated harness controller, not the public API.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

MAX_RECORDS = 256
MAX_BYTES = 256 * 1024
MAX_RECORD_BYTES = 2048
WINDOW_NS = 180 * 1_000_000_000
FINALIZE_NS = 15 * 1_000_000_000
PHASES = frozenset(
    (
        "hello_decode frame_decode acceptor hello_admitted frame_decoded "
        "generation_close handler_enter handler_failed "
        "validation_response inventory_decode inventory_adopt inventory_finalize "
        "ready_ack_send mark_ready held_resume answer_dispatch pending_command_replay "
        "envelope_decode conversation_lock_wait preflight assignment_check "
        "event_publication accept_return state_lookup reservation_wait state_lock_wait "
        "event_decode conversation_append publication_finalize transaction_begin "
        "domain_effect receipt_insert before_commit commit_enter commit_call_returned "
        "rollback_enter rollback_call_returned transaction_exit response_send_enter "
        "response_send_done response_send_failed original_command_emit "
        "command_outcome_seen receipt_lookup capture_start capture_final"
    ).split()
)
EXCEPTIONS: dict[type[BaseException], str] = {
    t: t.__name__
    for t in (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        ConnectionError,
        TimeoutError,
        RuntimeError,
        sqlite3.OperationalError,
        sqlite3.IntegrityError,
        sqlite3.DatabaseError,
        asyncio.CancelledError,
    )
}
FRAME_FUNCTIONS = frozenset(
    (
        "_session _handle _handle_message accept_envelope _accept_envelope "
        "receive_remote_backend_event preflight accept _transaction _commit commit "
        "publish_effect append _append_record _finalize_publication "
        "_publish_remote_backend_event"
    ).split()
)
SQLITE_CODES = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
    sqlite3.SQLITE_READONLY,
    sqlite3.SQLITE_CONSTRAINT,
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_CORRUPT,
}
SQLITE_NAMES = frozenset(
    (
        "SQLITE_BUSY SQLITE_LOCKED SQLITE_READONLY SQLITE_CONSTRAINT SQLITE_FULL "
        "SQLITE_IOERR SQLITE_CORRUPT"
    ).split()
)


@dataclass(frozen=True)
class CaptureConfig:
    sink: Path
    status: Path
    startup_receipt: Path
    diagnostic_revision: str
    mac_revision: str
    target: dict[str, Any]


def _private_json(path: Path) -> Any:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("oversized private input")
    return json.loads(raw)


def safe_call(observer: object | None, method: str, *args: Any, **kwargs: Any) -> Any:
    """Even an injected or broken observer cannot replace a domain exception."""
    if observer is None:
        return None
    try:
        return getattr(observer, method)(*args, **kwargs)
    except BaseException:
        return None


@contextmanager
def observe(observer: Observation | None, phase: str, *, role: str | None = None) -> Iterator[None]:
    safe_call(observer, "enter", phase, role=role)
    try:
        yield
    except BaseException as error:
        safe_call(observer, "fail", phase, error, role=role)
        raise
    else:
        safe_call(observer, "complete", phase, role=role)


def get_capture(db_path: str | None = None) -> Capture | None:
    path = os.environ.get("PANELS_DIAGNOSTIC_CAPTURE_CONFIG")
    if path is None and db_path is not None:
        path = str(Path(db_path).parent / "diagnostic-capture.json")
    if not path:
        return None
    try:
        raw = _private_json(Path(path))
        return Capture(
            CaptureConfig(
                Path(raw["sink"]),
                Path(raw["status"]),
                Path(raw["startup_receipt"]),
                raw["diagnostic_revision"],
                raw["mac_revision"],
                raw["target"],
            )
        )
    except BaseException:
        return None


def capture_hello(capture: Capture | None, identity: Any) -> Observation | None:
    return cast("Observation | None", safe_call(capture, "hello", identity))


def capture_request(capture: Capture | None, identity: Any, message: Any) -> Observation | None:
    return cast("Observation | None", safe_call(capture, "request", identity, message))


class Capture:
    def __init__(self, config: CaptureConfig, *, timer: bool = True) -> None:
        target = config.target
        if any(
            re.fullmatch(r"[0-9a-f]{40}", revision) is None
            for revision in (config.diagnostic_revision, config.mac_revision)
        ):
            raise ValueError("invalid revision")
        exact = {
            "sender_id": "c819dff02dc340df84e8179f61d26fc2",
            "conversation_id": "conv_2ea03c97da924b61abf1a0a8c4ec724d",
            "submission_id": "80c2ef60ce3947e4ad2c146eb343a45d",
            "sequence": 80,
            "kind": "backend_event",
            "method": "tool_call_started",
            "executor_incarnation": "2d89b304-9f78-434f-b5ae-8b1091f55c41",
            "payload_sha256": "cfb7edf8402904d5ad8858c061997a7934660332b7dccb701744f59e3692b431",
        }
        if any(target.get(key) != value for key, value in exact.items()):
            raise ValueError("invalid retained target")
        self.config = config
        self.created_ns = time.monotonic_ns()
        self.deadline_ns = self.created_ns + WINDOW_NS
        self.startup_ns: int | None = None
        self.lock = threading.RLock()
        self.records = self.bytes = self.dropped = 0
        self.truncated = self.closed = False
        self.generations: list[tuple[int, int]] = []
        self.setup_generation: int | None = None
        self.natural_generation: int | None = None
        self.last_phase: str | None = None
        self.target_seen = False
        self.setup_settled = False
        self.other_frames = 0
        self.receipt_checked = False
        self.target_requests: set[str] = set()
        self.resolution_in_progress = False
        self.pending_finish: str | None = None
        self.pending_terminal_ns: int | None = None
        self.last_completed_phase: str | None = None
        self.active: dict[str, Observation] = {}
        # Exclusive creation avoids appending to a previous capture or following a symlink.
        try:
            fd = os.open(config.sink, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except BaseException:
            self._status_failure()
            raise
        self.sink = os.fdopen(fd, "ab", buffering=0)
        self._record({"phase": "capture_start", "direct_host_evidence": "unavailable"})
        if timer:
            threading.Thread(target=self._watch, daemon=True, name="executor-capture").start()

    def _status_failure(self) -> None:
        try:
            fd = os.open(
                self.config.status, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
            )
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b'{"capture_status":"sink_failure"}\n')
        except BaseException:
            pass

    def _record(self, fields: dict[str, Any], *, final: bool = False) -> None:
        with self.lock:
            self._write_record(fields, final=final)

    def _write_record(self, fields: dict[str, Any], *, final: bool = False) -> None:
        if self.closed:
            return
        record = {
            "utc": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "process_id": os.getpid(),
            "diagnostic_revision": self.config.diagnostic_revision,
            "mac_revision": self.config.mac_revision,
            **fields,
        }
        encoded = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_RECORD_BYTES or (
            not final
            and (
                self.records >= MAX_RECORDS - 1
                or self.bytes + len(encoded) > MAX_BYTES - MAX_RECORD_BYTES
            )
        ):
            if final:
                self.closed = True
                self._status_failure()
                return
            self.dropped += 1
            self.truncated = True
            self.finish("inconclusive_setup" if self.natural_generation is None else "truncated")
            return
        try:
            self.sink.write(encoded)
            self.records += 1
            self.bytes += len(encoded)
        except BaseException:
            self.closed = True
            self._status_failure()
            try:
                self.sink.close()
            except BaseException:
                pass

    def _watch(self) -> None:
        while not self.closed:
            try:
                self.tick()
            except BaseException:
                with self.lock:
                    self.finish("capture_failure")
            time.sleep(0.1)

    def tick(self) -> None:
        with self.lock:
            if self.closed:
                return
            now = time.monotonic_ns()
            if self.startup_ns is None and self.config.startup_receipt.exists():
                try:
                    receipt = _private_json(self.config.startup_receipt)
                    stamp = receipt["monotonic_ns"]
                    valid = (
                        receipt["process_id"] == os.getpid()
                        and receipt["diagnostic_revision"] == self.config.diagnostic_revision
                        and type(stamp) is int
                        and self.created_ns <= stamp <= now
                    )
                    if not valid:
                        self.finish("inconclusive_setup")
                        return
                    self.startup_ns = stamp
                    self.deadline_ns = stamp + WINDOW_NS
                    # A late receipt cannot retrospectively invent missing generation coverage.
                    if any(admitted < stamp for _, admitted in self.generations):
                        self.finish("inconclusive_setup")
                        return
                    self._select_generations()
                except BaseException:
                    self.finish("inconclusive_setup")
                    return
            if self.pending_terminal_ns is not None and now >= self.pending_terminal_ns:
                self.finish("terminal_failure")
                return
            if now >= self.deadline_ns:
                self.finish(
                    "inconclusive_setup"
                    if self.startup_ns is None
                    or self.natural_generation is None
                    and self.setup_generation is not None
                    and not self.setup_settled
                    else "no_natural_opportunity"
                    if self.natural_generation is None
                    else "unfinished_operation"
                    if any(o.phase != "frame_decode" for o in self.active.values())
                    else "no_target_ingress"
                )

    def _select_generations(self) -> None:
        if self.startup_ns is None:
            return
        eligible = [g for g, stamp in self.generations if stamp >= self.startup_ns]
        self.setup_generation = eligible[0] if eligible else None
        self.natural_generation = eligible[1] if len(eligible) > 1 else None

    def hello(self, identity: Any) -> Observation | None:
        with self.lock:
            self.tick()
            if self.closed:
                return None
            if identity is None:
                return Observation(self, SimpleNamespace(connection_generation=None), False)
            if identity.executor_incarnation != self.config.target["executor_incarnation"]:
                self.finish("identity_changed")
                return None
            generation = identity.connection_generation
            if generation not in [g for g, _ in self.generations]:
                self.generations.append((generation, time.monotonic_ns()))
            self._select_generations()
            if self.natural_generation is not None and generation != self.natural_generation:
                return None
            observation = Observation(self, identity, False)
            observation.complete("hello_admitted")
            return observation

    def request(self, identity: Any, message: Any) -> Observation | None:
        with self.lock:
            self.tick()
            if self.closed or (
                self.natural_generation is not None
                and identity.connection_generation != self.natural_generation
            ):
                return None
            observation = Observation(self, identity, False)
            observation.connection = str(getattr(message, "kind", "")) in {
                "reconcile_inventory",
                "reconciliation_ready",
            }
            payload = getattr(message, "payload", {})
            envelope = payload.get("envelope", payload) if isinstance(payload, dict) else {}
            if isinstance(envelope, dict) and (
                (
                    envelope.get("sequence") == self.config.target["sequence"]
                    and envelope.get("conversation_id") == self.config.target["conversation_id"]
                    and envelope.get("sender_id") == self.config.target["sender_id"]
                )
                or envelope.get("submission_id") == self.config.target["submission_id"]
            ):
                observation.bind_envelope(envelope)
            else:
                self.other_frames = min(self.other_frames + 1, 2**31 - 1)
            if observation.target or observation.connection:
                observation.complete("frame_decoded")
            return observation

    def command(self, command_id: str, operation: str | None, conversation_id: str) -> None:
        safe_call(
            Observation(self, SimpleNamespace(connection_generation=None), False),
            "command",
            command_id,
            operation,
            conversation_id,
        )

    def finish(self, status: str) -> None:
        with self.lock:
            if (
                self.resolution_in_progress
                and time.monotonic_ns() < self.deadline_ns
                and status in {"terminal_failure", "target_settled", "no_target_ingress"}
            ):
                if self.pending_finish != "terminal_failure":
                    self.pending_finish = status
                return
            if self.closed:
                return
            self._record(
                {
                    "phase": "capture_final",
                    "capture_status": status,
                    "truncated": self.truncated,
                    "dropped_records": self.dropped,
                    "final_phase": self.last_phase,
                    "other_frames": self.other_frames,
                    "open_phases": [o.phase for o in list(self.active.values())[:8]],
                    "completion": "unknown" if self.active else "settled",
                    "last_completed_phase": self.last_completed_phase,
                },
                final=True,
            )
            self.closed = True
            try:
                self.sink.close()
            except BaseException:
                self._status_failure()


class Observation:
    def __init__(self, capture: Capture, identity: Any, target: bool) -> None:
        self.capture = capture
        self.identity = identity
        self.target = target
        self.request_id = uuid4().hex
        self.phase: str | None = None
        self.started: dict[str, int] = {}
        self.commit_status = "unknown"
        self.disposition: str | None = None
        self.send_status = "unknown"
        self.failed = False
        self.connection = True

    def hello_admitted(self, identity: Any) -> None:
        admitted = self.capture.hello(identity)
        if admitted is not None:
            self.identity = identity

    def bind_envelope(self, envelope: Any) -> None:
        def read(key: str) -> Any:
            return envelope.get(key) if isinstance(envelope, dict) else getattr(envelope, key, None)

        target = self.capture.config.target
        candidate = read("submission_id") == target["submission_id"] or (
            read("sequence") == target["sequence"]
            and read("conversation_id") == target["conversation_id"]
            and read("sender_id") == target["sender_id"]
        )
        if not candidate:
            return
        keys = (
            "sender_id",
            "conversation_id",
            "submission_id",
            "sequence",
            "kind",
            "payload_sha256",
        )
        if any(read(key) != target[key] for key in keys):
            self.capture.finish("target_mismatch")
            return
        payload = read("payload")
        method = payload.get("method") if isinstance(payload, dict) else None
        child = payload.get("child") if isinstance(payload, dict) else None
        valid_child = (
            isinstance(child, dict)
            and child.get("executor_incarnation") == target["executor_incarnation"]
            and child.get("child_id") == "b67b9942-bcc5-4925-b3bd-ce551df037a3"
            and child.get("conversation_id") == target["conversation_id"]
        )
        if method != target["method"] or not valid_child:
            self.capture.finish("target_mismatch")
            return
        self.target = True
        self.capture.target_seen = True
        self.capture.target_requests.add(self.request_id)

    def _event(
        self, phase: str, event: str, role: str | None = None, error: BaseException | None = None
    ) -> None:
        capture = self.capture
        with capture.lock:
            capture.tick()
            if capture.closed or phase not in PHASES:
                return
            detailed = self.target or phase not in {
                "envelope_decode",
                "conversation_lock_wait",
                "preflight",
                "assignment_check",
                "event_publication",
                "accept_return",
                "state_lookup",
                "reservation_wait",
                "state_lock_wait",
                "event_decode",
                "conversation_append",
                "publication_finalize",
                "transaction_begin",
                "domain_effect",
                "receipt_insert",
                "before_commit",
                "commit_enter",
                "commit_call_returned",
                "rollback_enter",
                "rollback_call_returned",
                "transaction_exit",
            }
            if not detailed or not (self.target or self.connection):
                return
            self.phase = phase
            capture.last_phase = phase
            if event == "complete":
                capture.last_completed_phase = phase
            fields: dict[str, Any] = {
                "phase": phase,
                "event": event,
                "site": phase,
                "epoch": "natural"
                if capture.natural_generation is not None
                and self.identity.connection_generation == capture.natural_generation
                else "setup",
                "generation": self.identity.connection_generation,
                "request_id": self.request_id,
                "identity_evidence": (
                    "executor_hello"
                    if self.identity.connection_generation is not None
                    else "vps_cached"
                ),
                "commit_status": self.commit_status,
                "receipt_disposition": self.disposition,
                "response_send_status": self.send_status,
            }
            if self.identity.connection_generation is not None:
                fields["executor_incarnation"] = capture.config.target["executor_incarnation"]
            if role in {"preflight", "acceptance"}:
                fields["transaction_role"] = role
            if self.target:
                fields.update(
                    {
                        key: capture.config.target[key]
                        for key in (
                            "sender_id",
                            "conversation_id",
                            "submission_id",
                            "sequence",
                            "kind",
                            "method",
                            "payload_sha256",
                        )
                    }
                )
            if event == "enter":
                self.started[phase] = time.monotonic_ns()
                capture.active[self.request_id] = self
            else:
                entered_phase = (
                    "response_send_enter"
                    if phase in {"response_send_done", "response_send_failed"}
                    else phase
                )
                started = self.started.pop(entered_phase, None)
                if started is not None:
                    fields["elapsed_ms"] = (time.monotonic_ns() - started) / 1e6
                if not self.started:
                    capture.active.pop(self.request_id, None)
            if error is not None:
                fields["exception_class"] = EXCEPTIONS.get(type(error), "other")
                frames = traceback.extract_tb(error.__traceback__)
                fields["source_frames"] = [
                    {
                        "module": "planner."
                        + "/".join(f.filename.split("/planner/")[-1].split("/")[:-1]).replace(
                            "/", "."
                        )
                        + "."
                        + Path(f.filename).stem,
                        "function": f.name,
                        "line": f.lineno,
                    }
                    for f in frames
                    if "/planner/" in f.filename
                    and f.name in FRAME_FUNCTIONS
                    and f.filename.split("/planner/")[-1]
                    in {
                        "executor/vps.py",
                        "executor/store.py",
                        "conversation/system.py",
                        "core/db.py",
                    }
                ][-8:]
                code = getattr(error, "sqlite_errorcode", None)
                name = getattr(error, "sqlite_errorname", None)
                if type(code) is int and code in SQLITE_CODES:
                    fields["sqlite_code"] = code
                if type(name) is str and name in SQLITE_NAMES:
                    fields["sqlite_name"] = name
            capture._record(fields)

    def enter(self, phase: str, *, role: str | None = None) -> None:
        self._event(phase, "enter", role)

    def complete(self, phase: str, *, role: str | None = None) -> None:
        if (
            phase == "pending_command_replay"
            and self.identity.connection_generation == self.capture.setup_generation
        ):
            self.capture.setup_settled = True
        if phase == "commit_call_returned" and role == "acceptance":
            self.commit_status = "commit_call_returned"
        if phase in {"response_send_done", "response_send_enter"}:
            self.send_status = "complete"
        self._event(phase, "complete", role)

    def fail(self, phase: str, error: BaseException, *, role: str | None = None) -> None:
        if phase in {
            "handler_failed",
            "response_send_failed",
            "generation_close",
            "validation_response",
        } or isinstance(error, asyncio.CancelledError):
            self.failed = True
        if phase.startswith("response_send"):
            self.send_status = "failed"
        self._event(phase, "failure", role, error)

    def receipt(self, receipt: Any) -> None:
        if not self.target:
            return
        target = self.capture.config.target
        if (receipt.sender_id, receipt.submission_id, receipt.payload_sha256) != (
            target["sender_id"],
            target["submission_id"],
            target["payload_sha256"],
        ):
            self.capture.finish("target_mismatch")
            return
        if receipt.disposition in {"accepted", "refused"}:
            self.disposition = str(receipt.disposition)
            self.commit_status = "durable_receipt"
        self.complete("accept_return")

    def command(self, command_id: str, operation: str | None, conversation_id: str) -> None:
        if conversation_id != self.capture.config.target["conversation_id"] or operation not in {
            "start",
            "turn",
            "stop",
            "cancel",
            "answer_permission_ask",
            "answer_user_input",
            "start_child",
            "write_prompt",
            "stop_child",
            "cancel_turn",
            None,
        }:
            return
        # IDs originate in existing local commands. Never copy free-form remote text.
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", command_id) is None:
            return
        identifier = command_id
        with self.capture.lock:
            self.capture.tick()
            self.capture._record(
                {
                    "phase": "command_outcome_seen"
                    if operation is None
                    else "original_command_emit",
                    "command_id": identifier,
                    "operation": operation,
                }
            )

    def command_outcome(self, outcome: Any) -> None:
        from uuid import UUID

        child = outcome.child
        target = self.capture.config.target
        if (
            child.executor_incarnation != target["executor_incarnation"]
            or child.child_id != "b67b9942-bcc5-4925-b3bd-ce551df037a3"
            or child.conversation_id != target["conversation_id"]
        ):
            return
        try:
            identifier = str(UUID(outcome.command_id))
        except (ValueError, TypeError, AttributeError):
            return
        self.command(identifier, None, child.conversation_id)

    def generation_closed(self, identity: Any, error: BaseException | None = None) -> None:
        if identity is not None and identity.connection_generation == self.capture.setup_generation:
            self.capture.setup_settled = True
        if error is not None:
            self.fail("generation_close", error)
        else:
            self.complete("generation_close")
        if (
            identity is not None
            and identity.connection_generation == self.capture.natural_generation
        ):
            if self.capture.target_requests:
                self.capture.pending_terminal_ns = min(
                    self.capture.deadline_ns, time.monotonic_ns() + FINALIZE_NS
                )
            else:
                self.capture.finish("terminal_failure" if error else "no_target_ingress")

    def settled(self, db_path: str) -> None:
        capture = self.capture
        with capture.lock:
            if (
                self.failed
                and (self.target or self.connection)
                and self.identity.connection_generation == capture.natural_generation
            ):
                capture.pending_finish = "terminal_failure"
            resolve = (
                self.target
                and self.disposition is None
                and not capture.receipt_checked
                and not capture.closed
            )
            if resolve:
                capture.receipt_checked = True
                capture.resolution_in_progress = True
        if resolve:
            deadline = min(capture.deadline_ns, time.monotonic_ns() + FINALIZE_NS)
            connection: sqlite3.Connection | None = None
            try:
                if time.monotonic_ns() >= deadline:
                    raise TimeoutError
                connection = sqlite3.connect(
                    Path(db_path).absolute().as_uri() + "?mode=ro", uri=True, timeout=0
                )
                connection.execute("PRAGMA query_only=ON")
                connection.set_progress_handler(lambda: int(time.monotonic_ns() >= deadline), 1)
                target = capture.config.target
                row = connection.execute(
                    "SELECT sequence,payload_sha256,disposition,accepted_at_vps_ms FROM "
                    "executor_receipts WHERE sender_id=? AND submission_id=?",
                    (target["sender_id"], target["submission_id"]),
                ).fetchone()
                if row is not None:
                    if row[0] != target["sequence"] or row[1] != target["payload_sha256"]:
                        capture.finish("target_mismatch")
                        return
                    if row[2] in {"accepted", "refused"}:
                        self.disposition, self.commit_status = row[2], "durable_receipt"
                self.complete("receipt_lookup")
            except BaseException as error:
                self.fail("receipt_lookup", error)
            finally:
                if connection is not None:
                    connection.close()
                with capture.lock:
                    capture.resolution_in_progress = False
                    if capture.pending_finish is not None:
                        status, capture.pending_finish = capture.pending_finish, None
                        capture.finish(status)
        with capture.lock:
            capture.target_requests.discard(self.request_id)
        if self.identity.connection_generation == capture.natural_generation and (
            self.failed
            and (self.target or self.connection)
            or self.target
            and self.send_status != "unknown"
        ):
            capture.finish("terminal_failure" if self.failed else "target_settled")
