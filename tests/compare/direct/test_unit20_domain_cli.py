"""Retained dataset rows cross the real CLI and HTTP boundary in both output modes.

The route-list seed cannot publish dataset results. Reuse the existing canonical
source/dataset setup and lifecycle writers before the fixture-owned host starts.
No provider, script runtime, additional host type or raw seed SQL enters this proof.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.compare.direct import unit20_cli as driver
from tests.compare.direct.unit20_cli import (
    PYTHON,
    ROOT,
    TYPESCRIPT,
    CliHost,
    comparable,
    environment,
    run_cli,
)
from tests.compare.direct.unit20_wire import _body, compare_wire, wire_view
from tests.compare.runner import Normalizer, backup_database, substitute
from tests.support.measure_sources import NOW, configured_source
from tests.support.server import ServerHandle
from tests.support.server_fixtures import _start_server, _stop_server
from tests.unit.test_dataset_persistence import dataset

from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.datasets import actions as dataset_actions
from planner.datasets.contracts import (
    CandidateDatasetResult,
    DatasetField,
    DatasetFieldKind,
    DatasetRow,
)
from planner.measure_sources import run_actions
from planner.measure_sources.contracts import RunMetrics, RunMode, RunResult, RunStatus
from planner.tickets import data as tickets_data
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.configuration import load_worker_runtime_definitions
from planner.worker_types.contracts import FieldDefinition, StageDefinition
from planner.worker_types.store import read_definition, write_definition

LIST = ROOT / "tests/compare/lists/unit-20-commands.domain.json"


def test_unit20_retained_dataset_cli(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    captures: dict[str, Any] = {}
    seed_seconds: dict[str, float] = {}

    def seed(target: Path) -> None:
        # _start_server scopes synthetic provider homes around this callback and
        # creates the schema before calling it. No server process owns this DB yet.
        with closing(connect(str(target))) as conn:
            source, _ = configured_source(conn, enabled=False)
            item = dataset(conn, source.id)
            item = dataset_actions.update_dataset(
                conn,
                item.id,
                {
                    "expected_revision": item.definition_revision,
                    "fields": tuple(
                        DatasetField(year, year, "events", DatasetFieldKind.count)
                        for year in ("2025", "2024")
                    ),
                },
                principal=OWNER_PRINCIPAL,
                now=NOW,
            )
            assert source.active_script is not None
            admitted = run_actions.request_run(
                conn,
                source.id,
                mode=RunMode.collection,
                history=None,
                expected_script_revision=source.active_script.revision,
                principal=OWNER_PRINCIPAL,
                now=NOW,
            )
            claimed = run_actions.claim_run(conn, admitted.id, now=NOW)
            assert claimed is not None
            candidate = CandidateDatasetResult(
                item.id,
                "2026-10-26",
                (DatasetRow({"region": "de", "device": "web"}, {"2025": 7.25, "2024": 3.0}),),
                float(NOW),
                query_start_at=float(NOW - 86400),
                labels={"region": {"de": "Deutschland"}},
            )
            completed = run_actions.complete_run(
                conn,
                admitted.id,
                RunResult((), None, RunMetrics(0, 0, 0, 1, 0), (candidate,)),
                now=NOW,
            )
            assert completed.run.status is RunStatus.succeeded
            captures["retained_dataset"] = item.id
            fields = ("2025", "2024", "consequences")
            definition = replace(
                read_definition(conn, "coding"),
                worker_type="unit20_year_fields",
                fields=tuple(FieldDefinition(field, field) for field in fields),
                stages=tuple(
                    StageDefinition("needs_" + field, field, field, False, StageOwnershipMode.user)
                    for field in fields
                )
                + (StageDefinition("done", "Done", None, True, None),),
            )
            write_definition(conn, definition, now=NOW)
            ticket = tickets_data.create_ticket(
                conn,
                title="Year field search",
                principal=OWNER_PRINCIPAL,
                now=NOW,
                title_max_chars=200,
                brief=None,
                project_id="project_vylo",
                worker_type=definition.worker_type,
                worker_runtime_definitions=load_worker_runtime_definitions(conn),
            )
            for field, text in (
                ("2025", "unit20-left-2025"),
                ("2024", "unit20-right-2024"),
            ):
                ticket = tickets_data.complete_user_owned_gate(
                    conn,
                    ticket.id,
                    field=field,
                    new_body=text,
                    principal=OWNER_PRINCIPAL,
                    now=NOW,
                )
            proposal_started = time.perf_counter()
            ticket = tickets_data.file_current_proposal(
                conn,
                ticket.id,
                body="Retained consequences for the receipt proof",
                principal=Principal(PrincipalKind.ticket, ticket.id),
                now=NOW,
            )
            captures.update(
                numeric_ticket=ticket.id, decision_revision=ticket.pending_proposal_revision
            )
            seed_seconds["python_proposal"] = time.perf_counter() - proposal_started
            active_seed_started = time.perf_counter()
            # Earlier excluded rows make a limit=1 request prove filtering precedes paging.
            for deferred in (True, False):
                excluded = tickets_data.create_ticket(
                    conn, title="Year field search", principal=OWNER_PRINCIPAL,
                    now=NOW - (2 if deferred else 1), title_max_chars=200, brief=None,
                    project_id="project_vylo", worker_type=definition.worker_type,
                    worker_runtime_definitions=load_worker_runtime_definitions(conn),
                )
                if deferred:
                    tickets_data.set_ticket_backlog(
                        conn, excluded.id, is_backlogged=True, principal=OWNER_PRINCIPAL, now=NOW,
                    )
                else:
                    for field in fields:
                        excluded = tickets_data.complete_user_owned_gate(
                            conn, excluded.id, field=field, new_body="Complete",
                            principal=OWNER_PRINCIPAL, now=NOW,
                        )
            seed_seconds["active_list"] = time.perf_counter() - active_seed_started

    spec = json.loads(LIST.read_text())
    steps = [step for step in spec["direct_requests"] if step["argv"] == ["dataset", "read"]]
    assert len(steps) == 1, "the command list owns exactly one retained dataset read case"
    handles: list[ServerHandle] = []
    evidence: dict[str, Any] = {"list": LIST.name, "comparisons": {}}
    try:
        reference = _start_server(
            tmp_path / "python",
            fake_now="2026-10-27T12:00:00",
            server_module="tests.compare.host",
            seed_db=seed,
        )
        handles.append(reference)
        step = substitute(steps[0], captures)
        # SQLite backup is read-only on the running source and restores only into
        # the candidate's not-yet-running database, as the existing runner does.
        snapshot = tmp_path / "seed.db"
        backup_database(reference.db_path, snapshot)

        def seed_candidate(target: Path) -> None:
            copy_started = time.perf_counter()
            backup_database(snapshot, target)
            seed_seconds["typescript_snapshot"] = time.perf_counter() - copy_started

        candidate_host = _start_server(
            tmp_path / "typescript",
            fake_now="2026-10-27T12:00:00",
            server_module="tests.compare.ts_launcher",
            seed_db=seed_candidate,
        )
        handles.append(candidate_host)
        # Python's action delegates to the dataset reader. TS uses database.read.
        # The POST read changes no data, so both clients reuse each existing host.
        for mode in ("json", "text"):
            python_cli = run_cli(
                CliHost(reference, tmp_path),
                step,
                False,
                tmp_path / "python-input",
                mode,
            )
            ts_cli_reference = run_cli(
                CliHost(reference, tmp_path),
                step,
                True,
                tmp_path / "typescript-input",
                mode,
            )
            ts_cli_candidate = run_cli(
                CliHost(candidate_host, tmp_path),
                step,
                True,
                tmp_path / "candidate-input",
                mode,
            )
            evidence["comparisons"][mode] = {
                "python_cli_python_host": python_cli,
                "typescript_cli_python_host": ts_cli_reference,
                "typescript_cli_typescript_host": ts_cli_candidate,
            }
            wire_rules = json.loads((LIST.parent.parent / "rules" / LIST.name).read_text())
            outcomes = []
            for index, answer in enumerate((python_cli, ts_cli_reference, ts_cli_candidate)):
                outcome = comparable(answer)
                document = json.loads(answer["stdout"])
                assert [field["name"] for field in document["datasets"][0]["fields"]] == [
                    "2025",
                    "2024",
                ]
                assert len(document["results"]) == 1
                result = document["results"][0]
                assert len(result["rows"]) == 1
                assert [field["name"] for field in result["output"]["fields"]] == [
                    "2025",
                    "2024",
                ]
                assert result["rows"][0]["values"] == {"2025": 7.25, "2024": 3.0}
                assert list(result["rows"][0]["values"]) == (
                    ["2025", "2024"] if index == 0 else ["2024", "2025"]
                )
                outcomes.append(
                    {
                        **outcome,
                        "stdout": _body(
                            outcome["stdout"],
                            Normalizer(wire_rules, tmp_path),
                            "dataset",
                            None,
                            year_fields=True,
                        ),
                    }
                )
            assert outcomes[0] == outcomes[1] == outcomes[2], evidence
            for other, handle in (
                (ts_cli_reference, reference),
                (ts_cli_candidate, candidate_host),
            ):
                compare_wire(
                    python_cli,
                    other,
                    Normalizer(wire_rules, reference.db_path.parent),
                    Normalizer(wire_rules, handle.db_path.parent),
                    year_fields=True,
                )
        # One existing-host physical crosscheck isolates interception from CLI behavior.
        env = environment(CliHost(reference, tmp_path), step["identity"], tmp_path / "python-input")
        transparency_started = time.perf_counter()
        CliHost(reference, tmp_path).check()
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
            oracle = client.post(
                reference.base + "/api/datasets/read", json=json.loads(step["stdin"])
            )
        assert oracle.status_code == 200
        evidence["wire_oracle"] = {
            "status": oracle.status_code,
            "body_base64": base64.b64encode(oracle.content).decode(),
        }
        evidence["wire_transparency"] = []
        for command, observed in ((PYTHON, python_cli), (TYPESCRIPT, ts_cli_reference)):
            CliHost(reference, tmp_path).check()
            plain = driver.raw_process(
                [*command, *step["argv"]], env, input=step.get("stdin", "").encode()
            )
            actual = driver.process_answer(plain)
            assert comparable(actual) == comparable(observed)
            assert len(observed["wire"]) == 1
            assert base64.b64decode(observed["wire"][0]["body_base64"]) == oracle.content
            evidence["wire_transparency"].append(actual)
        evidence["wire_transparency_seconds"] = time.perf_counter() - transparency_started
        ticket_started = time.perf_counter()
        evidence["numeric_ticket_human"] = []
        for parts in ("search", None, "2025,2024", "2024,2025"):
            searching = parts == "search"
            argv = (
                ["ticket", "list", "--search", "unit20-left-2025\nunit20-right-2024"]
                if searching
                else [
                    "ticket",
                    "show",
                    captures["numeric_ticket"],
                    *([parts] if parts else []),
                ]
            )
            order = parts.split(",") if parts and not searching else ["2025", "2024"]
            step = {
                "kind": "cli",
                "argv": argv,
                "identity": {"actor": "owner"},
                "exit_code": 0,
                "assert_http_status": 200,
                "proof": (
                    "Search joins numeric field bodies in workflow order."
                    if searching
                    else "Human Ticket order follows the manifest or explicit requested parts."
                ),
            }
            answers = [
                run_cli(
                    CliHost(handle, tmp_path),
                    step,
                    candidate_cli,
                    tmp_path / "numeric-ticket-input",
                    "json" if searching else "text",
                )
                for handle, candidate_cli in (
                    (reference, False),
                    (reference, True),
                    (candidate_host, True),
                )
            ]
            for answer in answers:
                if searching:
                    body = json.loads(answer["stdout"])
                    assert [ticket["id"] for ticket in body["tickets"]] == [
                        captures["numeric_ticket"]
                    ]
                    assert body["page"]["match_count"] == 1
                else:
                    assert answer["stdout"].index("\n  " + order[0] + ":") < answer["stdout"].index(
                        "\n  " + order[1] + ":"
                    )
            assert comparable(answers[0]) == comparable(answers[1]) == comparable(answers[2])
            observed = {
                "argv": argv,
                "proof": step["proof"],
                "order": order,
                "answers": answers,
            }
            if searching:
                evidence["declared_field_search"] = observed
            else:
                evidence["numeric_ticket_human"].append(observed)
        evidence["numeric_ticket_proof_seconds"] = time.perf_counter() - ticket_started
        evidence["active_list"] = _active_list_proof(
            reference, candidate_host, tmp_path, captures["numeric_ticket"]
        )
        evidence["proposal_revision_cli"] = _proposal_revision_cli_proof(
            reference, candidate_host, tmp_path, captures
        )
        evidence["deferral"] = {
            name: _deferral_proof(handle, tmp_path, captures["numeric_ticket"])
            for name, handle in (("python", reference), ("typescript", candidate_host))
        }
        deferred_python, deferred_typescript = evidence["deferral"].values()
        assert deferred_python["responses"] == deferred_typescript["responses"]
        assert deferred_python["snapshots"] == deferred_typescript["snapshots"]
        evidence["decision_seed_seconds"] = seed_seconds
        evidence["decision_operations"] = {
            name: _decision_operation_proof(handle, tmp_path, captures)
            for name, handle in (("python", reference), ("typescript", candidate_host))
        }
        left, right = evidence["decision_operations"].values()
        assert left["envelopes"] == right["envelopes"]
        assert left["rows"] == right["rows"]
        evidence["runtime_guidance"] = _runtime_proof(reference, candidate_host, tmp_path)
    finally:
        for handle in reversed(handles):
            _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        driver.write_report(request, "unit20-retained-dataset-cli", evidence)


def _active_list_proof(
    reference: ServerHandle, candidate: ServerHandle, root: Path, ticket_id: str
) -> dict[str, Any]:
    """Reuse populated hosts to prove active filtering before pagination and CLI transport."""
    started = time.perf_counter()
    step = {
        "kind": "cli", "argv": [
            "ticket", "list", "--active", "--search", "Year field search", "--limit", "1",
        ],
        "identity": {"actor": "owner"}, "exit_code": 0, "assert_http_status": 200,
        "proof": "Active filtering excludes earlier deferred and terminal rows before limit=1.",
    }
    answers = [
        run_cli(CliHost(handle, root), step, native, root / "active-list-input", "json")
        for handle, native in ((reference, False), (reference, True), (candidate, True))
    ]
    assert comparable(answers[0]) == comparable(answers[1]) == comparable(answers[2])
    for answer in answers:
        body = json.loads(answer["stdout"])
        assert [row["id"] for row in body["tickets"]] == [ticket_id]
        assert body["page"]["match_count"] == 1
    envelopes = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
        for params, status in (
            ({"active": "false", "search": "Year field search"}, 200),
            ({"active": "true", "is_backlogged": "true"}, 422),
            ({"active": "true", "include_terminal": "true"}, 422),
            ({"active": "true", "detail": "full"}, 422),
            ({"active": "true", "detail": "full", "id": ticket_id}, 422),
            ({"active": "invalid"}, 422),
        ):
            pair = [client.get(handle.base + "/api/tickets", params=params)
                    for handle in (reference, candidate)]
            assert all(answer.status_code == status for answer in pair)
            bodies = [answer.json() for answer in pair]
            assert bodies[0] == bodies[1]
            if status == 200:
                assert len(bodies[0]["tickets"]) == bodies[0]["page"]["match_count"] == 2
                assert any(row["is_backlogged"] for row in bodies[0]["tickets"])
            envelopes.append({"query": params, "status": status, "body": bodies[0]})
    return {"answers": answers, "envelopes": envelopes, "seconds": time.perf_counter() - started}


def _proposal_revision_cli_proof(
    reference: ServerHandle, candidate: ServerHandle, root: Path, captures: dict[str, Any]
) -> dict[str, Any]:
    """Explicit stale revisions must never approve or revise the current proposal."""
    started = time.perf_counter()
    cases = []
    for action, methods in (("accept", ["GET"]), ("revise", ["POST"]), ("submit", [])):
        argv = [
            "ticket", "proposal", captures["numeric_ticket"], action,
            "--expected-proposal-revision", str(captures["decision_revision"] + 1),
        ]
        if action == "accept":
            argv += ["--ceiling", "needs_consequences", "--kickoff-title", "Must not change"]
        step = {
            "kind": "cli", "argv": argv, "identity": {"actor": "owner"},
            "stdin": "Keep the current proposal", "exit_code": 1,
            "proof": "Stale revision refuses mutation and submit rejects the revision option.",
        }
        answers = [
            run_cli(CliHost(handle, root), step, native, root / "proposal-revision-input", "json")
            for handle, native in ((reference, False), (reference, True), (candidate, True))
        ]
        assert comparable(answers[0]) == comparable(answers[1]) == comparable(answers[2])
        for answer in answers:
            assert [row["method"] for row in answer["wire"]] == methods
        cases.append({"argv": argv, "answers": answers})
    return {"cases": cases, "seconds": time.perf_counter() - started}


def _decision_operation_proof(
    handle: ServerHandle, root: Path, captures: dict[str, Any]
) -> dict[str, Any]:
    """Reuse this host and Ticket to compare actual receipts, privacy, and replay writes."""
    started = time.perf_counter()
    CliHost(handle, root).check()
    ticket_id = captures["numeric_ticket"]
    path = handle.base + f"/api/tickets/{ticket_id}/decision-operations/unit20-send-back"
    command = {
        "decision": "send_back",
        "field": "consequences",
        "expected_proposal_revision": captures["decision_revision"],
    }

    def retained_rows() -> dict[str, Any]:
        return _ticket_source_rows(handle, ticket_id, (
            "tickets", "ticket_decision_messages", "ticket_decision_operations",
        ))

    with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
        applied = client.post(path, json=command)
        assert applied.status_code == 200, applied.text
        envelope = applied.json()
        assert envelope["receipt_contract"] == 1 and envelope["receipt"]["outcome"] == "applied"
        saved = retained_rows()
        assert saved["tickets"][0]["pending_proposal"] is None
        assert len(saved["ticket_decision_messages"]) == 1
        assert len(saved["ticket_decision_operations"]) == 1
        replies = [
            client.get(path), client.post(path, json=command),
            client.get(path, headers={"X-Plan-Actor": "chief"}),
        ]
        assert all(reply.status_code == 200 for reply in replies), [r.text for r in replies]
        envelopes = [envelope, *(reply.json() for reply in replies)]
        assert envelopes[:3] == [envelope] * 3
        assert envelopes[3] == {"receipt_contract": 1, "receipt": None}
        assert retained_rows() == saved
    return {"envelopes": envelopes, "rows": saved, "seconds": time.perf_counter() - started}


def _ticket_source_rows(
    handle: ServerHandle, ticket_id: str, tables: tuple[str, ...]
) -> dict[str, Any]:
    with closing(sqlite3.connect(f"file:{handle.db_path}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        stored = {}
        for table in tables:
            column = "subject_id" if table.startswith("notification_") else (
                "id" if table == "tickets" else "ticket_id"
            )
            order = "notification_type,generation" if table.startswith("notification_") else (
                "operation_id" if table == "ticket_decision_operations" else
                "decision_id" if table == "ticket_decision_messages" else "id"
            )
            subject = " AND subject_kind='ticket'" if table.startswith("notification_") else ""
            stored[table] = [dict(row) for row in conn.execute(
                f"SELECT * FROM {table} WHERE {column}=?{subject} ORDER BY {order}", (ticket_id,),
            )]
    # Keep command/request identity bytes exact; decode ordinary document containers only.
    containers = {
        "field_values", "field_approvals", "pending_proposal", "ceiling_holder",
        "workflow_json", "receipt_json", "context_json", "destination_json",
    }
    for values in stored.values():
        for row in values:
            for key in row.keys() & containers:
                if row[key] is not None:
                    row[key] = json.loads(row[key])
    return stored



def _deferral_proof(handle: ServerHandle, root: Path, ticket_id: str) -> dict[str, Any]:
    """Defer/resume the existing proposal on both actual hosts before its receipt proof."""
    started = time.perf_counter()
    CliHost(handle, root).check()
    tables = (
        "tickets", "notification_attention_state", "notification_attention_edges",
        "attention_gate_intervals",
    )
    snapshots = [_ticket_source_rows(handle, ticket_id, tables)]
    responses = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
        for action in ("backlog", "resume"):
            answer = client.post(handle.base + f"/api/tickets/{ticket_id}/{action}", json={})
            assert answer.status_code == 200, answer.text
            responses.append(answer.json())
            snapshots.append(_ticket_source_rows(handle, ticket_id, tables))
    before, paused, resumed = snapshots
    original = before["tickets"][0]
    for index, snapshot in enumerate(snapshots):
        ticket = snapshot["tickets"][0]
        assert bool(ticket["is_backlogged"]) is (index == 1)
        for key in (
            "pending_proposal", "pending_proposal_revision", "field_values",
            "field_approvals", "stage", "ceiling", "ceiling_holder",
        ):
            assert ticket[key] == original[key]
    for kind in ("assigned", "awaiting_approval"):
        states = [
            next(row for row in state["notification_attention_state"]
                 if row["notification_type"] == kind)
            for state in snapshots
        ]
        assert [state["active"] for state in states] == [1, 0, 1]
        generation = states[0]["generation"]
        assert [state["generation"] for state in states] == [generation, generation, generation + 1]
    assert paused["notification_attention_edges"] == before["notification_attention_edges"]
    assert len(resumed["notification_attention_edges"]) == (
        len(before["notification_attention_edges"]) + 2
    )
    for row in before["notification_attention_edges"]:
        assert row in resumed["notification_attention_edges"]
    assert len(paused["attention_gate_intervals"]) == len(before["attention_gate_intervals"])
    assert paused["attention_gate_intervals"][-1]["closed_at"] is not None
    assert len(resumed["attention_gate_intervals"]) == len(before["attention_gate_intervals"]) + 1
    assert resumed["attention_gate_intervals"][-1]["closed_at"] is None
    return {
        "responses": responses, "snapshots": snapshots,
        "seconds": time.perf_counter() - started,
    }


def _runtime_proof(
    reference: ServerHandle,
    candidate: ServerHandle,
    root: Path,
) -> dict[str, Any]:
    """Fresh shared exports pin every byte and each accepted schema language path."""
    from tests.compare.direct import unit20_domain_fixtures as fixtures
    from tests.compare.direct.unit20_metadata import schema_declarations

    from planner.measure_sources.guidance import RUN, SCRIPT, runtime_guide

    started = time.perf_counter()
    expected_python = {
        "runtime": runtime_guide(),
        "schema-script": SCRIPT.reference(),
        "schema-run": RUN.reference(),
    }
    exported = fixtures.node_export(
        CliHost(reference, root),
        root / "runtime-export",
        """
