"""Scoped view risks reuse the canonical navigation fixture, without a new host."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
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
            "projection_version": 4,
            "scope_generation": 1,
            "locale": "en-US",
            "selected": None if selected is None else {"kind": "ticket", "id": selected},
            "selected_conversation_id": None,
            "windows": [window],
        }
    )


def activate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    prepare(conn)
    rebuild(conn)
    conn.commit()


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


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_locale_order_matches_manager_children_and_sidebar_tickets(
    tmp_path: Path, locale: str
) -> None:
    with current_navigation_database(tmp_path / "db") as conn:
        activate(conn)
        conn.execute("BEGIN")
        add_parent(conn, "manager")
        # Match the frontend mirror corpus, including base-equivalent titles
        # whose identifiers themselves need locale-sensitive comparison.
        for identity, title, updated_at in (
            ("t_zulu", "Zulu", 10),
            ("t_z", "echo", 10),
            ("t_latest", "Latest", 20),
            ("t_å", "Écho", 10),
            ("t_aland", "Åland", 10),
        ):
            add_ticket(conn, identity, "manager")
            conn.execute(
                "UPDATE tickets SET title=?,updated_at=? WHERE id=?",
                (title, updated_at, identity),
            )
        conn.commit()

        query = descriptor()
        query["locale"] = locale
        manager_window: ViewWindowRequest = {
            **query["windows"][0],
            "owner": {"kind": "manager", "id": "manager"},
            "group": "empty",
        }
        query["windows"] = [
            manager_window,
            {**manager_window, "section": "rail_tickets", "child_filter": "all"},
        ]
        _, content = read_content(conn, request(query), current=True)
        expected = ["t_latest", "t_aland", "t_å", "t_z", "t_zulu"]
        assert [row["id"] for row in content["windows"][0]["members"]] == expected
        assert [row["id"] for row in content["windows"][1]["members"]] == expected


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
            "projection_version": 4,
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
        assert current["projection_version"] == 4
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
        assert degraded["projection_version"] == 4
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
