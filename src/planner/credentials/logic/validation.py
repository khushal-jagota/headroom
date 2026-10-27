"""Credential validation never reflects submitted secret content in errors."""

import json
import re
from urllib.parse import urlsplit

from planner.core.contracts import ErrorCode, PlannerError
from planner.credentials import configuration as limits
from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialScheme,
    CredentialSecret,
    CredentialWrite,
    HeaderSecret,
)


def invalid(message: str = "invalid credential input") -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


def text(value: object, *, maximum: int, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not allow_empty and not value.strip())
    ):
        raise invalid()
    return value


def origins(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values or len(values) > limits.MAX_ORIGINS:
        raise invalid("credential requires bounded allowed origins")
    normalized: set[str] = set()
    for raw in values:
        if (
            not isinstance(raw, str)
            or len(raw) > limits.MAX_ORIGIN_LENGTH
            or any(ord(char) <= 32 or ord(char) >= 127 for char in raw)
        ):
            raise invalid("invalid credential origin")
        try:
            parsed = urlsplit(raw)
            port = parsed.port
            host = parsed.hostname
        except ValueError:
            raise invalid("invalid credential origin") from None
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "\\" in raw
        ):
            raise invalid("credential origin must be exact HTTPS origin")
        if port is not None and not 1 <= port <= 65535:
            raise invalid("invalid credential origin")
        if host.endswith(".") or "%" in host:
            raise invalid("invalid credential origin")
        hostname = f"[{host.lower()}]" if ":" in host else host.lower()
        normalized.add(f"https://{hostname}" + (f":{port}" if port not in (None, 443) else ""))
    return tuple(sorted(normalized))


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
        text(secret.username, maximum=limits.MAX_SECRET_LENGTH)
        text(secret.password, maximum=limits.MAX_SECRET_LENGTH, allow_empty=True)
        if ":" in secret.username:
            raise invalid()
    else:
        value = secret.token if isinstance(secret, BearerSecret) else secret.value
        text(value, maximum=limits.MAX_SECRET_LENGTH)
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise invalid()
        if isinstance(secret, HeaderSecret):
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", secret.header_name):
                raise invalid()
            if secret.header_name.lower() in limits.RESERVED_AUTH_HEADER_NAMES:
                raise invalid("credential header is reserved")


def validate_write(write: CredentialWrite) -> CredentialWrite:
    text(write.scope.project_id, maximum=limits.MAX_NAME_LENGTH)
    text(write.scope.account, maximum=limits.MAX_ACCOUNT_LENGTH)
    text(write.reference.name, maximum=limits.MAX_NAME_LENGTH)
    if write.reference.scheme is not CredentialScheme.local:
        raise invalid("unsupported credential resolver")
    validate_secret(write.secret, write.auth_kind)
    return CredentialWrite(
        write.scope, write.reference, write.auth_kind, origins(write.allowed_origins), write.secret
    )


def associated_data(metadata: CredentialMetadata) -> bytes:
    return json.dumps(
        {
            "project_id": metadata.scope.project_id,
            "account": metadata.scope.account,
            "scheme": metadata.reference.scheme,
            "name": metadata.reference.name,
            "auth_kind": metadata.auth_kind,
            "allowed_origins": list(metadata.allowed_origins),
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
