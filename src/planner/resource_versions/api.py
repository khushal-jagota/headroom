"""Read-only, demand-scoped version probes and coherent resource bodies."""

from __future__ import annotations

import inspect
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Request

from planner.core.errors import ErrorCode, PlannerError
from planner.resource_versions.contracts import (
    ResourceBodyResponse,
    ResourceSnapshot,
    ResourceVersion,
    ResourceVersionsResponse,
)
from planner.resource_versions.registry import PROVIDERS
from planner.tickets.api import DbConn

router = APIRouter()
MAX_RESOURCE_KEYS = 64
MAX_RESOURCE_KEY_LENGTH = 2048


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """A read transaction never commits and never publishes a change signal."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    finally:
        conn.rollback()


def capture(conn: sqlite3.Connection, request: Request, key: str) -> ResourceSnapshot | None:
    for provider in PROVIDERS:
        snapshot = provider.capture(conn, request, key)
        if snapshot is not None:
            return snapshot
    return None


def checked_key(key: str) -> str:
    if not key.startswith("/api/") or len(key) > MAX_RESOURCE_KEY_LENGTH:
        raise PlannerError(ErrorCode.validation, "invalid resource key", {})
    return key


@router.get("/resource-versions")
async def resource_versions(keys: str, request: Request, conn: DbConn) -> ResourceVersionsResponse:
    try:
        requested = json.loads(keys)
    except (ValueError, TypeError) as exc:
        raise PlannerError(ErrorCode.validation, "invalid resource keys", {}) from exc
    if (
        not isinstance(requested, list)
        or len(requested) > MAX_RESOURCE_KEYS
        or any(not isinstance(key, str) for key in requested)
    ):
        raise PlannerError(ErrorCode.validation, "invalid resource keys", {})
    with read_snapshot(conn):
        resources: list[ResourceVersion] = []
        for key in dict.fromkeys(requested):
            snapshot = capture(conn, request, checked_key(key))
            resources.append({"key": key, "revision": snapshot.revision if snapshot else None})
    return {"epoch": request.app.state.resource_version_epoch, "resources": resources}


@router.get("/resource-body")
async def resource_body(key: str, request: Request, conn: DbConn) -> ResourceBodyResponse:
    with read_snapshot(conn):
        snapshot = capture(conn, request, checked_key(key))
        if snapshot is None:
            raise PlannerError(ErrorCode.not_found, "unregistered resource", {"key": key})
        body = snapshot.read()
        if inspect.isawaitable(body):
            body = await body
        return {
            "epoch": request.app.state.resource_version_epoch,
            "key": key,
            "revision": snapshot.revision,
            "body": body,
        }
