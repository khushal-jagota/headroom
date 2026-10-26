"""Bounded artifact windows with selected-owner filesystem reconciliation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

import anyio.to_thread
from fastapi import APIRouter, Request

from planner.core import authority
from planner.core.authctx import request_context
from planner.core.authority import require_above_or_self
from planner.core.errors import ErrorCode, PlannerError
from planner.files.catalog.config import (
    INVENTORY_CACHE_MAX_BYTES,
    INVENTORY_CACHE_OWNERS,
    INVENTORY_REFRESH_SECONDS,
    PAGE_MAX_BYTES,
    PAGE_SIZE,
)
from planner.files.catalog.links import canonical_origin
from planner.files.catalog_contracts import (
    ArtifactCatalogEntry,
    ArtifactDirectoryPage,
    ArtifactOwnerKind,
)
from planner.files.contracts import ArtifactEntry
from planner.files.logic.listing import fold_directory
from planner.files.logic.paths import (
    _validate_relative_path,
    _validate_safe_id,
    manager_files_root,
    ticket_files_root,
)
from planner.resource_versions.data import source_revision
from planner.worker_types.configuration import configured_worker_type_registry

router = APIRouter()


@dataclass
class Inventory:
    checked_at: float
    directories: dict[str, list[ArtifactCatalogEntry]]
    seen: set[str]
    revision: str
    bytes: int


_cache: OrderedDict[str, Inventory] = OrderedDict()
_guard = threading.Lock()


def _inventory(root: Path, prefix: str) -> Inventory:
    cache_key = str(root)
    with _guard:
        existing = _cache.get(cache_key)
        if existing and time.monotonic() - existing.checked_at < INVENTORY_REFRESH_SECONDS:
            _cache.move_to_end(cache_key)
            return existing
        directories: dict[str, list[ArtifactCatalogEntry]] = {}
        seen: set[str] = set()

        def visit(entries: tuple[ArtifactEntry, ...], directory: str) -> None:
            rows: list[ArtifactCatalogEntry] = []
            for item in entries:
                path = f"{directory}/{item.name}" if directory else item.name
                href = (
                    prefix + "/".join(quote(part, safe="~!*'()") for part in item.opens.split("/"))
                    if item.opens
                    else None
                )
                if href:
                    seen.add(href)
                rows.append(
                    {
                        "key": "managed:" + path,
                        "path": item.opens or path,
                        "label": item.name.rsplit(".", 1)[0]
                        if item.name.rfind(".") > 0
                        else item.name,
                        "kind": item.opens.rsplit(".", 1)[-1]
                        if item.opens and "." in item.opens.rsplit("/", 1)[-1]
                        else "file"
                        if item.opens
                        else str(len(item.children)),
                        "href": href,
                        "directory": path if item.children else None,
                        "child_count": len(item.children),
                    }
                )
                if item.children:
                    visit(item.children, path)
            directories[directory] = rows

        entries = (
            () if any(path.is_symlink() for path in (root, *root.parents)) else fold_directory(root)
        )
        visit(entries, "")
        encoded = json.dumps(directories, ensure_ascii=False, sort_keys=True).encode()
        inventory = Inventory(
            time.monotonic(), directories, seen, hashlib.sha256(encoded).hexdigest(), len(encoded)
        )
        _cache.pop(cache_key, None)
        if inventory.bytes <= INVENTORY_CACHE_MAX_BYTES:
            _cache[cache_key] = inventory
            while (
                len(_cache) > INVENTORY_CACHE_OWNERS
                or sum(item.bytes for item in _cache.values()) > INVENTORY_CACHE_MAX_BYTES
            ):
                _cache.popitem(last=False)
        return inventory


def _linked(
    conn: sqlite3.Connection, ticket_id: str, origin: str, seen: set[str], offset: int, limit: int
) -> tuple[int, list[ArtifactCatalogEntry]]:
    row = conn.execute("SELECT worker_type FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {})
    fields = (
        "@proposal",
        *reversed(configured_worker_type_registry().require(str(row[0])).field_ids()),
    )
    ranks = {field: index for index, field in enumerate(fields)}
    conn.create_function("artifact_field_rank", 1, lambda field: ranks.get(field, len(ranks)))
    conn.create_function("artifact_visible", 1, lambda target: int(target not in seen))
    conn.create_function("artifact_known_field", 1, lambda field: int(field in ranks))
    conn.create_function("artifact_stem_key", 1, lambda stem: str(stem).lower())
    conn.create_function(
        "artifact_same_origin",
        1,
        lambda value: int(
            canonical_origin(str(value), urlsplit(origin).scheme) == canonical_origin(origin)
        ),
    )
    cte = """WITH ranked AS (
        SELECT *,row_number() OVER(
            PARTITION BY target ORDER BY artifact_field_rank(field_id),ordinal) duplicate
        FROM ticket_artifact_links WHERE ticket_id=? AND artifact_known_field(field_id)
        AND (origin IS NULL OR artifact_same_origin(origin))
    ), unique_links AS (
        SELECT *,count(*) OVER(PARTITION BY artifact_stem_key(stem)) collisions
        FROM ranked WHERE duplicate=1
    ), visible AS (SELECT * FROM unique_links WHERE artifact_visible(target)) """
    args = (ticket_id,)
    count = int(conn.execute(cte + "SELECT count(*) FROM visible", args).fetchone()[0])
    result: list[ArtifactCatalogEntry] = []
    for row in conn.execute(
        cte
        + ("SELECT * FROM visible ORDER BY artifact_field_rank(field_id),ordinal LIMIT ? OFFSET ?"),
        (*args, limit, offset),
    ):
        target = str(row["target"])
        result.append(
            {
                "key": "linked:" + target,
                "path": unquote(target).split("/", 4)[4],
                "label": f"{row['parent']} / {row['stem']}"
                if row["collisions"] > 1 and row["parent"]
                else str(row["stem"]),
                "kind": str(row["kind"]),
                "href": target,
                "directory": None,
                "child_count": 0,
            }
        )
    return count, result


def _read(
    request: Request,
    kind: ArtifactOwnerKind,
    owner_id: str,
    directory: str,
    offset: int,
    limit: int,
) -> ArtifactDirectoryPage:
    try:
        _validate_safe_id(owner_id, "owner")
        if directory:
            _validate_relative_path(directory)
    except ValueError as exc:
        raise PlannerError(ErrorCode.validation, "invalid artifact directory", {}) from exc
    if offset < 0 or not 1 <= limit <= PAGE_SIZE:
        raise PlannerError(ErrorCode.validation, "invalid artifact page", {})
    conn = request.app.state.conn_factory()
    try:
        conn.execute("BEGIN")
        if kind == "manager":
            require_above_or_self(
                conn, request_context(request).principal, authority.manager(owner_id)
            )
        else:
            if conn.execute("SELECT 1 FROM tickets WHERE id=?", (owner_id,)).fetchone() is None:
                raise PlannerError(ErrorCode.not_found, "ticket not found", {})
        db_path = request.app.state.config.db_path
        root = (
            ticket_files_root(db_path) if kind == "ticket" else manager_files_root(db_path)
        ) / owner_id
        prefix = f"/files/{'tickets' if kind == 'ticket' else 'managers'}/{owner_id}/"
        inventory = _inventory(root, prefix)
        rows = inventory.directories.get(directory, [])
        total = len(rows)
        window = rows[offset : offset + limit]
        link_revision = "0"
        if not directory and kind == "ticket":
            link_count, linked = _linked(
                conn,
                owner_id,
                str(request.base_url).rstrip("/"),
                inventory.seen,
                max(0, offset - len(rows)),
                max(0, limit - len(window)),
            )
            total += link_count
            window = [*window, *linked]
            link_revision = source_revision(conn, "ticket-artifact-links", owner_id)
        revision = inventory.revision + ":" + link_revision
        entries: list[ArtifactCatalogEntry] = []
        bytes_used = 0
        for row in window:
            size = len(json.dumps(row, ensure_ascii=False).encode())
            if bytes_used + size > PAGE_MAX_BYTES:
                if not entries:
                    raise PlannerError(
                        ErrorCode.validation, "artifact metadata exceeds page budget", {}
                    )
                break
            bytes_used += size
            entries.append(row)
        return {
            "revision": revision,
            "directory": directory,
            "offset": offset,
            "total": total,
            "entries": entries,
        }
    finally:
        conn.close()


@router.get("/artifact-catalog/{kind}/{owner_id}")
async def artifact_catalog(
    request: Request,
    kind: ArtifactOwnerKind | Literal["sprint_item"],
    owner_id: str,
    directory: str = "",
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> ArtifactDirectoryPage:
    canonical_kind: ArtifactOwnerKind = "manager" if kind == "sprint_item" else kind
    return await anyio.to_thread.run_sync(
        _read, request, canonical_kind, owner_id, directory, offset, limit
    )
