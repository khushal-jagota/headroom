"""Atomic random witnesses and conservative command visibility certification."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any, cast

from planner.core.commit_capture import (
    Capture,
    _current_capture,
    begin_capture,
    capture_command_commits,
    end_capture,
    register_capture_task,
)
from planner.core.commit_observers import CommitObserver

__all__ = [
    "Capture",
    "begin_capture",
    "end_capture",
    "capture_command_commits",
    "register_capture_task",
    "prepare_witness",
    "response_fence",
    "certify",
    "prune",
]
from planner.navigation.contracts import SCHEMA_VERSION, SUPPORTED_SEMANTICS, parse_revision
from planner.navigation.proof_contracts import CommittedWitness, Fence, ProofResult
from planner.navigation.proof_limits import (
    MAX_CAPTURE_WITNESSES,
    MAX_FENCE_BYTES,
    RETAINED_WITNESSES,
)


def _available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='navigation_commit_witnesses'"
        ).fetchone()
        is not None
    )


def _connection_capture(conn: sqlite3.Connection) -> Capture | None:
    capture = _current_capture()
    if capture is not None and capture.db_path is not None:
        path = str(Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve())
        if path != capture.db_path:
            return None
    return capture


def prepare_witness(conn: sqlite3.Connection) -> CommittedWitness | None:
    if not _available(conn):
        return None
    if conn.execute("SELECT 1 FROM navigation_dirty LIMIT 1").fetchone() is None:
        return None
    identity = secrets.token_hex(32)
    conn.execute("INSERT INTO navigation_commit_witnesses(identity) VALUES(?)", (identity,))
    # Retention is independent of journal pruning, epochs, and rebuilds.
    prune(conn)
    return CommittedWitness(identity, _connection_capture(conn))


def _committed(conn: sqlite3.Connection, results: tuple[object, ...]) -> None:
    for result in results:
        if not isinstance(result, CommittedWitness) or not isinstance(result.capture, Capture):
            continue
        capture = result.capture
        with capture.lock:
            if capture.closed:
                continue
            if len(capture.witnesses) >= capture.max_witnesses:
                capture.complete = False
            else:
                capture.witnesses.append(result.identity)


def _failed(results: tuple[object, ...]) -> None:
    for result in results:
        if isinstance(result, CommittedWitness) and isinstance(result.capture, Capture):
            with result.capture.lock:
                result.capture.complete = False


COMMIT_OBSERVER = CommitObserver(_committed, _failed)


def prune(conn: sqlite3.Connection, retain_count: int = RETAINED_WITNESSES) -> int:
    if not conn.in_transaction:
        raise RuntimeError("witness pruning requires a write transaction")
    if not 1 <= retain_count <= RETAINED_WITNESSES:
        raise ValueError("witness retention count is out of bounds")
    cursor = conn.execute(
        "DELETE FROM navigation_commit_witnesses WHERE sequence <= "
        "(SELECT MAX(sequence)-? FROM navigation_commit_witnesses)",
        (retain_count,),
    )
    return cursor.rowcount


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _signature(conn: sqlite3.Connection, value: dict[str, Any]) -> str:
    secret = conn.execute(
        "SELECT secret FROM navigation_proof_identity WHERE singleton=1"
    ).fetchone()
    if secret is None:
        raise RuntimeError("navigation proof identity is absent")
    return hmac.new(str(secret[0]).encode(), _canonical(value).encode(), hashlib.sha256).hexdigest()


def _continuity(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT scope,schema_version,epoch,revision FROM navigation_meta WHERE singleton=1"
    ).fetchone()
    if row[1] != SCHEMA_VERSION:
        raise ValueError("proof requires the current navigation schema")
    return {
        "scope": row[0],
        "schema_version": row[1],
        "semantics": SUPPORTED_SEMANTICS,
        "epoch": row[2],
        "revision": str(row[3]),
    }


def response_fence(conn: sqlite3.Connection, capture: Capture) -> Fence:
    owns = not conn.in_transaction
    if owns:
        conn.execute("BEGIN")
    try:
        continuity = _continuity(conn)
        with capture.lock:
            complete = capture.complete
            witnesses = list(capture.witnesses)
        if capture.db_path is not None:
            database_path = str(Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve())
            if database_path != capture.db_path:
                complete = False
        # Evidence can expire during an unusually long command. Never certify a subset.
        if complete and any(not _contains(conn, identity) for identity in witnesses):
            complete = False
        body = {
            "version": 1,
            **continuity,
            "coverage": "complete" if complete else "unknown",
            "witnesses": witnesses,
        }
        if len(_canonical(body).encode()) + 100 > MAX_FENCE_BYTES:
            body["coverage"] = "unknown"
            body["witnesses"] = []
        body["signature"] = _signature(conn, body)
        return cast(Fence, body)
    finally:
        if owns:
            conn.rollback()


def _contains(conn: sqlite3.Connection, identity: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM navigation_commit_witnesses WHERE identity=?", (identity,)
        ).fetchone()
        is not None
    )


def certify(conn: sqlite3.Connection, fence: object) -> ProofResult:
    """Prove every issued witness within the caller's consistent snapshot transaction."""
    if not conn.in_transaction:
        raise RuntimeError("certification requires the snapshot read transaction")
    unknown: ProofResult = {"status": "unknown", "reason": "invalid_fence"}
    if not isinstance(fence, dict) or len(fence) != 9:
        return unknown
    try:
        if len(_canonical(fence).encode()) > MAX_FENCE_BYTES:
            return unknown
        signature = fence.get("signature")
        body = {key: value for key, value in fence.items() if key != "signature"}
        if not isinstance(signature, str) or not hmac.compare_digest(
            _signature(conn, body), signature
        ):
            return unknown
        witnesses = fence["witnesses"]
        if (
            fence["version"] != 1
            or not isinstance(witnesses, list)
            or len(witnesses) > MAX_CAPTURE_WITNESSES
            or any(not isinstance(item, str) or len(item) != 64 for item in witnesses)
        ):
            return unknown
        parse_revision(fence["revision"])
        current = _continuity(conn)
        if any(fence[key] != current[key] for key in ("scope", "schema_version", "semantics")):
            return {"status": "unknown", "reason": "incompatible_scope"}
        if fence["coverage"] != "complete":
            return {"status": "unknown", "reason": "incomplete_capture"}
        if any(not _contains(conn, identity) for identity in witnesses):
            return {"status": "unknown", "reason": "missing_witness"}
        return {"status": "included" if witnesses else "no_op", "reason": None}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return unknown
