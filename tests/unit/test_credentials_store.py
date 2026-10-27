"""Credential persistence, authority, private error, and host-key boundaries."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.authctx import RequestContext, request_context
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, PlannerError
from planner.core.db import connect
from planner.core.server import handle_planner_error
from planner.credentials import actions, api, data
from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialReference,
    CredentialScheme,
    CredentialScope,
    CredentialWrite,
    HeaderSecret,
)
from planner.credentials.logic.validation import associated_data, validate_secret
from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher

SECRET = "synthetic-credential-do-not-expose-94"
REPLACEMENT = "synthetic-replacement-75"


def write() -> CredentialWrite:
    return CredentialWrite(
        CredentialScope("project_vylo", "fixture-account"),
        CredentialReference(CredentialScheme.local, "example-read-key"),
        CredentialAuthKind.bearer,
        BearerSecret(SECRET),
    )


def test_encryption_names_only_scope_replace_and_authenticated_metadata(
    tmp_db: Connection, tmp_path: Path
) -> None:
    key = tmp_path / "private" / "key"
    metadata = actions.create_credential(
        tmp_db, write(), principal=OWNER_PRINCIPAL, now=1, key_path=key
    )
    encrypted = data.read_encrypted(tmp_db, metadata.id)
    assert SECRET.encode() not in encrypted.ciphertext
    assert SECRET not in json.dumps(data.metadata_json(metadata))
    listing = data.list_metadata(tmp_db, project_id=None, account=None, limit=10, offset=0)
    public = json.dumps([data.metadata_json(value) for value in listing.rows])
    assert SECRET not in public and "ciphertext" not in public and "nonce" not in public
    cipher = FileCredentialCipher(key, has_records=True)
    resolver = DatabaseCredentialResolver(tmp_db, cipher)
    assert resolver.resolve(metadata.scope, metadata.reference).secret == BearerSecret(SECRET)
    with pytest.raises(PlannerError, match="scope"):
        resolver.resolve(replace(metadata.scope, account="other-account"), metadata.reference)
    with pytest.raises(PlannerError, match="decrypted"):
        cipher.decrypt(
            encrypted,
            associated_data=associated_data(
                replace(metadata, scope=replace(metadata.scope, account="other-account"))
            ),
        )
    changed = actions.replace_credential(
        tmp_db,
        metadata.id,
        BearerSecret(REPLACEMENT),
        expected_revision=1,
        principal=OWNER_PRINCIPAL,
        now=2,
        key_path=key,
    )
    assert changed.reference == metadata.reference and changed.id == metadata.id
    assert resolver.resolve(metadata.scope, metadata.reference).secret == BearerSecret(REPLACEMENT)
    with pytest.raises(PlannerError, match="owner"):
        actions.replace_credential(
            tmp_db,
            metadata.id,
            BearerSecret(SECRET),
            expected_revision=2,
            principal=CHIEF_PRINCIPAL,
            now=4,
            key_path=key,
        )
    with pytest.raises(PlannerError, match="changed"):
        actions.replace_credential(
            tmp_db,
            metadata.id,
            BearerSecret(SECRET),
            expected_revision=1,
            principal=OWNER_PRINCIPAL,
            now=4,
            key_path=key,
        )

    actions.delete_credential(tmp_db, metadata.id, principal=OWNER_PRINCIPAL)
    with pytest.raises(PlannerError, match="unavailable"):
        resolver.resolve(metadata.scope, metadata.reference)


def test_missing_wrong_key_fails_without_reset(tmp_db: Connection, tmp_path: Path) -> None:
    key = tmp_path / "private" / "key"
    metadata = actions.create_credential(
        tmp_db, write(), principal=OWNER_PRINCIPAL, now=1, key_path=key
    )
    original = key.read_bytes()
    key.unlink()
    with pytest.raises(PlannerError, match="unavailable"):
        FileCredentialCipher(key, has_records=True)
    assert not key.exists()
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    with pytest.raises(PlannerError, match="decrypted"):
        actions.create_credential(
            tmp_db,
            replace(write(), reference=CredentialReference(CredentialScheme.local, "second")),
            principal=OWNER_PRINCIPAL,
            now=2,
            key_path=key,
        )
    assert tmp_db.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 1
    key.write_bytes(b"short")
    with pytest.raises(PlannerError, match="unusable"):
        FileCredentialCipher(key, has_records=True)
    key.write_bytes(original)
    key.chmod(0o644)
    alias = tmp_path / "alias"
    alias.symlink_to(key)
    cipher = FileCredentialCipher(alias, has_records=True)
    assert DatabaseCredentialResolver(tmp_db, cipher).resolve(
        metadata.scope, metadata.reference
    ).secret == BearerSecret(SECRET)


def test_concurrent_empty_store_initialization_never_overwrites_key(tmp_path: Path) -> None:
    key = tmp_path / "private" / "key"

    def initialize() -> FileCredentialCipher | None:
        try:
            return FileCredentialCipher(key, has_records=False)
        except PlannerError:
            # A reader can refuse a concurrently incomplete file; it cannot replace it.
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ciphers = [
            value for value in pool.map(lambda _: initialize(), range(2)) if value is not None
        ]
    assert ciphers
    encrypted = ciphers[0].encrypt(BearerSecret(SECRET), associated_data=b"fixture")
    assert FileCredentialCipher(key, has_records=False).decrypt(
        encrypted, associated_data=b"fixture"
    ) == BearerSecret(SECRET)
    assert key.stat().st_mode & 0o777 == 0o600
    assert key.parent.stat().st_mode & 0o777 == 0o700


def test_actual_route_parsing_never_echoes_secret_input(tmp_db: Connection) -> None:
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    config = load_config(path=None, env={"PLAN_DB_PATH": path, "PLAN_TEST_MODE": "1"})
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    app.add_exception_handler(PlannerError, handle_planner_error)
    app.state.config = config
    app.state.clock = build_clock(config)
    app.state.conn_factory = lambda: connect(path)
    app.dependency_overrides[request_context] = lambda: RequestContext(OWNER_PRINCIPAL)
    with TestClient(app) as client:
        for malformed in ([SECRET], {"secret": SECRET}, {"secret": {SECRET: SECRET}}):
            response = client.post("/api/credentials", json=malformed)
            assert response.status_code == 400
            assert SECRET not in response.text
        deeply_nested = client.post("/api/credentials", content="[" * 2000 + "0" + "]" * 2000)
        assert deeply_nested.status_code == 400
        body = {
            "project_id": "project_vylo",
            "account": "fixture-account",
            "scheme": "local",
            "name": "example-read-key",
            "auth_kind": "bearer",
            "secret": {"token": SECRET},
        }
        response = client.post("/api/credentials", json=body)
        assert response.status_code == 200 and SECRET not in response.text
        assert SECRET not in client.get("/api/credentials").text
        app.dependency_overrides[request_context] = lambda: RequestContext(CHIEF_PRINCIPAL)
        denied = client.post("/api/credentials", json=body)
        assert denied.status_code == 400 and SECRET not in denied.text
        assert denied.json()["error"]["code"] == "agent_forbidden"


def test_auth_values_follow_protocol_syntax_without_extra_policy() -> None:
    validate_secret(BasicSecret("", "synthetic"), CredentialAuthKind.basic)
    validate_secret(HeaderSecret("Accept", "synthetic"), CredentialAuthKind.header)
    with pytest.raises(PlannerError):
        validate_secret(HeaderSecret("invalid header", "synthetic"), CredentialAuthKind.header)
    with pytest.raises(PlannerError):
        validate_secret(BasicSecret("invalid:username", "synthetic"), CredentialAuthKind.basic)
