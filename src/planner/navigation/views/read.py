"""Indexed bounded reads. Request paths never scan canonical archive bodies."""

from __future__ import annotations

import base64
import json
import sqlite3
from typing import Any, cast, get_args

from planner.navigation.contracts import SCHEMA_VERSION, Continuity, NavigationRecord
from planner.navigation.delivery_authority import DeliveryUnavailable
from planner.navigation.review import canonical_review_address, review_message_items
from planner.navigation.views.configuration import DEFAULT_CONFIG, ViewConfig
from planner.navigation.views.contracts import (
    VIEW_PROJECTION_VERSION,
    ViewContent,
    ViewGroup,
    ViewRequest,
    ViewSection,
    ViewWindowRequest,
)
from planner.navigation.views.logic import compact, digest, project, section_key
from planner.navigation.views.ordering import ORDERING_GENERATION
from planner.navigation.views.projection import Key, record


def request(value: object, config: ViewConfig = DEFAULT_CONFIG) -> ViewRequest:
    if not isinstance(value, dict) or set(value) != {
        "projection_version",
        "scope_generation",
        "locale",
        "selected",
        "selected_conversation_id",
        "review_conversation_ids",
        "windows",
    }:
        raise ValueError("invalid view request fields")
    if (
        type(value["projection_version"]) is not int
        or value["projection_version"] != VIEW_PROJECTION_VERSION
        or type(value["scope_generation"]) is not int
        or not 0 <= value["scope_generation"] <= 2**53 - 1
    ):
        raise ValueError("invalid view generation")
    if value["locale"] not in config.locales:
        raise ValueError("unsupported navigation ordering locale")

    def owner(item: object) -> bool:
        return (
            item is None
            or isinstance(item, dict)
            and set(item) == {"kind", "id"}
            and item["kind"] in ("chief", "manager", "ticket", "goal")
            and isinstance(item["id"], str)
            and 0 < len(item["id"]) <= 256
            and (item["kind"] != "chief" or item["id"] == "chief")
        )

    if (
        not owner(value["selected"])
        or value["selected_conversation_id"] is not None
        and (
            not isinstance(value["selected_conversation_id"], str)
            or not 0 < len(value["selected_conversation_id"]) <= 256
        )
    ):
        raise ValueError("invalid selected owner")
    review_ids = value["review_conversation_ids"]
    if (
        not isinstance(review_ids, list)
        or len(review_ids) > config.maximum_requested_rows
        or any(not isinstance(item, str) or not 0 < len(item) <= 256 for item in review_ids)
        or len(set(review_ids)) != len(review_ids)
    ):
        raise ValueError("invalid Review conversation context")
    windows = value["windows"]
    if not isinstance(windows, list) or len(windows) > config.maximum_windows:
        raise ValueError("invalid view windows")
    seen: set[str] = set()
    total = len(review_ids)
    for window in windows:
        if not isinstance(window, dict) or set(window) != {
            "section",
            "owner",
            "group",
            "child_filter",
            "anchor",
            "direction",
            "limit",
        }:
            raise ValueError("invalid window fields")
        if (
            window["section"] not in get_args(ViewSection)
            or window["group"] not in get_args(ViewGroup)
            or window["child_filter"] not in ("all", "active", "backlogged", "done")
            or not owner(window["owner"])
        ):
            raise ValueError("invalid window membership")
        if (
            window["direction"] not in ("before", "after")
            or type(window["limit"]) is not int
            or not 0 <= window["limit"] <= config.maximum_window_rows
        ):
            raise ValueError("invalid window bounds")
        if window["anchor"] is not None and (
            not isinstance(window["anchor"], str) or len(window["anchor"]) > 8192
        ):
            raise ValueError("invalid window anchor")
        section = window["section"]
        if section in ("rail_tickets", "children") and (
            window["owner"] is None or window["owner"]["kind"] not in ("chief", "manager")
        ):
            raise ValueError("window requires a parent")
        if (
            section in ("rail_parents", "settled", "review", "goals")
            and window["owner"] is not None
        ):
            raise ValueError("global window has no parent")
        if (
            section not in ("children", "rail_tickets")
            and window["group"] != "all"
            or section != "children"
            and window["child_filter"] != "all"
        ):
            raise ValueError("unsupported window filter")
        identity = window_key(cast(ViewWindowRequest, window))
        if identity in seen:
            raise ValueError("duplicate view window")
        seen.add(identity)
        total += window["limit"]
    if total > config.maximum_requested_rows:
        raise ValueError("view request exceeds row budget")
    return cast(ViewRequest, value)


