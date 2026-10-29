"""Temporary destination-private comparison, with no collection or authority.

Only fixed finite classifications escape. Private inputs are borrowed references,
never serialized, hashed, read again, or retained after ``finish``.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields
from pathlib import Path
from typing import cast

from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution.claude_input_contracts import (
    ClaudeCredentialMarker,
    ClaudeInputDirectory,
    ClaudeInputDocument,
    ClaudeInputLocation,
    ClaudeInputVector,
    ClaudeJsonValue,
)
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding, LaunchContext
from planner.conversation.model_resolution.diagnostic_contracts import (
    _ROLE_NAMES,
    _ROLES,
    DiagnosticStatus,
)
from planner.conversation.model_resolution.diagnostic_contracts import (
    DiagnosticState as DiagnosticState,
)
from planner.conversation.model_resolution.diagnostic_contracts import (
    unavailable_record as unavailable_record,
)
from planner.conversation.model_resolution.diagnostic_contracts import (
    validate_record as validate_record,
)
from planner.conversation.model_resolution.logic.claude_configuration import ClaudeConfigurationRole
from planner.executor.contracts import ExecutorIdentity, JsonObject
from planner.tickets.contracts import TicketLocale


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticCapture:
    context: LaunchContext
    environment: tuple[tuple[str, str], ...]
    vector: ClaudeInputVector | None


type _Record = (
    DiagnosticCapture | LaunchContext | CatalogWorkspaceBinding | ExecutorIdentity
    | ClaudeInputVector | ClaudeInputDocument | ClaudeInputDirectory | ClaudeInputLocation
    | ClaudeCredentialMarker | ClaudeJsonValue
)

_RECORD_TYPES = (
    DiagnosticCapture, LaunchContext, CatalogWorkspaceBinding, ExecutorIdentity,
    ClaudeInputVector, ClaudeInputDocument, ClaudeInputDirectory, ClaudeInputLocation,
    ClaudeCredentialMarker, ClaudeJsonValue,
)


# Container item expectations are checked during the existing bounded walk.
# They add no traversal of private data and never invoke arbitrary equality.
_FIELD_SHAPES: dict[type, dict[str, str]] = {
    DiagnosticCapture: {"context": "context", "environment": "environment", "vector": "vector"},
    LaunchContext: {
        "context_ref": "nonempty_string", "destination": "destination", "backend_key": "backend",
        "executor_identity": "optional_executor", "workspace": "workspace",
        "localized_workspace_folder": "path", "executable_identity": "optional_nonempty_string",
        "executable_version": "optional_nonempty_string",
        "provider_scope": "optional_nonempty_string",
        "account_generation": "absent",
    },
    CatalogWorkspaceBinding: {
        "workspace_folder": "path", "workspace_path_policy": "path_policy",
        "workspace_home_relative": "optional_string",
    },
    ExecutorIdentity: {
        "protocol_version": "integer", "application_revision": "string",
        "spool_format_version": "integer", "executor_incarnation": "string",
        "connection_generation": "integer",
    },
    ClaudeInputVector: {
        "global_selection": "string", "documents": "documents", "directories": "directories",
        "credential": "credential",
    },
    ClaudeInputDocument: {"role": "role", "location": "location", "content": "content"},
    ClaudeInputDirectory: {"location": "location", "candidates": "paths"},
    ClaudeInputLocation: {"logical": "path", "resolved": "path"},
    ClaudeCredentialMarker: {
        "location": "location", "resolved_parent": "path", "metadata": "metadata",
    },
}
_ITEM_SHAPES = {
    "environment": "environment_pair", "environment_pair": "string",
    "documents": "document", "directories": "directory", "paths": "path", "metadata": "integer",
}
_SHAPE_TYPES: dict[str, type] = {
    "context": LaunchContext, "vector": ClaudeInputVector, "document": ClaudeInputDocument,
    "directory": ClaudeInputDirectory, "credential": ClaudeCredentialMarker,
    "location": ClaudeInputLocation, "content": ClaudeJsonValue, "string": str, "integer": int,
    "workspace": CatalogWorkspaceBinding, "optional_executor": ExecutorIdentity,
    "destination": TicketLocale, "backend": ConversationBackendKey,
    "path_policy": WorkspacePathPolicy,
    "nonempty_string": str, "optional_nonempty_string": str, "optional_string": str,
    "absent": type(None),
}
_OPTIONAL_SHAPES = (
    "vector", "content", "metadata", "optional_executor",
    "optional_nonempty_string", "optional_string",
)


# Exactly the eight computed facts and eight role facts. Placeholders have no bit.
_FACT_MASKS = {key: 1 << index for index, key in enumerate((
    "executable_identity", "executable_version", "launch_environment", "workspace",
    "executor_identity", "candidate_directory", "credential_metadata", "source_selection",
))}
_ROLE_MASKS = {role: (1 << (8 + 2 * index), 1 << (9 + 2 * index))
               for index, role in enumerate(_ROLE_NAMES)}
_ALL_MASK = (1 << 16) - 1
_DIRECTORY_MASK = _FACT_MASKS["candidate_directory"] | _FACT_MASKS["source_selection"]


def _field_mask(value: object, name: str, inherited: int) -> int:
    """Map borrowed fields to finite outputs, without retaining private paths."""
    if type(value) is DiagnosticCapture:
        return _FACT_MASKS["launch_environment"] if name == "environment" else _ALL_MASK
    if type(value) is LaunchContext:
        if name in ("workspace", "localized_workspace_folder"):
            return _FACT_MASKS["workspace"]
        if name in ("destination", "executor_identity"):
            return _FACT_MASKS["executor_identity"]
        return _FACT_MASKS.get(name, _ALL_MASK)
    if type(value) is ClaudeInputVector:
        return {"global_selection": _FACT_MASKS["source_selection"],
                "directories": _DIRECTORY_MASK,
                "credential": _FACT_MASKS["credential_metadata"]}.get(name, _ALL_MASK)
    if type(value) is ClaudeInputDocument:
        source, content = _ROLE_MASKS[value.role]
        return content if name == "content" else source | content | _FACT_MASKS["source_selection"]
    return inherited


class _Limit(Exception):
    """Only the finite first failing bound may escape private comparison."""

    def __init__(self, status: DiagnosticStatus) -> None:
        self.status = status


def _clock_value(clock: Callable[[], float]) -> float:
    value = clock()
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("Invalid diagnostic clock")
    return value


class _Budget:
    def __init__(self, clock: Callable[[], float], elapsed: float = 0.0) -> None:
        self.elapsed = elapsed
        self.clock = clock
        self.last = _clock_value(clock)
        self.started: float | None = None
        self.exhaustion: DiagnosticStatus | None = None
        self.units = 0
        self.nodes = 0
        self.documents = 0
        self.candidates = 0
        self.scalars = 0
        self.blocked = 0
        self.first_limit: DiagnosticStatus | None = None

    def _sample(self) -> float:
        now = _clock_value(self.clock)
        if now < self.last:
            raise ValueError("Invalid diagnostic clock")
        self.last = now
        return now

    def begin(self) -> None:
        if self.started is not None:
            raise ValueError("Nested diagnostic work")
        self.started = self._sample()

    def end(self) -> None:
        now = self._sample()
        if self.started is not None:
            self.elapsed += now - self.started
            self.started = None
        self._time_limit()

    def _time_limit(self) -> None:
        elapsed = self.elapsed
        if self.started is not None:
            elapsed += self.last - self.started
        if elapsed > 0.025:
            self.exhaustion = self.exhaustion or "limit_time"
            raise _Limit(self.exhaustion)

    def step(self) -> None:
        self.units += 1
        self._sample()
        if self.units > 262_144:
            self.exhaustion = self.exhaustion or "limit_work"
        self._time_limit()
        if self.exhaustion is not None:
            raise _Limit(self.exhaustion)

    def scalar(self, value: object, mask: int = 0) -> None:
        if isinstance(value, str):
            self.scalars += 4 * len(value)
            if len(value) > 4096:
                if not mask:  # Path rendering retains its global fallback.
                    raise _Limit("limit_string")
                self.blocked |= mask
                if self.first_limit is None:
                    self.first_limit = "limit_string"
        elif type(value) is int:
            bits = value.bit_length()
            if bits > 4096:
                raise _Limit("limit_integer")
            self.scalars += (bits + 7) // 8
        elif type(value) is float:
            if not math.isfinite(value):
                raise ValueError("Invalid diagnostic input")
            self.scalars += 8
        elif type(value) is bool:
            self.scalars += 1
        if self.scalars > 1_048_576:
            raise _Limit("limit_scalars")

    def check(
        self, value: object, depth: int = 0, shape: str | None = None, mask: int = _ALL_MASK,
    ) -> None:
        """Preflight every input, including unequal tails, before any equality."""
        self.step()
        if shape is not None and not (value is None and shape in _OPTIONAL_SHAPES):
            if shape == "path":
                valid = isinstance(value, Path)
            elif shape == "role":
                valid = type(value) in (str, ClaudeConfigurationRole)
            elif shape in _ITEM_SHAPES:
                valid = type(value) is tuple
                if valid and shape == "environment_pair":
                    valid = len(cast(tuple[object, ...], value)) == 2
            else:
                valid = type(value) is _SHAPE_TYPES[shape]
                if valid and shape in ("nonempty_string", "optional_nonempty_string"):
                    valid = len(cast(str, value)) > 0
            if not valid:
                raise ValueError("Invalid diagnostic input")
        if isinstance(value, str) or value is None or type(value) in (int, bool, float):
            self.scalar(value, mask)
        elif isinstance(value, Path):
            # Bound the existing path components before constructing its string.
            if len(value.parts) > 4096:
                raise _Limit("limit_string")
            length = 0
            for part in value.parts:
                self.step()
                length += len(part)
                if length > 4096:
                    raise _Limit("limit_string")
            self.scalar(str(value))
        elif type(value) is tuple:
            if len(value) > 32768:
                raise _Limit("limit_sequence")
            for item in value:
                self.check(item, depth, _ITEM_SHAPES.get(shape or ""), mask)
        elif type(value) in _RECORD_TYPES:
            if isinstance(value, ClaudeInputVector):
                if type(value.documents) is not tuple or type(value.directories) is not tuple:
                    raise ValueError("Invalid diagnostic input")
                self.documents += len(value.documents)
                if self.documents > 64:
                    raise _Limit("limit_documents")
                for directory in value.directories:
                    self.step()
                    if (type(directory) is not ClaudeInputDirectory
                            or type(directory.candidates) is not tuple):
                        raise ValueError("Invalid diagnostic input")
                    self.candidates += len(directory.candidates)
                    if self.candidates > 256:
                        raise _Limit("limit_candidates")
            if isinstance(value, ClaudeInputDocument):
                if (type(value.role) not in (str, ClaudeConfigurationRole) or len(value.role) > 32
                        or value.role not in _ROLE_NAMES):
                    raise ValueError("Invalid diagnostic input")
            if isinstance(value, ClaudeJsonValue):
                self.nodes += 1
                if self.nodes > 32768:
                    raise _Limit("limit_nodes")
                if depth > 64:
                    raise _Limit("limit_depth")
                kind, content = value.kind, value.value
                if type(kind) is not str or len(kind) > 7:
                    raise ValueError("Invalid diagnostic input")
                expected: dict[str, type] = {
                    "null": type(None), "boolean": bool, "integer": int,
                    "float": float, "string": str, "array": tuple, "object": tuple,
                }
                if kind not in expected or type(content) is not expected[kind]:
                    raise ValueError("Invalid diagnostic input")
                self.scalar(kind)
                children = cast(tuple[object, ...], content)
                if kind in ("array", "object") and len(children) > 32768:
                    raise _Limit("limit_sequence")
                if kind == "array":
                    for item in children:
                        self.step()
                        if type(item) is not ClaudeJsonValue:
                            raise ValueError("Invalid diagnostic input")
                elif kind == "object":
                    for pair in children:
                        self.step()
                        if (type(pair) is not tuple or len(pair) != 2
                                or type(pair[0]) is not str
                                or type(pair[1]) is not ClaudeJsonValue):
                            raise ValueError("Invalid diagnostic input")
                self.check(content, depth + 1, mask=mask)
            else:
                for field in fields(cast(_Record, value)):
                    self.check(
                        getattr(value, field.name), depth,
                        _FIELD_SHAPES.get(type(value), {}).get(field.name),
                        _field_mask(value, field.name, mask),
                    )
        else:
            raise ValueError("Invalid diagnostic input")

    def equal(self, left: object, right: object) -> bool:
        """Only called after complete preflight; never invoke container equality."""
        self.step()
        if isinstance(left, str) and isinstance(right, str):
            return len(left) == len(right) and left == right
        if type(left) is not type(right):
            return False
        if isinstance(left, Path) and isinstance(right, Path):
            return self.equal(str(left), str(right))
        if type(left) is tuple and type(right) is tuple:
            return len(left) == len(right) and all(
                self.equal(a, b) for a, b in zip(left, right, strict=True)
            )
        if type(left) in _RECORD_TYPES:
            return all(self.equal(getattr(left, field.name), getattr(right, field.name))
                       for field in fields(cast(_Record, left)))
        if left is None or type(left) in (int, bool, float):
            return bool(left == right)
        raise ValueError("Invalid diagnostic input")

    def state(self, left: object, right: object) -> DiagnosticState:
        return "equal" if self.equal(left, right) else "changed"


def _compare(
    before: DiagnosticCapture, after: DiagnosticCapture, budget: _Budget,
    *, preflight: bool = True, environment: DiagnosticState | None = None,
) -> JsonObject:
    if preflight:
        budget.check(before)
        budget.check(after)
    result = unavailable_record("capture_missing")
    left, right = before.context, after.context

    def available(key: str) -> bool:
        return not budget.blocked & _FACT_MASKS[key]

    for key in ("executable_identity", "executable_version"):
        a, b = getattr(left, key), getattr(right, key)
        if available(key) and a is not None and b is not None:
            result[key] = budget.state(a, b)
    if environment is not None:
        result["launch_environment"] = environment
    elif available("launch_environment"):
        result["launch_environment"] = budget.state(before.environment, after.environment)
    if available("workspace"):
        result["workspace"] = budget.state(
            (left.workspace, left.localized_workspace_folder),
            (right.workspace, right.localized_workspace_folder),
        )
    if available("executor_identity"):
        result["executor_identity"] = budget.state(
            (left.destination, left.executor_identity), (right.destination, right.executor_identity)
        )
    a, b = before.vector, after.vector
    if a is None or b is None:
        result["diagnostic_status"] = budget.first_limit or "capture_missing"
        return result
    if available("candidate_directory"):
        result["candidate_directory"] = budget.state(a.directories, b.directories)
    if available("credential_metadata"):
        result["credential_metadata"] = budget.state(a.credential, b.credential)
    if available("source_selection"):
        sources_equal = budget.equal(a.global_selection, b.global_selection)
        sources_equal = budget.equal(a.directories, b.directories) and sources_equal
        # Selection compares the full ordered source sequence, even across role groups.
        if len(a.documents) != len(b.documents):
            sources_equal = False
        else:
            for first, second in zip(a.documents, b.documents, strict=True):
                same = (budget.equal(first.role, second.role)
                        and budget.equal(first.location, second.location)
                        and (first.content is None) == (second.content is None))
                sources_equal = same and sources_equal
        result["source_selection"] = "equal" if sources_equal else "changed"
    roles: JsonObject = {}
    for source_role, role in zip(_ROLE_NAMES, _ROLES, strict=True):
        source_mask, content_mask = _ROLE_MASKS[source_role]
        source: DiagnosticState = "unavailable"
        content: DiagnosticState = "unavailable"
        if not budget.blocked & source_mask:
            first_group = tuple(
                document for document in a.documents if document.role == source_role
            )
            second_group = tuple(
                document for document in b.documents if document.role == source_role
            )
            same = len(first_group) == len(second_group)
            if same:
                for first, second in zip(first_group, second_group, strict=True):
                    matched = (budget.equal(first.location, second.location)
                               and (first.content is None) == (second.content is None))
                    same = matched and same
            source = "equal" if same else "changed"
            if same and not budget.blocked & content_mask:
                content_equal = True
                for first, second in zip(first_group, second_group, strict=True):
                    matched = budget.equal(first.content, second.content)
                    content_equal = matched and content_equal
                content = "equal" if content_equal else "changed"
        roles[role] = {"source_validity": source, "semantic_content": content}
    result["roles"] = roles
    result["diagnostic_status"] = budget.first_limit or "compared"
    budget.step()
    return result


class DiagnosticSink:
    """One operation owns at most two references; finish always releases them."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._budget: _Budget | None = None
        self._captures: list[DiagnosticCapture] = []
        self._certified: list[object | None] = []
        self._lease: _ObservationLease | None = None
        self._invocation: _CaptureInvocation | None = None
        self._claimed = False
        self._provider: bool | None = None
        self._failure: DiagnosticStatus | None = None
        self._finished = False
        self._environment: DiagnosticState | None = None

    def _fail(self, status: DiagnosticStatus) -> None:
        if self._failure is None or status == "internal_error":
            self._failure = status

    def _allowance(self) -> _Budget:
        if self._budget is None:
            self._budget = _Budget(self._clock)
        return self._budget

    @contextmanager
    def _work(self) -> Iterator[None]:
        if self._finished:
            try:
                yield
            except Exception:
                pass
            return
        budget: _Budget | None = None
        try:
            budget = self._allowance()
            budget.begin()
            budget.step()
        except _Limit as limit:
            self._fail(limit.status)
        except Exception:
            self._fail("internal_error")
        try:
            yield
        except _Limit as limit:
            self._fail(limit.status)
        except Exception:
            self._fail("internal_error")
        finally:
            if budget is not None:
                try:
                    budget.end()
                except _Limit as limit:
                    self._fail(limit.status)
                except Exception:
                    self._fail("internal_error")

    def capture(self, value: DiagnosticCapture) -> None:
        if self._finished:
            return
        with self._work():
            if type(value) is not DiagnosticCapture or len(self._captures) == 2:
                self._fail("internal_error")
                return
            invocation = _invocation.get()
            certified: object | None = None
            if invocation is not None:
                if not invocation.owned(self):
                    self._fail("internal_error")
                    return
                if invocation.pending is not None:
                    if invocation.pending is not value.vector or invocation.consumed:
                        self._fail("internal_error")
                        return
                    certified = invocation.identity
                    invocation.pending = None
                    invocation.consumed = True
                elif invocation.consumed:
                    self._fail("internal_error")
                    return
            self._captures.append(value)
            self._certified.append(certified)

    def provider(self, equal: bool) -> None:
        if self._finished:
            return
        with self._work():
            if type(equal) is not bool or self._provider is not None:
                self._fail("internal_error")
                return
            self._provider = equal

    def _with_provider(self, result: JsonObject) -> JsonObject:
        if self._provider is not None:
            result["provider_source_snapshot"] = "equal" if self._provider else "changed"
        return result

    def _preflight_certified(self, budget: _Budget) -> None:
        # Preserve every original root/field charge, changing only visit order.
        for capture in self._captures:
            budget.step()
            if (type(capture) is not DiagnosticCapture
                    or type(capture.vector) is not ClaudeInputVector):
                raise ValueError("Invalid diagnostic input")
            budget.check(capture.context, shape="context", mask=_ALL_MASK)
            budget.check(capture.environment, shape="environment",
                         mask=_FACT_MASKS["launch_environment"])
        if not budget.blocked & _FACT_MASKS["launch_environment"]:
            self._environment = budget.state(
                self._captures[0].environment, self._captures[1].environment,
            )
        for capture in self._captures:
            budget.check(capture.vector, shape="vector", mask=_ALL_MASK)

    def _fallback(self, budget: _Budget, status: DiagnosticStatus) -> JsonObject:
        budget.first_limit = budget.first_limit or status
        result = unavailable_record(budget.first_limit)
        if budget.exhaustion is None and self._environment is not None:
            result["launch_environment"] = self._environment
        return self._with_provider(result)

    def finish(self) -> JsonObject:
        clean = unavailable_record()
        budget: _Budget | None = None
        try:
            if self._finished or self._failure == "internal_error":
                return clean
            budget = self._allowance()
            budget.begin()
            try:
                budget.step()
                if self._failure is not None:
                    raise _Limit(self._failure)
                result = unavailable_record("capture_missing")
                if len(self._captures) == 2:
                    certified = (all(item is not None for item in self._certified)
                                 and self._certified[0] is not self._certified[1])
                    if certified:
                        self._preflight_certified(budget)
                    result = _compare(
                        self._captures[0], self._captures[1], budget,
                        preflight=not certified, environment=self._environment,
                    )
                self._with_provider(result)
            except _Limit as limit:
                result = self._fallback(budget, limit.status)
            # Finite validation also consumes this allowance, including fallback.
            try:
                budget.step()
            except _Limit as limit:
                result = self._fallback(budget, limit.status)
            clean = validate_record(result)
            try:
                budget.step()
            except _Limit as limit:
                clean = self._fallback(budget, limit.status)
            try:
                budget.end()
            except _Limit as limit:
                clean = self._fallback(budget, limit.status)
            return clean
        except Exception:
            return unavailable_record()
        finally:
            if budget is not None and budget.started is not None:
                try:
                    budget.end()
                except Exception:
                    pass
            self._captures.clear()
            self._certified.clear()
            if self._lease is not None:
                self._lease.clear()
            self._lease = None
            if self._invocation is not None:
                self._invocation.clear()
            self._invocation = None
            self._provider = None
            self._failure = None
            self._environment = None
            self._budget = None
            self._finished = True


