"""Exact old-source ingress and selected reader reversal versus lossy restoration.

Risk: a current migration fixture cannot prove compatibility with real retained
source or its canonical readers. One subprocess-backed case crosses that missing
boundary; existing runtime proofs own obsolete cursors and lost command witnesses.
All databases, homes, skill files and producer leases stay under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.support.navigation_current import current_navigation_database
from tests.support.navigation_historical_source import (
    CURRENT_STAGING_TIP,
    HISTORICAL_TIP,
    historical_source,
    run_historical,
    source_facts,
)

from planner.navigation import snapshot


def _copy_closed_database(source: Path, target: Path) -> None:
    # This local helper accepts only explicitly synthetic test paths.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)


def _expected_reader_values(
    result: dict[str, Any], *, expected_tip: str, later: bool = False,
) -> None:
    assert result["tip"] == expected_tip
    assert result["foreign_keys"] == []
    assert result["denied_writes"] == 2
    assert result["projects"]["project_historical"] == (
        "Later fixture project" if later else "Historical project"
    )
    assert result["ticket"] == {
        "title": "Later fixture Ticket" if later else "Historical Ticket",
        "stage": "needs_plan",
        "claim": "out",
        "backlog": True,
        "status": "awaiting_approval",
        "project": "project_historical",
        "orchestrator": "si_historical",
        "conversation": "c_current",
        "fields": {
            "brief": "Approved brief",
            "success_condition": "Preserve facts",
            "what_changes": "Verify migration",
        },
        "proposal": "Proposed plan α",
        "proposal_revision": 3,
    }
    source = result["source"]
    assert source["aliases"] == [["c_current", "t_historical"], ["c_history", "t_historical"]]
    assert source["conversations"] == [["c_current", 2, 1, 0], ["c_history", 2, 1, 0]]
    assert len(source["events"]) == 4
    assert [row[2] for row in source["events"]] == [
        "prompt_delivery_uncertain",
        "prompt_discarded",
        "prompt_delivery_uncertain",
        "prompt_discarded",
    ]
    for _, _, _, payload, _ in source["events"]:
        assert json.loads(payload)["text"] == "Synthetic receipt α\nKeep exact bytes."


@pytest.mark.parametrize("expected_tip", [HISTORICAL_TIP, CURRENT_STAGING_TIP], ids=["595", "86"])
def test_exact_staging_ingress_reader_reversal_and_lossy_fixture_restore(
    tmp_path: Path,
    expected_tip: str,
) -> None:
    source = historical_source()
    if expected_tip == CURRENT_STAGING_TIP:
        source = source.parent / "t_3p0mtprp-baseline-86"
    if not (source / ".venv/bin/python").exists():
        pytest.skip(
            f"exact {expected_tip} tree/interpreter unavailable at {source}"
        )
    # The old source executes its migrations and seed, then exits before transfer.
    original = tmp_path / "staging-source.db"
    before = run_historical(source, original, "seed", expected_tip=expected_tip)
    _expected_reader_values(before, expected_tip=expected_tip)
    assert before["source"]["heads"] == [["remove_priority"]]
    candidate = tmp_path / "upgraded.db"
    _copy_closed_database(original, candidate)

    with current_navigation_database(candidate) as connection:
        current = snapshot(connection)
        assert current["schema_version"] == 6
        initial_epoch = current["epoch"]
        facts = source_facts(connection)
        assert facts["heads"] == [["navigation_current_workspace"]]
        assert {key: value for key, value in facts.items() if key != "heads"} == {
            key: value for key, value in before["source"].items() if key != "heads"
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute("SELECT COUNT(*) FROM conversation_lifecycle_facts").fetchone()[0]
            == 0
        )
        record = next(
            row
            for row in current["records"]
            if row["kind"] == "ticket" and row["id"] == "t_historical"
        )
        assert record["value"]["ticket_status"] == "awaiting_approval"
        assert record["value"]["worker_step_claim_out"] is True
        assert record["value"]["is_backlogged"] is True
        assert record["value"]["blocked"] is False
        # Deliberate writes after the backup establish exactly what restoration loses.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE projects SET name='Later fixture project' WHERE id='project_historical'"
        )
        connection.execute(
            "UPDATE tickets SET title='Later fixture Ticket' WHERE id='t_historical'"
        )
        connection.commit()
        later_facts = source_facts(connection)

    # Stop every candidate owner before invoking bounded legacy read-only readers.
    contents = candidate.read_bytes()
    after = run_historical(source, candidate, "read", expected_tip=expected_tip)
    _expected_reader_values(after, expected_tip=expected_tip, later=True)
    assert after["source"] == later_facts
    assert candidate.read_bytes() == contents

    # f097 positively read the old candidate, but cannot read approved priority
    # removal. Exercise the actual old readers read-only; do not invent defaults.
    from tests.support.navigation_historical_source import F097_TIP

    f097 = source.parent / "t_jghnv9gu-baseline-f097"
    unsupported = run_historical(f097, candidate, "read_incompatible", expected_tip=F097_TIP)
    assert set(unsupported["failures"]) == {"projects", "ticket"}
    assert "priority" in unsupported["failures"]["projects"]
    assert unsupported["source"] == later_facts
    assert candidate.read_bytes() == contents

    # Restore the old synthetic backup into another target, never over the candidate.
    restored = tmp_path / "restored-staging.db"
    _copy_closed_database(original, restored)
    reopened = run_historical(source, restored, "read", expected_tip=expected_tip)
    _expected_reader_values(reopened, expected_tip=expected_tip)
    assert reopened["source"] == before["source"]
    with current_navigation_database(restored) as connection:
        restored_snapshot = snapshot(connection)
        assert restored_snapshot["schema_version"] == 6
        assert restored_snapshot["epoch"] != initial_epoch
        ticket = next(
            row
            for row in restored_snapshot["records"]
            if row["kind"] == "ticket" and row["id"] == "t_historical"
        )
        project = next(
            row
            for row in restored_snapshot["records"]
            if row["kind"] == "project" and row["id"] == "project_historical"
        )
        assert ticket["value"]["title"] == "Historical Ticket"
        assert project["value"]["name"] == "Historical project"
        facts = source_facts(connection)
        assert facts["events"] == before["source"]["events"]
        assert facts["conversations"] == before["source"]["conversations"]
        assert facts["aliases"] == before["source"]["aliases"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    # The original upgraded fixture retains the deliberately discarded later writes.
    retained = run_historical(source, candidate, "read", expected_tip=expected_tip)
    assert retained["ticket"]["title"] == "Later fixture Ticket"
    print(
        json.dumps(
            {
                "historical_source": str(source),
                "tip": before["tip"],
                "module": before["module"],
                "python_prefix": before["python_prefix"],
                "migration_sha256": before["migration"],
                "scope": (
                    "projects.list_projects, tickets.read_ticket, bounded raw transcript SQL only"
                ),
            }
        )
    )
