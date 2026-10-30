"""Pure risks: joint arithmetic, common snapshots, and honest retained coverage."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from planner.core.contracts import PlannerError
from planner.datasets.contracts import (
    Dataset,
    DatasetCategoryLabel,
    DatasetDimension,
    DatasetField,
    DatasetFieldKind,
    DatasetProvenance,
    DatasetResult,
    DatasetRow,
    DatasetsRead,
    OutputDataset,
)
from planner.goals.contracts import (
    CategoryChartProjection,
    CategoryChartRange,
    ChartBlock,
    ChartKind,
    DatasetFieldMetric,
    DatasetFilter,
    DatasetRatioMetric,
    ScalarChartInput,
)
from planner.goals.logic.categories import (
    category_bounds,
    dataset_chart_projection,
    scalar_inputs_chart_projection,
    validate_category_chart,
    validate_category_references,
)
from planner.measures.contracts import (
    Measure,
    MeasureKind,
    MeasureOrigin,
    MeasureReadStatus,
    MeasureValue,
    ScalarCoverageStatus,
)

NOW = int(datetime(2026, 10, 29, 12, tzinfo=UTC).timestamp())
FIELDS = tuple(DatasetField(name, name, "", DatasetFieldKind.count) for name in ("n", "d"))


def dataset(fields: tuple[DatasetField, ...] = FIELDS) -> Dataset:
    return Dataset(
        "dataset",
        "project",
        "source",
        "Counts",
        "UTC",
        (DatasetDimension("surface", "Surface"), DatasetDimension("country", "Country")),
        fields,
        2,
        NOW,
        NOW,
    )


def result(
    day: str,
    rows: tuple[DatasetRow, ...] = (),
    *,
    fields: tuple[DatasetField, ...] = FIELDS,
) -> DatasetResult:
    end = datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(days=1)
    output = OutputDataset("dataset", 1, "UTC", dataset().dimensions, fields, day)
    provenance = DatasetProvenance("run-" + day, "source", 2, 3, "sha", 1, NOW)
    return DatasetResult(
        "dataset", day, rows, end.timestamp(), None, None, None, provenance, output
    )


def row(code: str, n: float, d: float = 1.0, *, country: str = "US") -> DatasetRow:
    return DatasetRow({"surface": code, "country": country}, {"n": n, "d": d})


def dataset_chart(
    *,
    aggregation: Literal["sum", "latest"] = "sum",
    kind: ChartKind = "bar",
    metric: DatasetFieldMetric | DatasetRatioMetric | None = None,
    filters: list[DatasetFilter] | None = None,
) -> ChartBlock:
    return {
        "type": "chart",
        "id": "chart",
        "kind": kind,
        "placement": "primary",
        "binding": {
            "kind": "dataset",
            "dataset_id": "dataset",
            "group_by": "surface",
            "filters": filters or [],
            "aggregation": aggregation,
            "metric": metric if metric is not None else {"field": "n"},
        },
    }


def project(
    chart: ChartBlock,
    results: tuple[DatasetResult, ...],
    *,
    definition: Dataset | None = None,
    first: str | None = "2026-10-28",
    last: str | None = "2026-10-29",
    labels: tuple[DatasetCategoryLabel, ...] = (),
) -> CategoryChartProjection:
    reading = DatasetsRead(
        "2026-09-01",
        "2026-10-31",
        (definition or dataset(),),
        results,
        labels,
        (),
        (),
        sum(len(item.rows) for item in results),
        sum(len(item.rows) for item in results),
        False,
    )
    return dataset_chart_projection(
        chart,
        reading,
        now=NOW,
        earliest_date=min((item.date for item in results), default=None),
        first_date=first,
        last_date=last,
    )


def selected(projection: CategoryChartProjection, key: str = "all") -> CategoryChartRange:
    return next(item for item in projection["ranges"] if item["id"] == key)


def measure(identifier: str, *, origin: MeasureOrigin = MeasureOrigin.manual) -> Measure:
    return Measure(
        identifier,
        "project",
        identifier,
        "h",
        origin,
        MeasureKind.reading,
        "UTC",
        None,
        None,
        None,
        1,
        NOW,
        NOW,
    )


def scalar(identifier: str, day: str, value: float | None) -> MeasureValue:
    return MeasureValue(
        identifier,
        day,
        1,
        MeasureReadStatus.value if value is not None else MeasureReadStatus.missing_input,
        value,
        (),
        (),
    )


def scalar_chart(
    *,
    kind: ChartKind = "allocation",
    aggregation: Literal["sum", "latest"] = "latest",
) -> ChartBlock:
    inputs: list[ScalarChartInput] = [
        {"key": "actual", "label": "Spent", "measure_id": "spent"},
        {"key": "target", "label": "Planned", "measure_id": "planned"},
    ]
    if kind == "time-budget":
        inputs[0]["role"], inputs[1]["role"] = "spent", "planned"
    return {
        "type": "chart",
        "id": "scalar-chart",
        "kind": kind,
        "placement": "primary",
        "binding": {"kind": "scalar_inputs", "inputs": inputs, "aggregation": aggregation},
    }


def scalar_project(
    chart: ChartBlock,
    values: tuple[MeasureValue, ...],
    submitted: dict[str, frozenset[str]],
) -> CategoryChartProjection:
    return scalar_inputs_chart_projection(
        chart,
        {key: measure(key) for key in ("spent", "planned")},
        values,
        now=NOW,
        earliest_date="2026-10-28",
        submitted_dates=submitted,
        first_date="2026-10-28",
        last_date="2026-10-29",
    )


def test_joint_filters_weight_ratios_and_preserve_code_identity_across_ranges() -> None:
    """Independent filters and renamed/colliding labels cannot change weighted totals or keys."""
    chart = dataset_chart(
        metric={"numerator": "n", "denominator": "d", "scale": 100.0},
        filters=[
            {"dimension": "surface", "codes": ["actual", "target"]},
            {"dimension": "country", "codes": ["US"]},
        ],
    )
    results = (
        result("2026-10-01", (row("actual", 1, 2), row("actual", 100, 1, country="DE"))),
        result("2026-10-28", (row("target", 9, 10), row("other", 500, 1))),
    )
    labels = tuple(
        DatasetCategoryLabel("dataset", "surface", code, "Renamed") for code in ("actual", "target")
    )
    projection = project(chart, results, first="2026-10-01", last="2026-10-28", labels=labels)
    total = selected(projection)["summary"]["actual"]
    assert (total["numerator"], total["denominator"]) == (10, 12)
    assert total["value"] == pytest.approx(1000 / 12)
    assert selected(projection, "week")["summary"]["actual"]["value"] == 90
    points = selected(projection)["series"][0]["points"]
    assert [item["key"] for item in points] == ['category:"actual"', 'category:"target"']
    assert [item["label"] for item in points] == ["Renamed", "Renamed"]
    assert all(item["date"] is None for item in points)
    dated = project({**chart, "kind": "line"}, results, first="2026-10-01", last="2026-10-28")
    assert [item["key"] for item in selected(dated)["series"]] == [item["key"] for item in points]
    assert dated["dataset_results"] == list(results)


def test_empty_history_uses_captured_names_and_missing_dates_never_become_zero() -> None:
    """An edited definition cannot give old empty output a new field or erase old row fields."""
    fields = (*FIELDS, DatasetField("new", "New", "", DatasetFieldKind.count))
    new_empty = result("2026-10-28", fields=fields)
    projection = project(
        dataset_chart(kind="line", metric={"field": "new"}),
        (result("2026-10-27"), new_empty),
        definition=dataset(fields),
        first="2026-10-27",
    )
    inspections = selected(projection)["point_summaries"]
    assert inspections[0]["summary"]["actual"]["status"] == MeasureReadStatus.missing_input
    assert "lacks: new" in inspections[0]["summary"]["actual"]["reasons"][0]
    assert inspections[1]["summary"]["actual"]["value"] == 0
    assert inspections[2]["summary"]["actual"]["missing_dates"] == ["2026-10-29"]
    total = selected(projection)["summary"]["actual"]
    assert total["value"] == 0 and total["partial"]
    old = result("2026-10-29", (row("old", 6),))
    current = dataset((fields[-1],))
    retained = project(dataset_chart(), (old,), definition=current, first="2026-10-29")
    assert selected(retained)["summary"]["actual"]["value"] == 6


def test_latest_snapshot_uses_one_date_and_excludes_disappeared_current_members() -> None:
    """A snapshot uses current membership and never accumulates or borrows an older category."""
    fields = (DatasetField("n", "Balance", "USD", DatasetFieldKind.snapshot),)
    older = result("2026-10-28", (row("actual", 100), row("target", 50)), fields=fields)
    latest = replace(
        result("2026-10-29", (row("actual", 120),), fields=fields), query_end_at=float(NOW)
    )
    chart = dataset_chart(aggregation="latest", kind="allocation")
    view = selected(project(chart, (older, latest), definition=dataset(fields)))
    assert view["summary"]["date"] == "2026-10-29"
    assert view["summary"]["actual"]["value"] == 120
    assert not view["summary"]["actual"]["partial"]
    assert view["series"][1]["summary"] is None
    assert view["series"][1]["points"][0]["actual"]["value"] == 50
    assert view["series"][1]["points"][1]["actual"]["value"] is None
    empty = result("2026-10-30", fields=fields)
    empty_view = selected(
        project(chart, (older, latest, empty), definition=dataset(fields), last="2026-10-30")
    )
    assert empty_view["summary"]["date"] == "2026-10-30"
    assert empty_view["summary"]["actual"]["value"] is None
    unavailable = replace(empty, unavailable_reason="Provider unavailable")
    unavailable_view = selected(
        project(chart, (latest, unavailable), definition=dataset(fields), last="2026-10-30")
    )
    assert "Provider unavailable" in unavailable_view["summary"]["actual"]["reasons"][0]


def test_partial_additive_intervals_keep_subtotals_without_zero_for_omitted_members() -> None:
    """Limited subtotals compare against targets while omitted categories remain missing."""
    older = result("2026-10-28", (row("actual", 10), row("target", 20)))
    latest = replace(
        result("2026-10-29", (row("actual", 5),)),
        query_end_at=float(NOW),
        query_start_at=float(NOW - 6 * 3600),
        limitation="Sampled population",
    )
    chart: ChartBlock = {**dataset_chart(kind="line"), "target": 40.0}
    view = selected(project(chart, (older, latest)))
    assert view["summary"]["actual"]["value"] == 35
    assert view["summary"]["actual"]["partial"]
    assert view["summary"]["difference"] == -5
    assert view["summary"]["meets_expectation"] is False
    assert view["series"][1]["points"][1]["actual"]["value"] is None
    assert "Sampled population" in view["summary"]["actual"]["reasons"]
    complete = result("2026-10-29", latest.rows)
    complete_view = selected(project(chart, (older, complete)))
    assert complete_view["series"][1]["points"][1]["actual"]["value"] == 0
    assert not complete_view["summary"]["actual"]["partial"]
    assert complete_view["summary"]["meets_expectation"] is False


@pytest.mark.parametrize("numerator_kind", [DatasetFieldKind.count, DatasetFieldKind.snapshot])
def test_mixed_ratio_keeps_additive_coverage_without_changing_snapshot_emptiness(
    numerator_kind: DatasetFieldKind,
) -> None:
    """A ratio compares its value and retains a count numerator's partial coverage."""
    fields = (
        DatasetField("n", "Numerator", "", numerator_kind),
        DatasetField("d", "Denominator", "", DatasetFieldKind.snapshot),
    )
    observed = replace(
        result("2026-10-29", (row("actual", 5, 10),), fields=fields),
        query_end_at=float(NOW),
    )
    chart: ChartBlock = {
        **dataset_chart(
            aggregation="latest", kind="number", metric={"numerator": "n", "denominator": "d"}
        ),
        "target": 0.4,
    }
    summary = selected(project(chart, (observed,), first="2026-10-29"))["summary"]
    assert summary["actual"]["value"] == 0.5
    assert summary["difference"] == pytest.approx(0.1)
    if numerator_kind == DatasetFieldKind.count:
        assert summary["actual"]["partial"]
    else:
        assert not summary["actual"]["partial"]
    assert summary["meets_expectation"] is True
    empty = selected(project(chart, (replace(observed, rows=()),), first="2026-10-29"))
    assert empty["summary"]["actual"]["status"] == MeasureReadStatus.missing_input
    assert empty["summary"]["actual"]["value"] is None


