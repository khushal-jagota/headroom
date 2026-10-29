"""SQLite proves replacement, retained identity, mixed rollback, authority, and read bounds."""

from dataclasses import replace
from sqlite3 import Connection, IntegrityError

import pytest
from tests.unit.test_manager_wakes import _item_and_ticket
from tests.unit.test_measure_persistence import NOW, run, source, taken

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, PlannerError, Principal, PrincipalKind
from planner.datasets import actions, data
from planner.datasets.contracts import (
    CandidateDatasetResult,
    CreateDatasetBody,
    Dataset,
    DatasetDimension,
    DatasetField,
    DatasetFieldKind,
    DatasetRow,
    ReadDatasetsBody,
    UpdateDatasetBody,
)
from planner.measure_sources import data as sources
from planner.measures import data as measures
from planner.measures.contracts import CandidateObservation


def dataset(conn: Connection, source_id: str, *, principal: Principal = CHIEF_PRINCIPAL) -> Dataset:
    return actions.create_dataset(
        conn,
        CreateDatasetBody(
            project_id="project_vylo",
            source_id=source_id,
            name="Joint records",
            timezone="UTC",
            dimensions=(DatasetDimension("region", "Region"), DatasetDimension("device", "Device")),
            fields=(DatasetField("events", "Events", "events", DatasetFieldKind.count),),
        ),
        principal=principal,
        now=NOW,
    )


def candidate(item: Dataset, day: str, *codes: str) -> CandidateDatasetResult:
    return CandidateDatasetResult(
        item.id,
        day,
        tuple(DatasetRow({"region": code, "device": "web"}, {"events": 2.0}) for code in codes),
        NOW,
    )


def collect(conn: Connection, item: Dataset, *values: CandidateDatasetResult) -> int:
    with measures.transaction(conn):
        return data.commit_results(
            conn,
            run_id="run",
            source_id=item.source_id,
            outputs=data.prepare_outputs(conn, item.source_id, frozen_at=NOW),
            candidates=values,
            collected_at=NOW,
        )


def request(item: Dataset, *, limit: int | None = None) -> ReadDatasetsBody:
    result = ReadDatasetsBody(
        dataset_ids=[item.id], first_date="2026-10-25", last_date="2026-10-28"
    )
    if limit is not None:
        result["limit"] = limit
    return result


def test_fewer_empty_replay_and_other_dates_survive(tmp_db: Connection) -> None:
    item = dataset(tmp_db, source(tmp_db))
    run(tmp_db, item.source_id)
    before_version = tmp_db.execute(
        "SELECT revision FROM resource_source_versions "
        "WHERE source_kind='automatic-measures' AND source_id='all'"
    ).fetchone()[0]
    assert (
        collect(
            tmp_db,
            item,
            candidate(item, "2026-10-25", "de", "us"),
            candidate(item, "2026-10-26", "fr"),
        )
        == 3
    )
    collect(tmp_db, item, candidate(item, "2026-10-25", "de"))
    replaced = data.read_values(tmp_db, request(item), now=NOW)
    assert [row.dimensions["region"] for row in replaced.results[0].rows] == ["de"]
    assert [row.dimensions["region"] for row in replaced.results[1].rows] == ["fr"]
    collect(tmp_db, item, candidate(item, "2026-10-25", "de"))
    assert collect(tmp_db, item, candidate(item, "2026-10-25")) == 0
    values = data.read_values(tmp_db, request(item), now=NOW)
    assert [(value.date, len(value.rows)) for value in values.results] == [
        ("2026-10-25", 0),
        ("2026-10-26", 1),
    ]
    assert [value.date for value in values.missing_dates] == ["2026-10-27", "2026-10-28"]
    assert values.results[0].unavailable_reason is None
    assert values.results[1].provenance.source_id == item.source_id
    assert values.results[1].provenance.script_sha256 == "synthetic"
    assert values.sources[0].source_id == item.source_id
    assert values.returned_rows == values.matching_rows == 1
    assert not values.truncated
    assert tmp_db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        tmp_db.execute(
            "SELECT revision FROM resource_source_versions "
            "WHERE source_kind='automatic-measures' AND source_id='all'"
        ).fetchone()[0]
        > before_version
    )


