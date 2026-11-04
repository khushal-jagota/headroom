"""Private persisted record codecs. No credential-bearing type enters these codecs."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from planner.datasets.contracts import CandidateDatasetResult, OutputDataset
from planner.datasets.logic.validation import parse_dimensions, parse_fields, parse_result
from planner.measure_sources.contracts import FailureCode, RunFailure, RunMetrics, RuntimeBudget
from planner.measures.contracts import (
    CandidateObservation,
    CountPeriod,
    HistoricalUnavailableReason,
    MeasureKind,
    OutputMeasure,
    PeriodInterval,
)


def encode_dataset_outputs(outputs: tuple[OutputDataset, ...]) -> str:
    return json.dumps([asdict(item) for item in outputs], allow_nan=False, separators=(",", ":"))


def decode_dataset_outputs(raw: str) -> tuple[OutputDataset, ...]:
    return tuple(
        OutputDataset(
            dataset_id=row["dataset_id"],
            definition_revision=row["definition_revision"],
            timezone=row["timezone"],
            dimensions=parse_dimensions(row["dimensions"]),
            fields=parse_fields(row["fields"]),
            required_date=row["required_date"],
        )
        for row in json.loads(raw)
    )


def encode_dataset_results(results: tuple[CandidateDatasetResult, ...]) -> str:
    return json.dumps([asdict(item) for item in results], allow_nan=False, separators=(",", ":"))


def decode_dataset_results(raw: str | None) -> tuple[CandidateDatasetResult, ...] | None:
    return None if raw is None else tuple(parse_result(row) for row in json.loads(raw))


def encode_outputs(outputs: tuple[OutputMeasure, ...]) -> str:
    return json.dumps([asdict(item) for item in outputs], allow_nan=False, separators=(",", ":"))


def decode_outputs(raw: str) -> tuple[OutputMeasure, ...]:
    rows = json.loads(raw)
    return tuple(
        OutputMeasure(
            measure_id=row["measure_id"],
            definition_revision=row["definition_revision"],
            kind=MeasureKind(row["kind"]),
            timezone=row["timezone"],
            period=CountPeriod(row["period"]) if row["period"] is not None else None,
            required_date=row["required_date"],
            period_interval=PeriodInterval(**row["period_interval"])
            if row["period_interval"] is not None
            else None,
        )
        for row in rows
    )


def failure_json(failure: RunFailure | None) -> dict[str, Any] | None:
    return None if failure is None else asdict(failure)


def decode_failure(raw: str | None) -> RunFailure | None:
    if raw is None:
        return None
    value = json.loads(raw)
    return RunFailure(
        code=FailureCode(value["code"]),
        budget=RuntimeBudget(value["budget"]) if value["budget"] is not None else None,
        http_status=value["http_status"],
        measure_id=value["measure_id"],
        date=value["date"],
        exception_type=value.get("exception_type"),
        source_line=value.get("source_line"),
    )


def encode_candidates(values: tuple[CandidateObservation, ...]) -> str:
    return json.dumps([asdict(item) for item in values], allow_nan=False, separators=(",", ":"))


def decode_candidates(raw: str | None) -> tuple[CandidateObservation, ...] | None:
    if raw is None:
        return None
    return tuple(
        CandidateObservation(
            measure_id=row["measure_id"],
            date=row["date"],
            value=row["value"],
            unavailable_reason=HistoricalUnavailableReason(row["unavailable_reason"])
            if row["unavailable_reason"] is not None
            else None,
            query_start_at=row.get("query_start_at"),
            query_end_at=row.get("query_end_at"),
            limitation=row.get("limitation"),
        )
        for row in json.loads(raw)
    )


def decode_metrics(raw: str | None) -> RunMetrics | None:
    return None if raw is None else RunMetrics(**json.loads(raw))
