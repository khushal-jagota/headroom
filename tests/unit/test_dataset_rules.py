"""Pure acceptance rules protect stored tuple identity and whole-run validity.

Existing scalar rules cannot prove named dimensional rows, empty date results,
or category-label consistency. These cases need no database or browser host.
"""

from dataclasses import asdict, replace
from typing import cast

import pytest

from planner.core.contracts import ErrorCode, PlannerError
from planner.datasets.contracts import (
    CandidateDatasetResult,
    Dataset,
    DatasetDimension,
    DatasetField,
    DatasetFieldKind,
    DatasetRow,
    OutputDataset,
)
from planner.datasets.logic.validation import (
    dimension_key,
    parse_dimensions,
    parse_fields,
    parse_result,
    validate_batch,
    validate_definition,
    validate_result,
)

DIMENSIONS = (DatasetDimension("country", "Country"), DatasetDimension("browser", "Browser"))
FIELDS = (DatasetField("events", "Events", "events", DatasetFieldKind.count),)
OUTPUT = OutputDataset("events", 7, "Europe/Berlin", DIMENSIONS, FIELDS, "2026-10-29")
ROW = DatasetRow({"country": "DE", "browser": "Firefox"}, {"events": -2.5})
RESULT = CandidateDatasetResult("events", "2026-10-29", (ROW,), 100.5)
DEFINITION = Dataset(
    "events", "project", "source", "Events", "Europe/Berlin", DIMENSIONS, FIELDS, 7, 0, 0
)


def test_definition_declarations_round_trip_without_kind_arithmetic() -> None:
    validate_definition(DEFINITION)
    assert parse_dimensions([asdict(item) for item in DIMENSIONS]) == DIMENSIONS
    for kind in DatasetFieldKind:
        fields = (replace(FIELDS[0], kind=kind, unit=""),)
        assert parse_fields([asdict(item) for item in fields]) == fields
        validate_definition(replace(DEFINITION, fields=fields))
        validate_result(replace(OUTPUT, fields=fields), RESULT)


@pytest.mark.parametrize(
    "dataset",
    [
        replace(DEFINITION, name=" "),
        replace(DEFINITION, timezone="not/a/timezone"),
        replace(DEFINITION, dimensions=()),
        replace(DEFINITION, dimensions=(DIMENSIONS[0], DIMENSIONS[0])),
        replace(DEFINITION, dimensions=(DatasetDimension("", "Country"),)),
        replace(DEFINITION, fields=()),
        replace(DEFINITION, fields=(FIELDS[0], FIELDS[0])),
    ],
)
def test_invalid_definitions_never_reach_storage(dataset: Dataset) -> None:
    with pytest.raises(PlannerError) as error:
        validate_definition(dataset)
    assert error.value.code is ErrorCode.validation


@pytest.mark.parametrize(
    "value",
    [None, {}, [{"name": "country"}], [{"name": "country", "label": "Country", "codes": []}]],
)
def test_dimension_parser_rejects_noncanonical_declarations(value: object) -> None:
    with pytest.raises(PlannerError):
        parse_dimensions(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        [{"name": "events", "label": "Events", "unit": "events", "kind": "ratio"}],
        [{"name": "events", "label": "Events", "kind": "count"}],
    ],
)
def test_field_parser_rejects_noncanonical_declarations(value: object) -> None:
    with pytest.raises(PlannerError):
        parse_fields(value)


def test_result_parser_uses_contract_names_and_optional_defaults() -> None:
    assert parse_result(asdict(RESULT)) == RESULT
    parsed = parse_result(
        {"dataset_id": "events", "date": "2026-10-29", "rows": [], "query_end_at": 123}
    )
    assert parsed == replace(RESULT, rows=(), query_end_at=123.0)
    assert parsed.labels == {}


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in asdict(RESULT).items() if key != "query_end_at"},
        dict(asdict(RESULT), query_end_at=None),
        dict(asdict(RESULT), cutoff=100),
        dict(asdict(RESULT), date="20261029"),
        dict(asdict(RESULT), date="2026-W44-4"),
        dict(asdict(RESULT), date="2026-02-30"),
        dict(asdict(RESULT), rows=[{"dimensions": ROW.dimensions}]),
        dict(asdict(RESULT), rows=None),
    ],
)
def test_result_parser_rejects_missing_cutoffs_bad_dates_and_shape(
    body: dict[str, object],
) -> None:
    with pytest.raises(PlannerError):
        parse_result(body)


