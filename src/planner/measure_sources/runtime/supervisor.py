"""Own both child processes, all pipes, cancellation, and one absolute deadline."""

from __future__ import annotations

import hashlib
import math
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import BinaryIO

from planner.credentials.contracts import ResolvedCredential
from planner.measure_sources import sandbox
from planner.measure_sources.configuration import (
    BROKER_CONFIGURATION_BYTES,
    DEFAULT_LIMITS,
    PIPE_CHUNK_BYTES,
    POLL_SECONDS,
    REAP_SECONDS,
    SCRIPT_BYTES,
    SCRIPT_CONTEXT_BYTES,
)
from planner.measure_sources.contracts import (
    BrokerConfiguration,
    FailureCode,
    HttpMethod,
    LatestReply,
    RecordReply,
    RunAdmission,
    RunFailure,
    RunMetrics,
    RunResult,
    RuntimeBudget,
    ScriptContext,
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


class _Child:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.cpu_seconds = 0.0
        self.status: int | None = None
        self.namespace_pid: int | None = None
        self.namespace_pidfd: int | None = None

    def poll(self) -> int | None:
        if self.status is None:
            pid, status, usage = os.wait4(self.process.pid, os.WNOHANG)
            if pid:
                self.status = os.waitstatus_to_exitcode(status)
                self.process.returncode = self.status
                self.cpu_seconds = usage.ru_utime + usage.ru_stime
        return self.status

    def identify_namespace(self, pid: int) -> None:
        # The trusted bwrap info channel identifies its direct namespace child.
        # Hold a pidfd so cleanup cannot signal a recycled numeric PID.
        try:
            descriptor = os.pidfd_open(pid)
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[1]) != self.process.pid:
                os.close(descriptor)
                return
        except (OSError, ValueError):
            if "descriptor" in locals():
                os.close(descriptor)
            return
        self.namespace_pid = pid
        self.namespace_pidfd = descriptor

    def stop(self) -> None:
        if self.poll() is None and self.namespace_pidfd is not None:
            try:
                signal.pidfd_send_signal(self.namespace_pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Let the monitor reap PID1 and return its resource usage first.
            deadline = time.monotonic() + REAP_SECONDS
            while self.poll() is None and time.monotonic() < deadline:
                time.sleep(0.002)
        if self.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.kill(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + REAP_SECONDS
            while self.poll() is None and time.monotonic() < deadline:
                time.sleep(0.002)
        if self.namespace_pidfd is not None:
            os.close(self.namespace_pidfd)
            self.namespace_pidfd = None
        for stream in (self.stdin, self.stdout, self.stderr):
            if not stream.closed:
                stream.close()


def _spawn(command: list[str], pass_fds: tuple[int, ...] = ()) -> _Child:
    return _Child(
        subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=pass_fds,
            env={},
            start_new_session=True,
        )
    )


def _broker_command(admission: RunAdmission) -> list[str]:
    limits = admission.limits
    # Static trusted startup code; argv holds only resource numbers, never secrets.
    bootstrap = (
        "import resource;"
        f"resource.setrlimit(resource.RLIMIT_AS,({limits.broker_memory_bytes},)*2);"
        f"resource.setrlimit(resource.RLIMIT_CPU,({limits.broker_cpu_seconds},)*2);"
        f"resource.setrlimit(resource.RLIMIT_NOFILE,({limits.broker_file_descriptors},)*2);"
        "resource.setrlimit(resource.RLIMIT_CORE,(0,0));"
        "from planner.measure_sources.broker import main;main()"
    )
    return [sys.executable, "-I", "-c", bootstrap]


class Runtime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancellations: dict[str, threading.Event] = {}
        self._pre_cancelled: set[str] = set()

    def cancel(self, run_id: str) -> None:
        with self._lock:
            event = self._cancellations.get(run_id)
            if event is not None:
                event.set()
            else:
                self._pre_cancelled.add(run_id)

    def execute(
        self, admission: RunAdmission, credentials: tuple[ResolvedCredential, ...]
    ) -> RunResult:
        cancellation = threading.Event()
        with self._lock:
            if admission.run.id in self._cancellations:
                return RunResult(
                    (), RunFailure(FailureCode.runtime_failure), RunMetrics(0, 0, 0, 0, 0, 0)
                )
            if admission.run.id in self._pre_cancelled:
                self._pre_cancelled.remove(admission.run.id)
                cancellation.set()
            self._cancellations[admission.run.id] = cancellation
        try:
            return _Execution(admission, credentials, cancellation).run()
        finally:
            with self._lock:
                self._cancellations.pop(admission.run.id, None)


class _Execution:
    def __init__(
        self,
        admission: RunAdmission,
        credentials: tuple[ResolvedCredential, ...],
        cancellation: threading.Event,
    ) -> None:
        self.admission = admission
        self.credentials = credentials
        self.cancellation = cancellation
        self.limits = admission.limits
        self.started = time.monotonic()
        self.deadline = min(admission.deadline_monotonic, self.started + self.limits.wall_seconds)
        self.selector = selectors.DefaultSelector()
        self.children: dict[str, _Child] = {}
        self.pending: dict[str, bytearray] = {"source": bytearray(), "broker": bytearray()}
        self.buffers: dict[str, bytearray] = {"source": bytearray(), "broker": bytearray()}
        self.readers: set[str] = set()
        self.observations: list[CandidateObservation] = []
        self.info = bytearray()
        self.info_stream: BinaryIO | None = None
        self.ready = False
        self.sequence = 0
        self.awaiting: int | None = None
        self.requests = 0
        self.response_bytes = 0
        self.child_bytes = 0
        self.broker_bytes = 0
        self.output_ids = {item.measure_id for item in admission.run.required_outputs}

    def run(self) -> RunResult:
        failure: RunFailure | None = None
        try:
            self._validate_admission()
            with tempfile.TemporaryDirectory(prefix="panels-measure-") as name:
                directory = Path(name)
                source_path = sandbox.private_source(directory, self.admission.script_text)
                with (directory / "filter.bpf").open("w+b") as handle:
                    sandbox.write_filter(handle)
                    self._launch("broker", _broker_command(self.admission))
                    info_read, info_write = os.pipe2(os.O_CLOEXEC)
                    try:
                        self._launch(
                            "source",
                            sandbox.command(source_path, handle.fileno(), self.limits, info_write),
                            (handle.fileno(), info_write),
                        )
                    except BaseException:
                        os.close(info_read)
                        raise
                    finally:
                        os.close(info_write)
                    info_stream = os.fdopen(info_read, "rb", buffering=0)
                    self.info_stream = info_stream
                    os.set_blocking(info_read, False)
                    self.readers.add("source:info")
                    self.selector.register(info_stream, selectors.EVENT_READ, "source:info")
                configuration = BrokerConfiguration(
                    self.admission.scope,
                    self.admission.destinations,
                    self.credentials,
                    self.limits,
                    self.deadline,
                )
                self._queue(
                    "broker",
                    encode(configuration),
                    BROKER_CONFIGURATION_BYTES,
                    RuntimeBudget.result_bytes,
                )
                context = ScriptContext(
                    self.admission.run.id,
                    self.admission.run.frozen_at,
                    self.admission.run.required_outputs,
                    self.admission.run.history,
                    tuple(grant.id for grant in self.admission.destinations),
                )
                self._queue(
                    "source", encode(context), SCRIPT_CONTEXT_BYTES, RuntimeBudget.result_bytes
                )
                self._loop()
                if len(encode(self.observations)) > self.limits.result_bytes:
                    raise refuse(FailureCode.budget_exhausted, RuntimeBudget.result_bytes)
        except Refusal as refusal:
            failure = refusal.failure
        except OSError:
            failure = RunFailure(
                FailureCode.sandbox_unavailable if not self.ready else FailureCode.runtime_failure
            )
        except Exception:
            failure = RunFailure(FailureCode.runtime_failure)
        finally:
            for child in self.children.values():
                child.stop()
            if self.info_stream is not None and not self.info_stream.closed:
                self.info_stream.close()
            self.selector.close()
        source = self.children.get("source")
        broker = self.children.get("broker")
        metrics = RunMetrics(
            self.requests,
            self.response_bytes,
            self.child_bytes,
            round((time.monotonic() - self.started) * 1000),
            round(source.cpu_seconds * 1000) if source else 0,
            round(broker.cpu_seconds * 1000) if broker else 0,
        )
        return RunResult(tuple(self.observations) if failure is None else (), failure, metrics)

    def _validate_admission(self) -> None:
        for item in fields(self.limits):
            value = getattr(self.limits, item.name)
            if value < 0 or value > getattr(DEFAULT_LIMITS, item.name):
                raise refuse(FailureCode.runtime_failure)
        source = self.admission.script_text.encode()
        if (
            len(source) > SCRIPT_BYTES
            or hashlib.sha256(source).hexdigest() != self.admission.run.script_sha256
        ):
            raise refuse(FailureCode.invalid_output)
        if len(self.output_ids) != len(self.admission.run.required_outputs):
            raise refuse(FailureCode.invalid_output)

    def _launch(self, name: str, command: list[str], pass_fds: tuple[int, ...] = ()) -> None:
        self._check_deadline()
        child = _spawn(command, pass_fds)
        self.children[name] = child
        for stream in (child.stdin, child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False)
        for kind, stream in (("stdout", child.stdout), ("stderr", child.stderr)):
            key = name + ":" + kind
            self.readers.add(key)
            self.selector.register(stream, selectors.EVENT_READ, key)

    def _queue(self, name: str, data: bytes, limit: int, budget: RuntimeBudget) -> None:
        if len(data) > limit:
            raise refuse(FailureCode.budget_exhausted, budget)
        self.pending[name].extend(data)
        stream = self.children[name].stdin
        try:
            self.selector.get_key(stream)
        except KeyError:
            self.selector.register(stream, selectors.EVENT_WRITE, name + ":stdin")

    def _check_deadline(self) -> None:
        if self.cancellation.is_set():
            raise refuse(FailureCode.interrupted)
        if time.monotonic() >= self.deadline:
            raise refuse(FailureCode.budget_exhausted, RuntimeBudget.wall_seconds)

    def _loop(self) -> None:
        while True:
            self._check_deadline()
            for key, _ in self.selector.select(
                max(0, min(POLL_SECONDS, self.deadline - time.monotonic()))
            ):
                name, kind = str(key.data).split(":")
                if kind == "stdin":
                    self._write(name)
                else:
                    self._read(name, kind, key.fileobj)  # type: ignore[arg-type]
            source = self.children["source"]
            broker = self.children["broker"]
            source_status = source.poll()
            broker_status = broker.poll()
            source_drained = not any(x.startswith("source:") for x in self.readers)
            broker_drained = not any(x.startswith("broker:") for x in self.readers)
            if source_status is not None and source_status != 0 and source_drained:
                if source.cpu_seconds >= self.limits.script_cpu_seconds:
                    raise refuse(FailureCode.budget_exhausted, RuntimeBudget.script_cpu_seconds)
                raise refuse(
                    FailureCode.runtime_failure if self.ready else FailureCode.sandbox_unavailable
                )
            if broker_status is not None and broker_drained:
                if broker.cpu_seconds >= self.limits.broker_cpu_seconds:
                    raise refuse(FailureCode.budget_exhausted, RuntimeBudget.broker_cpu_seconds)
                raise refuse(FailureCode.runtime_failure)
            if source_status == 0 and not any(x.startswith("source:") for x in self.readers):
                if not self.ready or self.awaiting is not None or self.buffers["source"]:
                    raise refuse(FailureCode.malformed_protocol)
                return

    def _write(self, name: str) -> None:
        stream = self.children[name].stdin
        try:
            count = os.write(stream.fileno(), self.pending[name][:PIPE_CHUNK_BYTES])
        except BlockingIOError:
            return
        except BrokenPipeError:
            raise refuse(FailureCode.runtime_failure) from None
        del self.pending[name][:count]
        if not self.pending[name]:
            self.selector.unregister(stream)

    def _read(self, name: str, kind: str, stream: BinaryIO) -> None:
        try:
            chunk = os.read(stream.fileno(), PIPE_CHUNK_BYTES)
        except BlockingIOError:
            return
        if not chunk:
            self.selector.unregister(stream)
            self.readers.remove(name + ":" + kind)
            stream.close()
            if kind == "info" and self.info:
                data = object_value(decode(bytes(self.info)))
                self.children["source"].identify_namespace(integer(data.get("child-pid")))
            if kind == "stdout" and self.buffers[name]:
                raise refuse(FailureCode.malformed_protocol)
            return
        if kind == "info":
            self.info.extend(chunk)
            if len(self.info) > 4096:
                raise refuse(FailureCode.sandbox_unavailable)
            return
        if name == "source":
            self.child_bytes += len(chunk)
            if self.child_bytes > self.limits.child_output_bytes:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.child_output_bytes)
        else:
            self.broker_bytes += len(chunk)
            if self.broker_bytes > self.limits.total_response_bytes + self.limits.response_bytes:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.total_response_bytes)
        if kind == "stderr":
            return
        buffer = self.buffers[name]
        buffer.extend(chunk)
        limit = self.limits.request_frame_bytes if name == "source" else self.limits.response_bytes
        while b"\n" in buffer:
            index = buffer.index(10)
            frame = bytes(buffer[:index])
            del buffer[: index + 1]
            if len(frame) + 1 > limit:
                budget = (
                    RuntimeBudget.request_frame_bytes
                    if name == "source"
                    else RuntimeBudget.response_bytes
                )
                raise refuse(FailureCode.budget_exhausted, budget)
            if name == "source":
                self._source_frame(frame)
            else:
                self._broker_frame(frame)
        if len(buffer) > limit:
            budget = (
                RuntimeBudget.request_frame_bytes
                if name == "source"
                else RuntimeBudget.response_bytes
            )
            raise refuse(FailureCode.budget_exhausted, budget)

    def _source_frame(self, frame: bytes) -> None:
        data = object_value(decode(frame, depth=self.limits.json_depth))
        if not self.ready:
            if data != {"op": "ready"}:
                raise refuse(FailureCode.malformed_protocol)
            self.ready = True
            return
        if data == {"op": "memory_exhausted"}:
            raise refuse(FailureCode.budget_exhausted, RuntimeBudget.script_memory_bytes)
        if self.awaiting is not None or self.pending["source"]:
            raise refuse(FailureCode.malformed_protocol)
        identifier = integer(data.get("id"))
        if identifier != self.sequence + 1:
            raise refuse(FailureCode.malformed_protocol)
        self.sequence = identifier
        operation = string(data.get("op"))
        if operation == "request":
            if set(data) != {"op", "id", "grant_id", "url", "method", "json_body"}:
                raise refuse(FailureCode.malformed_protocol)
            string(data["grant_id"])
            string(data["url"])
            try:
                HttpMethod(string(data["method"]))
            except ValueError:
                raise refuse(FailureCode.unauthorized_request) from None
            self.requests += 1
            if self.requests > self.limits.request_count:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.request_count)
            self.awaiting = identifier
            self._queue(
                "broker",
                frame + b"\n",
                self.limits.request_frame_bytes,
                RuntimeBudget.request_frame_bytes,
            )
            return
        measure_id = string(data.get("measure_id"))
        if measure_id not in self.output_ids:
            raise refuse(FailureCode.invalid_output)
        if operation == "latest" and set(data) == {"op", "id", "measure_id"}:
            previous = [item for item in self.admission.previous if item.measure_id == measure_id]
            observation = max(previous, key=lambda item: item.date) if previous else None
            reply = encode(LatestReply(identifier, observation))
        elif operation == "record" and set(data) == {
            "op",
            "id",
            "measure_id",
            "date",
            "value",
            "unavailable_reason",
        }:
            date_value = string(data["date"])
            try:
                if date.fromisoformat(date_value).isoformat() != date_value:
                    raise ValueError
                raw_value = data["value"]
                value = None
                if raw_value is not None:
                    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                        raise ValueError
                    value = float(raw_value)
                    if not math.isfinite(value):
                        raise ValueError
                reason = (
                    HistoricalUnavailableReason(string(data["unavailable_reason"]))
                    if data["unavailable_reason"] is not None
                    else None
                )
            except (ValueError, OverflowError):
                raise refuse(FailureCode.invalid_output) from None
            if (value is None) != (reason is not None):
                raise refuse(FailureCode.invalid_output)
            self.observations.append(CandidateObservation(measure_id, date_value, value, reason))
            if len(self.observations) > self.limits.observation_count:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.observation_count)
            reply = encode(RecordReply(identifier))
        else:
            raise refuse(FailureCode.malformed_protocol)
        self._queue("source", reply, self.limits.response_bytes, RuntimeBudget.response_bytes)

    def _broker_frame(self, frame: bytes) -> None:
        data = object_value(decode(frame, depth=self.limits.json_depth + 4))
        if data.get("op") == "failure":
            raise Refusal(failure_from_wire(data.get("failure")))
        if self.awaiting is None or data.get("op") != "request" or data.get("id") != self.awaiting:
            raise refuse(FailureCode.malformed_protocol)
        self.response_bytes += len(frame) + 1
        if self.response_bytes > self.limits.total_response_bytes:
            raise refuse(FailureCode.budget_exhausted, RuntimeBudget.total_response_bytes)
        self.awaiting = None
        self._queue(
            "source", frame + b"\n", self.limits.response_bytes, RuntimeBudget.response_bytes
        )