def test_named_latest_keeps_unavailable_submission_and_dynamic_budget_comparison() -> None:
    """A new unavailable planned input defeats yesterday's complete shared snapshot."""
    values = (
        scalar("spent", "2026-10-28", 3),
        scalar("planned", "2026-10-28", 8),
        scalar("spent", "2026-10-29", 4),
        scalar("planned", "2026-10-29", None),
    )
    submitted = {key: frozenset(("2026-10-28", "2026-10-29")) for key in ("spent", "planned")}
    budget = selected(scalar_project(scalar_chart(kind="time-budget"), values, submitted))
    assert budget["summary"]["date"] == "2026-10-29"
    assert budget["summary"]["actual"]["value"] == 4
    comparison = budget["summary"]["comparison"]
    assert comparison is not None and comparison["value"] is None
    assert budget["summary"]["difference"] is None
    allocation = selected(scalar_project(scalar_chart(), values, submitted))
    assert allocation["summary"]["actual"]["value"] is None
    assert [item["key"] for item in allocation["series"]] == ['input:"actual"', 'input:"target"']
    assert allocation["series"][0]["summary"] is not None
    good = selected(
        scalar_project(
            scalar_chart(kind="time-budget"),
            values[:2],
            {key: frozenset(("2026-10-28",)) for key in submitted},
        )
    )
    assert good["last_date"] == "2026-10-29"
    assert good["summary"]["date"] == "2026-10-28"
    assert good["summary"]["difference"] == -5
    assert good["summary"]["meets_expectation"] is True


