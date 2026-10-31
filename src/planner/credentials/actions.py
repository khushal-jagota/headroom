"""Canonical credential writes with separate plaintext and metadata authority."""

import sqlite3
from dataclasses import replace
from pathlib import Path

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.ids import new_id
from planner.credentials import data
from planner.credentials.configuration import MAX_NAME_LENGTH
from planner.credentials.contracts import (
    CredentialMetadata,
    CredentialReference,
    CredentialSecret,
    CredentialWrite,
)
from planner.credentials.logic.validation import (
    associated_data,
    invalid,
    text,
    validate_secret,
    validate_write,
)
from planner.credentials.store import FileCredentialCipher


def require_plaintext_owner(principal: Principal) -> None:
    if principal.kind is not PrincipalKind.owner:
        raise PlannerError(
            ErrorCode.agent_forbidden, "only the owner can submit credential secrets"
        )


def _check_revision(metadata: CredentialMetadata, expected: int) -> None:
    if type(expected) is not int or metadata.revision != expected:
        raise PlannerError(ErrorCode.stale_claim, "credential changed")


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("credential write requires the caller transaction")


def _require_unused_name(
    conn: sqlite3.Connection, reference: CredentialReference, *, except_id: str | None = None
) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM credentials WHERE scheme=? AND name=? AND id!=? LIMIT 1",
            (reference.scheme, reference.name, except_id or ""),
        ).fetchone()
        is not None
    ):
        raise invalid("credential reference already exists")


def create_credential_in_transaction(
    conn: sqlite3.Connection,
    write: CredentialWrite,
    *,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    """Write under the caller's transaction without savepoints or commits."""
    require_plaintext_owner(principal)
    _require_transaction(conn)
    write = validate_write(write)
    _require_unused_name(conn, write.reference)
    metadata = CredentialMetadata(
        new_id("credential"), write.reference, write.auth_kind, 1, now, now
    )
    cipher = FileCredentialCipher(key_path, has_records=data.has_records(conn))
    existing = conn.execute("SELECT id FROM credentials LIMIT 1").fetchone()
    if existing is not None:
        known = data.read_metadata(conn, existing["id"])
        cipher.decrypt(
            data.read_encrypted(conn, known.id),
            associated_data=data.read_encryption_context(conn, known.id).associated_data,
        )
    context = associated_data(metadata)
    encrypted = cipher.encrypt(write.secret, associated_data=context)
    conn.execute(
        "INSERT INTO credentials(id,scheme,name,auth_kind,encryption_version,encryption_context,"
        "nonce,ciphertext,revision,created_at,updated_at) VALUES(?,?,?,?,2,?,?,?,?,?,?)",
        (
            metadata.id,
            metadata.reference.scheme,
            metadata.reference.name,
            metadata.auth_kind,
            context,
            encrypted.nonce,
            encrypted.ciphertext,
            1,
            now,
            now,
        ),
    )
    return metadata


def create_credential(
    conn: sqlite3.Connection,
    write: CredentialWrite,
    *,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    require_plaintext_owner(principal)
    with data.transaction(conn):
        return create_credential_in_transaction(
            conn, write, principal=principal, now=now, key_path=key_path
        )


def replace_credential_in_transaction(
    conn: sqlite3.Connection,
    credential_id: str,
    secret: CredentialSecret,
    *,
    expected_revision: int,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    """Replace under the caller's transaction without savepoints or commits."""
    require_plaintext_owner(principal)
    _require_transaction(conn)
    current = data.read_metadata(conn, credential_id)
    _check_revision(current, expected_revision)
    validate_secret(secret, current.auth_kind)
    cipher = FileCredentialCipher(key_path, has_records=True)
    context = data.read_encryption_context(conn, credential_id)
    # Authenticate the current value: a wrong host key is not a reset path.
    cipher.decrypt(
        data.read_encrypted(conn, credential_id), associated_data=context.associated_data
    )
    metadata = replace(current, revision=current.revision + 1, updated_at=now)
    encrypted = cipher.encrypt(secret, associated_data=context.associated_data)
    conn.execute(
        "UPDATE credentials SET nonce=?,ciphertext=?,revision=?,updated_at=? WHERE id=?",
        (encrypted.nonce, encrypted.ciphertext, metadata.revision, now, credential_id),
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
        return replace_credential_in_transaction(
            conn,
            credential_id,
            secret,
            expected_revision=expected_revision,
            principal=principal,
            now=now,
            key_path=key_path,
        )


def rename_credential(
    conn: sqlite3.Connection,
    credential_id: str,
    name: str,
    *,
    expected_revision: int,
    principal: Principal,
    now: int,
    key_path: Path,
) -> CredentialMetadata:
    """Authenticate and rename the same saved value, without secret reentry."""
    require_plaintext_owner(principal)
    text(name, maximum=MAX_NAME_LENGTH)
    with data.transaction(conn):
        current = data.read_metadata(conn, credential_id)
        _check_revision(current, expected_revision)
        reference = CredentialReference(current.reference.scheme, name)
        _require_unused_name(conn, reference, except_id=credential_id)
        cipher = FileCredentialCipher(key_path, has_records=True)
        secret = cipher.decrypt(
            data.read_encrypted(conn, credential_id),
            associated_data=data.read_encryption_context(conn, credential_id).associated_data,
        )
        metadata = replace(
            current, reference=reference, revision=current.revision + 1, updated_at=now
        )
        context = associated_data(metadata)
        encrypted = cipher.encrypt(secret, associated_data=context)
        conn.execute(
            "UPDATE credentials SET name=?,encryption_version=2,encryption_context=?,nonce=?,"
            "ciphertext=?,revision=?,updated_at=? WHERE id=?",
            (
                name,
                context,
                encrypted.nonce,
                encrypted.ciphertext,
                metadata.revision,
                now,
                credential_id,
            ),
        )
        return metadata


def delete_credential(
    conn: sqlite3.Connection, credential_id: str, *, principal: Principal
) -> None:
    authority.require_above(conn, principal, authority.owner_only("credentials"))
    with data.transaction(conn):
        data.read_metadata(conn, credential_id)
        conn.execute("DELETE FROM credentials WHERE id=?", (credential_id,))