def test_labels_follow_codes_and_schema_edits_preserve_captured_maps(tmp_db: Connection) -> None:
    item = dataset(tmp_db, source(tmp_db))
    run(tmp_db, item.source_id)
    outputs = data.prepare_outputs(tmp_db, item.source_id, frozen_at=NOW)
    collect(
        tmp_db,
        item,
        replace(candidate(item, "2026-10-25", "de"), labels={"region": {"de": "Germany"}}),
        candidate(item, "2026-10-27"),
    )
    edited = actions.update_dataset(
        tmp_db,
        item.id,
        UpdateDatasetBody(
            expected_revision=1,
            dimensions=(DatasetDimension("new_region", "New region"),),
            fields=(DatasetField("new_events", "New events", "events", DatasetFieldKind.count),),
        ),
        principal=CHIEF_PRINCIPAL,
        now=NOW + 1,
    )
    # Admission captures the old schema. A later definition edit does not reject its output.
    with measures.transaction(tmp_db):
        data.commit_results(
            tmp_db,
            run_id="run",
            source_id=item.source_id,
            outputs=outputs,
            candidates=(
                replace(
                    candidate(item, "2026-10-26", "de"), labels={"region": {"de": "Deutschland"}}
                ),
                candidate(item, "2026-10-28"),
            ),
            collected_at=NOW + 2,
        )
    values = actions.read_values(tmp_db, request(item), now=NOW)
    assert values.datasets == (edited,)
    assert values.labels[0].code == "de" and values.labels[0].label == "Deutschland"
    assert all(
        result.rows[0].dimensions == {"region": "de", "device": "web"}
        for result in values.results[:2]
    )
    assert all(result.rows[0].values == {"events": 2} for result in values.results[:2])
    assert all(result.rows == () for result in values.results[2:])
    assert all(result.output == outputs[0] for result in values.results)
    assert [field.name for field in values.results[2].output.fields] == ["events"]
    assert [field.name for field in edited.fields] == ["new_events"]
    assert all(result.provenance.definition_revision == 1 for result in values.results)
    assert [
        row[0] for row in tmp_db.execute("SELECT DISTINCT dimension_key FROM dataset_rows")
    ] == ['[["device","web"],["region","de"]]']


def test_mixed_transaction_failure_retains_rows_labels_and_scalar(tmp_db: Connection) -> None:
    item = dataset(tmp_db, source(tmp_db))
    scalar = taken(tmp_db, item.source_id)
    run(tmp_db, item.source_id)
    original = replace(candidate(item, "2026-10-25", "de"), labels={"region": {"de": "Germany"}})
    collect(tmp_db, item, original)
    outputs = measures.prepare_outputs(tmp_db, item.source_id, frozen_at=NOW)
    with pytest.raises(PlannerError):
        with measures.transaction(tmp_db):
            measures.commit_observations(
                tmp_db,
                run_id="run",
                source_id=item.source_id,
                frozen_at=NOW,
                outputs=outputs,
                candidates=(CandidateObservation(scalar.id, "2026-10-25", 9),),
                history=None,
            )
            collect(
                tmp_db,
                item,
                replace(original, labels={"region": {"de": "Renamed"}}),
                candidate(item, "2026-10-25", "us"),
            )
    assert measures.read_observations(tmp_db, scalar.id, "2026-10-25", "2026-10-25") == ()
    values = data.read_values(tmp_db, request(item), now=NOW)
    assert values.results[0].rows == original.rows
    assert values.labels[0].label == "Germany"
    # A SQL failure follows a successful date replacement and label change.
    # The caller must roll back all mutations, including an earlier scalar write.
    tmp_db.execute(
        "CREATE TEMP TRIGGER fail_dataset_row BEFORE INSERT ON dataset_rows "
        "WHEN NEW.date='2026-10-26' BEGIN SELECT RAISE(ABORT,'injected row failure'); END"
    )
    with pytest.raises(IntegrityError, match="injected row failure"):
        with measures.transaction(tmp_db):
            measures.commit_observations(
                tmp_db,
                run_id="run",
                source_id=item.source_id,
                frozen_at=NOW,
                outputs=outputs,
                candidates=(CandidateObservation(scalar.id, "2026-10-25", 9),),
                history=None,
            )
            data.commit_results(
                tmp_db,
                run_id="run",
                source_id=item.source_id,
                outputs=data.prepare_outputs(tmp_db, item.source_id, frozen_at=NOW),
                candidates=(
                    replace(
                        candidate(item, "2026-10-25", "us"), labels={"region": {"de": "Renamed"}}
                    ),
                    candidate(item, "2026-10-26", "fr"),
                ),
                collected_at=NOW + 1,
            )
    assert data.read_values(tmp_db, request(item), now=NOW) == values
    assert measures.read_observations(tmp_db, scalar.id, "2026-10-25", "2026-10-25") == ()
    tmp_db.execute("DROP TRIGGER fail_dataset_row")


