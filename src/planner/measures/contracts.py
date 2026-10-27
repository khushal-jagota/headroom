"""Canonical measure definitions, expression trees, observations, and read results.

Dates are validated ISO calendar-date strings. Timestamps are UTC Unix seconds.
Expressions are data, never executable Python. Numeric rules live in logic/.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict


class MeasureOrigin(StrEnum):
    taken = "taken"
    assembled = "assembled"


class MeasureKind(StrEnum):
    reading = "reading"
    running_total = "running_total"
    period_count = "period_count"


class CountPeriod(StrEnum):
    day = "day"
    week = "week"
    month = "month"


class ConstantExpression(TypedDict):
    op: Literal["constant"]
    value: float


class ReferenceExpression(TypedDict):
    op: Literal["reference"]
    measure_id: str
    day_offset: int


class BinaryExpression(TypedDict):
    op: Literal["add", "subtract", "multiply", "divide"]
    left: FormulaExpression
    right: FormulaExpression


class SumDailyExpression(TypedDict):
    """Sum daily counts over the complete period ending at the requested date."""

    op: Literal["sum_daily"]
    measure_id: str
    period: CountPeriod


type FormulaExpression = (
    ConstantExpression | ReferenceExpression | BinaryExpression | SumDailyExpression
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


class CreateMeasureBody(TypedDict):
    project_id: str
    name: str
    unit: str
    origin: MeasureOrigin
    kind: MeasureKind
    timezone: str
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
    run_id: str
    observed_at: int
    definition_revision: int
    period_interval: PeriodInterval | None


class HistoricalUnavailableReason(StrEnum):
    provider_has_no_history = "provider_has_no_history"
    provider_has_no_value = "provider_has_no_value"


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    """Null is permitted only for an explicit historical gap, never required output."""

    measure_id: str
    date: str
    value: float | None
    unavailable_reason: HistoricalUnavailableReason | None = None


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    first_date: str
    last_date: str


@dataclass(frozen=True, slots=True)
class OutputMeasure:
    """Frozen taken definition and required current date for one admitted run."""

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
    stale = "stale"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class SourceHealthSummary:
    source_id: str
    source_name: str
    health: SourceHealth
    latest_success_at: int | None
    latest_failure_run_id: str | None
    next_status_change_at: int | None


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


@dataclass(frozen=True, slots=True)
class MeasureValue:
    measure_id: str
    date: str
    definition_revision: int
    status: MeasureReadStatus
    value: float | None
    inputs: tuple[InputObservation, ...]
    missing_inputs: tuple[MeasureDate, ...]


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