def window_key(window: ViewWindowRequest) -> str:
    owner = window["owner"]
    return section_key(
        window["section"],
        None if owner is None else (owner["kind"], owner["id"]),
        window["group"],
        window["child_filter"],
    )


def identity(value: ViewRequest) -> str:
    return digest(
        {**value, "scope_generation": 0, "windows": sorted(value["windows"], key=window_key)}
    )


def continuity(conn: sqlite3.Connection) -> Continuity:
    row = conn.execute("SELECT scope,schema_version,epoch,revision FROM navigation_meta").fetchone()
    meta = conn.execute(
        "SELECT projection_version,epoch,revision,ordering_generation FROM navigation_view_meta"
    ).fetchone()
    if (
        row is None
        or row[1] != SCHEMA_VERSION
        or meta is None
        or tuple(meta) != (1, row[2], row[3], ORDERING_GENERATION)
        or conn.execute("SELECT 1 FROM navigation_dirty LIMIT 1").fetchone()
    ):
        raise DeliveryUnavailable("scoped navigation projection is not activated")
    return {
        "scope": row[0],
        "schema_version": row[1],
        "semantics": "attention-knowledge-v9-review-messages",
        "epoch": row[2],
        "revision": str(row[3]),
    }


def _anchor(binding: str, row: tuple[Any, ...]) -> str:
    values = [row[0], row[1].hex(), row[2].hex(), row[3], row[4]]
    return base64.urlsafe_b64encode(compact([binding, values]).encode()).decode()


class StaleViewAnchor(ValueError):
    """Well-formed position from a different source/order/profile domain."""


def _decode(binding: str, value: str) -> tuple[Any, ...]:
    try:
        parsed = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if not isinstance(parsed, list) or len(parsed) != 2 or not isinstance(parsed[0], str):
            raise ValueError("invalid anchor envelope")
        row = parsed[1]
        if (
            not isinstance(row, list)
            or len(row) != 5
            or type(row[0]) is not int
            or not all(isinstance(v, str) for v in row[1:])
        ):
            raise ValueError("invalid anchor shape")
        result = (row[0], bytes.fromhex(row[1]), bytes.fromhex(row[2]), row[3], row[4])
        if _anchor(parsed[0], result) != value:
            raise ValueError("noncanonical anchor")
        if parsed[0] != binding:
            raise StaleViewAnchor("anchor source, ordering or membership changed")
        return result
    except StaleViewAnchor:
        raise
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ValueError("invalid navigation anchor") from exc


