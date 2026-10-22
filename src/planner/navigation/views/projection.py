"""Incremental indexes and aggregates in the canonical navigation transaction."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from planner.navigation.contracts import SCHEMA_VERSION, NavigationRecord, RecordKey
from planner.navigation.views.configuration import DEFAULT_CONFIG, ViewConfig
from planner.navigation.views.logic import GROUPS, compact, digest, group, project, section_key
from planner.navigation.views.ordering import ORDERING_GENERATION, sort_key
from planner.navigation.views.schema import prepared
from planner.tickets.logic.review import eligible_for_owner_review

Key = tuple[str, str]
Member = tuple[str, str, str, str, str, int, bytes, bytes]


def record(conn: sqlite3.Connection, key: Key) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM navigation_records WHERE kind=? AND id=?", key).fetchone()
    return None if row is None else json.loads(row[0])


def _attention(conn: sqlite3.Connection, conversation: str | None) -> dict[str, Any] | None:
    value = record(conn, ("conversation", conversation)) if conversation else None
    return value["attention"] if value else None


def _metadata(
    conn: sqlite3.Connection, key: Key, value: dict[str, Any], content_hash: str
) -> tuple[Any, ...]:
    kind, identity = key
    conversation = value.get("conversation_id")
    parent = (
        ("chief", "chief")
        if kind == "ticket" and value["manager_kind"] == "chief"
        else (
            ("manager", value["manager_id"])
            if kind == "ticket"
            else (value.get("parent_kind"), value.get("parent_id"))
        )
    )
    attention = value["attention"] if kind == "conversation" else _attention(conn, conversation)
    current = attention and attention["current"]
    review = (
        kind == "ticket"
        and eligible_for_owner_review(
            ticket_status=value["ticket_status"],
            is_backlogged=value["is_backlogged"],
            holder_is_owner=value["ceiling_holder"] == {"kind": "owner", "id": "owner"},
            has_gating_field=value["gating_field"] is not None,
            has_proposal=all(
                value[name] is not None for name in ("proposal_field", "proposal_created_at")
            ),
        )
    )
    unread = (
        kind == "address"
        and bool(attention and attention["durable"]["message_for_owner"])
        and record(conn, (value["owner_kind"], value["owner_id"])) is not None
    )
    return (
        *key,
        content_hash,
        conversation,
        *parent,
        value.get("owner_kind"),
        value.get("owner_id"),
        value.get("project_id"),
        int(bool(current and current["signal"]["working"])),
        int(bool(value.get("worker_step_claim_out"))),
        int(review),
        int(unread),
        int(bool(value.get("settled"))),
    )


def _members(
    conn: sqlite3.Connection,
    key: Key,
    value: dict[str, Any],
    metadata: tuple[Any, ...],
    config: ViewConfig,
) -> set[Member]:
    kind, identity = key
    result: set[Member] = set()
    parent = (str(metadata[4]), str(metadata[5]))
    attention = _attention(conn, value.get("conversation_id"))
    for profile in ("current", "durable"):
        signal = (
            None
            if attention is None
            else attention["current"]
            if profile == "current"
            else {
                "signal": {
                    "working": False,
                    "message_for_owner": attention["durable"]["message_for_owner"],
                },
                "awaiting_answer": False,
                "last_turn_failed": attention["durable"]["last_turn_failed"],
            }
        )
        for locale in config.locales:
            title = sort_key(str(value.get("title", "")), locale)
            identity_key = sort_key(identity, locale, base=False)

            def add(
                section: str,
                owner: Key | None,
                grouping: str = "all",
                child_filter: str = "all",
                *,
                rank: int = 0,
                title_key: bytes = title,
                id_key: bytes = identity_key,
                profile: str = profile,
                locale: str = locale,
            ) -> None:
                result.add(
                    (
                        section_key(section, owner, grouping, child_filter),
                        profile,
                        locale,
                        kind,
                        identity,
                        rank,
                        title_key,
                        id_key,
                    )
                )

            if kind == "manager":
                add(
                    "settled" if value["settled"] else "rail_parents",
                    None,
                    rank=int(value["created_at"]),
                    title_key=b"",
                )
            elif kind == "ticket":
                child_filter = (
                    "done"
                    if value["stage"] == "done"
                    else "backlogged"
                    if value["is_backlogged"]
                    else "active"
                )
                child_group = group(value, signal, rail=True)
                rank = (
                    int(value["created_at"]) if parent[0] == "chief" else -int(value["updated_at"])
                )
                child_title = b"" if parent[0] == "chief" else title
                child_id = identity.encode() if parent[0] == "chief" else identity_key
                # Chief excludes non-done terminal stages, as its existing API page does.
                if parent[0] != "chief" or child_filter == "done" or not value["terminal"]:
                    for filtering in ("all", child_filter):
                        for grouping in ("all", child_group):
                            add(
                                "children",
                                parent,
                                grouping,
                                filtering,
                                rank=(
                                    GROUPS.index(child_group)
                                    if parent[0] != "chief"
                                    and filtering == "active"
                                    and grouping == "all"
                                    else rank
                                ),
                                title_key=(
                                    (-int(value["updated_at"]) + 2**63).to_bytes(8, "big") + title
                                    if parent[0] != "chief"
                                    and filtering == "active"
                                    and grouping == "all"
                                    else child_title
                                ),
                                id_key=child_id,
                            )
                parent_value = record(conn, parent)
                eligible = (
                    not value["is_backlogged"]
                    and not value["blocked"]
                    and value["ticket_status"] != "blocked"
                    and value["stage"] != "done"
                    and not (parent_value or {}).get("settled", False)
                )
                if eligible:
                    rail_group = group(value, signal, rail=True)
                    # All-group windows retain group order, then the group's existing order.
                    for grouping in ("all", rail_group):
                        rank = GROUPS.index(rail_group) if grouping == "all" else 0
                        ordered_title = (-int(value["updated_at"]) + 2**63).to_bytes(
                            8, "big"
                        ) + title
                        add("rail_tickets", parent, grouping, rank=rank, title_key=ordered_title)
                if metadata[11]:
                    add(
                        "review",
                        None,
                        rank=int(value["proposal_created_at"]),
                        title_key=b"",
                        id_key=identity.encode(),
                    )
            elif kind == "address" and metadata[12]:
                # One canonical address per owner/conversation. Preserve current-first order.
                add("unread", None, rank=-int(value["current"]), title_key=b"")
                owner = (str(value["owner_kind"]), str(value["owner_id"]))
                if owner[0] == "ticket":
                    add("unread", owner, rank=-int(value["current"]), title_key=b"")
                owner_value = record(conn, owner) or {}
                if (
                    value["parent_kind"] is not None
                    and parent != owner
                    and not owner_value.get("is_backlogged", False)
                ):
                    add("unread", parent, rank=-int(value["current"]), title_key=b"")
    return result


def _delta(conn: sqlite3.Connection, table: str, keys: tuple[Any, ...], amount: int) -> None:
    if not amount:
        return
    if table == "navigation_view_globals":
        conn.execute(
            "INSERT INTO navigation_view_globals VALUES(?,?) ON CONFLICT(name) DO "
            "UPDATE SET value=value+excluded.value",
            (*keys, max(0, amount)),
        ) if amount > 0 else conn.execute(
            "UPDATE navigation_view_globals SET value=value+? WHERE name=?", (amount, *keys)
        )
    else:
        conn.execute(
            "INSERT INTO navigation_view_counts VALUES(?,?,?,?) ON "
            "CONFLICT(section_key,profile,locale) DO UPDATE SET "
            "value=value+excluded.value",
            (*keys, amount),
        ) if amount > 0 else conn.execute(
            "UPDATE navigation_view_counts SET value=value+? WHERE section_key=? "
            "AND profile=? AND locale=?",
            (amount, *keys),
        )


def update(
    conn: sqlite3.Connection,
    upserts: list[NavigationRecord],
    deletes: list[RecordKey],
    config: ViewConfig = DEFAULT_CONFIG,
    *,
    initializing: bool = False,
) -> None:
    if not prepared(conn):
        return
    if not conn.in_transaction:
        raise RuntimeError("view projection requires the source transaction")
    epoch, revision = conn.execute("SELECT epoch,revision FROM navigation_meta").fetchone()
    meta = conn.execute("SELECT epoch,ordering_generation FROM navigation_view_meta").fetchone()
    if not initializing and (meta is None or tuple(meta) != (epoch, ORDERING_GENERATION)):
        return  # Physical preparation cannot silently activate a view.
    changed: set[Key] = {(r["kind"], r["id"]) for r in deletes if r["kind"] != "totals"}
    for item in upserts:
        if item["kind"] == "totals":
            continue
        key: Key = (item["kind"], item["id"])
        old = conn.execute(
            "SELECT content_hash FROM navigation_view_index WHERE kind=? AND id=?", key
        ).fetchone()
        if old is None or old[0] != digest(project(item, config)):
            changed.add(key)
    affected = set(changed)
    for kind, identity in changed:
        if kind == "conversation":
            affected.update(
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT kind,id FROM navigation_view_index WHERE conversation_id=?", (identity,)
                )
            )
        if kind in ("ticket", "manager", "chief"):
            affected.update(
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT kind,id FROM navigation_view_index WHERE owner_kind=? AND owner_id=?",
                    (kind, identity),
                )
            )
        if kind == "manager":
            affected.update(
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT kind,id FROM navigation_view_index WHERE "
                    "parent_kind='manager' AND parent_id=?",
                    (identity,),
                )
            )
    sections: set[str] = set()
    pointer_ids: set[str] = {identity for kind, identity in changed if kind == "conversation"}
    globals_changed = False
    for key in sorted(affected):
        old = conn.execute(
            "SELECT * FROM navigation_view_index WHERE kind=? AND id=?", key
        ).fetchone()
        value = record(conn, key)
        new = (
            None
            if value is None
            else _metadata(
                conn,
                key,
                value,
                digest(
                    project(
                        cast(NavigationRecord, {"kind": key[0], "id": key[1], "value": value}),
                        config,
                    )
                ),
            )
        )
        for candidate in (old, new):
            if (
                candidate
                and candidate[0] in ("ticket", "manager", "chief", "address")
                and candidate[3]
            ):
                pointer_ids.add(candidate[3])
        old_members = {
            tuple(row)
            for row in conn.execute(
                "SELECT * FROM navigation_view_memberships WHERE kind=? AND id=?", key
            )
        }
        new_members = set() if new is None else _members(conn, key, value, new, config)  # type: ignore[arg-type]
        for member in old_members - new_members:
            conn.execute(
                "DELETE FROM navigation_view_memberships WHERE section_key=? AND "
                "profile=? AND locale=? AND kind=? AND id=?",
                member[:5],
            )
            _delta(conn, "navigation_view_counts", member[:3], -1)
            sections.add(member[0])
        for member in new_members - old_members:
            conn.execute(
                "INSERT OR REPLACE INTO navigation_view_memberships VALUES(?,?,?,?,?,?,?,?)", member
            )
            _delta(conn, "navigation_view_counts", member[:3], 1)
            sections.add(member[0])
        for name, column in (("out_claims", 10), ("review", 11), ("settled", 13)):
            difference = (new[column] if new else 0) - (old[column] if old else 0)
            _delta(conn, "navigation_view_globals", (name,), difference)
            globals_changed |= difference != 0
        if new:
            conn.execute(
                "INSERT OR REPLACE INTO navigation_view_index VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                new,
            )
        else:
            conn.execute("DELETE FROM navigation_view_index WHERE kind=? AND id=?", key)
    for identity in pointer_ids:
        old_unread = conn.execute(
            "SELECT reference_count FROM navigation_view_unread_conversations "
            "WHERE conversation_id=?",
            (identity,),
        ).fetchone()
        unread = conn.execute(
            "SELECT count(*) FROM navigation_view_index WHERE conversation_id=? "
            "AND kind='address' AND unread=1",
            (identity,),
        ).fetchone()[0]
        unread_delta = int(bool(unread)) - int(bool(old_unread))
        _delta(conn, "navigation_view_globals", ("unread",), unread_delta)
        globals_changed |= unread_delta != 0
        if unread:
            conn.execute(
                "INSERT OR REPLACE INTO navigation_view_unread_conversations VALUES(?,?)",
                (identity, unread),
            )
        else:
            conn.execute(
                "DELETE FROM navigation_view_unread_conversations WHERE conversation_id=?",
                (identity,),
            )
        old = conn.execute(
            "SELECT reference_count,working FROM "
            "navigation_view_current_conversations WHERE conversation_id=?",
            (identity,),
        ).fetchone()
        references = conn.execute(
            "SELECT count(*) FROM navigation_view_index WHERE conversation_id=? "
            "AND kind IN ('chief','ticket','manager')",
            (identity,),
        ).fetchone()[0]
        current = conn.execute(
            "SELECT working FROM navigation_view_index WHERE kind='conversation' AND id=?",
            (identity,),
        ).fetchone()
        working = int(bool(current and current[0]))
        difference = (working if references else 0) - (old[1] if old else 0)
        _delta(conn, "navigation_view_globals", ("running_conversations",), difference)
        globals_changed |= difference != 0
        if references:
            conn.execute(
                "INSERT OR REPLACE INTO navigation_view_current_conversations VALUES(?,?,?)",
                (identity, references, working),
            )
        else:
            conn.execute(
                "DELETE FROM navigation_view_current_conversations WHERE conversation_id=?",
                (identity,),
            )
    conn.execute(
        "INSERT OR REPLACE INTO navigation_view_changes VALUES(?,?)",
        (
            revision,
            compact(
                {"keys": sorted(changed), "sections": sorted(sections), "globals": globals_changed}
            ),
        ),
    )
    conn.execute(
        "DELETE FROM navigation_view_changes WHERE revision<?",
        (max(0, revision - config.retained_revisions),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO navigation_view_meta VALUES(1,1,?,?,?)",
        (epoch, revision, ORDERING_GENERATION),
    )


def rebuild(conn: sqlite3.Connection, config: ViewConfig = DEFAULT_CONFIG) -> None:
    if not prepared(conn):
        return
    if conn.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] != SCHEMA_VERSION:
        return
    if not conn.in_transaction:
        raise RuntimeError("view activation requires the source transaction")
    for suffix in (
        "meta",
        "index",
        "memberships",
        "counts",
        "current_conversations",
        "unread_conversations",
        "globals",
        "changes",
    ):
        conn.execute("DELETE FROM navigation_view_" + suffix)
    for name in ("running_conversations", "out_claims", "review", "unread", "settled"):
        conn.execute("INSERT INTO navigation_view_globals VALUES(?,0)", (name,))
    records = [
        cast(NavigationRecord, {"kind": kind, "id": identity, "value": json.loads(value)})
        for kind, identity, value in conn.execute(
            "SELECT kind,id,value FROM navigation_records WHERE kind!='totals'"
        )
    ]
    update(conn, records, [], config, initializing=True)