def test_named_additive_summary_supplies_each_allocation_quantity_and_partial_budget() -> None:
    """Interval summaries retain input totals and expose partial comparisons."""
    values = (
        scalar("spent", "2026-10-28", 2),
        scalar("planned", "2026-10-28", 8),
        scalar("spent", "2026-10-29", 3),
        scalar("planned", "2026-10-29", None),
    )
    submitted = {
        "spent": frozenset(("2026-10-28", "2026-10-29")),
        "planned": frozenset(("2026-10-28",)),
    }
    allocation = selected(scalar_project(scalar_chart(aggregation="sum"), values, submitted))
    assert allocation["summary"]["actual"]["value"] == 13
    assert allocation["summary"]["actual"]["partial"]
    assert [
        item["summary"]["value"] if item["summary"] is not None else None
        for item in allocation["series"]
    ] == [5, 8]
    assert allocation["summary"]["date"] is None
    budget = selected(
        scalar_project(scalar_chart(kind="time-budget", aggregation="sum"), values, submitted)
    )
    assert budget["summary"]["actual"]["value"] == 5
    assert budget["summary"]["difference"] == -3
    assert budget["summary"]["meets_expectation"] is True


def test_ratio_zero_denominator_and_arithmetic_limit_remain_explicit() -> None:
    """A category's zero denominator does not discard its paired operands from the headline."""
    chart = dataset_chart(metric={"numerator": "n", "denominator": "d", "scale": 100})
    view = selected(
        project(
            chart,
            (result("2026-10-29", (row("actual", 1, 0), row("target", 9, 10))),),
            first="2026-10-29",
        )
    )
    assert view["summary"]["actual"]["value"] == 100
    assert view["series"][0]["points"][0]["actual"]["status"] == MeasureReadStatus.division_by_zero
    zero = selected(
        project(chart, (result("2026-10-29", (row("actual", 5, 0),)),), first="2026-10-29")
    )["summary"]["actual"]
    assert zero["status"] == MeasureReadStatus.division_by_zero
    assert (zero["value"], zero["numerator"], zero["denominator"]) == (None, 5, 0)
    overflow = selected(
        project(
            dataset_chart(),
            (result("2026-10-29", (row("a", 1e308), row("b", 1e308))),),
            first="2026-10-29",
        )
    )["summary"]["actual"]
    assert overflow["status"] == MeasureReadStatus.arithmetic_limit
    assert overflow["value"] is None
    ratio_overflow = selected(
        project(
            chart,
            (result("2026-10-29", (row("a", 1e308), row("b", 1e308))),),
            first="2026-10-29",
        )
    )["summary"]["actual"]
    assert ratio_overflow["status"] == MeasureReadStatus.arithmetic_limit
    extreme_comparison = selected(
        project(
            {**dataset_chart(), "target": -1e308},
            (result("2026-10-29", (row("a", 1e308),)),),
            first="2026-10-29",
        )
    )["summary"]
    assert extreme_comparison["actual"]["value"] == 1e308
    assert extreme_comparison["difference"] is None
    assert extreme_comparison["meets_expectation"] is True


