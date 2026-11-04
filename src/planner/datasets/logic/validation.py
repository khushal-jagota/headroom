"""Validate captured dataset outputs without consulting storage or providers."""

import json
from datetime import UTC, datetime
from typing import cast

from planner.datasets.contracts import (
    CandidateDatasetResult,
    Dataset,
    DatasetDimension,
    DatasetField,
    DatasetFieldKind,
    DatasetRow,
    OutputDataset,
)
from planner.measures.logic.dates import calendar_date, invalid, timezone
from planner.measures.logic.formulas import number


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise invalid(f"invalid dataset {name}")
    return value


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise invalid(f"invalid dataset {name}")
    return cast(dict[str, object], value)


def _sequence(value: object, name: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise invalid(f"invalid dataset {name}")
    return tuple(value)


def parse_dimensions(value: object) -> tuple[DatasetDimension, ...]:
    dimensions = []
    for item in _sequence(value, "dimensions"):
        if isinstance(item, DatasetDimension):
            dimension = item
        else:
            body = _object(item, "dimension")
            if set(body) != {"name", "label"}:
                raise invalid("invalid dataset dimension declaration")
            dimension = DatasetDimension(
                _text(body["name"], "dimension name"), _text(body["label"], "dimension label")
            )
        _text(dimension.name, "dimension name")
        _text(dimension.label, "dimension label")
        dimensions.append(dimension)
    if not dimensions or len({item.name for item in dimensions}) != len(dimensions):
        raise invalid("dataset dimensions must be nonempty and unique")
    return tuple(dimensions)


def parse_fields(value: object) -> tuple[DatasetField, ...]:
    fields = []
    for item in _sequence(value, "fields"):
        if isinstance(item, DatasetField):
            dataset_field = item
        else:
            body = _object(item, "field")
            if set(body) != {"name", "label", "unit", "kind"}:
                raise invalid("invalid dataset field declaration")
            try:
                kind = DatasetFieldKind(_text(body["kind"], "field kind"))
            except (TypeError, ValueError):
                raise invalid("invalid dataset field kind") from None
            dataset_field = DatasetField(
                _text(body["name"], "field name"),
                _text(body["label"], "field label"),
                _text(body["unit"], "field unit", allow_empty=True),
                kind,
            )
        _text(dataset_field.name, "field name")
        _text(dataset_field.label, "field label")
        _text(dataset_field.unit, "field unit", allow_empty=True)
        if not isinstance(dataset_field.kind, DatasetFieldKind):
            raise invalid("invalid dataset field kind")
        fields.append(dataset_field)
    if not fields or len({item.name for item in fields}) != len(fields):
        raise invalid("dataset fields must be nonempty and unique")
    return tuple(fields)


def validate_definition(dataset: Dataset) -> None:
    _text(dataset.id, "id")
    _text(dataset.project_id, "Project")
    _text(dataset.source_id, "source")
    _text(dataset.name, "name")
    timezone(dataset.timezone)
    parse_dimensions(dataset.dimensions)
    parse_fields(dataset.fields)


def dimension_key(dimensions: dict[str, str]) -> str:
    """Name every code so declaration order and delimiter text cannot collide."""
    return json.dumps(sorted(dimensions.items()), separators=(",", ":"))


def _parse_row(value: object) -> DatasetRow:
    body = _object(value, "row")
    if set(body) != {"dimensions", "values"}:
        raise invalid("invalid dataset row")
    dimensions = {
        name: _text(code, "category code", allow_empty=True)
        for name, code in _object(body["dimensions"], "row dimensions").items()
    }
    values = {
        name: number(value) for name, value in _object(body["values"], "row values").items()
    }
    return DatasetRow(dimensions, values)


def _parse_labels(value: object) -> dict[str, dict[str, str]]:
    return {
        dimension: {
            code: _text(label, "category label")
            for code, label in _object(labels, "category labels").items()
        }
        for dimension, labels in _object(value, "labels").items()
    }


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _timestamp(value: object) -> float:
    timestamp = number(value)
    try:
        datetime.fromtimestamp(timestamp, UTC)
    except (OverflowError, OSError, ValueError):
        raise invalid("dataset query timestamp is outside the UTC datetime range") from None
    return timestamp


def parse_result(value: object) -> CandidateDatasetResult:
    body = _object(value, "result")
    required = {"dataset_id", "date", "rows", "query_end_at"}
    optional = {"query_start_at", "labels", "unavailable_reason", "limitation"}
    if not required <= set(body) or set(body) - required - optional:
        raise invalid("invalid dataset result fields")
    start = body.get("query_start_at")
    candidate = CandidateDatasetResult(
        dataset_id=_text(body["dataset_id"], "id"),
        date=_text(body["date"], "date"),
        rows=tuple(_parse_row(row) for row in _sequence(body["rows"], "rows")),
        query_end_at=number(body["query_end_at"]),
        query_start_at=None if start is None else number(start),
        labels=_parse_labels(body.get("labels", {})),
        unavailable_reason=_optional_text(body.get("unavailable_reason"), "unavailable reason"),
        limitation=_optional_text(body.get("limitation"), "limitation"),
    )
    _validate_candidate(candidate)
    return candidate


def _validate_candidate(candidate: CandidateDatasetResult) -> None:
    _text(candidate.dataset_id, "id")
    calendar_date(candidate.date)
    end = _timestamp(candidate.query_end_at)
    if candidate.query_start_at is not None and _timestamp(candidate.query_start_at) > end:
        raise invalid("dataset query interval is reversed")
    _optional_text(candidate.unavailable_reason, "unavailable reason")
    _optional_text(candidate.limitation, "limitation")
    if candidate.unavailable_reason is not None and candidate.rows:
        raise invalid("unavailable dataset result cannot contain rows")
    _parse_labels(candidate.labels)
    seen: set[str] = set()
    for row in candidate.rows:
        for code in _object(row.dimensions, "row dimensions").values():
            _text(code, "category code", allow_empty=True)
        for value in _object(row.values, "row values").values():
            number(value)
        key = dimension_key(row.dimensions)
        if key in seen:
            raise invalid("duplicate dataset dimension tuple")
        seen.add(key)


def validate_result(output: OutputDataset, candidate: CandidateDatasetResult) -> None:
    _validate_candidate(candidate)
    if candidate.dataset_id != output.dataset_id:
        raise invalid("foreign dataset output")
    dimensions = {dimension.name for dimension in output.dimensions}
    fields = {dataset_field.name for dataset_field in output.fields}
    if any(
        set(row.dimensions) != dimensions or set(row.values) != fields for row in candidate.rows
    ):
        raise invalid("dataset row names differ from captured output")
    if set(candidate.labels) - dimensions:
        raise invalid("dataset labels name an undeclared dimension")


def validate_batch(
    outputs: tuple[OutputDataset, ...], candidates: tuple[CandidateDatasetResult, ...]
) -> None:
    by_id = {output.dataset_id: output for output in outputs}
    if len(by_id) != len(outputs):
        raise invalid("duplicate captured dataset output")
    seen: set[tuple[str, str]] = set()
    labels: dict[tuple[str, str, str], str] = {}
    for candidate in candidates:
        output = by_id.get(candidate.dataset_id)
        if output is None:
            raise invalid("foreign dataset output")
        pair = (candidate.dataset_id, candidate.date)
        if pair in seen:
            raise invalid("duplicate dataset date result")
        seen.add(pair)
        validate_result(output, candidate)
        for dimension, dimension_labels in candidate.labels.items():
            for code, label in dimension_labels.items():
                key = (candidate.dataset_id, dimension, code)
                if key in labels and labels[key] != label:
                    raise invalid("dataset category labels conflict within the run")
                labels[key] = label