def _task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass(slots=True, repr=False)
class _ObservationLease:
    sink: DiagnosticSink | None
    observer: object | None
    task: object | None
    thread: int
    active: bool = True
    provider_issued: bool = False
    provider_operation: _ProviderOperation | None = None

    def owned(self, sink: DiagnosticSink) -> bool:
        return (self.active and self.sink is sink and self.task is _task()
                and self.thread == threading.get_ident() and sink._lease is self
                and not sink._finished)

    def clear(self) -> None:
        if self.provider_operation is not None:
            # Observation teardown already owns a work segment. Never nest its timer.
            self.provider_operation._release()
        self.provider_operation = None
        self.active = False
        self.sink = None
        self.observer = None
        self.task = None


@dataclass(slots=True, repr=False)
class _ProviderOperation:
    """Explicit one-use authority for one nested provider comparison task."""

    sink: DiagnosticSink | None
    lease: _ObservationLease | None
    observer: object | None
    parent_task: object | None
    thread: int
    task: object | None = None
    active: bool = True
    claimed: bool = False
    consumed: bool = False

    def registered(self, sink: DiagnosticSink) -> bool:
        lease = self.lease
        return (self.active and self.sink is sink and lease is not None
                and lease.active and lease.sink is sink and sink._lease is lease
                and lease.provider_issued and lease.provider_operation is self
                and lease.observer is self.observer and lease.task is self.parent_task
                and lease.thread == self.thread == threading.get_ident()
                and not sink._finished)

    def owned(self, sink: DiagnosticSink) -> bool:
        return (self.registered(sink) and self.claimed and not self.consumed
                and self.task is _task() and _provider_operation.get() is self
                and _observation.get() is self.lease and _sink.get() is sink)

    @contextmanager
    def claim(self) -> Iterator[None]:
        """Only the explicit inner operation closure receives this capability."""
        sink = self.sink
        token = None
        if sink is not None:
            with sink._work():
                task = _task()
                if (not self.registered(sink) or self.claimed or task is None
                        or task is self.parent_task or _sink.get() is not sink
                        or _observation.get() is not self.lease):
                    sink._fail("internal_error")
                else:
                    self.claimed = True
                    self.task = task
                    token = _provider_operation.set(self)
        try:
            yield
        finally:
            try:
                self.clear()
            finally:
                if token is not None:
                    _provider_operation.reset(token)

    def clear(self) -> None:
        sink = self.sink
        try:
            if sink is not None:
                with sink._work():
                    self.active = False
        finally:
            self._release()

    def _release(self) -> None:
        self.active = False
        self.sink = None
        self.lease = None
        self.observer = None
        self.parent_task = None
        self.task = None