def test_bounds_clip_literal_calendars_and_refuse_unbounded_history() -> None:
    """Range labels never shift to the latest retained result or overflow calendar boundaries."""
    assert category_bounds("2026-10-29", "2026-10-01", None, None) == [
        ("week", "2026-10-23", "2026-10-29"),
        ("month", "2026-09-30", "2026-10-29"),
        ("all", "2026-10-01", "2026-10-29"),
    ]
    assert category_bounds("0001-01-01", None, "0001-01-01", "0001-01-02") == [
        (key, "0001-01-01", "0001-01-02") for key in ("week", "month", "all")
    ]
    with pytest.raises(PlannerError, match="narrower"):
        category_bounds("2026-10-29", "2000-01-01", None, None)
    with pytest.raises(PlannerError, match="reversed"):
        category_bounds("2026-10-29", None, "2026-10-30", None)


def test_read_limits_cannot_produce_apparently_complete_totals() -> None:
    """A truncated or shorter supplied read is rejected before any subtotal is presented."""
    reading = DatasetsRead("2026-10-28", "2026-10-29", (dataset(),), (), (), (), (), 0, 1, True)
    with pytest.raises(PlannerError, match="complete dataset read"):
        dataset_chart_projection(
            dataset_chart(), reading, now=NOW, earliest_date=None, first_date="2026-10-28"
        )
    with pytest.raises(PlannerError, match="complete projection interval"):
        dataset_chart_projection(
            dataset_chart(),
            replace(reading, truncated=False),
            now=NOW,
            earliest_date=None,
            first_date="2026-10-27",
        )