def test_read_limit_counts_all_rows_and_preserves_empty_headers(tmp_db: Connection) -> None:
    item = dataset(tmp_db, source(tmp_db))
    run(tmp_db, item.source_id)
    collect(
        tmp_db,
        item,
        candidate(item, "2026-10-25", "a", "b", "c"),
        replace(candidate(item, "2026-10-26"), unavailable_reason="provider_has_no_history"),
        replace(candidate(item, "2026-10-27", "d"), limitation="Provider sample"),
    )
    values = data.read_values(tmp_db, request(item, limit=2), now=NOW)
    assert values.returned_rows == 2 and values.matching_rows == 4 and values.truncated
    assert [(result.date, len(result.rows)) for result in values.results] == [
        ("2026-10-25", 2),
        ("2026-10-26", 0),
        ("2026-10-27", 0),
    ]
    assert values.results[1].unavailable_reason == "provider_has_no_history"
    assert values.results[2].limitation == "Provider sample"
    assert [missing.date for missing in values.missing_dates] == ["2026-10-28"]
    assert data.read_values(tmp_db, request(item), now=NOW).returned_rows == 4
    page = data.list_datasets(
        tmp_db, project_id="project_vylo", source_id=item.source_id, limit=1, offset=0
    )
    assert page.rows == (item,)


def test_source_authority_project_agreement_and_revisions(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    manager, _ = _item_and_ticket(tmp_db, fake_clock)
    other_manager, other_ticket = _item_and_ticket(tmp_db, fake_clock)
    responsible = Principal(PrincipalKind.manager, manager)
    source_id = source(tmp_db, responsible=responsible)
    before = sources.read_source(tmp_db, source_id).source_revision
    item = dataset(tmp_db, source_id, principal=responsible)
    assert sources.read_source(tmp_db, source_id).source_revision == before + 1
    for actor in (
        Principal(PrincipalKind.manager, other_manager),
        Principal(PrincipalKind.ticket, other_ticket),
    ):
        with pytest.raises(PlannerError):
            dataset(tmp_db, source_id, principal=actor)
        with pytest.raises(PlannerError):
            actions.update_dataset(
                tmp_db,
                item.id,
                UpdateDatasetBody(expected_revision=1, name="No"),
                principal=actor,
                now=NOW,
            )
    with pytest.raises(PlannerError, match="Projects differ"):
        actions.create_dataset(
            tmp_db,
            CreateDatasetBody(
                project_id="project_other",
                source_id=source_id,
                name=item.name,
                timezone=item.timezone,
                dimensions=item.dimensions,
                fields=item.fields,
            ),
            principal=responsible,
            now=NOW,
        )
    actions.update_dataset(
        tmp_db,
        item.id,
        UpdateDatasetBody(expected_revision=1, name="Edited"),
        principal=responsible,
        now=NOW,
    )
    with pytest.raises(PlannerError, match="changed"):
        actions.update_dataset(
            tmp_db,
            item.id,
            UpdateDatasetBody(expected_revision=1, name="Stale"),
            principal=responsible,
            now=NOW,
        )
    assert sources.read_source(tmp_db, source_id).source_revision == before + 2
    foreign_source = source(tmp_db)
    with pytest.raises(PlannerError, match="foreign"):
        data.validate_batch(
            tmp_db,
            source_id=foreign_source,
            outputs=data.prepare_outputs(tmp_db, source_id, frozen_at=NOW),
            candidates=(),
        )
