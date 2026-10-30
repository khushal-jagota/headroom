"""Canonical goal records, page-kit documents and browser projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from planner.datasets.contracts import DatasetResult
from planner.measures.contracts import (
    ExpressionValue,
    FormulaExpression,
    MeasureReadStatus,
    MeasureValue,
    ScalarCoverageStatus,
    SourceHealthSummary,
)

Tone = Literal["neutral", "attention", "bad", "good"]
TableCellTone = Literal["neutral", "attention", "bad", "good", "info"]
GoalKind = Literal["binary", "quantitative"]
ChartKind = Literal[
    "line", "bar", "gauge", "number", "binary", "progress", "time-budget", "allocation"
]


class ChartPoint(TypedDict):
    label: str
    value: float | None
    axis: NotRequired[str]


class ChartSeries(TypedDict):
    key: NotRequired[str]
    color: NotRequired[Literal["blue", "blue-muted", "blue-dark"]]
    name: str
    points: list[ChartPoint]


class ChartRange(TypedDict):
    target: NotRequired[float]
    domain: NotRequired[tuple[float, float]]
    id: str
    label: str
    series: list[ChartSeries]
    caption: NotRequired[str]
    period: NotRequired[str]


class ChartReference(TypedDict):
    date: str
    value: float


class ChartFormat(TypedDict, total=False):
    prefix: str
    suffix: str
    decimals: int


class ManualChartControls(TypedDict):
    model: Literal["daily_quota", "cumulative_target", "individual_readings"]
    increments: NotRequired[list[float]]


class DatasetFilter(TypedDict):
    dimension: str
    codes: list[str]


class DatasetFieldMetric(TypedDict):
    field: str


class DatasetRatioMetric(TypedDict):
    numerator: str
    denominator: str
    scale: NotRequired[float]


class DatasetChartBinding(TypedDict):
    kind: Literal["dataset"]
    dataset_id: str
    group_by: str
    filters: list[DatasetFilter]
    aggregation: Literal["sum", "latest"]
    metric: DatasetFieldMetric | DatasetRatioMetric


class ScalarChartInput(TypedDict):
    key: str
    label: str
    measure_id: str
    role: NotRequired[Literal["spent", "planned"]]


class ScalarInputsChartBinding(TypedDict):
    kind: Literal["scalar_inputs"]
    inputs: list[ScalarChartInput]
    aggregation: Literal["sum", "latest"]


type ChartBinding = DatasetChartBinding | ScalarInputsChartBinding


class ChartBlock(TypedDict):
    type: Literal["chart"]
    id: str
    kind: ChartKind
    placement: Literal["primary", "supporting"]
    title: NotRequired[str]
    caption: NotRequired[str]
    period: NotRequired[str]
    unit: NotRequired[str]
    measure_id: NotRequired[str]
    binding: NotRequired[ChartBinding]
    target: NotRequired[float]
    domain: NotRequired[tuple[float, float]]
    direction: NotRequired[Literal["atleast", "atmost"]]
    reference: NotRequired[list[ChartReference]]
    expectation: NotRequired[FormulaExpression]
    format: NotRequired[ChartFormat]
    series: NotRequired[list[ChartSeries]]
    ranges: NotRequired[list[ChartRange]]
    stacked: NotRequired[bool]
    aggregate: NotRequired[Literal["sum"]]
    below_target_tone: NotRequired[Literal["attention", "bad"]]
    target_label: NotRequired[Literal["target", "floor", "limit"]]
    difference_unit: NotRequired[Literal["pp"]]
    allocation_mode: NotRequired[Literal["value", "percentage"]]
    total_label: NotRequired[str]
    plot_height: NotRequired[int]
    scale_label: NotRequired[str]
    shortfall_label: NotRequired[Literal["below target", "to target"]]
    comparison_decimals: NotRequired[int]
    within_limit_label: NotRequired[Literal["under limit", "remaining"]]
    emphasis: NotRequired[Literal["hero"]]
    manual: NotRequired[ManualChartControls]


class ChecklistItem(TypedDict):
    id: str
    label: str
    state: Literal["done", "open", "blocked"]
    detail: NotRequired[str]
    action: NotRequired[str]


class TextBlock(TypedDict):
    type: Literal["text"]
    id: str
    text: str


class ChecklistBlock(TypedDict):
    type: Literal["checklist"]
    id: str
    items: list[ChecklistItem]
    title: NotRequired[str]
    doneTitle: NotRequired[str]


class TableBlock(TypedDict):
    type: Literal["table"]
    id: str
    columns: list[str]
    rows: list[list[str]]
    cell_tones: NotRequired[list[list[TableCellTone | None]]]
    caption: NotRequired[str]
    empty_message: NotRequired[str]
    column_widths: NotRequired[list[float]]


class SectionBlock(TypedDict):
    type: Literal["section"]
    id: str
    title: str
    summary: NotRequired[str]
    blocks: NotRequired[list[Block]]
    views: NotRequired[list[SegmentedView]]


class GridBlock(TypedDict):
    type: Literal["grid"]
    id: str
    blocks: list[Block]
    columns: NotRequired[Literal[2, 3, 4]]
    joined: NotRequired[bool]
    align: NotRequired[Literal["stretch", "start"]]


class SegmentedView(TypedDict):
    id: str
    label: str
    blocks: list[Block]


class SegmentedBlock(TypedDict):
    type: Literal["segmented"]
    id: str
    views: list[SegmentedView]


class CalloutBlock(TypedDict):
    type: Literal["callout"]
    id: str
    tone: Tone
    text: str
    title: NotRequired[str]


class MetricItem(TypedDict):
    label: str
    value: str
    hint: NotRequired[str]
    change: NotRequired[str]
    tone: NotRequired[Tone]


class MetricsBlock(TypedDict):
    type: Literal["metrics"]
    id: str
    items: list[MetricItem]


class PropertyItem(TypedDict):
    label: str
    value: str


class PropertiesBlock(TypedDict):
    type: Literal["properties"]
    id: str
    items: list[PropertyItem]


class StatusBlock(TypedDict):
    type: Literal["status"]
    id: str
    label: str
    tone: Tone


class RowItem(TypedDict):
    title: str
    detail: NotRequired[str]
    meta: NotRequired[str]
    status: NotRequired[str]
    tone: NotRequired[Tone]


class RowsBlock(TypedDict):
    type: Literal["rows"]
    id: str
    items: list[RowItem]


type Block = (
    TextBlock
    | ChecklistBlock
    | ChartBlock
    | TableBlock
    | SectionBlock
    | GridBlock
    | SegmentedBlock
    | CalloutBlock
    | MetricsBlock
    | PropertiesBlock
    | StatusBlock
    | RowsBlock
)


class PageDocument(TypedDict):
    kind: Literal["headroom.page"]
    version: Literal[1]
    title: str
    summary: str
    blocks: list[Block]


# Persisted text checks also apply to partial edits. Creation additionally
# requires the author's kind and that kind's explicit main-display selection.
GOAL_REQUIRED_TEXT = ("title", "project_id")
GOAL_CREATE_REQUIRED = (*GOAL_REQUIRED_TEXT, "kind")


class GoalCommonFields(TypedDict, total=False):
    project_id: str
    title: str
    why: str
    active: bool
    completed: bool
    deadline: str | None
    page: PageDocument


class GoalWriteFields(GoalCommonFields, total=False):
    kind: GoalKind
    primary_checklist_block_id: str | None
    primary_chart: ChartBlock | None


class GoalCreateFields(GoalCommonFields, total=False):
    # The existing create writer accepts and ignores this compatibility field.
    expected_revision: object


class BinaryGoalCreateBody(GoalCreateFields):
    kind: Literal["binary"]
    primary_checklist_block_id: str | None
    primary_chart: NotRequired[ChartBlock | None]


class QuantitativeGoalCreateBody(GoalCreateFields):
    kind: Literal["quantitative"]
    primary_chart: ChartBlock
    primary_checklist_block_id: NotRequired[str | None]


type GoalCreateBody = BinaryGoalCreateBody | QuantitativeGoalCreateBody


class GoalEditBody(GoalWriteFields):
    expected_revision: int


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    project_id: str
    title: str
    why: str
    kind: GoalKind
    active: bool
    # Stored binary-component input; effective completion belongs to GoalResponse.
    binary_checked: bool
    primary_checklist_block_id: str | None
    deadline: str | None
    primary_chart: ChartBlock | None
    page: PageDocument
    revision: int
    created_at: int
    updated_at: int
    employee_backend: str
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None


class GoalResponse(TypedDict):
    id: str
    project_id: str
    title: str
    why: str
    kind: GoalKind
    active: bool
    completed: bool
    primary_checklist_block_id: str | None
    deadline: str | None
    primary_chart: ChartBlock | None
    page: PageDocument
    revision: int
    created_at: int
    updated_at: int
    employee_backend: str
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None
    conversation_id: str | None
    status: Tone
    status_date: str
    next_refresh_at: int


class GoalsResponse(TypedDict):
    goals: list[GoalResponse]
    status_date: str
    next_refresh_at: int


class ScalarChartPoint(TypedDict):
    date: str
    actual: MeasureValue
    expected: ExpressionValue
    difference: float | None
    meets_expectation: bool | None
    break_before: bool


class ScalarChartProjection(TypedDict):
    kind: Literal["scalar"]
    first_date: str
    last_date: str
    today: str
    expected_source: Literal["expectation"]
    points: list[ScalarChartPoint]
    summary_date: str | None


class CategoryChartValue(TypedDict):
    status: MeasureReadStatus
    value: float | None
    numerator: float | None
    denominator: float | None
    partial: bool
    missing_dates: list[str]
    reasons: list[str]
    coverage_status: NotRequired[ScalarCoverageStatus]


class CategoryChartSummary(TypedDict):
    actual: CategoryChartValue
    comparison: CategoryChartValue | None
    difference: float | None
    meets_expectation: bool | None
    date: str | None


class CategoryChartPoint(TypedDict):
    key: str
    label: str
    date: str | None
    actual: CategoryChartValue


class CategoryChartSeries(TypedDict):
    key: str
    label: str
    points: list[CategoryChartPoint]
    summary: CategoryChartValue | None


class CategoryPointSummary(TypedDict):
    key: str
    summary: CategoryChartSummary


class CategoryChartRange(TypedDict):
    id: Literal["week", "month", "all"]
    label: str
    first_date: str
    last_date: str
    axis: Literal["category", "date"]
    series: list[CategoryChartSeries]
    summary: CategoryChartSummary
    point_summaries: list[CategoryPointSummary]


class CategoryChartProjection(TypedDict):
    kind: Literal["category"]
    today: str
    timezone: str
    ranges: list[CategoryChartRange]
    dataset_results: list[DatasetResult]
    measure_values: list[MeasureValue]


type ChartProjection = ScalarChartProjection | CategoryChartProjection


class GoalReadingsResponse(TypedDict):
    goal_id: str
    revision: int
    status: Tone
    status_date: str
    next_refresh_at: int
    values: list[MeasureValue]
    sources: list[SourceHealthSummary]
    charts: dict[str, ChartProjection]