def test_captured_timezone_controls_dst_interval_coverage() -> None:
    """A 25-hour local day retains its captured boundaries after a timezone definition edit."""
    observed = result("2026-10-25", (row("actual", 10),))
    output = replace(observed.output, timezone="Europe/Berlin")
    full = replace(
        observed, output=output, query_end_at=datetime(2026, 10, 25, 23, tzinfo=UTC).timestamp()
    )
    complete = selected(project(dataset_chart(), (full,), first="2026-10-25", last="2026-10-25"))
    assert not complete["summary"]["actual"]["partial"]
    short = replace(full, query_end_at=full.query_end_at - 1800)
    partial = selected(project(dataset_chart(), (short,), first="2026-10-25", last="2026-10-25"))
    assert partial["summary"]["actual"]["value"] == 10
    assert partial["summary"]["actual"]["partial"]


def test_binding_validation_rejects_ambiguous_geometry_without_kind_algebra() -> None:
    """Shape/reference errors stay separate from descriptive field kinds and legitimate codes."""
    ratio = dataset_chart(metric={"numerator": "n", "denominator": "d"})
    invalid_charts: list[ChartBlock] = [{**ratio, "stacked": True}, {**ratio, "kind": "allocation"}]
    for chart in invalid_charts:
        with pytest.raises(ValueError, match="ratio metrics"):
            validate_category_chart(chart)
    with pytest.raises(ValueError, match="other live or authored"):
        validate_category_chart({**ratio, "measure_id": "legacy"})
    with pytest.raises(ValueError, match="unknown dataset field"):
        validate_category_references(
            dataset_chart(metric={"field": "absent"}), datasets={"dataset": dataset()}, measures={}
        )
    with pytest.raises(ValueError, match="one spent and one planned"):
        validate_category_chart({**scalar_chart(), "kind": "time-budget"})
    snapshot = dataset((DatasetField("n", "Snapshot", "", DatasetFieldKind.snapshot),))
    validate_category_references(dataset_chart(), datasets={"dataset": snapshot}, measures={})
    validate_category_references(
        dataset_chart(aggregation="latest"), datasets={"dataset": dataset()}, measures={}
    )
    validate_category_chart(
        dataset_chart(filters=[{"dimension": "surface", "codes": ["", "actual", "target"]}])
    )


