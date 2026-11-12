"""Keep the live Python/TypeScript credential-store boundary in comparison CI."""

import subprocess
from contextlib import closing
from pathlib import Path

from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.credentials import actions, data
from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialReference,
    CredentialScheme,
    CredentialWrite,
    HeaderSecret,
)
from planner.credentials.logic.validation import associated_data
from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher

REPOSITORY = Path(__file__).resolve().parents[2]


def _node(database: Path, key: Path, mode: str) -> None:
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).with_name("credential_interop.ts")),
            str(database),
            str(key),
            mode,
        ],
        cwd=REPOSITORY,
        capture_output=True,
        check=False,
        timeout=30,
    )
    # A failed adapter never forwards private values or output into pytest evidence.
    assert result.returncode == 0, f"credential interoperability failed ({result.returncode})"
    assert not result.stdout, "credential interoperability must not emit output"


def test_python_typescript_credentials_interoperate_on_real_store(tmp_path: Path) -> None:
    """Metadata HTTP comparison and fixed vectors cannot prove live writes both ways."""
    home = tmp_path / "home"
    home.mkdir()
    homes = create_synthetic_homes(home)
    database = tmp_path / "credentials.sqlite"
    key = tmp_path / "private" / "key"
    with synthetic_home_environment(homes):
        with closing(connect(str(database))) as conn:
            create_schema(conn)
            metadata = actions.create_credential(
                conn,
                CredentialWrite(
                    CredentialReference(CredentialScheme.local, "café 😀"),
                    CredentialAuthKind.bearer,
                    BearerSecret("synthetic-token"),
                ),
                principal=OWNER_PRINCIPAL,
                now=100,
                key_path=key,
            )
            assert data.read_encryption_context(
                conn, metadata.id
            ).associated_data == associated_data(metadata)
            legacy_context = (
                b'{"account":"old-account","auth_kind":"header","name":"legacy",'
                b'"project_id":"old-project","scheme":"local"}'
            )
            encrypted = FileCredentialCipher(key, has_records=True).encrypt(
                HeaderSecret("X-Demo", "synthetic-header"), associated_data=legacy_context
            )
            conn.execute(
                "INSERT INTO credentials VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "legacy",
                    "local",
                    "legacy",
                    "header",
                    1,
                    legacy_context,
                    encrypted.nonce,
                    encrypted.ciphertext,
                    1,
                    100,
                    100,
                ),
            )
            conn.commit()
        assert key.stat().st_mode & 0o777 == 0o600
        assert key.parent.stat().st_mode & 0o777 == 0o700
        _node(database, key, "mutate")
        with closing(connect(str(database))) as conn:
            resolver = DatabaseCredentialResolver(conn, FileCredentialCipher(key, has_records=True))
            renamed = resolver.resolve(CredentialReference(CredentialScheme.local, "renamed 😀"))
            assert renamed.secret == BearerSecret("synthetic-token")
            assert data.read_encryption_context(conn, renamed.metadata.id).associated_data == (
                associated_data(renamed.metadata)
            )
            legacy = resolver.resolve(CredentialReference(CredentialScheme.local, "legacy"))
            assert legacy.secret == HeaderSecret("X-Demo", "synthetic-replaced")
            assert data.read_encryption_context(conn, "legacy").associated_data == legacy_context
            basic = resolver.resolve(CredentialReference(CredentialScheme.local, "ts-basic"))
            assert basic.secret == BasicSecret("", "synthetic-basic")
            actions.replace_credential(
                conn,
                basic.metadata.id,
                BasicSecret("demo", "synthetic-python-replaced"),
                expected_revision=1,
                principal=OWNER_PRINCIPAL,
                now=102,
                key_path=key,
            )
        _node(database, key, "verify")
