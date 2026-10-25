"""Scoped view risks reuse the canonical navigation fixture, without a new host."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.integration.test_navigation_attention import Host, Turn
from tests.integration.test_navigation_attention import prepared as prepared
from tests.integration.test_navigation_signal_publication import (
    _ActualBinding,
)
from tests.integration.test_navigation_signal_publication import (
    actual_binding as actual_binding,
)
from tests.integration.test_navigation_transactions import add_conversation, add_parent, add_ticket
from tests.support.navigation_current import (
    current_navigation_database,
    prepared_navigation_database,
)

from planner.navigation.views.configuration import DEFAULT_CONFIG
from planner.navigation.views.contracts import (
    ViewContent,
    ViewGroup,
    ViewRequest,
    ViewSnapshot,
    ViewWindowRequest,
)
from planner.navigation.views.delivery import difference
from planner.navigation.views.logic import compact, digest, section_key
from planner.navigation.views.projection import rebuild
from planner.navigation.views.read import read_content, request
from planner.navigation.views.schema import prepare
from planner.tickets.views import _review_items, review_view


def descriptor(*, limit: int = 30, selected: str | None = None) -> ViewRequest:
    window: ViewWindowRequest = {
        "section": "children",
        "owner": {"kind": "chief", "id": "chief"},
        "group": "all",
        "child_filter": "active",
        "anchor": None,
        "direction": "after",
        "limit": limit,
    }
    return request(
        {
            "projection_version": 6,
            "scope_generation": 1,
            "locale": "en-US",
            "selected": None if selected is None else {"kind": "ticket", "id": selected},
            "selected_conversation_id": None,
            "review_conversation_ids": [],
            "windows": [window],
        }
    )


def activate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    prepare(conn)
    rebuild(conn)
    conn.commit()


def test_blocked_windows_outrank_activity_but_keep_owner_attention(prepared: Host) -> None:
    host = prepared
    host.attach()
    conn = host.connect()
    activate(conn)
    conn.execute("BEGIN")
    add_parent(conn, "manager")
    add_ticket(conn, "blocker")
    add_ticket(conn, "chief-ticket")
    conn.execute("UPDATE tickets SET manager_kind='manager',manager_id='manager' WHERE id='t'")
    conn.execute(
        "UPDATE tickets SET conversation_id='c',stage='needs_plan' WHERE id='chief-ticket'"
    )
    conn.execute("UPDATE tickets SET worker_step_claim='out' WHERE id IN ('t','chief-ticket')")
    conn.executemany("INSERT INTO ticket_blocks VALUES('blocker',?)", [("t",), ("chief-ticket",)])
    conn.commit()
    query = descriptor()
    query["windows"] = [
        cast(
            ViewWindowRequest,
            {
                **query["windows"][0],
                "section": section,
                "owner": owner,
                "group": grouping,
                "child_filter": "all",
            },
        )
        for owner in ({"kind": "manager", "id": "manager"}, {"kind": "chief", "id": "chief"})
        for section, grouping in (
            ("children", "blocked"),
            ("children", "awaiting_answer"),
            ("rail_tickets", "all"),
        )
    ]

    def memberships(
        *, answering: bool = False, grouping: ViewGroup = "blocked", in_rail: bool = False
    ) -> None:
        for index in (0, 3):
            query["windows"][index]["group"] = grouping
        for current in (True, False):
            _, content = read_content(conn, query, current=current)
            for offset, identity in ((0, "t"), (3, "chief-ticket")):
                blocked, answer, rail = content["windows"][offset : offset + 3]
                assert [row["id"] for row in blocked["members"]] == (
                    [] if current and answering else [identity]
                )
                assert [row["id"] for row in answer["members"]] == (
                    [identity] if current and answering else []
                )
                assert (identity in {row["id"] for row in rail["members"]}) is in_rail

    # A claimed step with no running turn is still canonically Agent, even with
    # a dependency. An owner ceiling alone is not an owner-attention request.
    memberships()
    _, content = read_content(conn, query, current=True)
    for row in content["records"]:
        if row["kind"] == "ticket" and row["id"] in ("t", "chief-ticket"):
            assert row["value"]["blocked"] is True
            assert row["value"]["ticket_status"] == "agent"
            assert row["value"]["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    writer = host.mutation(Turn(), "blocked-working")
    writer.commit()
    memberships()
    _, content = read_content(conn, query, current=True)
    attention = next(row for row in content["records"] if row["kind"] == "conversation")["value"]
    current_attention = attention["attention"]["current"]
    assert current_attention is not None and current_attention["signal"]["working"] is True
    assert all(
        row["value"]["ticket_status"] == "agent"
        for row in content["records"]
        if row["kind"] == "ticket" and row["id"] != "blocker"
    )
    writer = host.mutation(Turn({"permission"}), "blocked-awaiting-answer")
    writer.commit()
    memberships(answering=True)
    writer = host.mutation(None, "blocked-failed-history")
    writer.execute(
        "INSERT INTO conversation_events VALUES('c',1,'turn_ended','{\"ending\":\"failed\"}',0)"
    )
    writer.commit()
    memberships()
    _, content = read_content(conn, query, current=False)
    attention = next(row for row in content["records"] if row["kind"] == "conversation")["value"]
    assert attention["attention"]["durable"]["last_turn_failed"] is True
    conn.execute("BEGIN")
    proposal = json.dumps(
        {"field": "plan", "body": "Review", "proposed_by": "worker", "created_at": 1}
    )
    conn.execute(
        "UPDATE tickets SET pending_proposal=? WHERE id IN ('t','chief-ticket')", (proposal,)
    )
    conn.commit()
    memberships(grouping="awaiting_approval")
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET is_backlogged=1 WHERE id IN ('t','chief-ticket')")
    conn.commit()
    memberships(grouping="backlog")
    conn.execute("BEGIN")
    conn.execute(
        "UPDATE tickets SET is_backlogged=0,pending_proposal=NULL WHERE id IN ('t','chief-ticket')"
    )
    definition = json.loads(
        conn.execute(
            "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
        ).fetchone()[0]
    )
    plan_stage = next(stage for stage in definition["stages"] if stage["id"] == "needs_plan")
    plan_stage["ownership_mode"] = "user"
    conn.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
        (json.dumps(definition),),
    )
    conn.commit()
    memberships(grouping="assigned")
    conn.execute("BEGIN")
    plan_stage["ownership_mode"] = "worker"
    conn.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
        (json.dumps(definition),),
    )
    conn.execute("DELETE FROM ticket_blocks WHERE blocking_ticket_id='blocker'")
    conn.commit()
    memberships(grouping="errored", in_rail=True)
    writer = host.mutation(Turn(), "unblocked-working")
    writer.commit()
    # Current activity masks failed history; durable mode deliberately cannot.
    for current, grouping in ((True, "agent"), (False, "errored")):
        for index in (0, 3):
            query["windows"][index]["group"] = cast(ViewGroup, grouping)
        _, content = read_content(conn, query, current=current)
        assert [row["id"] for row in content["windows"][0]["members"]] == ["t"]
        assert [row["id"] for row in content["windows"][3]["members"]] == ["chief-ticket"]


def add_review_tickets(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    for identity in ("active-a", "active-b", "backlogged-a", "backlogged-b"):
        add_ticket(conn, identity)
    proposal = json.dumps(
        {"field": "implementation", "body": "Review me", "proposed_by": "worker", "created_at": 1}
    )
    conn.execute("UPDATE tickets SET pending_proposal=?,pending_proposal_revision=1", (proposal,))
    conn.execute("UPDATE tickets SET is_backlogged=1 WHERE id LIKE 'backlogged-%'")
    conn.commit()


def assert_review_queue(conn: sqlite3.Connection, expected: set[str]) -> None:
    assert {item["ticket_id"] for item in _review_items(conn)} == expected
    assert {item["ticket_id"] for item in review_view(conn)["items"]} == expected
    query = descriptor()
    query["windows"] = [
        {**query["windows"][0], "section": "review", "owner": None, "child_filter": "all"}
    ]
    for current in (True, False):
        _, content = read_content(conn, query, current=current)
        assert {item["id"] for item in content["windows"][0]["members"]} == expected
        assert content["windows"][0]["total"] == len(expected)
        assert content["counts"]["review"] == len(expected)


def test_backlog_changes_review_membership_and_counts_without_removing_proposals(
    tmp_path: Path,
) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        add_review_tickets(conn)
        proposals = list(conn.execute("SELECT id,pending_proposal FROM tickets ORDER BY id"))
        assert_review_queue(conn, {"active-a", "active-b"})
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET is_backlogged=0 WHERE id='backlogged-a'")
        conn.commit()
        assert_review_queue(conn, {"active-a", "active-b", "backlogged-a"})
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET is_backlogged=1 WHERE id='backlogged-a'")
        conn.commit()
        assert_review_queue(conn, {"active-a", "active-b"})
        assert (
            list(conn.execute("SELECT id,pending_proposal FROM tickets ORDER BY id")) == proposals
        )


def test_signal_install_repairs_persisted_backlogged_review_memberships(tmp_path: Path) -> None:
    path = tmp_path / "db"
    with current_navigation_database(path) as conn:
        activate(conn)
        add_review_tickets(conn)
        proposals = [
            tuple(row)
            for row in conn.execute("SELECT id,pending_proposal FROM tickets ORDER BY id")
        ]
    review_section = section_key("review", None, "all", "all")
    # Persist the old bug's derived state without a canonical write or rebuild.
    # Both backlogged Tickets already retain their unchanged pending proposals.
    with sqlite3.connect(path) as stale:
        stale.execute("UPDATE navigation_view_index SET review=1 WHERE kind='ticket'")
        for identity in ("backlogged-a", "backlogged-b"):
            stale.execute(
                "INSERT INTO navigation_view_memberships "
                "SELECT section_key,profile,locale,kind,?,rank,title_key,? "
                "FROM navigation_view_memberships WHERE section_key=? AND id='active-a'",
                (identity, identity.encode(), review_section),
            )
        stale.execute(
            "UPDATE navigation_view_counts SET value=4 WHERE section_key=?", (review_section,)
        )
        stale.execute("UPDATE navigation_view_globals SET value=4 WHERE name='review'")
    # Physical preparation alone must leave the stale state intact. Only the
    # canonical Signal installation in the next context can repair it.
    with prepared_navigation_database(path) as persisted:
        assert (
            persisted.execute(
                "SELECT value FROM navigation_view_globals WHERE name='review'"
            ).fetchone()[0]
            == 4
        )
        assert (
            persisted.execute(
                "SELECT count(*) FROM navigation_view_index WHERE review=1"
            ).fetchone()[0]
            == 4
        )
        assert {
            tuple(row)
            for row in persisted.execute(
                "SELECT value FROM navigation_view_counts WHERE section_key=?", (review_section,)
            )
        } == {(4,)}
        assert {
            tuple(row)
            for row in persisted.execute(
                "SELECT id FROM navigation_view_memberships WHERE section_key=?", (review_section,)
            )
        } == {(identity,) for identity in ("active-a", "active-b", "backlogged-a", "backlogged-b")}
    # The real producer installation on reopen must repair every derived surface.
    with current_navigation_database(path) as reopened:
        assert_review_queue(reopened, {"active-a", "active-b"})
        assert set(
            tuple(row)
            for row in reopened.execute("SELECT id FROM navigation_view_index WHERE review=1")
        ) == {("active-a",), ("active-b",)}
        assert [
            tuple(row)
            for row in reopened.execute("SELECT id,pending_proposal FROM tickets ORDER BY id")
        ] == proposals


def test_settled_update_order_pages_and_live_changes_preserve_active_order(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        conn.execute("BEGIN")
        for identity, created_at, updated_at, settled in (
            ("a", 10, 100, 1),
            ("b", 20, 300, 1),
            ("c", 30, 300, 1),
            ("d", 40, 200, 1),
            ("x", 60, 900, 0),
            ("y", 50, 100, 0),
        ):
            add_parent(conn, identity)
            conn.execute(
                "UPDATE managers SET created_at=?,updated_at=?,settled=? WHERE id=?",
                (created_at, updated_at, settled, identity),
            )
        conn.commit()
        query = descriptor(limit=2)
        settled_window: ViewWindowRequest = {
            **query["windows"][0],
            "section": "settled",
            "owner": None,
            "child_filter": "all",
        }
        query["windows"] = [settled_window]
        for current in (True, False):
            _, first = read_content(conn, query, current=current)
            assert [row["id"] for row in first["windows"][0]["members"]] == ["b", "c"]
            page = {**query, "windows": [{**settled_window, "anchor": first["windows"][0]["next"]}]}
            _, second = read_content(conn, cast(ViewRequest, page), current=current)
            assert [row["id"] for row in second["windows"][0]["members"]] == ["d", "a"]
            previous = {
                **query,
                "windows": [
                    {
                        **settled_window,
                        "anchor": second["windows"][0]["previous"],
                        "direction": "before",
                    }
                ],
            }
            _, back = read_content(conn, cast(ViewRequest, previous), current=current)
            assert back["windows"][0]["members"] == first["windows"][0]["members"]
            assert first["counts"]["settled"] == second["windows"][0]["total"] == 4
        conn.execute("BEGIN")
        conn.execute("UPDATE managers SET updated_at=400 WHERE id='a'")
        conn.commit()
        for current in (True, False):
            _, changed = read_content(conn, query, current=current)
            assert [row["id"] for row in changed["windows"][0]["members"]] == ["a", "b"]
            manager = next(
                row for row in changed["records"] if row["kind"] == "manager" and row["id"] == "a"
            )
            assert manager["value"]["updated_at"] == 400
            query["windows"] = [{**settled_window, "section": "rail_parents"}]
            _, active = read_content(conn, query, current=current)
            assert [row["id"] for row in active["windows"][0]["members"]] == ["y", "x"]
            query["windows"] = [settled_window]


def test_signal_install_repairs_old_settled_records_order_and_cursors(tmp_path: Path) -> None:
    path = tmp_path / "db"
    query = descriptor(limit=1)
    query["windows"] = [
        {**query["windows"][0], "section": "settled", "owner": None, "child_filter": "all"}
    ]
    with current_navigation_database(path) as conn:
        activate(conn)
        conn.execute("BEGIN")
        for identity, created_at, updated_at in (("a", 10, 100), ("b", 20, 200)):
            add_parent(conn, identity)
            conn.execute(
                "UPDATE managers SET settled=1,created_at=?,updated_at=? WHERE id=?",
                (created_at, updated_at, identity),
            )
        conn.commit()
        before, content = read_content(conn, query, current=True)
        old_anchor = content["windows"][0]["next"]
    settled_section = section_key("settled", None)
    # Reproduce the prior build's persisted wire shape and creation-time ranks.
    with sqlite3.connect(path) as stale:
        for identity, created_at in (("a", 10), ("b", 20)):
            raw = json.loads(
                stale.execute(
                    "SELECT value FROM navigation_records WHERE kind='manager' AND id=?",
                    (identity,),
                ).fetchone()[0]
            )
            del raw["updated_at"]
            stale.execute(
                "UPDATE navigation_records SET value=? WHERE kind='manager' AND id=?",
                (json.dumps(raw), identity),
            )
            stale.execute(
                "UPDATE navigation_view_memberships SET rank=? WHERE section_key=? AND id=?",
                (created_at, settled_section, identity),
            )
    with prepared_navigation_database(path) as prepared:
        assert {
            row[0]
            for row in prepared.execute(
                "SELECT rank FROM navigation_view_memberships WHERE section_key=?",
                (settled_section,),
            )
        } == {10, 20}
    with current_navigation_database(path) as reopened:
        after, content = read_content(reopened, query, current=True)
        assert after["epoch"] != before["epoch"]
        assert [row["id"] for row in content["windows"][0]["members"]] == ["b"]
        assert (
            next(row for row in content["records"] if row["kind"] == "manager")["value"][
                "updated_at"
            ]
            == 200
        )
        stale_query = {**query, "windows": [{**query["windows"][0], "anchor": old_anchor}]}
        _, rebased = read_content(reopened, cast(ViewRequest, stale_query), current=True)
        assert rebased["windows"][0]["request"]["anchor"] is None
        assert rebased["windows"][0]["members"] == content["windows"][0]["members"]


def test_indexed_windows_counts_and_scope_eviction_are_atomic(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        conn.execute("BEGIN")
        for index in range(90):
            add_ticket(conn, f"t{index:03}")
        conn.commit()
        trace: list[str] = []
        conn.set_trace_callback(trace.append)
        coverage, first = read_content(conn, descriptor(), current=True)
        conn.set_trace_callback(None)
        window = first["windows"][0]
        assert window["total"] == 90
        assert len(window["members"]) == 30
        assert len(first["records"]) == 31
        assert window["next"] is not None
        assert all("WHERE kind=" in query for query in trace if "FROM navigation_records" in query)
        next_request = descriptor()
        next_request["windows"][0]["anchor"] = window["next"]
        _, second = read_content(conn, next_request, current=True)
        assert [r["id"] for r in second["windows"][0]["members"]] == [
            f"t{i:03}" for i in range(30, 60)
        ]
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET is_backlogged=1 WHERE id='t000'")
        conn.commit()
        later, changed = read_content(conn, descriptor(), current=True)
        assert int(later["revision"]) > int(coverage["revision"])
        assert changed["windows"][0]["total"] == 89
        assert changed["windows"][0]["members"][-1]["id"] == "t030"
        assert all(r["id"] != "t000" for r in changed["records"])
        _, selected = read_content(conn, descriptor(selected="t000"), current=True)
        assert any(r["id"] == "t000" for r in selected["records"])
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET is_backlogged=0 WHERE id='t000'")
        conn.rollback()
        assert read_content(conn, descriptor(), current=True)[1] == changed
        plans = conn.execute(
            "EXPLAIN QUERY PLAN SELECT kind,id FROM navigation_view_memberships "
            "WHERE section_key=? AND profile=? AND locale=? "
            "ORDER BY rank,title_key,id_key,kind,id LIMIT 30",
            ("x", "current", "en-US"),
        ).fetchall()
        assert any("navigation_view_window" in str(tuple(row)) for row in plans)


def test_sequence_bookkeeping_changes_coverage_without_public_content(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        add_conversation(conn)
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.execute("UPDATE tickets SET conversation_id='c' WHERE id='t'")
        conn.commit()
        before, first = read_content(conn, descriptor(), current=True)
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE conversations SET latest_sequence=latest_sequence+1,"
            "latest_agent_activity_sequence=latest_agent_activity_sequence+1 "
            "WHERE conversation_id='c'"
        )
        conn.commit()
        after, second = read_content(conn, descriptor(), current=True)
        assert first == second
        assert int(after["revision"]) > int(before["revision"])
        footprint = json.loads(
            conn.execute(
                "SELECT footprint FROM navigation_view_changes WHERE revision=?",
                (int(after["revision"]),),
            ).fetchone()[0]
        )
        assert footprint == {"globals": False, "keys": [], "sections": []}
        common = {
            "wire_version": 3,
            "projection_version": 6,
            "view_id": "view",
            "scope_generation": 1,
            "ordering_generation": "order",
            "authority": {},
            "content_version": digest(first),
            "content": first,
        }
        frame = difference(
            cast(ViewSnapshot, {**common, "coverage": before}),
            cast(ViewSnapshot, {**common, "coverage": after}),
        )
        assert frame["event"] == "checkpoint"
        assert "upserts" not in frame
        assert frame["previous_coverage"] == before


def test_partial_index_failure_rolls_back_canonical_change(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.commit()
        before = read_content(conn, descriptor(), current=True)
        conn.execute("BEGIN")
        conn.execute(
            "CREATE TEMP TRIGGER reject_view BEFORE INSERT ON navigation_view_changes "
            "BEGIN SELECT RAISE(ABORT,'view failure'); END"
        )
        conn.commit()
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET title='changed' WHERE id='t'")
        with pytest.raises(sqlite3.IntegrityError, match="view failure"):
            conn.commit()
        conn.rollback()
        assert read_content(conn, descriptor(), current=True) == before
        assert conn.execute("SELECT title FROM tickets WHERE id='t'").fetchone()[0] == "t"


def test_hidden_historical_unread_counts_distinct_conversations(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        add_conversation(conn, "old")
        add_conversation(conn, "new")
        conn.execute("BEGIN")
        add_ticket(conn, "a")
        add_ticket(conn, "b")
        conn.execute("UPDATE tickets SET conversation_id='new'")
        conn.execute("INSERT INTO ticket_conversations VALUES('old','a')")
        conn.execute("INSERT INTO conversation_events VALUES('old',1,'message_to_owner','{}',0)")
        conn.execute("INSERT INTO conversation_events VALUES('new',1,'message_to_owner','{}',0)")
        conn.commit()
        query = descriptor(limit=0)
        _, empty = read_content(conn, query, current=True)
        assert empty["counts"]["unread"] == 2
        assert empty["windows"][0]["total"] == 2
        assert len(empty["records"]) == 1  # Chief, no hidden archive body.
        query["selected"] = {"kind": "ticket", "id": "a"}
        query["selected_conversation_id"] = "old"
        _, selected = read_content(conn, query, current=True)
        assert any(row["id"] == "old" for row in selected["records"])
        _, durable = read_content(conn, query, current=False)
        assert durable["counts"]["running_conversations"] is None
        assert durable["counts"]["unread"] == 2
        assert all(
            row["value"]["attention"]["current"] is None
            for row in durable["records"]
            if row["kind"] == "conversation"
        )
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=1 WHERE conversation_id='new'"
        )
        conn.commit()
        assert read_content(conn, query, current=True)[1]["counts"]["unread"] == 1


def test_global_working_count_deduplicates_current_pointers_and_excludes_history(
    prepared: Host,
) -> None:
    host = prepared
    host.attach()
    conn = host.connect()
    activate(conn)
    query = descriptor(limit=0)
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 0

    writer = host.mutation(Turn(), "global-count-start")
    writer.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 1

    # An unread owner message does not cancel a working turn.
    conn.execute("BEGIN")
    conn.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}',0)")
    conn.commit()
    _, content = read_content(conn, query, current=True)
    assert content["counts"]["unread"] == 1
    assert content["counts"]["running_conversations"] == 1
    assert content["windows"][0]["members"] == []

    conn.execute("BEGIN")
    conn.execute("INSERT INTO agents VALUES('chief_of_staff','c')")
    conn.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 1

    conn.execute("BEGIN")
    add_parent(conn, "manager")
    conn.execute("INSERT INTO agents VALUES('manager_manager','c')")
    conn.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 1

    conn.execute("BEGIN")
    conn.execute("INSERT INTO ticket_conversations VALUES('c','t')")
    conn.execute("UPDATE tickets SET conversation_id=NULL WHERE id='t'")
    conn.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 1

    conn.execute("BEGIN")
    conn.execute("DELETE FROM agents WHERE agent_key='chief_of_staff'")
    conn.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 1

    conn.execute("BEGIN")
    conn.execute("DELETE FROM agents WHERE agent_key='manager_manager'")
    conn.commit()
    _, historical = read_content(conn, query, current=True)
    assert historical["counts"]["running_conversations"] == 0
    assert historical["counts"]["unread"] == 1
    assert host.runtime.running_turn is not None

    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET conversation_id='c' WHERE id='t'")
    conn.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 1

    writer = host.mutation(None, "global-count-stop")
    writer.commit()
    assert read_content(conn, query, current=True)[1]["counts"]["running_conversations"] == 0


def test_scoped_snapshot_is_fenced_by_real_source_authority(actual_binding: _ActualBinding) -> None:
    from planner.navigation.delivery_authority import DeliveryUnavailable
    from planner.navigation.views.delivery import ScopedDeliveryRuntime

    host = actual_binding
    host.binding.attach()
    conn = host.composition.connect()
    try:
        activate(conn)
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.execute("UPDATE tickets SET conversation_id='actual' WHERE id='t'")
        conn.execute("INSERT INTO conversation_events VALUES('actual',1,'message_to_owner','{}',0)")
        conn.commit()
        runtime = ScopedDeliveryRuntime(host.delivery)
        query = descriptor(selected="t")
        current, access = asyncio.run(runtime.snapshot(query))
        assert current["projection_version"] == 6
        assert current["mode"] == "current"
        assert current["content"]["counts"]["unread"] == 1
        captured = asyncio.run(runtime.capture())
        host.composition.coordinator.invalidate("publication_failed")
        with pytest.raises(DeliveryUnavailable):
            host.delivery.admission.validate(access)
        with pytest.raises(DeliveryUnavailable):
            asyncio.run(runtime.snapshot(query, captured=captured))
        degraded, _ = asyncio.run(runtime.snapshot(query))
        assert degraded["mode"] == "degraded"
        assert degraded["projection_version"] == 6
        assert degraded["authority"]["permit"] is None
        assert degraded["content"]["counts"]["running_conversations"] is None
        assert degraded["content"]["counts"]["unread"] == 1
    finally:
        conn.close()


def test_settled_records_stay_lean_until_an_admitted_full_consumer_requires_them(
    tmp_path: Path,
) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        for conversation in ("own", "current", "historical"):
            add_conversation(conn, conversation)
        conn.execute("BEGIN")
        for index in range(94):
            add_parent(conn, f"o{index:03}")
        conn.execute("UPDATE managers SET settled=1,recap=?", ("A nonempty recap. " * 300,))
        add_parent(conn, "active")
        conn.execute("INSERT INTO agents VALUES('manager_o000','own')")
        add_ticket(conn, "t", "o001")
        conn.execute("UPDATE tickets SET conversation_id='current' WHERE id='t'")
        conn.execute("INSERT INTO ticket_conversations VALUES('historical','t')")
        for conversation in ("own", "current", "historical"):
            conn.execute(
                "INSERT INTO conversation_events VALUES(?,1,'message_to_owner','{}',0)",
                (conversation,),
            )
        conn.commit()
        query = descriptor(limit=60)
        settled: ViewWindowRequest = {
            **query["windows"][0],
            "section": "settled",
            "owner": None,
            "child_filter": "all",
        }
        query["windows"] = [settled]
        # Exact unread counts are visible even for owners with no admitted addresses.
        query["windows"].extend(
            {
                **settled,
                "section": "unread",
                "owner": {"kind": "manager", "id": owner},
                "limit": 0,
            }
            for owner in ("o000", "o001", "o002")
        )
        _, lean = read_content(conn, query, current=True)
        expected_fields = {"title", "created_at", "updated_at", "settled", "conversation_id"}
        managers = [row for row in lean["records"] if row["kind"] == "manager"]
        assert len(managers) == 60
        assert all(set(row["value"]) == expected_fields for row in managers)
        assert not any(row["kind"] in ("ticket", "project", "address") for row in lean["records"])
        assert any(row["kind"] == "conversation" and row["id"] == "own" for row in lean["records"])
        assert lean["counts"]["settled"] == 94
        assert [window["total"] for window in lean["windows"]] == [94, 0, 2, 0]
        assert lean["counts"]["unread"] == 3
        page: ViewRequest = {
            **query,
            "windows": [{**settled, "anchor": lean["windows"][0]["next"]}],
        }
        _, tail = read_content(conn, page, current=True)
        assert len(tail["windows"][0]["members"]) == 34
        assert tail["windows"][0]["next"] is None
        assert tail["windows"][0]["total"] == 94
        assert {row["id"] for row in lean["windows"][0]["members"]}.isdisjoint(
            row["id"] for row in tail["windows"][0]["members"]
        )

        def full_ids(content: ViewContent) -> set[str]:
            # Dynamic inspection is intentional: the assertion distinguishes exact
            # full and lean variants without inventing absent full fields.
            return {
                row["id"]
                for row in content["records"]
                if row["kind"] == "manager" and "recap_reference" in row["value"]
            }

        selected = {**query, "selected": {"kind": "manager", "id": "o003"}}
        assert full_ids(read_content(conn, cast(ViewRequest, selected), current=True)[1]) == {
            "o003"
        }
        selected_ticket = {
            **query,
            "selected": {"kind": "ticket", "id": "t"},
            "selected_conversation_id": "historical",
        }
        _, selected_content = read_content(conn, cast(ViewRequest, selected_ticket), current=True)
        assert full_ids(selected_content) == {"o001"}
        assert any(row["id"] == "historical" for row in selected_content["records"])

        ticket_unread = cast(
            ViewRequest,
            {
                **query,
                "windows": [
                    settled,
                    {
                        **settled,
                        "section": "unread",
                        "owner": {"kind": "ticket", "id": "t"},
                        "limit": 1,
                    },
                ],
            },
        )
        _, unread_current = read_content(conn, ticket_unread, current=True)
        assert full_ids(unread_current) == {"o001"}
        assert unread_current["windows"][1]["total"] == 2
        ticket_unread["windows"][1]["anchor"] = unread_current["windows"][1]["next"]
        _, unread_historical = read_content(conn, ticket_unread, current=True)
        assert full_ids(unread_historical) == {"o001"}
        assert any(
            row["kind"] == "address" and row["value"]["conversation_id"] == "historical"
            for row in unread_historical["records"]
        )

        mixed: ViewRequest = {
            **query,
            "windows": [
                *query["windows"],
                {
                    **settled,
                    "section": "children",
                    "owner": {"kind": "manager", "id": "o001"},
                    "limit": 1,
                },
                {**settled, "section": "unread", "limit": 1},
                {**settled, "section": "rail_parents", "limit": 1},
            ],
        }
        _, promoted = read_content(conn, mixed, current=True)
        assert full_ids(promoted) == {"o000", "o001", "active"}
        assert len({(row["kind"], row["id"]) for row in promoted["records"]}) == len(
            promoted["records"]
        )
        assert any(row["kind"] == "project" for row in promoted["records"])
        _, reversed_content = read_content(
            conn,
            cast(ViewRequest, {**mixed, "windows": list(reversed(mixed["windows"]))}),
            current=True,
        )
        assert reversed_content["records"] == promoted["records"]
        assert {window["window_id"]: window for window in reversed_content["windows"]} == {
            window["window_id"]: window for window in promoted["windows"]
        }


def test_settled_promotion_charges_replacement_bytes_and_refuses_incomplete_dependencies(
    tmp_path: Path,
) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        add_conversation(conn, "own")
        conn.execute("BEGIN")
        add_parent(conn, "o")
        conn.execute("UPDATE managers SET settled=1,recap=?", ("R" * 1500,))
        conn.execute("INSERT INTO agents VALUES('manager_o','own')")
        conn.execute("INSERT INTO conversation_events VALUES('own',1,'message_to_owner','{}',0)")
        conn.commit()
        query = descriptor(limit=1)
        settled: ViewWindowRequest = {
            **query["windows"][0],
            "section": "settled",
            "owner": None,
            "child_filter": "all",
        }
        query["windows"] = [settled, {**settled, "section": "unread"}]
        _, complete = read_content(conn, query, current=True)
        record_bytes = sum(len(compact(row).encode()) for row in complete["records"])
        # The budget has room for replacement deltas, but not a second full row.
        maximum_bytes = max(len(compact(complete).encode()), (record_bytes * 4 + 2) // 3)
        _, exact = read_content(
            conn,
            query,
            current=True,
            config=replace(DEFAULT_CONFIG, maximum_records=5, maximum_bytes=maximum_bytes),
        )
        assert exact == complete
        for budget in (
            replace(DEFAULT_CONFIG, maximum_records=4),
            replace(DEFAULT_CONFIG, maximum_bytes=maximum_bytes - 200),
        ):
            _, refused = read_content(conn, query, current=True, config=budget)
            assert len(refused["windows"][0]["members"]) == 1
            assert refused["windows"][1]["members"] == []
            assert refused["windows"][1]["total"] == 1
            assert not any(row["kind"] in ("project", "address") for row in refused["records"])
            assert all(
                set(row["value"])
                == {"title", "created_at", "updated_at", "settled", "conversation_id"}
                for row in refused["records"]
                if row["kind"] == "manager"
            )


def test_scoped_request_refuses_the_retired_projection() -> None:
    query = descriptor(limit=0)
    with pytest.raises(ValueError, match="invalid view generation"):
        request({**query, "projection_version": 3})


def test_byte_budget_shrinks_pages_fairly_and_overlap_keeps_cursor_neighbors(
    tmp_path: Path,
) -> None:
    from planner.navigation.views.configuration import DEFAULT_CONFIG

    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        conn.execute("BEGIN")
        for index in range(100):
            add_ticket(conn, f"t{index:03}")
        conn.commit()
        query = descriptor(limit=60)
        # Both profiles share rows; distinct windows still reserve complete memberships.
        second: ViewWindowRequest = {**query["windows"][0], "group": "empty"}
        query["windows"].append(second)
        _, content = read_content(
            conn, query, current=True, config=replace(DEFAULT_CONFIG, maximum_records=15)
        )
        assert len(content["records"]) <= 15
        assert all(2 <= len(window["members"]) < 60 for window in content["windows"])
        assert all(window["total"] == 100 for window in content["windows"])
        for limit in (2, 3, 5):
            first_request = descriptor(limit=limit)
            _, first = read_content(conn, first_request, current=True)
            first_window = first["windows"][0]
            following = descriptor(limit=limit)
            following["windows"][0]["anchor"] = first_window["forward_overlap"]
            _, next_page = read_content(conn, following, current=True)
            next_window = next_page["windows"][0]
            old = {row["id"] for row in first_window["members"]}
            new = {row["id"] for row in next_window["members"]}
            assert old & new and old != new
            following["windows"][0]["anchor"] = next_window["backward_overlap"]
            following["windows"][0]["direction"] = "before"
            _, backward = read_content(conn, following, current=True)
            assert old & {row["id"] for row in backward["windows"][0]["members"]}


def test_cursor_domain_rebase_empty_edge_and_all_count_scopes(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        conn.execute("BEGIN")
        for index in range(90):
            add_ticket(conn, f"t{index:03}")
        conn.commit()
        query = descriptor(limit=60)
        _, first = read_content(conn, query, current=True)
        query["windows"][0]["anchor"] = first["windows"][0]["next"]
        _, degraded = read_content(conn, query, current=False)
        assert degraded["windows"][0]["request"]["anchor"] is None
        assert degraded["windows"][0]["members"][0]["id"] == "t000"
        # Rebuild changes the source domain. A retained position safely starts at
        # the new domain's first page instead of making every retry unavailable.
        conn.execute("BEGIN")
        conn.execute("UPDATE navigation_meta SET epoch='replacement'")
        rebuild(conn)
        conn.commit()
        _, rebased = read_content(conn, query, current=True)
        assert rebased["windows"][0]["request"]["anchor"] is None
        assert rebased["windows"][0]["members"][0]["id"] == "t000"
        query["windows"][0]["anchor"] = rebased["windows"][0]["next"]
        conn.execute("BEGIN")
        conn.execute("DELETE FROM tickets WHERE id >= 't060'")
        conn.commit()
        _, edge = read_content(conn, query, current=True)
        assert edge["windows"][0]["members"][0]["id"] == "t000"
        assert edge["windows"][0]["members"][-1]["id"] == "t059"
        assert edge["windows"][0]["next"] is None
        assert edge["windows"][0]["request"]["anchor"] is None
        # Ordinary documents need up to 138 distinct count identities.
        query = descriptor(limit=0)
        query["windows"] = [
            {**query["windows"][0], "owner": {"kind": "manager", "id": f"o{i}"}} for i in range(138)
        ]
        checked = request(query)
        _, counts = read_content(conn, checked, current=True)
        assert len(counts["windows"]) == 138
        assert len(counts["records"]) == 1


def test_mixed_review_window_preserves_order_count_and_unread_boundary(tmp_path: Path) -> None:
    """A bounded page must not put messages behind proposals or consume a later run."""
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        add_conversation(conn, "unread")
        conn.execute("BEGIN")
        for index in range(32):
            identity = f"decision-{index:02}"
            add_ticket(conn, identity)
            proposal = json.dumps(
                {
                    "field": "implementation",
                    "body": "Review me",
                    "proposed_by": "worker",
                    "created_at": index + 10,
                }
            )
            conn.execute("UPDATE tickets SET pending_proposal=? WHERE id=?", (proposal, identity))
        conn.execute("UPDATE tickets SET conversation_id='unread' WHERE id='decision-00'")
        conn.execute("INSERT INTO conversation_events VALUES('unread',1,'message_to_owner','{}',5)")
        conn.execute(
            "INSERT INTO conversation_events VALUES('unread',2,'message_to_owner','{}',25)"
        )
        conn.commit()
        query = descriptor()
        query["windows"] = [
            {**query["windows"][0], "section": "review", "owner": None, "child_filter": "all"}
        ]
        listed = _review_items(conn)
        assert len(listed) == 32
        assert listed[0]["messages"][0]["conversation_id"] == "unread"
        assert listed[0]["waiting_since"] == 5
        for current in (True, False):
            _, content = read_content(conn, query, current=current)
            window = content["windows"][0]
            assert window["total"] == content["counts"]["review"] == 32
            assert len(window["members"]) == 30
            assert window["members"][0] == {"kind": "ticket", "id": "decision-00"}
            query["windows"][0]["anchor"] = window["next"]
            _, tail = read_content(conn, query, current=current)
            assert [key["id"] for key in tail["windows"][0]["members"]] == [
                "decision-30",
                "decision-31",
            ]
            query["windows"][0]["anchor"] = None
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=1 WHERE conversation_id='unread'"
        )
        conn.commit()
        listed = _review_items(conn)
        card = next(item for item in listed if item["ticket_id"] == "decision-00")
        assert card["messages"][0]["waiting_since"] == 25
        assert card["waiting_since"] == 10
        assert listed[0] == card
        _, partial = read_content(conn, query, current=True)
        assert partial["counts"]["review"] == 32
        assert partial["windows"][0]["members"][0] == {"kind": "ticket", "id": "decision-00"}
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=2 WHERE conversation_id='unread'"
        )
        conn.commit()
        _, finished = read_content(conn, query, current=True)
        assert finished["counts"]["review"] == 32
        assert finished["counts"]["unread"] == 0


def test_review_canonical_address_transfers_and_retains_historical_conversation(
    tmp_path: Path,
) -> None:
    """Address replacement must preserve a single message item and its full owner context."""
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        add_conversation(conn, "old")
        add_conversation(conn, "new")
        conn.execute("BEGIN")
        add_ticket(conn, "a")
        add_ticket(conn, "b")
        conn.execute("UPDATE tickets SET conversation_id='old',recap='Owner recap'")
        conn.execute("INSERT INTO ticket_conversations VALUES('old','a')")
        conn.execute("INSERT INTO conversation_events VALUES('old',1,'message_to_owner','{}',1)")
        conn.commit()
        query = descriptor()
        query["windows"] = [
            {**query["windows"][0], "section": "review", "owner": None, "child_filter": "all"}
        ]

        def check(owner: str) -> None:
            items = _review_items(conn)
            assert len(items) == 1
            assert items[0]["ticket_id"] == owner
            assert items[0]["messages"][0]["recap"] == "Owner recap"
            for current in (True, False):
                _, content = read_content(conn, query, current=current)
                assert content["counts"]["review"] == content["counts"]["unread"] == 1
                assert content["windows"][0]["total"] == 1
                assert content["windows"][0]["members"] == [{"kind": "ticket", "id": owner}]
                assert any(
                    row["kind"] == "ticket" and row["id"] == owner for row in content["records"]
                )

        check("a")
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET conversation_id='new' WHERE id='a'")
        conn.commit()
        check("b")
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET conversation_id='new' WHERE id='b'")
        conn.commit()
        check("a")


def test_review_owner_state_reversals_preserve_unread_discovery(tmp_path: Path) -> None:
    """Review state gates must not consume messages or hide live children of settled Managers."""
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        for conversation in ("worker", "manager", "chief"):
            add_conversation(conn, conversation)
        conn.execute("BEGIN")
        add_parent(conn, "m")
        add_ticket(conn, "t", "m")
        conn.execute("UPDATE tickets SET conversation_id='worker' WHERE id='t'")
        conn.execute("INSERT INTO agents VALUES('manager_m','manager')")
        conn.execute("INSERT INTO agents VALUES('chief_of_staff','chief')")
        for conversation in ("worker", "manager", "chief"):
            conn.execute(
                "INSERT INTO conversation_events VALUES(?,1,'message_to_owner','{}',1)",
                (conversation,),
            )
        conn.commit()
        query = descriptor()
        query["windows"] = [
            {**query["windows"][0], "section": "review", "owner": None, "child_filter": "all"}
        ]

        def check(total: int, ticket: bool, manager: bool) -> None:
            items = _review_items(conn)
            assert len(items) == total
            assert any(item["review_item_type"] == "ticket" for item in items) is ticket
            assert any(item.get("owner_kind") == "manager" for item in items) is manager
            assert any(item.get("owner_kind") == "chief" for item in items)
            for current in (True, False):
                _, content = read_content(conn, query, current=current)
                assert content["counts"]["review"] == content["windows"][0]["total"] == total
                assert content["counts"]["unread"] == 3
            unread = {**query, "windows": [{**query["windows"][0], "section": "unread"}]}
            _, content = read_content(conn, unread, current=True)
            assert len(content["windows"][0]["members"]) == 3

        check(3, True, True)
        for statement, expected in (
            ("UPDATE managers SET settled=1 WHERE id='m'", (2, True, False)),
            ("UPDATE tickets SET is_backlogged=1 WHERE id='t'", (1, False, False)),
            ("UPDATE tickets SET is_backlogged=0 WHERE id='t'", (2, True, False)),
            ("UPDATE tickets SET stage='done' WHERE id='t'", (1, False, False)),
            ("UPDATE tickets SET stage='needs_implementation' WHERE id='t'", (2, True, False)),
            ("UPDATE managers SET settled=0 WHERE id='m'", (3, True, True)),
        ):
            conn.execute("BEGIN")
            conn.execute(statement)
            conn.commit()
            check(*expected)


def test_review_ticket_closure_tracks_historical_constituents_without_rank_change(
    tmp_path: Path,
) -> None:
    """A new historical unread conversation needs delivery when the oldest rank stays fixed."""
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        for conversation in ("old", "current"):
            add_conversation(conn, conversation)
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.execute("UPDATE tickets SET conversation_id='current' WHERE id='t'")
        conn.execute("INSERT INTO ticket_conversations VALUES('old','t')")
        conn.execute(
            "INSERT INTO conversation_events VALUES('current',1,'message_to_owner','{}',1)"
        )
        conn.commit()
        query = descriptor()
        query["windows"] = [
            {**query["windows"][0], "section": "review", "owner": None, "child_filter": "all"}
        ]
        _, before = read_content(conn, query, current=True)
        conn.execute("BEGIN")
        conn.execute("INSERT INTO conversation_events VALUES('old',1,'message_to_owner','{}',2)")
        conn.commit()
        _, after = read_content(conn, query, current=True)
        assert (
            before["windows"][0]["members"]
            == after["windows"][0]["members"]
            == [{"kind": "ticket", "id": "t"}]
        )
        assert after["counts"]["review"] == 1
        assert after["counts"]["unread"] == 2
        addresses = [row for row in after["records"] if row["kind"] == "address"]
        assert {row["value"]["conversation_id"] for row in addresses} == {"old", "current"}
        footprint = json.loads(
            conn.execute(
                "SELECT footprint FROM navigation_view_changes ORDER BY revision DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert section_key("review", None) in footprint["sections"]
        assert len(_review_items(conn)[0]["messages"]) == 2
        assert _review_items(conn)[0]["waiting_since"] == 1
        # Review seeds both historical and current address closures as one admission.
        # Each budget misses the complete card by one record or one byte of capacity.
        record_bytes = sum(len(compact(row).encode()) for row in after["records"])
        for budget in (
            replace(DEFAULT_CONFIG, maximum_records=len(after["records"]) - 1),
            replace(DEFAULT_CONFIG, maximum_bytes=(record_bytes * 4 - 5) // 3),
        ):
            _, refused = read_content(conn, query, current=True, config=budget)
            assert refused["windows"][0]["members"] == []
            assert refused["windows"][0]["total"] == refused["counts"]["review"] == 1
            assert [(row["kind"], row["id"]) for row in refused["records"]] == [("chief", "chief")]
        _, restored = read_content(conn, query, current=True)
        assert restored == after


def test_review_held_context_follows_excluded_canonical_owner_and_survives_read(
    tmp_path: Path,
) -> None:
    """An absent queue row cannot conceal a changed reply destination or unread outcome."""
    from planner.navigation.delivery_authority import DeliveryUnavailable

    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        for conversation in ("old", "new"):
            add_conversation(conn, conversation)
        conn.execute("BEGIN")
        add_ticket(conn, "a")
        add_ticket(conn, "b")
        conn.execute("UPDATE tickets SET conversation_id='old'")
        conn.execute("UPDATE tickets SET is_backlogged=1 WHERE id='b'")
        conn.execute("INSERT INTO ticket_conversations VALUES('old','a')")
        conn.execute("INSERT INTO conversation_events VALUES('old',1,'message_to_owner','{}',1)")
        conn.commit()
        query = descriptor()
        query["review_conversation_ids"] = ["old"]
        query["windows"] = [
            {**query["windows"][0], "section": "review", "owner": None, "child_filter": "all"}
        ]

        def context_owner(content: ViewContent) -> str:
            context = content["review_context"]
            assert len(context) == 1 and context[0]["conversation_id"] == "old"
            address = next(
                row
                for row in content["records"]
                if (row["kind"] == "address" and row["id"] == context[0]["canonical_address_id"])
            )
            assert address["value"]["conversation_id"] == "old"
            return str(address["value"]["owner_id"])

        _, before = read_content(conn, query, current=True)
        assert context_owner(before) == "a"
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET conversation_id='new' WHERE id='a'")
        conn.commit()
        _, excluded = read_content(conn, query, current=True)
        assert excluded["windows"][0]["members"] == []
        assert excluded["counts"]["review"] == 0
        assert excluded["counts"]["unread"] == 1
        assert context_owner(excluded) == "b"
        with pytest.raises(DeliveryUnavailable, match="exceeds its budget"):
            read_content(
                conn, query, current=True, config=replace(DEFAULT_CONFIG, maximum_records=1)
            )
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=1 WHERE conversation_id='old'"
        )
        conn.commit()
        _, cleared = read_content(conn, query, current=True)
        assert context_owner(cleared) == "b"
        assert cleared["counts"]["unread"] == 0
        conn.execute("BEGIN")
        conn.execute("DELETE FROM tickets WHERE id IN ('a','b')")
        conn.commit()
        _, deleted = read_content(conn, query, current=True)
        assert deleted["review_context"] == [
            {"conversation_id": "old", "canonical_address_id": None}
        ]
        assert not any(row["kind"] == "address" for row in deleted["records"])
        common = {
            "wire_version": 3,
            "projection_version": 6,
            "view_id": "view",
            "scope_generation": 1,
            "ordering_generation": "order",
            "authority": {},
            "coverage": {},
        }
        frame = difference(
            cast(ViewSnapshot, {**common, "content_version": digest(cleared), "content": cleared}),
            cast(ViewSnapshot, {**common, "content_version": digest(deleted), "content": deleted}),
        )
        assert frame["event"] == "patch"
        assert frame["review_context"] == deleted["review_context"]
        for ids in ([""], ["old", "old"], ["x" * 257], [str(i) for i in range(257)]):
            with pytest.raises(ValueError, match="invalid Review conversation context"):
                request({**query, "review_conversation_ids": ids})
        with pytest.raises(ValueError, match="exceeds row budget"):
            request(query, config=replace(DEFAULT_CONFIG, maximum_requested_rows=30))
