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
from typing import Literal, cast

from planner.conversation.model_resolution.claude_input_contracts import (
    ClaudeCredentialMarker,
    ClaudeInputDirectory,
    ClaudeInputDocument,
    ClaudeInputLocation,
    ClaudeInputVector,
    ClaudeJsonValue,
)
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding, LaunchContext
from planner.executor.contracts import ExecutorIdentity, JsonObject

DiagnosticState = Literal["equal", "changed", "unavailable"]
_ROLES = ("selected_global_state", "settings", "managed_settings", "remote_policy_cache")
_ROLE_NAMES = ("global_state", "settings", "managed_settings", "remote_policy_cache")
_FACTS = ("executable_identity", "executable_version", "launch_environment", "workspace",
          "executor_identity")
_OTHER = ("source_selection", "inactive_global_candidate", "candidate_directory",
          "credential_metadata", "provider_source_snapshot", "ordinary_file_metadata",
          "raw_byte_equality")
_STATES = ("equal", "changed", "unavailable")


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


def unavailable_record() -> JsonObject:
    return {
        **dict.fromkeys((*_FACTS, *_OTHER), "unavailable"),
        "roles": {role: {"source_validity": "unavailable", "semantic_content": "unavailable"}
                  for role in _ROLES},
    }


def validate_record(value: object) -> JsonObject:
    """Reject nonfinite or extended evidence before any output serialization."""
    template = unavailable_record()
    if type(value) is not dict or len(value) != len(template):
        raise ValueError("Invalid diagnostic record")
    if any(type(key) is not str or len(key) > 32 for key in value):
        raise ValueError("Invalid diagnostic record")
    if value.keys() != template.keys():
        raise ValueError("Invalid diagnostic record")
    for key in (*_FACTS, *_OTHER):
        state = value[key]
        if type(state) is not str or len(state) > 11 or state not in _STATES:
            raise ValueError("Invalid diagnostic record")
        template[key] = state
    roles = value["roles"]
    if type(roles) is not dict or len(roles) != len(_ROLES):
        raise ValueError("Invalid diagnostic record")
    if any(type(key) is not str or len(key) > 32 for key in roles) or roles.keys() != set(_ROLES):
        raise ValueError("Invalid diagnostic record")
    clean_roles: JsonObject = {}
    for role in _ROLES:
        states = roles[role]
        if type(states) is not dict or len(states) != 2:
            raise ValueError("Invalid diagnostic record")
        if (any(type(key) is not str or len(key) > 32 for key in states)
                or states.keys() != {"source_validity", "semantic_content"}):
            raise ValueError("Invalid diagnostic record")
        clean: JsonObject = {}
        for key in ("source_validity", "semantic_content"):
            state = states[key]
            if type(state) is not str or len(state) > 11 or state not in _STATES:
                raise ValueError("Invalid diagnostic record")
            clean[key] = state
        clean_roles[role] = clean
    template["roles"] = clean_roles
    return template


class _Unavailable(Exception):
    """No private details belong in this failure."""


