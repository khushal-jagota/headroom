"""Canonical wire and provider boundary for demand-scoped resource reads."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypedDict

from fastapi import Request


class ResourceVersion(TypedDict):
    key: str
    revision: str | None


class ResourceVersionsResponse(TypedDict):
    epoch: str
    resources: list[ResourceVersion]


class ResourceBodyResponse(TypedDict):
    epoch: str
    key: str
    revision: str
    body: object


@dataclass(frozen=True)
class ResourceSnapshot:
    """Version and reader share one DB snapshot or captured immutable source."""

    revision: str
    read: Callable[[], object | Awaitable[object]]


class ResourceProvider(Protocol):
    def capture(
        self, conn: sqlite3.Connection, request: Request, key: str
    ) -> ResourceSnapshot | None:
        """Return None for an unsupported key. Never write or load a whole DB body here."""
        ...


@dataclass(frozen=True)
class ResourceSourceTrigger:
    """Domain-declared change dependency installed by the owning migration."""

    name: str
    table: str
    operation: str
    source_kind: str
    source_id_sql: str
    when_sql: str = "1"