@dataclass(slots=True, repr=False)
class _CaptureInvocation:
    lease: _ObservationLease | None
    observer: object | None
    identity: object
    active: bool = True
    eligible: bool = False
    entered: bool = False
    consumed: bool = False
    pending: ClaudeInputVector | None = None

    def owned(self, sink: DiagnosticSink) -> bool:
        return (self.active and self.lease is not None and self.lease.owned(sink)
                and self.observer is self.lease.observer and _invocation.get() is self
                and sink._invocation is self)

    def clear(self) -> None:
        self.active = False
        self.pending = None
        self.lease = None
        self.observer = None


_sink: ContextVar[DiagnosticSink | None] = ContextVar("catalog_diagnostic_sink", default=None)
_observation: ContextVar[_ObservationLease | None] = ContextVar(
    "catalog_diagnostic_observation", default=None,
)
_invocation: ContextVar[_CaptureInvocation | None] = ContextVar(
    "catalog_diagnostic_invocation", default=None,
)
_provider_operation: ContextVar[_ProviderOperation | None] = ContextVar(
    "catalog_diagnostic_provider_operation", default=None,
)


@contextmanager
def diagnostic_provider_operation(observer: object) -> Iterator[_ProviderOperation | None]:
    """Issue once from the outer owner, never from an inherited child context."""
    sink = _sink.get()
    lease = _observation.get()
    operation = None
    if sink is not None and not sink._finished:
        with sink._work():
            if (lease is None or not lease.owned(sink) or lease.observer is not observer
                    or lease.provider_issued):
                sink._fail("internal_error")
            else:
                lease.provider_issued = True
                operation = _ProviderOperation(
                    sink, lease, observer, lease.task, lease.thread,
                )
                lease.provider_operation = operation
    try:
        yield operation
    finally:
        if operation is not None:
            operation.clear()