class _Budget:
    def __init__(self, clock: Callable[[], float], elapsed: float = 0.0) -> None:
        self.elapsed = elapsed
        self.clock = clock
        self.started = clock()
        self.units = 0
        self.nodes = 0
        self.documents = 0
        self.candidates = 0
        self.scalars = 0

    def step(self) -> None:
        self.units += 1
        # Cap wrapper/structural work too, including zero-length private scalars.
        if self.units > 262_144 or self.elapsed + self.clock() - self.started > 0.025:
            raise _Unavailable

    def scalar(self, value: object) -> None:
        if isinstance(value, str):
            if len(value) > 4096:
                raise _Unavailable
            self.scalars += 4 * len(value)
        elif type(value) is int:
            bits = value.bit_length()
            if bits > 4096:
                raise _Unavailable
            self.scalars += (bits + 7) // 8
        elif type(value) is float:
            if not math.isfinite(value):
                raise _Unavailable
            self.scalars += 8
        elif type(value) is bool:
            self.scalars += 1
        if self.scalars > 1_048_576:
            raise _Unavailable

    def check(self, value: object, depth: int = 0) -> None:
        """Preflight every input, including unequal tails, before any equality."""
        self.step()
        if isinstance(value, str) or value is None or type(value) in (int, bool, float):
            self.scalar(value)
        elif isinstance(value, Path):
            # Bound the existing path components before constructing its string.
            if len(value.parts) > 4096:
                raise _Unavailable
            length = 0
            for part in value.parts:
                self.step()
                length += len(part)
                if length > 4096:
                    raise _Unavailable
            self.scalar(str(value))
        elif type(value) is tuple:
            if len(value) > 32768:
                raise _Unavailable
            for item in value:
                self.check(item, depth)
        elif type(value) in _RECORD_TYPES:
            if isinstance(value, ClaudeInputVector):
                self.documents += len(value.documents)
                if self.documents > 64:
                    raise _Unavailable
                for directory in value.directories:
                    self.step()
                    self.candidates += len(directory.candidates)
                    if self.candidates > 256:
                        raise _Unavailable
            if isinstance(value, ClaudeInputDocument):
                if len(value.role) > 32 or value.role not in _ROLE_NAMES:
                    raise _Unavailable
            if isinstance(value, ClaudeJsonValue):
                self.nodes += 1
                if self.nodes > 32768 or depth > 64:
                    raise _Unavailable
                self.scalar(value.kind)
                kind, content = value.kind, value.value
                expected: dict[str, type] = {
                    "null": type(None), "boolean": bool, "integer": int,
                    "float": float, "string": str, "array": tuple, "object": tuple,
                }
                if kind not in expected or type(content) is not expected[kind]:
                    raise _Unavailable
                children = cast(tuple[object, ...], content)
                if kind in ("array", "object") and len(children) > 32768:
                    raise _Unavailable
                if kind == "array":
                    for item in children:
                        self.step()
                        if type(item) is not ClaudeJsonValue:
                            raise _Unavailable
                elif kind == "object":
                    for pair in children:
                        self.step()
                        if (type(pair) is not tuple or len(pair) != 2
                                or type(pair[0]) is not str
                                or type(pair[1]) is not ClaudeJsonValue):
                            raise _Unavailable
                self.check(content, depth + 1)
            else:
                for field in fields(cast(_Record, value)):
                    self.check(getattr(value, field.name), depth)
        else:
            raise _Unavailable

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
        raise _Unavailable

    def state(self, left: object, right: object) -> DiagnosticState:
        return "equal" if self.equal(left, right) else "changed"


def _compare(before: DiagnosticCapture, after: DiagnosticCapture, budget: _Budget) -> JsonObject:
    budget.check(before)
    budget.check(after)
    result = unavailable_record()
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
    budget.step()
    return result


class DiagnosticSink:
    """One operation owns at most two references; finish always releases them."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._captures: list[DiagnosticCapture] = []
        self._provider: bool | None = None
        self._failed = False
        self._finished = False
        self._elapsed = 0.0

    @contextmanager
    def _work(self) -> Iterator[None]:
        # Charge diagnostic-only hooks, excluding all ordinary collection/waits.
        started: float | None = None
        try:
            started = self._clock()
        except Exception:
            self._failed = True
        try:
            yield
        except Exception:
            self._failed = True
        finally:
            if started is not None:
                try:
                    elapsed = self._clock() - started
                    if not math.isfinite(elapsed) or elapsed < 0:
                        self._failed = True
                    else:
                        self._elapsed += elapsed
                        if self._elapsed > 0.025:
                            self._failed = True
                except Exception:
                    self._failed = True

    def capture(self, value: DiagnosticCapture) -> None:
        if self._finished:
            return
        with self._work():
            if type(value) is not DiagnosticCapture or len(self._captures) == 2:
                self._failed = True
                return
            self._captures.append(value)

    def provider(self, equal: bool) -> None:
        if self._finished:
            return
        with self._work():
            if type(equal) is not bool or self._provider is not None:
                self._failed = True
                return
            self._provider = equal

    def finish(self) -> JsonObject:
        try:
            if self._finished or self._failed:
                return unavailable_record()
            budget = _Budget(self._clock, self._elapsed)
            result = unavailable_record()
            if len(self._captures) == 2:
                result = _compare(self._captures[0], self._captures[1], budget)
            if self._provider is not None:
                result["provider_source_snapshot"] = "equal" if self._provider else "changed"
            budget.step()
            clean = validate_record(result)
            budget.step()
            return clean
        except Exception:
            return unavailable_record()
        finally:
            self._captures.clear()
            self._provider = None
            self._failed = False
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
