"""Version-bound durable recap bodies outside the resident navigation view."""

from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path

from planner.navigation.delivery import _read_connection
from planner.navigation.views.projection import record


def recap(path: Path, kind: str, identity: str, version: str) -> str:
    if kind not in ("ticket", "manager") or len(identity) > 256 or len(version) != 64:
        raise ValueError("invalid recap reference")
    with contextlib.closing(_read_connection(path)) as conn:
        conn.execute("BEGIN")
        value = record(conn, (kind, identity))
        if value is None:
            raise LookupError("recap owner is absent")
        body = str(value["recap"])
        if hashlib.sha256(body.encode()).hexdigest() != version:
            raise LookupError("recap source changed")
        return body
