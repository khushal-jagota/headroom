"""Dataset definitions, captured outputs, and retained date results.

Category codes identify records. Labels only describe them. A date result exists
even when it has no rows, so successful empty output differs from absent output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import NotRequired, TypedDict

from planner.measures.contracts import SourceHealthSummary


class DatasetFieldKind(StrEnum):
    count = "count"
    amount = "amount"
    snapshot = "snapshot"


@dataclass(frozen=True, slots=True)
class DatasetDimension:
    name: str
    label: str


@dataclass(frozen=True, slots=True)
class DatasetField:
    name: str
    label: str
    unit: str
    kind: DatasetFieldKind


@dataclass(frozen=True, slots=True)
class Dataset:
    id: str
    project_id: str
    source_id: str
    name: str
    timezone: str
    dimensions: tuple[DatasetDimension, ...]
    fields: tuple[DatasetField, ...]
    definition_revision: int
    created_at: int
    updated_at: int


class CreateDatasetBody(TypedDict):
    project_id: str
    source_id: str
    name: str
    timezone: str
    dimensions: tuple[DatasetDimension, ...]
    fields: tuple[DatasetField, ...]


class UpdateDatasetBody(TypedDict, total=False):
    expected_revision: int
    name: str
    timezone: str
    dimensions: tuple[DatasetDimension, ...]
    fields: tuple[DatasetField, ...]


@dataclass(frozen=True, slots=True)
class OutputDataset:
    dataset_id: str
    definition_revision: int
    timezone: str
    dimensions: tuple[DatasetDimension, ...]
    fields: tuple[DatasetField, ...]
    required_date: str


@dataclass(frozen=True, slots=True)
class DatasetRow:
    dimensions: dict[str, str]
    values: dict[str, float]


@dataclass(frozen=True, slots=True)
class CandidateDatasetResult:
    dataset_id: str
    date: str
    rows: tuple[DatasetRow, ...]
    query_end_at: float
    query_start_at: float | None = None
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    unavailable_reason: str | None = None
    limitation: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetProvenance:
    run_id: str
    source_id: str
    source_revision: int
    script_revision: int
    script_sha256: str
    definition_revision: int
    collected_at: int


@dataclass(frozen=True, slots=True)
class DatasetResult:
    dataset_id: str
    date: str
    rows: tuple[DatasetRow, ...]
    query_end_at: float
    query_start_at: float | None
    unavailable_reason: str | None
    limitation: str | None
    provenance: DatasetProvenance
    # Preserve the declaration even when this date has no rows and later edits
    # change the current fields. This is observed schema, not a revision gate.
    output: OutputDataset


@dataclass(frozen=True, slots=True)
class DatasetCategoryLabel:
    dataset_id: str
    dimension: str
    code: str
    label: str


@dataclass(frozen=True, slots=True)
class DatasetDate:
    dataset_id: str
    date: str


class ReadDatasetsBody(TypedDict):
    dataset_ids: list[str]
    first_date: str
    last_date: str
    limit: NotRequired[int]


@dataclass(frozen=True, slots=True)
class DatasetsRead:
    first_date: str
    last_date: str
    datasets: tuple[Dataset, ...]
    results: tuple[DatasetResult, ...]
    labels: tuple[DatasetCategoryLabel, ...]
    missing_dates: tuple[DatasetDate, ...]
    sources: tuple[SourceHealthSummary, ...]
    returned_rows: int
    matching_rows: int
    truncated: bool
