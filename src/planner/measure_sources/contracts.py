"""Source authority, frozen runtime admission, host messages, and public results.

Only data/actions touch SQLite. A runtime receives an immutable admitted envelope
and returns candidates or an approved failure. No script frame grants authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, TypedDict

from planner.core.contracts import Principal
from planner.credentials.contracts import (
    CredentialReference,
    CredentialScope,
    ResolvedCredential,
)
from planner.measures.contracts import (
    CandidateObservation,
    HistoricalUnavailableReason,
    HistoryWindow,
    LatestMeasureObservation,
    Observation,
    OutputMeasure,
    SourceHealth,
)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


class PathMatch(StrEnum):
    exact = "exact"
    prefix = "prefix"


@dataclass(frozen=True, slots=True)
class DestinationGrant:
    """Prefix matching requires a segment boundary; origin and path are normalized."""

    id: str  # Stable, descriptive source-local name, not an opaque generated identifier.
    origin: str
    method: HttpMethod
    path: str
    path_match: PathMatch
    credential: CredentialReference | None


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
    repair_ticket_id: str | None
    enabled: bool
    local_time: str | None
    source_revision: int
    active_script: ScriptRevision | None
    pending_script: ScriptRevision | None
    destinations: tuple[DestinationGrant, ...]
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class SourceCreate:
    name: str
    scope: CredentialScope
    responsible: Principal
    destinations: tuple[DestinationGrant, ...]


class SourceScheduleBody(TypedDict):
    expected_revision: int
    enabled: bool
    local_time: str | None


class SourceScriptBody(TypedDict):
    expected_revision: int
    text: str
    expected_pending_revision: int | None


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
    sandbox_unavailable = "sandbox_unavailable"
    runtime_failure = "runtime_failure"
    malformed_protocol = "malformed_protocol"
    unauthorized_request = "unauthorized_request"
    dns_policy_refused = "dns_policy_refused"
    credential_unavailable = "credential_unavailable"
    transport_failure = "transport_failure"
    http_error = "http_error"
    invalid_json = "invalid_json"
    invalid_output = "invalid_output"
    missing_output = "missing_output"
    stale_definition = "stale_definition"
    budget_exhausted = "budget_exhausted"
    interrupted = "interrupted"


class RuntimeBudget(StrEnum):
    wall_seconds = "wall_seconds"
    script_cpu_seconds = "script_cpu_seconds"
    script_memory_bytes = "script_memory_bytes"
    broker_cpu_seconds = "broker_cpu_seconds"
    broker_memory_bytes = "broker_memory_bytes"
    request_count = "request_count"
    request_frame_bytes = "request_frame_bytes"
    response_bytes = "response_bytes"
    total_response_bytes = "total_response_bytes"
    child_output_bytes = "child_output_bytes"
    result_bytes = "result_bytes"
    observation_count = "observation_count"
    file_descriptors = "file_descriptors"


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Only approved context, never exception text, source output, or provider data."""

    code: FailureCode
    budget: RuntimeBudget | None = None
    http_status: int | None = None
    measure_id: str | None = None
    date: str | None = None


@dataclass(frozen=True, slots=True)
class RunMetrics:
    request_count: int
    response_bytes: int
    child_output_bytes: int
    elapsed_milliseconds: int
    script_cpu_milliseconds: int
    broker_cpu_milliseconds: int


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
    """Values come from configuration.py, never source-authored configuration."""

    wall_seconds: float
    script_cpu_seconds: int
    script_memory_bytes: int
    script_file_descriptors: int
    script_file_bytes: int
    broker_cpu_seconds: int
    broker_memory_bytes: int
    broker_file_descriptors: int
    request_count: int
    request_frame_bytes: int
    response_bytes: int
    total_response_bytes: int
    child_output_bytes: int
    result_bytes: int
    observation_count: int
    json_depth: int