import {runtime_guide,SCRIPT,RUN} from './server/measure_sources/guidance.ts';
import goal_guidance from './server/goals/cli/guidance.json' with {type:'json'};
import page_guidance from './server/managers/page_cli/guidance.json' with {type:'json'};
import {CREATE as MEASURE} from './server/measures/cli/schema.ts';
import {prepare_document} from './server/goals/logic/documents.ts';
import {prepare_plain_document} from './server/managers/logic/documents.ts';
import {validate_text} from './server/measures/actions.ts';
import {validate_definition} from './server/measures/logic/formulas.ts';
import {stringify_python_json as json} from './server/core/http/numeric_json.ts';
const declarations={runtime:runtime_guide(),'schema-script':SCRIPT,'schema-run':RUN};
prepare_document(goal_guidance.create.examples.checklist_goal.page,()=> 'block_example');
prepare_plain_document(page_guidance.examples.checklist_and_workers.page,()=> 'block_example');
const measure={...MEASURE.examples.taken_daily_count,id:'measure_example',formula:null,
  definition_revision:1,created_at:0,updated_at:0,write_revision:0};
validate_text(measure);validate_definition(measure,new Map());
process.stdout.write(JSON.stringify({...Object.fromEntries(Object.entries(declarations).map(
  ([name,data])=>[name,{data,json:json(data)+'\\n',
    text:json(data,{ensure_ascii:false,indent:2})+'\\n'}])),
  schemas:Object.fromEntries(Object.entries({goal:goal_guidance,page:page_guidance,measure:MEASURE})
    .map(([name,data])=>[name,json(data)+'\\n'])),
  validated_examples:['goal.checklist_goal.page','page.checklist_and_workers','measure.taken_daily_count']}));
