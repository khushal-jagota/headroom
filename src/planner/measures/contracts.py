"""Canonical measure definitions, expression trees, observations, and read results.

Dates are validated ISO calendar-date strings. Timestamps are UTC Unix seconds.
Expressions are data, never executable Python. Numeric rules live in logic/.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict

from planner.core.contracts import Principal


class MeasureOrigin(StrEnum):
    taken = "taken"
    assembled = "assembled"
    manual = "manual"


class MeasureKind(StrEnum):
    reading = "reading"
    running_total = "running_total"
    period_count = "period_count"


class CountPeriod(StrEnum):
    day = "day"
    week = "week"
    month = "month"


class ScalarCoverageStatus(StrEnum):
    complete = "complete"
    partial = "partial"
    unknown = "unknown"
    not_applicable = "not_applicable"


class ConstantExpression(TypedDict):
    op: Literal["constant"]
    value: float


class ReferenceExpression(TypedDict):
    op: Literal["reference"]
    measure_id: str
    day_offset: int


class BinaryExpression(TypedDict):
    op: Literal["add", "subtract", "multiply", "divide", "power", "min", "max"]
    left: FormulaExpression
    right: FormulaExpression


class ElapsedDaysExpression(TypedDict):
    op: Literal["elapsed_days"]
    start: str


class ReferenceAtExpression(TypedDict):
    op: Literal["reference_at"]
    measure_id: str
    date: str


class IfBeforeExpression(TypedDict):
    op: Literal["if_before"]
    date: str
    before: FormulaExpression
    otherwise: FormulaExpression


class SumDailyExpression(TypedDict):
    """Sum dated numeric inputs in the explicit day, ISO week or calendar month."""

    op: Literal["sum_daily"]
    measure_id: str
    period: CountPeriod


type FormulaExpression = (
    ConstantExpression
    | ReferenceExpression
    | BinaryExpression
    | SumDailyExpression
    | ElapsedDaysExpression
    | ReferenceAtExpression
    | IfBeforeExpression
)


@dataclass(frozen=True, slots=True)
class Measure:
    id: str
    project_id: str
    name: str
    unit: str
    origin: MeasureOrigin
    kind: MeasureKind
    timezone: str
    period: CountPeriod | None
    source_id: str | None
    formula: FormulaExpression | None
    definition_revision: int
    created_at: int
    updated_at: int
    write_revision: int = 0


class MeasureDefinitionFields(TypedDict):
    project_id: str
    name: str
    unit: str
    origin: MeasureOrigin
    kind: MeasureKind
    timezone: str


class CreateMeasureRequest(MeasureDefinitionFields):
    """Wire input: omitted nullable fields retain the parser's None defaults."""

    period: NotRequired[CountPeriod | None]
    source_id: NotRequired[str | None]
    formula: NotRequired[FormulaExpression | None]


class CreateMeasureBody(MeasureDefinitionFields):
    """Normalized action input with every nullable field present."""

    period: CountPeriod | None
    source_id: str | None
    formula: FormulaExpression | None


class UpdateMeasureBody(TypedDict, total=False):
    expected_revision: int
    name: str
    unit: str
    kind: MeasureKind
    timezone: str
    period: CountPeriod | None
    formula: FormulaExpression


@dataclass(frozen=True, slots=True)
class MeasureDate:
    measure_id: str
    date: str


@dataclass(frozen=True, slots=True)
class PeriodInterval:
    start_date: str
    end_date_exclusive: str


@dataclass(frozen=True, slots=True)
class Observation:
    measure_id: str
    date: str
    value: float
    run_id: str | None
    observed_at: int
    definition_revision: int
    period_interval: PeriodInterval | None
    manual_write_id: str | None = None
    query_start_at: float | None = None
    query_end_at: float | None = None
    limitation: str | None = None
    coverage_status: ScalarCoverageStatus = ScalarCoverageStatus.not_applicable
    coverage_reasons: tuple[str, ...] = ()