@pytest.mark.parametrize("value", [True, "2", float("nan"), float("inf"), 10**400])
def test_all_numeric_boundaries_reject_nonfinite_and_boolean_values(value: object) -> None:
    for candidate in (
        replace(RESULT, query_end_at=cast(float, value)),
        replace(RESULT, query_start_at=cast(float, value)),
        replace(RESULT, rows=(replace(ROW, values={"events": cast(float, value)}),)),
    ):
        with pytest.raises(PlannerError):
            validate_result(OUTPUT, candidate)
        with pytest.raises(PlannerError):
            parse_result(asdict(candidate))


def test_finite_timestamps_must_support_datetime_interpretation() -> None:
    for candidate in (
        replace(RESULT, query_end_at=1e300),
        replace(RESULT, query_start_at=-1e300),
    ):
        with pytest.raises(PlannerError, match="UTC datetime range"):
            validate_result(OUTPUT, candidate)
        with pytest.raises(PlannerError, match="UTC datetime range"):
            parse_result(asdict(candidate))
    validate_result(OUTPUT, replace(RESULT, rows=(replace(ROW, values={"events": 1e300}),)))


def test_dates_and_cutoffs_are_observations_without_extra_admission_gates() -> None:
    past = replace(RESULT, date="2000-01-01", query_start_at=-100, query_end_at=-100)
    validate_result(OUTPUT, past)
    validate_result(replace(OUTPUT, definition_revision=99), RESULT)
    validate_result(OUTPUT, replace(RESULT, rows=(), limitation="Provider retains seven days"))
    validate_result(OUTPUT, replace(RESULT, rows=(), unavailable_reason="Provider has no history"))
    with pytest.raises(PlannerError, match="interval"):
        validate_result(OUTPUT, replace(RESULT, query_start_at=101))
    with pytest.raises(PlannerError, match="cannot contain rows"):
        validate_result(OUTPUT, replace(RESULT, unavailable_reason="Provider has no history"))


@pytest.mark.parametrize(
    "row",
    [
        replace(ROW, dimensions={"country": "DE"}),
        replace(ROW, dimensions={**ROW.dimensions, "device": "desktop"}),
        replace(ROW, dimensions={"country": "DE", "Browser": "Firefox"}),
        replace(ROW, values={}),
        replace(ROW, values={"events": 1, "users": 2}),
        replace(ROW, values={"Events": 1}),
    ],
)
def test_rows_match_the_captured_dimension_and_field_names_exactly(row: DatasetRow) -> None:
    with pytest.raises(PlannerError, match="captured output"):
        validate_result(OUTPUT, replace(RESULT, rows=(row,)))


def test_tuple_identity_is_order_independent_and_preserves_names_and_codes() -> None:
    dimensions = {"country": 'DE,|["', "browser": "Firefox"}
    assert dimension_key(dimensions) == '[["browser","Firefox"],["country","DE,|[\\\""]]'
    assert dimension_key(dimensions) == dimension_key(dict(reversed(list(dimensions.items()))))
    assert dimension_key({"a": "b|c", "d": "e"}) != dimension_key({"a": "b", "c|d": "e"})
    reversed_row = replace(ROW, dimensions=dict(reversed(list(ROW.dimensions.items()))))
    with pytest.raises(PlannerError, match="duplicate.*tuple"):
        validate_result(OUTPUT, replace(RESULT, rows=(ROW, reversed_row)))


def test_batch_rejects_foreign_and_duplicate_dates_but_accepts_distinct_dates() -> None:
    previous = replace(RESULT, date="2026-10-28")
    validate_batch((OUTPUT,), (RESULT, previous))
    with pytest.raises(PlannerError, match="duplicate dataset date"):
        validate_batch((OUTPUT,), (RESULT, RESULT))
    with pytest.raises(PlannerError, match="foreign"):
        validate_batch((OUTPUT,), (previous, replace(RESULT, dataset_id="foreign")))
    with pytest.raises(PlannerError, match="foreign"):
        validate_result(OUTPUT, replace(RESULT, dataset_id="foreign"))


def test_labels_use_declared_dimensions_and_remain_consistent_within_each_dataset_run() -> None:
    current = replace(RESULT, labels={"country": {"DE": "Germany"}})
    previous = replace(current, date="2026-10-28")
    validate_batch((OUTPUT,), (current, previous))
    validate_batch((OUTPUT,), (current, replace(previous, labels={})))
    renamed = replace(previous, labels={"country": {"DE": "Deutschland"}})
    with pytest.raises(PlannerError, match="conflict"):
        validate_batch((OUTPUT,), (current, renamed))
    with pytest.raises(PlannerError, match="undeclared"):
        validate_result(OUTPUT, replace(RESULT, labels={"device": {"desktop": "Desktop"}}))
    other_output = replace(OUTPUT, dataset_id="other")
    validate_batch((OUTPUT, other_output), (current, replace(renamed, dataset_id="other")))