""",
    )
    steps = fixtures.direct_steps("measure-source")
    fresh_schemas = schema_declarations()
    for name, value in fresh_schemas.items():
        observed = json.loads(exported["schemas"][name])
        assert observed == value, name
    proof: dict[str, Any] = {
        "changed_paths": {},
        "outputs": {},
        "schema_drift": {
            "fresh_python": fresh_schemas,
            "typescript_json": exported["schemas"],
            "canonical_validated_examples": exported["validated_examples"],
            "boundary": (
                "Pure canonical goal-page, holder-page and measure-definition validators. "
                "Referenced record existence remains the API writer's responsibility."
            ),
        },
    }
    for name, expected_data in expected_python.items():
        changed = fixtures.differences(
            expected_data, exported[name]["data"], indexed_arrays=name != "runtime"
        )
        if name == "runtime":
            allowed = set(fixtures.rule("runtime_guidance")["paths"])
            step = next(item for item in steps if item["argv"] == ["measure-source", "runtime"])
        else:
            changed = {name + "." + path for path in changed}
            allowed = set(fixtures.rule("source_schemas")["paths"])
            step = next(
                item
                for item in steps
                if item["argv"] == ["measure-source", "schema", name.removeprefix("schema-")]
            )
        assert changed <= allowed, (name, changed - allowed)
        proof["changed_paths"][name] = sorted(changed)
        proof["outputs"][name] = {}
        for mode in ("json", "text"):
            expected = (
                json.dumps(
                    expected_data,
                    ensure_ascii=mode == "json",
                    indent=None if mode == "json" else 2,
                )
                + "\n"
            )
            left = run_cli(CliHost(reference, root), step, False, root / "runtime-py", mode)
            right = run_cli(CliHost(reference, root), step, True, root / "runtime-ts", mode)
            hosted = run_cli(CliHost(candidate, root), step, True, root / "runtime-hosted", mode)
            assert json.loads(left["stdout"]) == json.loads(expected) and left["stderr"] == ""
            assert (
                json.loads(right["stdout"]) == json.loads(exported[name][mode])
                and right["stderr"] == ""
            )
            assert comparable(right) == comparable(hosted)
            proof["outputs"][name][mode] = [left, right, hosted]
    build_environment = driver.environment

    def executor_environment(*args: Any, **kwargs: Any) -> dict[str, str]:
        return {
            **build_environment(*args, **kwargs),
            "PLAN_EXECUTOR_ROOT": str(root / "synthetic-executor"),
        }

    with patch.object(driver, "environment", executor_environment):
        proof["executor_callback_precedence"] = driver.help_batch(
            CliHost(reference, root),
            [
                (["preview", "register", "--port", "80"], 1),
                (["preview", "show", "--help"], 1),
            ],
            root / "executor-help",
        )
    proof["seconds"] = time.perf_counter() - started
    return proof


def test_unit20_attention_setup_cli(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One isolated setup per language pins exact factory text and all other source rows."""
    import shutil

    from tests.compare.direct import unit20_domain_fixtures as fixtures

    from planner.attention.collection import _script

    started = time.perf_counter()
    step = fixtures.direct_steps("attention")[0]
    handles: list[ServerHandle] = []
    evidence: dict[str, Any] = {
        "list": LIST.name,
        "implementations": [],
        "source_row_scope": (
            "Inherits runner.SHARED_DERIVED_TABLES navigation_view_ordered domain exclusion; "
            "strict Unit15 publication/route/notice proofs retain their independent boundary."
        ),
    }
    baseline = tmp_path / "baseline.db"
    try:
        reference = _start_server(tmp_path / "python", server_module="tests.compare.host")
        handles.append(reference)
        backup_database(reference.db_path, baseline)
        states = []
        outputs = []
        for implementation, module, candidate_cli in (
            ("python_cli_python_host", "tests.compare.host", False),
            ("typescript_cli_python_host", "tests.compare.host", True),
            ("typescript_cli_typescript_host", "tests.compare.ts_launcher", True),
        ):
            if implementation != "python_cli_python_host":
                _stop_server(handles.pop())
                shutil.rmtree(reference.db_path.parent)
                reference = _start_server(
                    tmp_path / "python",
                    server_module=module,
                    seed_db=lambda target: backup_database(baseline, target),
                )
                handles.append(reference)
            resolved = substitute(step, {"server_database": str(reference.db_path)})
            answer = run_cli(
                CliHost(reference, tmp_path),
                resolved,
                candidate_cli,
                tmp_path / "attention-input",
                "json",
            )
            returned = json.loads(answer["stdout"])
            human = run_cli(
                CliHost(reference, tmp_path),
                resolved,
                candidate_cli,
                tmp_path / "attention-input",
                "text",
            )
            assert json.loads(human["stdout"]) == returned
            if candidate_cli:
                expected = fixtures.node_export(
                    CliHost(reference, tmp_path),
                    tmp_path / "attention-export",
                    """
import {readFileSync} from 'node:fs';
import {collection_script} from './server/attention/setup.ts';
const value=JSON.parse(readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(collection_script(value.path,value.datasets,value.measures)));
""",
                    {
                        "path": str(reference.db_path),
                        "datasets": returned["dataset_ids"],
                        "measures": returned["measure_ids"],
                    },
                )
            else:
                expected = _script(
                    reference.db_path, returned["dataset_ids"], returned["measure_ids"]
                )
            state = fixtures.attention_state(reference, returned, expected, root=tmp_path)
            evidence["implementations"].append(
                {
                    "implementation": implementation,
                    "json": answer,
                    "text": human,
                    "state": state,
                }
            )
            states.append({"returned": state["returned"], "rows": state["rows"]})
            normalizer = Normalizer(
                json.loads(fixtures.RULES.read_text()), reference.db_path.parent
            )
            normalizer.ids[returned["source_id"]] = "<attention-source>"
            for kind in ("dataset_ids", "measure_ids"):
                for metric, identifier in returned[kind].items():
                    normalizer.ids[identifier] = f"<attention-{kind}-{metric}>"
            outputs.append(
                [
                    normalizer.normalize(comparable(answer)),
                    normalizer.normalize(comparable(human)),
                ]
            )
        left, right = evidence["implementations"][1:]
        wire_rules = json.loads(fixtures.RULES.read_text())
        normalizers = [Normalizer(wire_rules, reference.db_path.parent) for _ in range(2)]
        assert len(left["json"]["wire"]) == len(right["json"]["wire"]) == 13
        for index in range(13):
            replies = [item["json"]["wire"][index] for item in (left, right)]
            if index in (11, 12):
                for item, reply, normalizer in zip(
                    (left, right), replies, normalizers, strict=True
                ):
                    source = json.loads(item["json"]["stdout"])["source_id"]
                    suffix = "script" if index == 11 else "schedule"
                    assert reply["method"] == "PUT" and reply["status"] == 200
                    assert reply["path"] == f"/api/measure-sources/{source}/{suffix}"
                    body = json.loads(base64.b64decode(reply["body_base64"]))
                    assert body["active_script"]["sha256"] == item["state"]["sha256"]
                    normalizer.rules = {
                        **wire_rules,
                        "response_tokens": {
                            **wire_rules["response_tokens"],
                            "body.active_script.sha256": "Verified attention script SHA alias",
                        },
                    }
            try:
                compare_wire({"wire": [replies[0]]}, {"wire": [replies[1]]}, *normalizers)
            finally:
                for normalizer in normalizers:
                    normalizer.rules = wire_rules
        evidence["wire_scope"] = (
            "All 13 fresh JSON replies compared. Both digests match factory text and SQL. "
            "Idempotent human raw traces retain their own ID order."
        )
        evidence["wire_aliases"] = fixtures.rule("attention_scripts")["wire_aliases"]
        assert states[0] == states[1] == states[2], evidence
        assert outputs[0] == outputs[1] == outputs[2], evidence
    finally:
        for handle in reversed(handles):
            _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        driver.write_report(request, "unit20-attention-setup-cli", evidence)


