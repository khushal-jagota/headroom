"""Public metadata reads are separate from private ciphertext lookup."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.credentials import configuration as limits
from planner.credentials.contracts import (
    CredentialAuthKind,
    CredentialEncryptionContext,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    EncryptedCredential,
)
from planner.credentials.logic.validation import invalid, validate_encryption_context
from planner.list_reads.contracts import ListPage

PUBLIC_COLUMNS = "id,scheme,name,auth_kind,revision,created_at,updated_at"


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    nested = conn.in_transaction
    conn.execute("SAVEPOINT credential_action" if nested else "BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if nested:
            conn.execute("ROLLBACK TO credential_action")
            conn.execute("RELEASE credential_action")
        else:
            conn.rollback()
        raise
    else:
        if nested:
            conn.execute("RELEASE credential_action")
        else:
            conn.commit()


def metadata_json(metadata: CredentialMetadata) -> JsonDict:
    return asdict(metadata)


def _metadata(row: sqlite3.Row) -> CredentialMetadata:
    return CredentialMetadata(
        row["id"],
        CredentialReference(CredentialScheme(row["scheme"]), row["name"]),
        CredentialAuthKind(row["auth_kind"]),
        int(row["revision"]),
        int(row["created_at"]),
        int(row["updated_at"]),
    )


def read_metadata(conn: sqlite3.Connection, credential_id: str) -> CredentialMetadata:
    row = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM credentials WHERE id=?", (credential_id,)
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "credential not found")
    return _metadata(row)


def list_metadata(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
) -> ListPage[CredentialMetadata]:
    if (
        type(limit) is not int
        or not 1 <= limit <= limits.MAX_LIST_LIMIT
        or type(offset) is not int
        or offset < 0
    ):
        raise invalid("invalid bounded credential list")
    count = int(conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0])
    rows = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM credentials ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return ListPage(tuple(_metadata(row) for row in rows), count, limit, offset)


def read_encrypted(conn: sqlite3.Connection, credential_id: str) -> EncryptedCredential:
    row = conn.execute(
        "SELECT nonce,ciphertext FROM credentials WHERE id=?", (credential_id,)
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "credential not found")
    return EncryptedCredential(bytes(row["nonce"]), bytes(row["ciphertext"]))


def has_records(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM credentials LIMIT 1").fetchone() is not None


def read_encryption_context(
    conn: sqlite3.Connection, credential_id: str
) -> CredentialEncryptionContext:
    row = conn.execute(
        "SELECT encryption_version,encryption_context FROM credentials WHERE id=?",
        (credential_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "credential not found")
    context = CredentialEncryptionContext(
        int(row["encryption_version"]), bytes(row["encryption_context"])
    )
    validate_encryption_context(read_metadata(conn, credential_id), context)
    return context


def find_metadata(
    conn: sqlite3.Connection, reference: CredentialReference
) -> CredentialMetadata | None:
    rows = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM credentials WHERE scheme=? AND name=? ORDER BY id",
        (reference.scheme, reference.name),
    ).fetchall()
    if len(rows) > 1:
        raise PlannerError(
            ErrorCode.validation,
            "credential reference is ambiguous",
            {"candidates": [metadata_json(_metadata(row)) for row in rows]},
        )
    return None if not rows else _metadata(rows[0])