@contextmanager
def diagnostic_scope(sink: DiagnosticSink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def diagnostic_observation(observer: object | None = None) -> Iterator[None]:
    """Lease only the actual operation task; inherited context is not ownership."""
    sink = _sink.get()
    if sink is None or sink._finished:
        yield
        return
    with sink._work():
        lease = _ObservationLease(sink, observer, _task(), threading.get_ident())
        if sink._claimed:
            sink._fail("internal_error")
            lease.clear()
        else:
            sink._claimed = True
            sink._lease = lease
        token = _observation.set(lease)
    try:
        yield
    finally:
        with sink._work():
            try:
                if not sink._finished and not lease.owned(sink):
                    sink._fail("internal_error")
            finally:
                lease.clear()
                if sink._lease is lease:
                    sink._lease = None
                if sink._invocation is not None:
                    sink._invocation.clear()
                    sink._invocation = None
                _observation.reset(token)


@contextmanager
def diagnostic_capture_invocation(observer: object) -> Iterator[None]:
    lease = _observation.get()
    sink = _sink.get()
    if sink is None or lease is None or sink._finished:
        yield
        return
    with sink._work():
        invocation = _CaptureInvocation(lease, observer, object())
        if (not lease.owned(sink) or observer is not lease.observer
                or sink._invocation is not None):
            sink._fail("internal_error")
        else:
            sink._invocation = invocation
        token = _invocation.set(invocation)
    try:
        yield
    finally:
        with sink._work():
            try:
                if not sink._finished and not invocation.owned(sink):
                    sink._fail("internal_error")
            finally:
                invocation.clear()
                if sink._invocation is invocation:
                    sink._invocation = None
                _invocation.reset(token)


def _collector_entry(
    workspace: Path, environment: object, home: Path, managed_directory: Path,
) -> None:
    invocation = _invocation.get()
    sink = _sink.get()
    if sink is None or sink._finished or invocation is None or invocation.lease is None:
        return
    with sink._work():
        if not invocation.owned(sink) or invocation.entered:
            sink._fail("internal_error")
            return
        invocation.entered = True
        native_path = type(Path())
        invocation.eligible = (
            type(workspace) is native_path and type(home) is native_path
            and type(managed_directory) is native_path and type(environment) is dict
        )
        if invocation.eligible:
            selectors = cast(dict[object, object], environment)
            for key in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR",
                        "CLAUDE_CODE_CUSTOM_OAUTH_URL"):
                sink._allowance().step()
                if key in selectors and type(selectors[key]) is not str:
                    invocation.eligible = False


