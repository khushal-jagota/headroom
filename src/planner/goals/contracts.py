"""Canonical goal records, page-kit documents and browser projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from planner.measures.contracts import MeasureValue, SourceHealthSummary

Tone = Literal["neutral", "attention", "bad", "good"]
GoalKind = Literal["binary", "quantitative"]
ChartKind = Literal["line", "bar", "gauge", "number", "binary"]


class ChartPoint(TypedDict):
    label: str
    value: float | None
    axis: NotRequired[str]


class ChartSeries(TypedDict):
    name: str
    points: list[ChartPoint]


class ChartRange(TypedDict):
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
    target: NotRequired[float]
    domain: NotRequired[tuple[float, float]]
    direction: NotRequired[Literal["atleast", "atmost"]]
    reference: NotRequired[list[ChartReference]]
    format: NotRequired[ChartFormat]
    series: NotRequired[list[ChartSeries]]
    ranges: NotRequired[list[ChartRange]]


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
    caption: NotRequired[str]


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


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    project_id: str
    title: str
    why: str
    kind: GoalKind
    active: bool
    completed: bool
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


class GoalReadingsResponse(TypedDict):
    goal_id: str
    revision: int
    status: Tone
    status_date: str
    next_refresh_at: int
    values: list[MeasureValue]
    sources: list[SourceHealthSummary]
