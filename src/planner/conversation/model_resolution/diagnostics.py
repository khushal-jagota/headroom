"""Temporary destination-private comparison, with no collection or authority.

Only fixed finite classifications escape. Private inputs are borrowed references,
never serialized, hashed, read again, or retained after ``finish``.
"""
from __future__ import annotations

import math
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
    ClaudeInputDocument: {"role": "string", "location": "location", "content": "content"},
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
        self.started = _clock_value(clock)
        self.last = self.started
        self.units = 0
        self.nodes = 0
        self.documents = 0
        self.candidates = 0
        self.scalars = 0

    def step(self) -> None:
        self.units += 1
        # Cap wrapper/structural work too, including zero-length private scalars.
        if self.units > 262_144:
            raise _Limit("limit_work")
        now = _clock_value(self.clock)
        if now < self.last:
            raise ValueError("Invalid diagnostic clock")
        self.last = now
        if self.elapsed + now - self.started > 0.025:
            raise _Limit("limit_time")

    def scalar(self, value: object) -> None:
        if isinstance(value, str):
            if len(value) > 4096:
                raise _Limit("limit_string")
            self.scalars += 4 * len(value)
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

    def check(self, value: object, depth: int = 0, shape: str | None = None) -> None:
        """Preflight every input, including unequal tails, before any equality."""
        self.step()
        if shape is not None and not (value is None and shape in _OPTIONAL_SHAPES):
            if shape == "path":
                valid = isinstance(value, Path)
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
            self.scalar(value)
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
                self.check(item, depth, _ITEM_SHAPES.get(shape or ""))
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
                if (type(value.role) is not str or len(value.role) > 32
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
                self.check(content, depth + 1)
            else:
                for field in fields(cast(_Record, value)):
                    self.check(
                        getattr(value, field.name), depth,
                        _FIELD_SHAPES.get(type(value), {}).get(field.name),
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


def _compare(before: DiagnosticCapture, after: DiagnosticCapture, budget: _Budget) -> JsonObject:
    budget.check(before)
    budget.check(after)
    result = unavailable_record("capture_missing")
    left, right = before.context, after.context
    for key in ("executable_identity", "executable_version"):
        a, b = getattr(left, key), getattr(right, key)
        if a is not None and b is not None:
            result[key] = budget.state(a, b)
    result["launch_environment"] = budget.state(before.environment, after.environment)
    result["workspace"] = budget.state(
        (left.workspace, left.localized_workspace_folder),
        (right.workspace, right.localized_workspace_folder),
    )
    result["executor_identity"] = budget.state(
        (left.destination, left.executor_identity), (right.destination, right.executor_identity)
    )
    a, b = before.vector, after.vector
    if a is None or b is None:
        return result
    result["candidate_directory"] = budget.state(a.directories, b.directories)
    result["credential_metadata"] = budget.state(a.credential, b.credential)
    sources_equal = budget.equal(a.global_selection, b.global_selection)
    sources_equal = budget.equal(a.directories, b.directories) and sources_equal
    roles: JsonObject = {}
    # Selection compares the full ordered source sequence, even across role groups.
    if len(a.documents) != len(b.documents):
        sources_equal = False
    else:
        for first, second in zip(a.documents, b.documents, strict=True):
            same = (budget.equal(first.role, second.role)
                    and budget.equal(first.location, second.location)
                    and (first.content is None) == (second.content is None))
            sources_equal = same and sources_equal
    for source_role, role in zip(_ROLE_NAMES, _ROLES, strict=True):
        first_group = tuple(document for document in a.documents if document.role == source_role)
        second_group = tuple(document for document in b.documents if document.role == source_role)
        same = len(first_group) == len(second_group)
        if same:
            for first, second in zip(first_group, second_group, strict=True):
                matched = (budget.equal(first.location, second.location)
                           and (first.content is None) == (second.content is None))
                same = matched and same
        content: DiagnosticState = "unavailable"
        if same:
            content_equal = True
            for first, second in zip(first_group, second_group, strict=True):
                matched = budget.equal(first.content, second.content)
                content_equal = matched and content_equal
            content = "equal" if content_equal else "changed"
        roles[role] = {"source_validity": "equal" if same else "changed",
                       "semantic_content": content}
    result["roles"] = roles
    result["source_selection"] = "equal" if sources_equal else "changed"
    result["diagnostic_status"] = "compared"
    budget.step()
    return result


class DiagnosticSink:
    """One operation owns at most two references; finish always releases them."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._captures: list[DiagnosticCapture] = []
        self._provider: bool | None = None
        self._failure: DiagnosticStatus | None = None
        self._finished = False
        self._elapsed = 0.0

    def _fail(self, status: DiagnosticStatus) -> None:
        if self._failure is None or status == "internal_error":
            self._failure = status

    @contextmanager
    def _work(self) -> Iterator[None]:
        # Charge diagnostic-only hooks, excluding all ordinary collection/waits.
        started: float | None = None
        try:
            started = _clock_value(self._clock)
        except Exception:
            self._fail("internal_error")
        try:
            yield
        except Exception:
            self._fail("internal_error")
        finally:
            if started is not None:
                try:
                    elapsed = _clock_value(self._clock) - started
                    if not math.isfinite(elapsed) or elapsed < 0:
                        self._fail("internal_error")
                    else:
                        self._elapsed += elapsed
                        if self._elapsed > 0.025:
                            self._fail("limit_time")
                except Exception:
                    self._fail("internal_error")

    def capture(self, value: DiagnosticCapture) -> None:
        if self._finished:
            return
        with self._work():
            if type(value) is not DiagnosticCapture or len(self._captures) == 2:
                self._fail("internal_error")
                return
            self._captures.append(value)

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

    def finish(self) -> JsonObject:
        try:
            if self._finished or self._failure == "internal_error":
                return unavailable_record()
            budget = _Budget(self._clock, self._elapsed)
            try:
                if self._failure is not None:
                    raise _Limit(self._failure)
                result = unavailable_record("capture_missing")
                if len(self._captures) == 2:
                    result = _compare(self._captures[0], self._captures[1], budget)
                self._with_provider(result)
                budget.step()
                clean = validate_record(result)
                budget.step()
                return clean
            except _Limit as limit:
                # Outer comparisons are incomplete; the independently supplied,
                # cardinality-checked provider boolean remains an observation.
                return validate_record(self._with_provider(unavailable_record(limit.status)))
        except Exception:
            return unavailable_record()
        finally:
            self._captures.clear()
            self._provider = None
            self._failure = None
            self._elapsed = 0.0
            self._finished = True


_sink: ContextVar[DiagnosticSink | None] = ContextVar("catalog_diagnostic_sink", default=None)
_observation: ContextVar[bool] = ContextVar("catalog_diagnostic_observation", default=False)


@contextmanager
def diagnostic_scope(sink: DiagnosticSink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def diagnostic_observation() -> Iterator[None]:
    """Limit offers to observe.operation, excluding service/admission captures."""
    token = _observation.set(True)
    try:
        yield
    finally:
        _observation.reset(token)


def offer_capture(value: DiagnosticCapture) -> None:
    """Offer an existing return value; a diagnostic error never escapes."""
    try:
        sink = _sink.get()
        if sink is not None and _observation.get():
            sink.capture(value)
    except Exception:
        pass


def offer_provider_comparison(equal: bool) -> None:
    try:
        sink = _sink.get()
        if sink is not None and _observation.get():
            sink.provider(equal)
    except Exception:
        pass
