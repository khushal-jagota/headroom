"""Stored source scripts, dated run receipts, runtime admission, and public results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypedDict

from planner.core.contracts import Principal
from planner.credentials.contracts import (
    CredentialResolver,
    CredentialScope,
)
from planner.measures.contracts import (
    CandidateObservation,
    HistoryWindow,
    LatestMeasureObservation,
    Observation,
    OutputMeasure,
    SourceHealth,
)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ScriptRevision:
    revision: int
    text: str = field(repr=False)
    sha256: str
    authored_by: Principal
    created_at: int


@dataclass(frozen=True, slots=True)
class MeasureSource:
    id: str
    name: str
    scope: CredentialScope
    responsible: Principal
    responsibility_revision: int
    enabled: bool
    local_time: str | None
    source_revision: int
    active_script: ScriptRevision | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class SourceCreate:
    name: str
    scope: CredentialScope
    responsible: Principal


class SourceScheduleBody(TypedDict):
    expected_revision: int
    enabled: bool
    local_time: str | None


class SourceScriptBody(TypedDict):
    expected_revision: int
    text: str


class RunMode(StrEnum):
    collection = "collection"
    test = "test"


class RunRequestBody(TypedDict):
    mode: RunMode
    history: HistoryWindow | None
    expected_script_revision: int


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    interrupted = "interrupted"


class RunTrigger(StrEnum):
    scheduled = "scheduled"
    manual = "manual"
    test = "test"


class FailureCode(StrEnum):
    runtime_failure = "runtime_failure"
    malformed_protocol = "malformed_protocol"
    credential_unavailable = "credential_unavailable"
    transport_failure = "transport_failure"
    http_error = "http_error"
    invalid_json = "invalid_json"
    invalid_output = "invalid_output"
    stale_definition = "stale_definition"
    budget_exhausted = "budget_exhausted"
    interrupted = "interrupted"


class RuntimeBudget(StrEnum):
    wall_seconds = "wall_seconds"


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Only approved context, never exception text, source output, or provider data."""

    code: FailureCode
    budget: RuntimeBudget | None = None
    http_status: int | None = None
    measure_id: str | None = None
    date: str | None = None
    exception_type: str | None = None
    source_line: int | None = None


@dataclass(frozen=True, slots=True)
class RunMetrics:
    request_count: int
    response_bytes: int
    child_output_bytes: int
    elapsed_milliseconds: int
    script_cpu_milliseconds: int


@dataclass(frozen=True, slots=True)
class SourceRun:
    id: str
    source_id: str
    mode: RunMode
    trigger: RunTrigger
    status: RunStatus
    source_revision: int
    script_revision: int
    script_sha256: str
    frozen_at: int
    required_outputs: tuple[OutputMeasure, ...]
    history: HistoryWindow | None
    schedule_date: str | None
    schedule_time: str | None
    created_at: int
    started_at: int | None
    finished_at: int | None
    failure: RunFailure | None
    metrics: RunMetrics | None
    observation_count: int


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """The configured run timeout; other execution behavior belongs to ordinary Python."""

    wall_seconds: float


@dataclass(frozen=True, slots=True)
class RunAdmission:
    """Built after the run claim, with the deadline from a monotonic clock."""

    run: SourceRun
    script_text: str = field(repr=False)
    scope: CredentialScope
    previous: tuple[Observation, ...]
    limits: RuntimeLimits
    deadline_monotonic: float


@dataclass(frozen=True, slots=True)
class RunResult:
    """Exactly one of failure or a validated candidate batch is meaningful."""

    observations: tuple[CandidateObservation, ...]
    failure: RunFailure | None
    metrics: RunMetrics


@dataclass(frozen=True, slots=True)
class SourceRunDetail:
    run: SourceRun
    test_candidates: tuple[CandidateObservation, ...] | None


class SourceRuntime(Protocol):
    def cancel(self, run_id: str) -> None:
        """Idempotently cancel this exact run, including concurrent execute startup."""
        ...

    def execute(self, admission: RunAdmission, credentials: CredentialResolver) -> RunResult:
        """Execute current source code with actual-use named credential resolution."""
        ...


@dataclass(frozen=True, slots=True)
class FailureEpisode:
    source_id: str
    episode: int
    opened_at: int
    latest_run_id: str
    closed_at: int | None


@dataclass(frozen=True, slots=True)
class FailureNoticeIdentity:
    source_id: str
    episode: int
    responsibility_revision: int
    recipient: Principal


@dataclass(frozen=True, slots=True)
class SourceSummary:
    """List/status projection excludes script text and retained run payloads."""

    id: str
    name: str
    scope: CredentialScope
    responsible: Principal
    source_revision: int
    enabled: bool
    local_time: str | None
    active_script_revision: int | None


@dataclass(frozen=True, slots=True)
class SourceRunSummary:
    """Bounded status projection; complete captured output context is detail-only."""

    id: str
    mode: RunMode
    status: RunStatus
    created_at: int
    finished_at: int | None
    failure: RunFailure | None
    observation_count: int


@dataclass(frozen=True, slots=True)
class SourceStatus:
    source: SourceSummary
    health: SourceHealth
    latest_run: SourceRunSummary | None
    latest_success_at: int | None
    latest_values: tuple[LatestMeasureObservation, ...]  # Bounded preview, not every output.
    output_count: int
    failure_episode: FailureEpisode | None
    collection_failure: RunFailure | None
    running_run_id: str | None
