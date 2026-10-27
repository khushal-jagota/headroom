"""Host-only key storage and a replaceable scoped credential resolver."""

import os
import sqlite3
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from planner.credentials import configuration as limits
from planner.credentials import data
from planner.credentials.contracts import (
    CredentialCipher,
    CredentialReference,
    CredentialScope,
    CredentialSecret,
    EncryptedCredential,
    ResolvedCredential,
)
from planner.credentials.logic.validation import (
    associated_data,
    decode_secret,
    encode_secret,
    invalid,
    validate_secret,
)
from planner.measure_sources.configuration import SANDBOX_RUNTIME_ROOTS


def validate_key_path(path: Path) -> Path:
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise invalid("unsafe credential key path")
    for part in (absolute, *absolute.parents):
        if part.is_symlink():
            raise invalid("credential key path cannot contain symlinks")
    resolved = absolute.resolve(strict=False)
    for resource in SANDBOX_RUNTIME_ROOTS:
        for root in (Path(resource).absolute(), Path(resource).resolve(strict=False)):
            if resolved == root or root in resolved.parents:
                raise invalid("credential key path is exposed to source runtime")
    return absolute


def _load_key(path: Path, *, has_records: bool) -> bytes:
    path = validate_key_path(path)
    directory = path.parent
    if not directory.exists():
        if has_records:
            raise invalid("credential encryption key is unavailable")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    validate_key_path(path)
    info = directory.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise invalid("credential key directory must be private")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            if has_records:
                raise invalid("credential encryption key is unavailable") from None
            try:
                writer = os.open(
                    path.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                pass
            else:
                try:
                    os.fchmod(writer, 0o600)
                    generated = os.urandom(limits.KEY_BYTES)
                    written = os.write(writer, generated)
                    if written != len(generated):
                        raise invalid("credential key creation failed")
                    os.fsync(writer)
                finally:
                    os.close(writer)
                os.fsync(directory_fd)
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise invalid("credential key file must be private")
            key = os.read(descriptor, limits.KEY_BYTES + 1)
            if len(key) != limits.KEY_BYTES:
                raise invalid("credential encryption key is unusable")
            return key
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)


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

    def resolve(self, scope: CredentialScope, reference: CredentialReference) -> ResolvedCredential:
        row = self._conn.execute(
            "SELECT id FROM credentials WHERE project_id=? AND account=? AND scheme=? AND name=?",
            (scope.project_id, scope.account, reference.scheme, reference.name),
        ).fetchone()
        if row is None:
            raise invalid("credential is unavailable in this scope")
        metadata = data.read_metadata(self._conn, row["id"])
        secret = self._cipher.decrypt(
            data.read_encrypted(self._conn, metadata.id), associated_data=associated_data(metadata)
        )
        validate_secret(secret, metadata.auth_kind)
        return ResolvedCredential(metadata, secret)