def read_content(
    conn: sqlite3.Connection,
    value: ViewRequest,
    *,
    current: bool,
    config: ViewConfig = DEFAULT_CONFIG,
) -> tuple[Continuity, ViewContent]:
    coverage = continuity(conn)
    profile = "current" if current else "durable"
    records: dict[Key, Any] = {}
    used = 0
    # Window requests, member keys and cursors are bounded too; reserve room before
    # admitting record closures instead of discovering metadata overflow afterward.
    record_bytes = config.maximum_bytes - min(128 * 1024, config.maximum_bytes // 4)

    def closure(key: Key, *, settled_only: bool, review: bool = False) -> dict[Key, Any]:
        pending = [(key, not settled_only)]
        if review and key[0] == "ticket":
            pending.extend(
                (("address", message["address_id"]), True)
                for message in review_message_items(conn, ticket_id=key[1])
            )
        found: dict[Key, Any] = {}
        while pending:
            item, full = pending.pop()
            existing = found.get(item, records.get(item))
            if existing is not None and (
                item[0] != "manager" or not full or "recap_reference" in existing["value"]
            ):
                continue
            raw = record(conn, item)
            if raw is None:
                raise DeliveryUnavailable("navigation view dependency is absent")
            row = project(
                cast(NavigationRecord, {"kind": item[0], "id": item[1], "value": raw}),
                config,
                settled_only=not full,
            )
            if row["kind"] == "conversation" and not current:
                row["value"]["attention"] = {
                    "current": None,
                    "durable": raw["attention"]["durable"],
                }
            found[item] = row
            if item[0] in ("ticket", "manager", "chief", "goal"):
                if raw["conversation_id"]:
                    pending.append((("conversation", raw["conversation_id"]), True))
                if raw.get("project_id") and (item[0] != "manager" or full):
                    pending.append((("project", raw["project_id"]), True))
                if item[0] == "ticket":
                    pending.append(
                        (
                            ("chief", "chief")
                            if raw["manager_kind"] == "chief"
                            else ("manager", raw["manager_id"]),
                            True,
                        )
                    )
            elif item[0] == "address":
                # The admitted owner supplies its current dependencies. An address's
                # parent_id alone is never a reason to demand a full manager.
                pending.extend(
                    (
                        ((raw["owner_kind"], raw["owner_id"]), True),
                        (("conversation", raw["conversation_id"]), True),
                    )
                )
        return found

    def admit(
        key: Key, *, required: bool = False, settled_only: bool = False, review: bool = False
    ) -> bool:
        nonlocal used
        additional = closure(key, settled_only=settled_only, review=review)
        # A promotion replaces one key. Charge its byte delta and new dependencies
        # together, and publish none of them when the complete closure does not fit.
        size = sum(
            len(compact(row).encode())
            - (len(compact(records[item]).encode()) if item in records else 0)
            for item, row in additional.items()
        )
        new_keys = additional.keys() - records.keys()
        if len(records) + len(new_keys) > config.maximum_records or used + size > record_bytes:
            if required:
                raise DeliveryUnavailable("selected navigation content exceeds its budget")
            return False
        records.update(additional)
        used += size
        return True

    admit(("chief", "chief"), required=True)
    selected = value["selected"]
    selected_exists = bool(
        selected
        and conn.execute(
            "SELECT 1 FROM navigation_view_index WHERE kind=? AND id=?",
            (selected["kind"], selected["id"]),
        ).fetchone()
    )
    if selected and selected_exists:
        admit((selected["kind"], selected["id"]), required=True)
    if value["selected_conversation_id"] and (selected is None or selected_exists):
        if selected is None:
            raise ValueError("old conversation requires its selected owner")
        address = conn.execute(
            "SELECT id FROM navigation_view_index WHERE conversation_id=? AND "
            "owner_kind=? AND owner_id=? AND kind='address' LIMIT 1",
            (value["selected_conversation_id"], selected["kind"], selected["id"]),
        ).fetchone()
        if address is None:
            raise DeliveryUnavailable("selected conversation is not addressed to this owner")
        admit(("address", address[0]), required=True)
    review_context = []
    for conversation_id in value["review_conversation_ids"]:
        address_id = canonical_review_address(conn, conversation_id)
        if address_id is not None:
            admit(("address", address_id), required=True)
        review_context.append(
            {
                "conversation_id": conversation_id,
                "canonical_address_id": address_id,
            }
        )
    windows: list[Any] = []
    pending_windows: list[Any] = []
    for window in value["windows"]:
        key = window_key(window)
        binding = digest([key, profile, value["locale"], ORDERING_GENERATION, coverage["epoch"]])
        try:
            anchor = None if window["anchor"] is None else _decode(binding, window["anchor"])
        except StaleViewAnchor:
            # Rebase only position. Membership, selected owner, source transaction and
            # delivery authority are all independently validated and remain unchanged.
            window = {**window, "anchor": None, "direction": "after"}
            anchor = None
        base = (key, profile, value["locale"])
        count = conn.execute(
            "SELECT value FROM navigation_view_counts WHERE section_key=? AND "
            "profile=? AND locale=?",
            base,
        ).fetchone()
        total = count[0] if count else 0
        args: tuple[Any, ...] = base
        sql = (
            "SELECT rank,title_key,id_key,kind,id FROM navigation_view_memberships "
            "WHERE section_key=? AND profile=? AND locale=?"
        )
        backward = window["direction"] == "before"
        if anchor:
            sql += (
                " AND (rank,title_key,id_key,kind,id) "
                + ("<" if backward else ">")
                + " (?,?,?,?,?)"
            )
            args += anchor
        order = " DESC" if backward else " ASC"
        sql += (
            " ORDER BY "
            + ",".join(name + order for name in ("rank", "title_key", "id_key", "kind", "id"))
            + " LIMIT ?"
        )
        rows = list(conn.execute(sql, (*args, window["limit"])))
        if not rows and total and window["limit"] and anchor is not None:
            # The anchored suffix/prefix vanished. Return the nearest reachable edge,
            # not an empty page whose only continuation repeats the same anchor.
            backward = not backward
            order = " DESC" if backward else " ASC"
            rows = list(
                conn.execute(
                    "SELECT rank,title_key,id_key,kind,id FROM navigation_view_memberships "
                    "WHERE section_key=? AND profile=? AND locale=? ORDER BY "
                    + ",".join(
                        name + order for name in ("rank", "title_key", "id_key", "kind", "id")
                    )
                    + " LIMIT ?",
                    (*base, window["limit"]),
                )
            )
            window = {**window, "anchor": None, "direction": "before" if backward else "after"}
        pending_windows.append((window, key, binding, base, total, backward, rows, []))

    # Preserve all count identities. Give each admitted surface one member before
    # filling pages. A surface whose dependencies do not fit stays count-only and
    # can be promoted to the front by an explicit viewport navigation intent.
    for item in pending_windows:
        rows, accepted = item[6], item[7]
        for row in rows[:1]:
            if admit(
                (row[3], row[4]),
                settled_only=item[0]["section"] == "settled",
                review=item[0]["section"] == "review",
            ):
                accepted.append(row)
    for position in range(1, config.maximum_window_rows):
        for item in pending_windows:
            rows, accepted = item[6], item[7]
            if position < len(rows) and len(accepted) == position:
                row = rows[position]
                if admit(
                    (row[3], row[4]),
                    settled_only=item[0]["section"] == "settled",
                    review=item[0]["section"] == "review",
                ):
                    accepted.append(row)
    for window, key, binding, base, total, backward, _rows, accepted in pending_windows:
        if backward:
            accepted.reverse()

        def continuation(
            row: tuple[Any, ...],
            direction: str,
            base: tuple[str, str, str] = base,
            binding: str = binding,
        ) -> str | None:
            exists = conn.execute(
                "SELECT 1 FROM navigation_view_memberships WHERE section_key=? AND "
                "profile=? AND locale=? AND (rank,title_key,id_key,kind,id) "
                + direction
                + " (?,?,?,?,?) LIMIT 1",
                (*base, *row),
            ).fetchone()
            return _anchor(binding, row) if exists else None

        windows.append(
            {
                "forward_overlap": continuation(accepted[max(0, len(accepted) // 2 - 1)], ">")
                if len(accepted) > 1 and continuation(accepted[-1], ">")
                else None,
                "backward_overlap": continuation(accepted[len(accepted) // 2], "<")
                if len(accepted) > 1 and continuation(accepted[0], "<")
                else None,
                "window_id": digest(key),
                "request": window,
                "members": [{"kind": r[3], "id": r[4]} for r in accepted],
                "total": total,
                "previous": continuation(accepted[0], "<") if accepted else None,
                "next": continuation(accepted[-1], ">") if accepted else None,
            }
        )
    counts = dict(conn.execute("SELECT name,value FROM navigation_view_globals"))
    if set(counts) != {"running_conversations", "out_claims", "review", "unread", "settled"}:
        raise DeliveryUnavailable("navigation aggregates are incomplete")
    if not current:
        counts["running_conversations"] = None
    content = cast(
        ViewContent,
        {
            "records": [records[key] for key in sorted(records)],
            "windows": windows,
            "counts": counts,
            "review_context": review_context,
        },
    )

    if len(compact(content).encode()) > config.maximum_bytes:
        raise DeliveryUnavailable("navigation content and metadata exceed the view budget")
    return coverage, content
