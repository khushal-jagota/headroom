"Managed Markdown links indexed at the source transaction, never on a page tick."

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from urllib.parse import parse_qs, quote, unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

_PARSER = MarkdownIt("gfm-like")
_UNSAFE = re.compile(r"%(?:25|2e|2f|5c)", re.IGNORECASE)
_BAD_PERCENT = re.compile(r"%(?![a-fA-F0-9]{2})")


def markdown_hrefs(source: str) -> Iterator[str]:
    def walk(tokens: list[Token]) -> Iterator[str]:
        for token in tokens:
            if token.type == "link_open":
                href = token.attrGet("href")
                if href:
                    yield str(href)
            elif token.type == "image":
                href = token.attrGet("src")
                if href:
                    yield str(href)
            if token.children:
                yield from walk(token.children)

    yield from walk(_PARSER.parse(source))


def canonical_origin(value: str, default_scheme: str = "") -> str | None:
    parsed = urlsplit(value)
    if parsed.hostname is None:
        return None
    scheme = parsed.scheme or default_scheme
    host = parsed.hostname.lower()
    authority = f"[{host}]" if ":" in host else host
    port = parsed.port
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        authority += f":{port}"
    return (scheme + ":" if scheme else "") + "//" + authority


def managed_href(href: str, origin: str) -> str | None:
    """Mirror the browser's managed preview/direct address admission."""
    hash_href = href[1:] if href.startswith("/#") else href
    if hash_href.startswith("#"):
        path, _, query = hash_href[1:].partition("?")
        if path.rstrip("/") != "/preview":
            return None
        params = parse_qs(query)
        source = params.get("source", [""])[0]
        kind = (
            "tickets"
            if source == "ticket"
            else "managers"
            if source in {"manager", "sprint-item"}
            else "goals"
            if source == "goal"
            else ""
        )
        owner_key = (
            "ticket" if kind == "tickets" else "item" if source == "sprint-item" else "manager"
        )
        if kind == "goals":
            owner_key = "goal"
        owner = params.get(owner_key, [""])[0]
        file_path = params.get("path", [""])[0]
    else:
        parsed = urlsplit(href)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return None
        if parsed.netloc and canonical_origin(href, urlsplit(origin).scheme) != canonical_origin(
            origin
        ):
            return None
        if _BAD_PERCENT.search(parsed.path):
            return None
        parts = parsed.path.split("/", 4)
        if (
            len(parts) != 5
            or parts[1] != "files"
            or parts[2] not in {"tickets", "managers", "sprint-items", "goals"}
        ):
            return None
        try:
            kind, owner, file_path = (
                parts[2],
                unquote(parts[3], errors="strict"),
                unquote(parts[4], errors="strict"),
            )
        except UnicodeError:
            return None
    kind = "managers" if kind == "sprint-items" else kind
    expected = {
        "tickets": r"t_[a-z0-9]+", "managers": r"si_[a-z0-9]+", "goals": r"goal_[a-z0-9]+"
    }.get(kind, "")
    if not kind or not re.fullmatch(expected, owner) or not file_path or file_path.startswith("/"):
        return None
    if (
        "\\" in file_path
        or _UNSAFE.search(file_path)
        or any(part in {"", ".", ".."} for part in file_path.split("/"))
    ):
        return None
    return f"/files/{kind}/{owner}/" + "/".join(
        quote(part, safe="~!*'()") for part in file_path.split("/")
    )