@dataclass(frozen=True, slots=True)
class RunAdmission:
    """Built after the run claim, with the deadline from a monotonic clock."""

    run: SourceRun
    script_text: str = field(repr=False)
    scope: CredentialScope
    destinations: tuple[DestinationGrant, ...]
    previous: tuple[Observation, ...]
    limits: RuntimeLimits
    deadline_monotonic: float


@dataclass(frozen=True, slots=True)
class HostRequest:
    grant_id: str
    url: str
    method: HttpMethod
    json_body: JsonValue = None


@dataclass(frozen=True, slots=True)
class HostResponse:
    """All fields are redacted before script delivery; redirects are never followed."""

    status: int
    headers: tuple[tuple[str, str], ...]
    json_body: JsonValue


class SourceReadyFrame(TypedDict):
    """Trusted bootstrap emits this once after its limits and context are installed."""

    op: Literal["ready"]


class SourceMemoryErrorFrame(TypedDict):
    """Trusted bootstrap's bounded report of an unhandled script MemoryError."""

    op: Literal["memory_exhausted"]


class RequestFrame(TypedDict):
    op: Literal["request"]
    id: int
    grant_id: str
    url: str
    method: HttpMethod
    json_body: JsonValue


class LatestFrame(TypedDict):
    op: Literal["latest"]
    id: int
    measure_id: str


class RecordFrame(TypedDict):
    op: Literal["record"]
    id: int
    measure_id: str
    date: str
    value: float | None
    unavailable_reason: HistoricalUnavailableReason | None


type ScriptFrame = RequestFrame | LatestFrame | RecordFrame


@dataclass(frozen=True, slots=True)
class RunResult:
    """Exactly one of failure or a validated candidate batch is meaningful."""

    observations: tuple[CandidateObservation, ...]
    failure: RunFailure | None
    metrics: RunMetrics


@dataclass(frozen=True, slots=True)
class ScriptContext:
    """Only this projection initializes source code; it contains no secret material."""

    run_id: str
    frozen_at: int
    required_outputs: tuple[OutputMeasure, ...]
    history: HistoryWindow | None
    grant_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequestReply:
    id: int
    response: HostResponse
    op: Literal["request"] = "request"


@dataclass(frozen=True, slots=True)
class LatestReply:
    id: int
    observation: Observation | None
    op: Literal["latest"] = "latest"


@dataclass(frozen=True, slots=True)
class RecordReply:
    id: int
    op: Literal["record"] = "record"


@dataclass(frozen=True, slots=True)
class FailureReply:
    id: int | None
    failure: RunFailure
    op: Literal["failure"] = "failure"


type HostReply = RequestReply | LatestReply | RecordReply | FailureReply


@dataclass(frozen=True, slots=True)
class SourceRunDetail:
    run: SourceRun
    test_candidates: tuple[CandidateObservation, ...] | None


@dataclass(frozen=True, slots=True)
class BrokerConfiguration:
    """Private clean-process input, sent through a bounded host pipe, never argv."""

    scope: CredentialScope
    destinations: tuple[DestinationGrant, ...]
    credentials: tuple[ResolvedCredential, ...] = field(repr=False)
    limits: RuntimeLimits
    deadline_monotonic: float


@dataclass(frozen=True, slots=True)
class AuthenticatedHttpRequest:
    """Broker-authorized request; the production transport owns DNS and TLS policy."""

    url: str
    method: HttpMethod
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Private bounded response before redaction and JSON parsing."""

    status: int
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)


class HttpTransport(Protocol):
    def send(
        self,
        request: AuthenticatedHttpRequest,
        *,
        deadline_monotonic: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        """Production enforces public DNS, pinned connection, TLS, and no redirects."""
        ...


class SourceRuntime(Protocol):
    def cancel(self, run_id: str) -> None:
        """Idempotently cancel this exact run, including concurrent execute startup."""
        ...

    def execute(
        self, admission: RunAdmission, credentials: tuple[ResolvedCredential, ...]
    ) -> RunResult:
        """Run without database access and without implicit retries."""
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
    pending_script_revision: int | None
    repair_ticket_id: str | None


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
    next_status_change_at: int | None