def _collector_success(vector: ClaudeInputVector) -> None:
    invocation = _invocation.get()
    sink = _sink.get()
    if sink is None or sink._finished or invocation is None or invocation.lease is None:
        return
    with sink._work():
        if not invocation.owned(sink) or invocation.pending is not None or invocation.consumed:
            sink._fail("internal_error")
            return
        if invocation.entered and invocation.eligible:
            if type(vector) is not ClaudeInputVector:
                sink._fail("internal_error")
            else:
                invocation.pending = vector


def offer_capture(value: DiagnosticCapture) -> None:
    """Offer an existing return value; a diagnostic error never escapes."""
    try:
        sink = _sink.get()
        lease = _observation.get()
        if sink is not None and lease is not None:
            if not lease.owned(sink):
                with sink._work():
                    sink._fail("internal_error")
            else:
                sink.capture(value)
    except Exception:
        pass


def offer_provider_comparison(equal: bool) -> None:
    try:
        sink = _sink.get()
        lease = _observation.get()
        if sink is not None and lease is not None and not sink._finished:
            operation = _provider_operation.get()
            if lease.provider_issued:
                admitted = False
                with sink._work():
                    if operation is None or not operation.owned(sink):
                        sink._fail("internal_error")
                    else:
                        operation.consumed = True
                        admitted = True
                if admitted:
                    sink.provider(equal)
            elif not lease.owned(sink):
                with sink._work():
                    sink._fail("internal_error")
            else:
                sink.provider(equal)
    except Exception:
        pass