def test_unit20_real_preview_cli(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One owned socket and real Git worktree prove registration without a browser."""
    import sys
    from dataclasses import asdict

    from tests.compare.direct import unit20_domain_fixtures as fixtures
    from tests.integration.test_preview_registry import application

    from planner.previews.process import LinuxProcessInspector

    if sys.platform != "linux":
        pytest.skip("real preview registration requires Linux /proc")
    started = time.perf_counter()
    handles: list[ServerHandle] = []
    evidence: dict[str, Any] = {"list": LIST.name, "implementations": []}
    ingress = {
        "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
        "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@comparison.invalid",
        "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.comparison.invalid",
    }
    try:
        reference = _start_server(
            tmp_path / "python",
            server_module="tests.compare.host",
            trusted_ingress_env=ingress,
        )
        handles.append(reference)
        git_root = tmp_path / "project"
        git_root.mkdir()
        input_root = tmp_path / "native-input"
        input_root.mkdir()
        env = driver.environment(CliHost(reference, tmp_path), {"actor": "owner"}, input_root)
        for command in (
            ["git", "init", "-q", str(git_root)],
            [
                "git",
                "-C",
                str(git_root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@comparison.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
        ):
            subprocess.run(command, env=env, check=True, capture_output=True)
        project = fixtures.api(
            reference,
            "POST",
            "/api/projects",
            {"name": "CLI preview project", "folder_path": str(git_root)},
        )
        ticket = fixtures.api(
            reference,
            "POST",
            "/api/tickets",
            {
                "title": "CLI real preview",
                "worker_type": "coding",
                "project_id": project["id"],
                "manager": {"kind": "chief", "id": "chief"},
            },
        )
        tree = git_root / "worktrees" / (ticket["id"] + "-preview")
        subprocess.run(
            ["git", "-C", str(git_root), "worktree", "add", "--detach", str(tree)],
            env=env,
            check=True,
            capture_output=True,
        )
        snapshot = tmp_path / "preview-seed.db"
        backup_database(reference.db_path, snapshot)
        candidate = _start_server(
            tmp_path / "typescript",
            server_module="tests.compare.ts_launcher",
            trusted_ingress_env=ingress,
            seed_db=lambda target: backup_database(snapshot, target),
        )
        handles.append(candidate)
        results = []
        # The reused listener helper receives only synthetic HOME/provider homes.
        with (
            patch.dict(os.environ, env, clear=True),
            application(tree) as (child, port),
        ):
            actual_process = asdict(LinuxProcessInspector().discover(port, tree))
            for name, handle, candidate_cli in (
                ("python_cli_python_host", reference, False),
                ("typescript_cli_python_host", reference, True),
                ("typescript_cli_typescript_host", candidate, True),
            ):
                modes: dict[str, Any] = {}
                for mode in ("json", "text"):
                    normalizer = Normalizer(
                        json.loads(fixtures.RULES.read_text()), handle.db_path.parent
                    )
                    normalizer.ids[ticket["id"]] = "<preview-ticket>"
                    preview_steps = fixtures.direct_steps("preview")
                    failure = next(step for step in preview_steps if step["exit_code"] == 1)
                    refusal = run_cli(
                        CliHost(handle, tmp_path),
                        substitute(
                            failure,
                            {"preview_ticket": ticket["id"], "preview_port": str(port)},
                        ),
                        candidate_cli,
                        tmp_path / "preview-input",
                        mode,
                    )
                    refusal_wire = wire_view(refusal, normalizer)
                    evidence.setdefault("registration_refusals", []).append(
                        {"implementation": name, "mode": mode, "raw": refusal}
                    )
                    answers = []
                    generation, created_at = None, None
                    for step in (step for step in preview_steps if step["exit_code"] == 0):
                        resolved = substitute(
                            step,
                            {"preview_ticket": ticket["id"], "preview_port": str(port)},
                        )
                        interval_start = int(time.time())
                        answer = run_cli(
                            CliHost(handle, tmp_path),
                            resolved,
                            candidate_cli,
                            tmp_path / "preview-input",
                            mode,
                        )
                        interval = interval_start, int(time.time())
                        current = fixtures.api(
                            handle,
                            "GET",
                            "/api/previews",
                            identity={"ticket_id": ticket["id"]},
                        )
                        first = resolved["argv"][1] == "prepare"
                        if first:
                            generation, created_at = (
                                current["generation"],
                                current["created_at"],
                            )
                        assert (
                            current["generation"] == generation
                            and current["created_at"] == created_at
                        )
                        if not first:
                            assert current["process"] == actual_process
                        answer["wire_compared"] = wire_view(
                            answer, normalizer, preview_created_at=current["created_at"]
                        )
                        answers.append(
                            fixtures.preview_answer(
                                answer,
                                current,
                                handle,
                                interval,
                                normalizer,
                                mode,
                                first=first,
                            )
                        )
                        evidence["implementations"].append(
                            {
                                "implementation": name,
                                "mode": mode,
                                "argv": resolved["argv"],
                                "raw": answer,
                                "api": current,
                                "interval": interval,
                            }
                        )
                    modes[mode] = {
                        "registration_refusal": comparable(refusal),
                        "refusal_wire": refusal_wire,
                        "output": answers,
                        "wire": [
                            item["raw"]["wire_compared"]
                            for item in evidence["implementations"]
                            if item["implementation"] == name and item["mode"] == mode
                        ],
                    }
                results.append(modes)
                assert child.poll() is None
        assert results[0] == results[1] == results[2], evidence
    finally:
        for handle in reversed(handles):
            _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        driver.write_report(request, "unit20-real-preview-cli", evidence)


@pytest.mark.parametrize(
    "backend",
    ["tests.compare.host", "tests.compare.ts_launcher"],
    ids=["python", "typescript"],
)
def test_unit20_domain_same_python_host(
    tmp_path: Path, request: pytest.FixtureRequest, backend: str
) -> None:
    """Reuse the work-list reset proof for both CLI isolation and TS-backend bytes."""
    from tests.compare.direct.test_unit20_cli import compare_cli_sequence

    compare_cli_sequence(LIST, backend, tmp_path, request)