class HistoricalUnavailableReason(StrEnum):
    provider_has_no_history = "provider_has_no_history"
    provider_has_no_value = "provider_has_no_value"


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    """A value or an explicit unavailable result supplied by the source."""

    measure_id: str
    date: str
    value: float | None
    unavailable_reason: HistoricalUnavailableReason | None = None
    query_start_at: float | None = None
    query_end_at: float | None = None
    limitation: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    first_date: str
    last_date: str


@dataclass(frozen=True, slots=True)
class OutputMeasure:
    """Frozen definition with a suggested date; records need not use that date."""

    measure_id: str
    definition_revision: int
    kind: MeasureKind
    timezone: str
    period: CountPeriod | None
    required_date: str
    period_interval: PeriodInterval | None


class SourceHealth(StrEnum):
    disabled = "disabled"
    never_collected = "never_collected"
    healthy = "healthy"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class SourceHealthSummary:
    source_id: str
    source_name: str
    health: SourceHealth
    latest_success_at: int | None
    latest_failure_run_id: str | None


@dataclass(frozen=True, slots=True)
class LatestMeasureObservation:
    measure_id: str
    name: str
    unit: str
    kind: MeasureKind
    observation: Observation | None


class MeasureReadStatus(StrEnum):
    value = "value"
    missing_input = "missing_input"
    division_by_zero = "division_by_zero"
    invalid_period = "invalid_period"
    arithmetic_limit = "arithmetic_limit"
    invalid_value = "invalid_value"


@dataclass(frozen=True, slots=True)
class InputObservation:
    """One evaluated input, including its current definition and stored provenance."""

    measure_id: str
    date: str
    definition_revision: int
    value: float | None
    run_id: str | None
    manual_write_id: str | None = None
    query_start_at: float | None = None
    query_end_at: float | None = None
    limitation: str | None = None
    coverage_status: ScalarCoverageStatus = ScalarCoverageStatus.not_applicable
    coverage_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MeasureValue:
    measure_id: str
    date: str
    definition_revision: int
    status: MeasureReadStatus
    value: float | None
    inputs: tuple[InputObservation, ...]
    missing_inputs: tuple[MeasureDate, ...]
    query_start_at: float | None = None
    query_end_at: float | None = None
    limitation: str | None = None
    coverage_status: ScalarCoverageStatus = ScalarCoverageStatus.not_applicable
    coverage_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpressionValue:
    """An evaluated expression without an observed-measure identity."""

    status: MeasureReadStatus
    value: float | None
    inputs: tuple[InputObservation, ...]
    missing_inputs: tuple[MeasureDate, ...]
    coverage_status: ScalarCoverageStatus = ScalarCoverageStatus.not_applicable
    coverage_reasons: tuple[str, ...] = ()


class ReadMeasuresBody(TypedDict):
    measure_ids: list[str]
    first_date: str
    last_date: str
    limit: NotRequired[int]


@dataclass(frozen=True, slots=True)
class MeasuresRead:
    """All values come from one coherent SQLite snapshot."""

    values: tuple[MeasureValue, ...]
    sources: tuple[SourceHealthSummary, ...]


class ManualWriteCommon(TypedDict):
    request_id: str
    date: str
    expected_definition_revision: int
    basis: Literal["date", "latest"]


class ManualReplaceBody(ManualWriteCommon):
    operation: Literal["replace"]
    value: float
    expected_write_revision: int


class ManualIncrementBody(ManualWriteCommon):
    operation: Literal["increment"]
    delta: float


type ManualWriteBody = ManualReplaceBody | ManualIncrementBody


@dataclass(frozen=True, slots=True)
class ManualMeasureState:
    measure_id: str
    definition_revision: int
    write_revision: int
    timezone: str
    current_date: str
    date: str
    observation: Observation | None
    latest_observation: Observation | None


@dataclass(frozen=True, slots=True)
class ManualWriteReceipt:
    request_id: str
    measure_id: str
    actor: Principal
    payload_fingerprint: str
    date: str
    value: float
    write_revision: int
    accepted_at: int


@dataclass(frozen=True, slots=True)
class ManualWriteResult:
    receipt: ManualWriteReceipt
    state: ManualMeasureState
    replayed: bool
