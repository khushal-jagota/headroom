"""Ordinary trusted-script process, named credential lookup, and owned cleanup."""

from __future__ import annotations

import math
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date

from planner.credentials.contracts import CredentialReference, CredentialResolver, CredentialScheme
from planner.measure_sources.configuration import PIPE_CHUNK_BYTES, POLL_SECONDS
from planner.measure_sources.contracts import (
    FailureCode,
    RunAdmission,
    RunFailure,
    RunMetrics,
    RunResult,
    RuntimeBudget,
)
from planner.measure_sources.logic.protocol import (
    Refusal,
    decode,
    encode,
    failure_from_wire,
    integer,
    object_value,
    refuse,
    string,
)
from planner.measures.contracts import CandidateObservation, HistoricalUnavailableReason


class Runtime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, threading.Event] = {}
        self._canceled: set[str] = set()

    def cancel(self, run_id: str) -> None:
        with self._lock:
            event = self._runs.get(run_id)
            if event is None:
                self._canceled.add(run_id)
            else:
                event.set()

    def execute(self, admission: RunAdmission, credentials: CredentialResolver) -> RunResult:
        stop = threading.Event()
        with self._lock:
            if admission.run.id in self._canceled:
                stop.set()
                self._canceled.remove(admission.run.id)
            self._runs[admission.run.id] = stop
        try:
            return _Execution(admission, credentials, stop).run()
        finally:
            with self._lock:
                self._runs.pop(admission.run.id, None)


class _Execution:
    def __init__(
        self,
        admission: RunAdmission,
        credentials: CredentialResolver,
        stop: threading.Event,
    ) -> None:
        self.admission = admission
        self.credentials = credentials
        self.stop = stop
        self.started = time.monotonic()
        self.deadline = min(
            admission.deadline_monotonic,
            self.started + admission.limits.wall_seconds,
        )
        self.observations: list[CandidateObservation] = []
        self.outputs = {item.measure_id for item in admission.run.required_outputs}
        self.output_bytes = 0
        self.requests = 0
        self.response_bytes = 0
        self.cpu_milliseconds = 0
        self.done = False
        self.failure: RunFailure | None = None
        self.pending = bytearray()
        self.buffer = bytearray()

    def run(self) -> RunResult:
        process: subprocess.Popen[bytes] | None = None
        parent, child = socket.socketpair()
        try:
            self._check_stop()
            process = subprocess.Popen(
                [sys.executable, "-m", "planner.measure_sources.bootstrap", str(child.fileno())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                pass_fds=(child.fileno(),),
                start_new_session=True,
            )
            child.close()
            self.pending.extend(
                encode(
                    {
                        "script": self.admission.script_text,
                        "timeout_seconds": self.admission.limits.wall_seconds,
                        "context": {
                            "run_id": self.admission.run.id,
                            "frozen_at": self.admission.run.frozen_at,
                            "required_outputs": self.admission.run.required_outputs,
                            "history": self.admission.run.history,
                        },
                    }
                )
            )
            self._loop(process, parent)
        except Refusal as error:
            self.failure = error.failure
        except Exception:
            self.failure = RunFailure(FailureCode.runtime_failure)
        finally:
            parent.close()
            child.close()
            if process is not None:
                _cleanup(process)
        metrics = RunMetrics(
            self.requests,
            self.response_bytes,
            self.output_bytes,
            int((time.monotonic() - self.started) * 1000),
            self.cpu_milliseconds,
        )
        return RunResult(
            tuple(self.observations) if self.failure is None else (), self.failure, metrics
        )

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise refuse(FailureCode.interrupted)
        if time.monotonic() >= self.deadline:
            raise refuse(FailureCode.budget_exhausted, RuntimeBudget.wall_seconds)

    def _loop(self, process: subprocess.Popen[bytes], channel: socket.socket) -> None:
        channel.setblocking(False)
        with selectors.DefaultSelector() as selector:
            selector.register(channel, selectors.EVENT_READ | selectors.EVENT_WRITE, "control")
            assert process.stdout is not None and process.stderr is not None
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, "output")
            while not self.done:
                self._check_stop()
                for key, events in selector.select(
                    max(0, min(POLL_SECONDS, self.deadline - time.monotonic()))
                ):
                    if key.data == "control":
                        if events & selectors.EVENT_WRITE and self.pending:
                            sent = channel.send(self.pending[:PIPE_CHUNK_BYTES])
                            del self.pending[:sent]
                        if events & selectors.EVENT_READ:
                            chunk = channel.recv(PIPE_CHUNK_BYTES)
                            if not chunk:
                                if not self.done:
                                    raise refuse(FailureCode.runtime_failure)
                                break
                            self.buffer.extend(chunk)
                            while b"\n" in self.buffer:
                                raw, _, rest = self.buffer.partition(b"\n")
                                self.buffer = bytearray(rest)
                                self._frame(bytes(raw))
                        selector.modify(
                            channel,
                            selectors.EVENT_READ | (selectors.EVENT_WRITE if self.pending else 0),
                            "control",
                        )
                    else:
                        chunk = os.read(key.fd, PIPE_CHUNK_BYTES)
                        self.output_bytes += len(chunk)
                        if not chunk:
                            selector.unregister(key.fileobj)
                # EOF on the control channel classifies an exited child after its
                # final structured message has drained. Do not race poll() here.

    def _frame(self, raw: bytes) -> None:
        data = object_value(decode(raw))
        op = string(data.get("op"))
        if op == "done":
            self.failure = (
                failure_from_wire(data["failure"]) if data.get("failure") is not None else None
            )
            self.requests = integer(data.get("request_count"))
            self.response_bytes = integer(data.get("response_bytes"))
            self.cpu_milliseconds = integer(data.get("cpu_milliseconds"))
            self.done = True
            return
        if op == "credential":
            try:
                value = object_value(data.get("reference"))
                reference = CredentialReference(
                    CredentialScheme(string(value.get("scheme"))),
                    string(value.get("name")),
                )
                resolved = self.credentials.resolve(reference)
                reply = {
                    "value": {
                        "kind": resolved.metadata.auth_kind.value,
                        "secret": asdict(resolved.secret),
                    },
                }
            except Exception:
                self.pending.extend(encode({"failure": "credential_unavailable"}))
            else:
                self.pending.extend(encode(reply))
            return
        measure_id = string(data.get("measure_id"))
        if measure_id not in self.outputs:
            self.pending.extend(encode({"failure": "invalid_output"}))
        elif op == "latest":
            values = [item for item in self.admission.previous if item.measure_id == measure_id]
            observation = max(values, key=lambda item: item.date) if values else None
            self.pending.extend(encode({"value": observation}))
        elif op == "record":
            try:
                self.observations.append(_candidate(data))
            except (ValueError, OverflowError, Refusal):
                self.pending.extend(encode({"failure": "invalid_output"}))
            else:
                self.pending.extend(encode({"value": None}))
        else:
            raise refuse(FailureCode.malformed_protocol)


def _candidate(data: Mapping[str, object]) -> CandidateObservation:
    date_value = string(data.get("date"))
    if date.fromisoformat(date_value).isoformat() != date_value:
        raise ValueError
    raw_value = data.get("value")
    value = None
    if raw_value is not None:
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError
    raw_reason = data.get("unavailable_reason")
    reason = HistoricalUnavailableReason(string(raw_reason)) if raw_reason is not None else None
    if (value is None) != (reason is not None):
        raise ValueError
    return CandidateObservation(string(data.get("measure_id")), date_value, value, reason)


def _cleanup(process: subprocess.Popen[bytes]) -> None:
    # The group was created for this run. Include ordinary child processes it spawned.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
