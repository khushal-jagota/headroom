"""Canonical credential writes with separate plaintext and metadata authority."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.ids import new_id
from planner.credentials import data
from planner.credentials.contracts import CredentialMetadata, CredentialSecret, CredentialWrite
from planner.credentials.logic.validation import (
    associated_data,
    invalid,
    origins,
    validate_secret,
    validate_write,
)
from planner.credentials.store import FileCredentialCipher
from planner.measure_sources.data import bump_source_revision


def require_plaintext_owner(principal: Principal) -> None:
    if principal.kind is not PrincipalKind.owner:
        raise PlannerError(
            ErrorCode.agent_forbidden, "only the owner can submit credential secrets"
        )


def _check_revision(metadata: CredentialMetadata, expected: int) -> None:
    if type(expected) is not int or metadata.revision != expected:
        raise PlannerError(ErrorCode.stale_claim, "credential changed")


def create_credential(
    conn: sqlite3.Connection,
    write: CredentialWrite,
    *,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    require_plaintext_owner(principal)
    write = validate_write(write)
    with data.transaction(conn):
        if (
            conn.execute("SELECT 1 FROM projects WHERE id=?", (write.scope.project_id,)).fetchone()
            is None
        ):
            raise invalid("credential Project does not exist")
        if (
            conn.execute(
                "SELECT 1 FROM credentials WHERE project_id=? AND account=? AND scheme=? AND "
                "name=?",
                (
                    write.scope.project_id,
                    write.scope.account,
                    write.reference.scheme,
                    write.reference.name,
                ),
            ).fetchone()
            is not None
        ):
            raise invalid("credential reference already exists")
        metadata = CredentialMetadata(
            new_id("credential"),
            write.scope,
            write.reference,
            write.auth_kind,
            write.allowed_origins,
            1,
            now,
            now,
        )
        cipher = FileCredentialCipher(key_path, has_records=data.has_records(conn))
        existing = conn.execute("SELECT id FROM credentials LIMIT 1").fetchone()
        if existing is not None:
            known = data.read_metadata(conn, existing["id"])
            cipher.decrypt(
                data.read_encrypted(conn, known.id), associated_data=associated_data(known)
            )
        encrypted = cipher.encrypt(write.secret, associated_data=associated_data(metadata))
        conn.execute(
            "INSERT INTO credentials(id,project_id,account,scheme,name,auth_kind,"
            "allowed_origins,nonce,ciphertext,revision,created_at,updated_at) VALUES(?,?,"
            "?,?,?,?,?,?,?,?,?,?)",
            (
                metadata.id,
                metadata.scope.project_id,
                metadata.scope.account,
                metadata.reference.scheme,
                metadata.reference.name,
                metadata.auth_kind,
                json.dumps(metadata.allowed_origins),
                encrypted.nonce,
                encrypted.ciphertext,
                1,
                now,
                now,
            ),
        )
        return metadata


def replace_credential(
    conn: sqlite3.Connection,
    credential_id: str,
    secret: CredentialSecret,
    *,
    expected_revision: int,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    require_plaintext_owner(principal)
    with data.transaction(conn):
        current = data.read_metadata(conn, credential_id)
        _check_revision(current, expected_revision)
        validate_secret(secret, current.auth_kind)
        cipher = FileCredentialCipher(key_path, has_records=True)
        # Authenticate the existing record before replacement: a wrong key is not a reset path.
        cipher.decrypt(
            data.read_encrypted(conn, credential_id), associated_data=associated_data(current)
        )
        metadata = replace(current, revision=current.revision + 1, updated_at=now)
        encrypted = cipher.encrypt(secret, associated_data=associated_data(metadata))
        conn.execute(
            "UPDATE credentials SET nonce=?,ciphertext=?,revision=?,updated_at=? WHERE id=?",
            (encrypted.nonce, encrypted.ciphertext, metadata.revision, now, credential_id),
        )
        return metadata


def update_origins(
    conn: sqlite3.Connection,
    credential_id: str,
    allowed_origins: tuple[str, ...],
    *,
    expected_revision: int,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    authority.require_above(conn, principal, authority.owner_only("credentials"))
    normalized = origins(allowed_origins)
    with data.transaction(conn):
        current = data.read_metadata(conn, credential_id)
        _check_revision(current, expected_revision)
        cipher = FileCredentialCipher(key_path, has_records=True)
        secret = cipher.decrypt(
            data.read_encrypted(conn, credential_id), associated_data=associated_data(current)
        )
        metadata = replace(
            current, allowed_origins=normalized, revision=current.revision + 1, updated_at=now
        )
        encrypted = cipher.encrypt(secret, associated_data=associated_data(metadata))
        conn.execute(
            "UPDATE credentials SET allowed_origins=?,nonce=?,ciphertext=?,revision=?,"
            "updated_at=? WHERE id=?",
            (
                json.dumps(normalized),
                encrypted.nonce,
                encrypted.ciphertext,
                metadata.revision,
                now,
                credential_id,
            ),
        )
        # Capability edits invalidate admitted batches, without claiming to recall HTTP traffic.
        for row in conn.execute(
            "SELECT DISTINCT source_id FROM measure_source_destinations WHERE credential_id=?",
            (credential_id,),
        ).fetchall():
            bump_source_revision(conn, str(row["source_id"]), now=now)
        return metadata


def delete_credential(
    conn: sqlite3.Connection, credential_id: str, *, principal: Principal
) -> None:
    authority.require_above(conn, principal, authority.owner_only("credentials"))
    with data.transaction(conn):
        data.read_metadata(conn, credential_id)
        if (
            conn.execute(
                "SELECT 1 FROM measure_source_destinations WHERE credential_id=? LIMIT 1",
                (credential_id,),
            ).fetchone()
            is not None
        ):
            raise invalid("credential is referenced by a source")
        conn.execute("DELETE FROM credentials WHERE id=?", (credential_id,))
