"""Regenerate public synthetic credential cipher vectors with the real Python store."""

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    CredentialSecret,
    HeaderSecret,
)
from planner.credentials.logic.validation import associated_data, encode_secret
from planner.credentials.store import FileCredentialCipher


def main() -> None:
    key = bytes(range(32))
    examples: list[tuple[str, str, CredentialSecret, int]] = [
        ("fixture-bearer", "café 😀", BearerSecret("synthetic-fixture-bearer"), 2),
        ("fixture-header", "legacy", HeaderSecret("X-Demo", "synthetic-fixture-header"), 1),
        ("fixture-basic", "basic", BasicSecret("", "synthetic-fixture-basic"), 2),
    ]
    rows: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="panels-credential-fixture-") as temporary:
        key_path = Path(temporary) / "key"
        key_path.write_bytes(key)
        cipher = FileCredentialCipher(key_path, has_records=False)
        for index, (identity, name, secret, version) in enumerate(examples, 1):
            secret_body = json.loads(encode_secret(secret))
            kind = CredentialAuthKind(secret_body["kind"])
            metadata = CredentialMetadata(
                identity, CredentialReference(CredentialScheme.local, name), kind, 1, 100, 100
            )
            context = associated_data(metadata)
            if version == 1:
                context = (
                    b'{"account":"old-account","auth_kind":"header","name":"legacy",'
                    b'"project_id":"old-project","scheme":"local"}'
                )
            nonce = bytes([index]) * 12
            with patch("planner.credentials.store.os.urandom", return_value=nonce):
                encrypted = cipher.encrypt(secret, associated_data=context)
            rows.append(
                {
                    "metadata": asdict(metadata),
                    "version": version,
                    "associated_data_hex": context.hex(),
                    "canonical_associated_data_hex": associated_data(metadata).hex(),
                    "nonce_hex": encrypted.nonce.hex(),
                    "ciphertext_hex": encrypted.ciphertext.hex(),
                    "secret": secret_body,
                }
            )
    output = (
        Path(__file__).resolve().parents[3]
        / "server"
        / "credentials"
        / "fixtures"
        / "python-cipher.json"
    )
    output.write_text(json.dumps({"key_hex": key.hex(), "credentials": rows}, indent=2) + "\n")


if __name__ == "__main__":
    main()