@pytest.mark.parametrize("coverage", [ScalarCoverageStatus.partial, ScalarCoverageStatus.unknown])
def test_named_scalar_coverage_survives_points_comparisons_and_totals(
    coverage: ScalarCoverageStatus,
) -> None:
    """Named inputs must retain uncertainty in both comparison roles and additive headlines."""
    submitted = {key: frozenset(("2026-10-28", "2026-10-29")) for key in ("spent", "planned")}
    complete = (
        replace(scalar("spent", "2026-10-28", 2), coverage_status=ScalarCoverageStatus.complete),
        replace(scalar("planned", "2026-10-28", 8), coverage_status=ScalarCoverageStatus.complete),
        replace(scalar("spent", "2026-10-29", 3), coverage_status=ScalarCoverageStatus.complete),
        replace(scalar("planned", "2026-10-29", 8), coverage_status=ScalarCoverageStatus.complete),
    )
    for index in (2, 3):
        values = list(complete)
        values[index] = replace(
            values[index], coverage_status=coverage, coverage_reasons=("Limited input",)
        )
        for aggregation in ("latest", "sum"):
            chart = scalar_chart(kind="time-budget", aggregation=aggregation)
            view = selected(scalar_project(chart, tuple(values), submitted))
            summary = view["summary"]
            assert summary["actual"]["value"] == (3 if aggregation == "latest" else 5)
            assert summary["difference"] == (-5 if aggregation == "latest" else -11)
            assert summary["meets_expectation"] is True
            affected = summary["actual"] if index == 2 else summary["comparison"]
            assert affected is not None
            assert affected["coverage_status"] == coverage
            assert affected["partial"] == (coverage == ScalarCoverageStatus.partial)
            assert "Limited input" in affected["reasons"]
            assert view["point_summaries"][-1]["summary"]["meets_expectation"] is True
        allocation = selected(
            scalar_project(scalar_chart(aggregation="sum"), tuple(values), submitted)
        )
        assert allocation["summary"]["actual"]["coverage_status"] == coverage
        assert allocation["summary"]["actual"]["value"] == 21
    full = selected(scalar_project(scalar_chart(kind="time-budget"), complete, submitted))
    assert full["summary"]["meets_expectation"] is True
