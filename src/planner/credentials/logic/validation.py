"""Credential validation never reflects submitted secret content in errors."""

import json
import re

from planner.core.contracts import ErrorCode, PlannerError
from planner.credentials import configuration as limits
from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialEncryptionContext,
    CredentialMetadata,
    CredentialScheme,
    CredentialSecret,
    CredentialWrite,
    HeaderSecret,
)


def invalid(message: str = "invalid credential input") -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


def text(value: object, *, maximum: int | None = None, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (maximum is not None and len(value) > maximum)
        or (not allow_empty and not value.strip())
    ):
        raise invalid()
    return value


def auth_kind(secret: CredentialSecret) -> CredentialAuthKind:
    if isinstance(secret, BearerSecret):
        return CredentialAuthKind.bearer
    if isinstance(secret, HeaderSecret):
        return CredentialAuthKind.header
    if isinstance(secret, BasicSecret):
        return CredentialAuthKind.basic
    raise invalid()


def validate_secret(secret: CredentialSecret, kind: CredentialAuthKind) -> None:
    if auth_kind(secret) != kind:
        raise invalid("credential authentication type differs")
    if isinstance(secret, BasicSecret):
        text(secret.username, allow_empty=True)
        text(secret.password, allow_empty=True)
        if ":" in secret.username:
            raise invalid()
    else:
        value = secret.token if isinstance(secret, BearerSecret) else secret.value
        text(value)
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise invalid()
        if isinstance(secret, HeaderSecret):
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", secret.header_name):
                raise invalid()


def validate_write(write: CredentialWrite) -> CredentialWrite:
    text(write.reference.name, maximum=limits.MAX_NAME_LENGTH)
    if write.reference.scheme is not CredentialScheme.local:
        raise invalid("unsupported credential resolver")
    validate_secret(write.secret, write.auth_kind)
    return CredentialWrite(write.reference, write.auth_kind, write.secret)


def associated_data(metadata: CredentialMetadata) -> bytes:
    return json.dumps(
        {
            "credential_id": metadata.id,
            "scheme": metadata.reference.scheme,
            "name": metadata.reference.name,
            "auth_kind": metadata.auth_kind,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def encode_secret(secret: CredentialSecret) -> bytes:
    kind = auth_kind(secret)
    validate_secret(secret, kind)
    if isinstance(secret, BearerSecret):
        body = {"kind": kind, "token": secret.token}
    elif isinstance(secret, HeaderSecret):
        body = {"kind": kind, "header_name": secret.header_name, "value": secret.value}
    else:
        body = {"kind": kind, "username": secret.username, "password": secret.password}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def decode_secret(raw: bytes) -> CredentialSecret:
    try:
        body = json.loads(raw)
        kind = CredentialAuthKind(body["kind"])
        if kind is CredentialAuthKind.bearer:
            secret: CredentialSecret = BearerSecret(body["token"])
        elif kind is CredentialAuthKind.header:
            secret = HeaderSecret(body["header_name"], body["value"])
        else:
            secret = BasicSecret(body["username"], body["password"])
        validate_secret(secret, kind)
        return secret
    except (TypeError, KeyError, ValueError, UnicodeError):
        raise invalid("credential could not be decrypted") from None


def validate_encryption_context(
    metadata: CredentialMetadata, context: CredentialEncryptionContext
) -> None:
    """Retain legacy bytes while authenticating their public metadata binding."""
    try:
        body = json.loads(context.associated_data)
        required = {"scheme", "name", "auth_kind"}
        if context.version == 1:
            required |= {"project_id", "account"}
        elif context.version == 2:
            required |= {"credential_id"}
        else:
            raise ValueError
        if (
            not isinstance(body, dict)
            or set(body) != required
            or not all(isinstance(value, str) for value in body.values())
            or body["scheme"] != metadata.reference.scheme
            or body["name"] != metadata.reference.name
            or body["auth_kind"] != metadata.auth_kind
            or (context.version == 2 and body["credential_id"] != metadata.id)
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise invalid("credential could not be decrypted") from None
