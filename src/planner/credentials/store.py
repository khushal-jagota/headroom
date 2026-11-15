"""Host-only key storage and a replaceable scoped credential resolver."""

import os
import sqlite3
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from planner.credentials import configuration as limits
from planner.credentials import data
from planner.credentials.contracts import (
    CredentialCipher,
    CredentialReference,
    CredentialSecret,
    EncryptedCredential,
    ResolvedCredential,
)
from planner.credentials.logic.validation import (
    decode_secret,
    encode_secret,
    invalid,
    validate_secret,
)


def _load_key(path: Path, *, has_records: bool) -> bytes:
    if not path.exists():
        if has_records:
            raise invalid("credential encryption key is unavailable")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(os.urandom(limits.KEY_BYTES))
                output.flush()
                os.fsync(output.fileno())
    with path.open("rb") as source:
        key = source.read(limits.KEY_BYTES + 1)
    if len(key) != limits.KEY_BYTES:
        raise invalid("credential encryption key is unusable")
    return key


class FileCredentialCipher:
    def __init__(self, key_path: Path, *, has_records: bool) -> None:
        try:
            self._cipher = AESGCM(_load_key(key_path, has_records=has_records))
        except OSError:
            raise invalid("credential encryption key is unavailable") from None

    def encrypt(self, secret: CredentialSecret, *, associated_data: bytes) -> EncryptedCredential:
        nonce = os.urandom(limits.NONCE_BYTES)
        return EncryptedCredential(
            nonce, self._cipher.encrypt(nonce, encode_secret(secret), associated_data)
        )

    def decrypt(
        self, encrypted: EncryptedCredential, *, associated_data: bytes
    ) -> CredentialSecret:
        try:
            raw = self._cipher.decrypt(encrypted.nonce, encrypted.ciphertext, associated_data)
            return decode_secret(raw)
        except (InvalidTag, ValueError, TypeError):
            raise invalid("credential could not be decrypted") from None


class DatabaseCredentialResolver:
    def __init__(self, conn: sqlite3.Connection, cipher: CredentialCipher) -> None:
        self._conn = conn
        self._cipher = cipher

    def resolve(self, reference: CredentialReference) -> ResolvedCredential:
        metadata = data.find_metadata(self._conn, reference)
        if metadata is None:
            raise invalid("credential is unavailable")
        secret = self._cipher.decrypt(
            data.read_encrypted(self._conn, metadata.id),
            associated_data=data.read_encryption_context(self._conn, metadata.id).associated_data,
        )
        validate_secret(secret, metadata.auth_kind)
        return ResolvedCredential(metadata, secret)
