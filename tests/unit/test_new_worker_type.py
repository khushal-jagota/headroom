"""Job B — the shipped ``new_worker`` Worker type: its contract, its exact manifest,
and a compact drive-to-done through the real ``data.*`` writers.

``new_worker`` is the production type for designing another worker. Its worker
designs and lands ANOTHER worker; its lifecycle is a bespoke thinking scaffold
(``needs_kickoff -> needs_understanding -> needs_stages -> needs_thinking ->
needs_runtime_defaults -> needs_drafting ->
needs_closeout -> done``) whose five middle stages/fields are novel. This module proves:
- the definition validates and serializes to its exact manifest (default_ceiling is
  the leading ``needs_kickoff``; first worker stage is ``needs_understanding``);
- a ``new_worker`` ticket is created and driven stage-by-stage (propose -> approve ->
  advance) through the shared gate machinery, its novel states/fields round-tripping
  the DB, reaching ``done`` — and to ``dropped`` via the universal terminal.

Every assertion is a concrete value.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
)
from planner.tickets.logic import fields_codec
from planner.worker_types.configuration import (
    PRODUCTION_WORKER_TYPE_REGISTRY,
)
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.registry import WorkerTypeRegistry

# The isolated new_worker validator needs its specialist, coding's specialist, and the
# base role. This fixture does not compose the complete production registry.
_KNOWN_SKILLS = frozenset({"panels-worker", "panels-worker-coding", "panels-worker-new-worker"})
_KNOWN_TOOLSET_PROFILES = frozenset({"default"})


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    conn = connect(str(tmp_path / "new-worker-type.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


# =====================================================================
# Acceptance 1 — the definition validates; its contract asserted exactly
# =====================================================================


def test_registry_validates_new_worker() -> None:
    # The full validator accepts the new_worker definition through the same door the
    # production registry uses at build (no raise).
    WorkerTypeRegistry(
        (NEW_WORKER_TYPE_DEFINITION,),
        known_skills=_KNOWN_SKILLS,
        known_toolset_profiles=_KNOWN_TOOLSET_PROFILES,
    )


def test_production_registry_carries_new_worker() -> None:
    # new_worker ships in the production singleton alongside coding and exploration.
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "debugging",
        "new_worker",
        "exploration",
        "initiative_planning",
        "product_design",
        "planning-day",
        "planning-midday-check",
        "planning-sprint",
    )
    assert PRODUCTION_WORKER_TYPE_REGISTRY.require("new_worker").worker_type == "new_worker"


# The exact serialized manifest — the full dict (mirrors the coding manifest test).
NEW_WORKER_MANIFEST = {
    "worker_type": "new_worker",
    "label": "New Worker",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_understanding",
            "label": "Understanding",
            "gating_field": "understanding",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_stages",
            "label": "Stages",
            "gating_field": "stages",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_thinking",
            "label": "Thinking",
            "gating_field": "thinking",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_runtime_defaults",
            "label": "Runtime Defaults",
            "gating_field": "runtime_defaults",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_drafting",
            "label": "Drafting",
            "gating_field": "drafting",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_closeout",
            "label": "Closeout",
            "gating_field": "closeout",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "done",
            "label": "Done",
            "gating_field": None,
            "is_terminal": True,
            "default_ownership_mode": None,
        },
    ],
    "dropped": {
        "id": "dropped",
        "label": "Dropped",
        "gating_field": None,
        "is_terminal": True,
        "default_ownership_mode": None,
    },
    "advance": {
        "needs_kickoff": "needs_understanding",
        "needs_understanding": "needs_stages",
        "needs_stages": "needs_thinking",
        "needs_thinking": "needs_runtime_defaults",
        "needs_runtime_defaults": "needs_drafting",
        "needs_drafting": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "understanding", "label": "Understanding"},
        {"id": "stages", "label": "Stages"},
        {"id": "thinking", "label": "Thinking"},
        {"id": "runtime_defaults", "label": "Runtime Defaults"},
        {"id": "drafting", "label": "Drafting"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_understanding",
        "needs_stages",
        "needs_thinking",
        "needs_runtime_defaults",
        "needs_drafting",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-new-worker",
    "default_backend": "codex",
    "default_model": "gpt-5.6-sol",
    "default_reasoning_effort": "medium",
}


def test_new_worker_manifest_exact() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("new_worker") == NEW_WORKER_MANIFEST


def test_new_worker_manifest_json_roundtrips() -> None:
    assert (
        json.loads(json.dumps(PRODUCTION_WORKER_TYPE_REGISTRY.manifest("new_worker")))
        == NEW_WORKER_MANIFEST
    )


def test_new_worker_gate_map_is_golden() -> None:
    assert {
        stage: NEW_WORKER_TYPE_DEFINITION.gating_field(stage)
        for stage in NEW_WORKER_TYPE_DEFINITION.stage_ids()[:-1]
    } == {
        "needs_kickoff": "kickoff",
        "needs_understanding": "understanding",
        "needs_stages": "stages",
        "needs_thinking": "thinking",
        "needs_runtime_defaults": "runtime_defaults",
        "needs_drafting": "drafting",
        "needs_closeout": "closeout",
    }


def test_new_worker_field_order_is_golden() -> None:
    assert NEW_WORKER_TYPE_DEFINITION.field_ids() == (
        "kickoff",
        "understanding",
        "stages",
        "thinking",
        "runtime_defaults",
        "drafting",
        "closeout",
    )


def test_new_worker_default_ceiling_and_first_worker_stage() -> None:
    # Default ceiling is the leading needs_kickoff (global); the FIRST WORKER stage —
    # the distinct threshold — is needs_understanding.
    assert NEW_WORKER_TYPE_DEFINITION.default_ceiling() == "needs_kickoff"
    assert NEW_WORKER_TYPE_DEFINITION.first_worker_stage() == "needs_understanding"


def test_new_worker_declares_expected_default_ownership_modes() -> None:
    assert {
        stage.id: (
            stage.default_ownership_mode.value
            if stage.default_ownership_mode is not None
            else None
        )
        for stage in NEW_WORKER_TYPE_DEFINITION.stages
        if not stage.is_terminal
    } == {
        "needs_kickoff": "worker",
        "needs_understanding": "paired",
        "needs_stages": "worker",
        "needs_thinking": "worker",
        "needs_runtime_defaults": "paired",
        "needs_drafting": "worker",
        "needs_closeout": "worker",
    }


# =====================================================================
# Acceptance 2 — a compact drive-to-done through the real data.* writers
# =====================================================================


def _worker_session_exists(conn: Connection, tid: str) -> bool:
    """Whether a worker was ever started on this Ticket. Naming a conversation is the
    whole of it: a Ticket names one when its first step runs, and never before."""
    row = conn.execute("SELECT conversation_id FROM tickets WHERE id = ?", (tid,)).fetchone()
    return row["conversation_id"] is not None


def test_new_worker_drives_to_done_via_real_writers(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Design a research worker",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="new_worker",
    )
    tid = ticket.id

    # Created at needs_kickoff scoped to the leading needs_kickoff ceiling, kickoff parked.
    assert ticket.stage == "needs_kickoff"
    assert ticket.ceiling == "needs_kickoff"
    assert ticket.worker_type == "new_worker"
    assert fields_codec.get_slot(ticket.fields, "kickoff").proposal is not None

    # Accept kickoff, expanding the ceiling all the way to needs_closeout -> advances to
    # needs_understanding (the first novel stage).
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling="needs_closeout",
        at_cap=AtCap.propose,
    )
    assert t.stage == "needs_understanding"
    assert t.ceiling == "needs_closeout"

    # Understanding is paired, so its proposal parks for human approval even below the
    # ceiling. Approval advances to the existing worker-owned sequence.
    t = tickets_data.file_proposal(
        tmp_db,
        tid,
        field="understanding",
        body="understanding body",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_understanding"
    assert fields_codec.get_slot(t.fields, "understanding").proposal is not None
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="understanding",
        actor="human",
        now=now,
        next_ceiling="needs_closeout",
        at_cap=AtCap.propose,
    )
    assert t.stage == "needs_stages"
    assert fields_codec.get_slot(t.fields, "understanding").value == "understanding body"

    # Drive the worker-owned stages before Runtime Defaults.
    for field, next_state in (("stages", "needs_thinking"), ("thinking", "needs_runtime_defaults")):
        t = tickets_data.file_proposal(
            tmp_db, tid, field=field, body=f"{field} body", actor="agent", now=now
        )
        assert t.stage == next_state
        assert fields_codec.get_slot(t.fields, field).value == f"{field} body"
        assert fields_codec.get_slot(t.fields, field).proposal is None

    # Runtime Defaults is paired, so its proposal parks below the ceiling and approval
    # advances to Drafting. The approved value round-trips through the normal field slot.
    t = tickets_data.file_proposal(
        tmp_db,
        tid,
        field="runtime_defaults",
        body="codex / gpt-5.6-sol / medium",
        actor="agent",
        now=now,
    )
    assert t.stage == "needs_runtime_defaults"
    assert fields_codec.get_slot(t.fields, "runtime_defaults").proposal is not None
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="runtime_defaults",
        actor="human",
        now=now,
        next_ceiling="needs_closeout",
        at_cap=AtCap.propose,
    )
    assert t.stage == "needs_drafting"
    assert fields_codec.get_slot(t.fields, "runtime_defaults").value == (
        "codex / gpt-5.6-sol / medium"
    )

    t = tickets_data.file_proposal(
        tmp_db, tid, field="drafting", body="drafting body", actor="agent", now=now
    )
    assert t.stage == "needs_closeout"
    assert fields_codec.get_slot(t.fields, "drafting").value == "drafting body"

    # At needs_closeout (ceiling ==): propose closeout -> parks; then accept -> done.
    tickets_data.file_proposal(
        tmp_db, tid, field="closeout", body="closeout body", actor="agent", now=now
    )
    t = tickets_data.accept_proposal(
        tmp_db,
        tid,
        field="closeout",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )

    # Exact final state: done, every worker field settled to its accepted value.
    assert t.stage == "done"
    assert fields_codec.get_slot(t.fields, "understanding").value == "understanding body"
    assert fields_codec.get_slot(t.fields, "stages").value == "stages body"
    assert fields_codec.get_slot(t.fields, "thinking").value == "thinking body"
    assert fields_codec.get_slot(t.fields, "runtime_defaults").value == (
        "codex / gpt-5.6-sol / medium"
    )
    assert fields_codec.get_slot(t.fields, "drafting").value == "drafting body"
    assert fields_codec.get_slot(t.fields, "closeout").value == "closeout body"
    assert fields_codec.get_slot(t.fields, "closeout").proposal is None

    # No worker turn / session was created by the DATA-layer drive.
    assert _worker_session_exists(tmp_db, tid) is False


def test_new_worker_drives_to_dropped(tmp_db: Connection, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Abandon",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="new_worker",
    )
    # drop is the universal reserved bookend; it terminates a new_worker ticket at dropped.
    t = tickets_data.drop_ticket(tmp_db, ticket.id, actor="human", now=now)
    assert t.stage == "dropped"
    assert _worker_session_exists(tmp_db, ticket.id) is False