def install(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE ticket_artifact_links(ticket_id TEXT NOT NULL REFERENCES "
        "tickets(id) ON DELETE CASCADE,field_id TEXT NOT NULL,ordinal INTEGER "
        "NOT NULL,href TEXT NOT NULL,target TEXT NOT NULL,origin TEXT,stem TEXT"
        " NOT NULL,parent TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY "
        "KEY(ticket_id,field_id,ordinal))"
    )
    conn.execute(
        "CREATE TABLE ticket_artifact_link_dirty(ticket_id TEXT NOT "
        "NULL,field_id TEXT NOT NULL,PRIMARY KEY(ticket_id,field_id))"
    )
    for operation in ("INSERT", "UPDATE"):
        condition = (
            ""
            if operation == "INSERT"
            else (
                " WHEN OLD.field_values IS NOT NEW.field_values OR OLD.pending_proposal"
                " IS NOT NEW.pending_proposal"
            )
        )
        changed = (
            ""
            if operation == "INSERT"
            else " WHERE json_extract(OLD.field_values,'$.' || json_quote(j.key)) IS NOT j.value"
        )
        proposal_changed = (
            "1" if operation == "INSERT" else "OLD.pending_proposal IS NOT NEW.pending_proposal"
        )
        conn.execute(
            f"CREATE TRIGGER artifact_links_{operation.lower()} AFTER {operation} "
            f"ON tickets{condition} BEGIN "
            "INSERT OR IGNORE INTO ticket_artifact_link_dirty SELECT NEW.id,j.key "
            "FROM json_each(NEW.field_values) j"
            f"{changed}; INSERT OR IGNORE INTO ticket_artifact_link_dirty "
            f"SELECT NEW.id,'@proposal' WHERE {proposal_changed}; END"
        )
    conn.execute(
        "CREATE TRIGGER artifact_links_deleted_fields AFTER UPDATE OF "
        "field_values ON tickets BEGIN INSERT OR IGNORE INTO "
        "ticket_artifact_link_dirty SELECT NEW.id,j.key FROM "
        "json_each(OLD.field_values) j WHERE json_type(NEW.field_values,'$.' ||"
        " json_quote(j.key)) IS NULL; END"
    )
    conn.execute(
        "INSERT INTO ticket_artifact_link_dirty SELECT t.id,j.key FROM tickets "
        "t,json_each(t.field_values) j"
    )
    conn.execute("INSERT INTO ticket_artifact_link_dirty SELECT id,'@proposal' FROM tickets")
    finalize(conn)


def finalize(conn: sqlite3.Connection, /) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='ticket_artifact_link_dirty'"
        ).fetchone()
        is None
    ):
        return
    while True:
        rows = conn.execute(
            "SELECT ticket_id,field_id FROM ticket_artifact_link_dirty LIMIT 64"
        ).fetchall()
        if not rows:
            return
        for ticket_id, field in rows:
            if field == "@proposal":
                row = conn.execute(
                    "SELECT json_extract(pending_proposal,'$.body') FROM tickets WHERE id=?",
                    (ticket_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT json_extract(field_values,?) FROM tickets WHERE id=?",
                    ("$." + json.dumps(field), ticket_id),
                ).fetchone()
            values: list[tuple[str, str, str | None, str, str, str]] = []
            if row and row[0]:
                for href in markdown_hrefs(str(row[0])):
                    try:
                        origin = canonical_origin(href)
                        target = managed_href(href, origin or "http://planner.local")
                    except (ValueError, UnicodeError):
                        continue
                    if target:
                        path = unquote(target).split("/", 4)[4]
                        file = path.rsplit("/", 1)[-1]
                        stem = file.rsplit(".", 1)[0] if file.rfind(".") > 0 else file
                        parent = path.rsplit("/", 2)[-2] if "/" in path else ""
                        kind = file.rsplit(".", 1)[-1] if file.rfind(".") > 0 else "file"
                        values.append((href, target, origin, stem, parent, kind))
            previous = [
                tuple(value)
                for value in conn.execute(
                    (
                        "SELECT href,target,origin,stem,parent,kind FROM ticket_artifact_links "
                        "WHERE ticket_id=? AND field_id=? ORDER BY ordinal"
                    ),
                    (ticket_id, field),
                )
            ]
            if previous != values:
                conn.execute(
                    "DELETE FROM ticket_artifact_links WHERE ticket_id=? AND field_id=?",
                    (ticket_id, field),
                )
                conn.executemany(
                    "INSERT INTO ticket_artifact_links VALUES (?,?,?,?,?,?,?,?,?)",
                    ((ticket_id, field, index, *value) for index, value in enumerate(values)),
                )
                conn.execute(
                    (
                        "INSERT INTO resource_source_versions VALUES "
                        "('ticket-artifact-links',?,1) ON CONFLICT(source_kind,source_id) DO "
                        "UPDATE SET revision=revision+1"
                    ),
                    (ticket_id,),
                )
            conn.execute(
                "DELETE FROM ticket_artifact_link_dirty WHERE ticket_id=? AND field_id=?",
                (ticket_id, field),
            )
