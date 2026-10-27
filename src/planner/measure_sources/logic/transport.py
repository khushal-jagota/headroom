"""Destination grants, authentication attachment, and known-secret reflection rules."""

from __future__ import annotations

import base64
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from planner.credentials.configuration import RESERVED_AUTH_HEADER_NAMES
from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    HeaderSecret,
)
from planner.measure_sources.contracts import (
    AuthenticatedHttpRequest,
    BrokerConfiguration,
    FailureCode,
    HostRequest,
    HttpMethod,
    PathMatch,
)
from planner.measure_sources.logic.protocol import encode, refuse

_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def normalize_url(url: str) -> tuple[str, str, str]:
    if not url or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or "\\" in url:
        raise refuse(FailureCode.unauthorized_request)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port if parts.port is not None else 443
    except ValueError:
        raise refuse(FailureCode.unauthorized_request) from None
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or not 1 <= port <= 65535
        or host.endswith(".")
        or "%" in parts.netloc
    ):
        raise refuse(FailureCode.unauthorized_request)
    path = parts.path or "/"
    if re.search(r"%(?![0-9a-fA-F]{2})", path + parts.query):
        raise refuse(FailureCode.unauthorized_request)
    # Reject encodings whose meaning varies between an API router and this broker.
    if re.search(r"%(?:2f|5c|25|00)", path, re.I):
        raise refuse(FailureCode.unauthorized_request)
    try:
        decoded = unquote(path, errors="strict")
    except UnicodeError:
        raise refuse(FailureCode.unauthorized_request) from None
    if any(segment in {".", ".."} for segment in decoded.split("/")) or "//" in decoded:
        raise refuse(FailureCode.unauthorized_request)
    if any(ord(c) < 32 or c == "\\" for c in decoded):
        raise refuse(FailureCode.unauthorized_request)
    path = quote(decoded, safe="/!$&'()*+,-.:;=@_~")
    host = host.lower()
    authority = f"[{host}]" if ":" in host else host
    if port != 443:
        authority += f":{port}"
    origin = "https://" + authority
    return urlunsplit(("https", authority, path, parts.query, "")), origin, path


def authenticate(config: BrokerConfiguration, request: HostRequest) -> AuthenticatedHttpRequest:
    normalized, origin, path = normalize_url(request.url)
    grants = [g for g in config.destinations if g.id == request.grant_id]
    if len(grants) != 1:
        raise refuse(FailureCode.unauthorized_request)
    grant = grants[0]
    _, grant_origin, grant_path = normalize_url(grant.origin + grant.path)
    path_ok = path == grant_path or (
        grant.path_match == PathMatch.prefix and path.startswith(grant_path.rstrip("/") + "/")
    )
    if grant.origin != grant_origin or origin != grant_origin or not path_ok:
        raise refuse(FailureCode.unauthorized_request)
    if request.method != grant.method:
        raise refuse(FailureCode.unauthorized_request)
    if request.method == HttpMethod.GET and request.json_body is not None:
        raise refuse(FailureCode.unauthorized_request)
    headers = [("Accept", "application/json"), ("Accept-Encoding", "identity")]
    if grant.credential is not None:
        matches = [c for c in config.credentials if c.metadata.reference == grant.credential]
        if len(matches) != 1:
            raise refuse(FailureCode.credential_unavailable)
        credential = matches[0]
        if (
            credential.metadata.scope != config.scope
            or origin not in credential.metadata.allowed_origins
        ):
            raise refuse(FailureCode.unauthorized_request)
        secret = credential.secret
        kind = credential.metadata.auth_kind
        if isinstance(secret, BearerSecret) and kind == CredentialAuthKind.bearer:
            headers.append(("Authorization", "Bearer " + secret.token))
        elif isinstance(secret, BasicSecret) and kind == CredentialAuthKind.basic:
            token = base64.b64encode(f"{secret.username}:{secret.password}".encode()).decode()
            headers.append(("Authorization", "Basic " + token))
        elif isinstance(secret, HeaderSecret) and kind == CredentialAuthKind.header:
            if (
                not _HEADER.fullmatch(secret.header_name)
                or secret.header_name.lower() in RESERVED_AUTH_HEADER_NAMES
            ):
                raise refuse(FailureCode.credential_unavailable)
            headers.append((secret.header_name, secret.value))
        else:
            raise refuse(FailureCode.credential_unavailable)
        if any("\r" in value or "\n" in value for _, value in headers):
            raise refuse(FailureCode.credential_unavailable)
    body = encode(request.json_body).rstrip(b"\n") if request.method == HttpMethod.POST else None
    if body is not None:
        headers.append(("Content-Type", "application/json"))
    return AuthenticatedHttpRequest(normalized, request.method, tuple(headers), body)


def secret_patterns(config: BrokerConfiguration) -> tuple[str, ...]:
    values: set[str] = set()
    for credential in config.credentials:
        secret = credential.secret
        if isinstance(secret, BearerSecret):
            raw = [secret.token]
        elif isinstance(secret, HeaderSecret):
            raw = [secret.value]
        else:
            raw = [secret.username, secret.password, f"{secret.username}:{secret.password}"]
        for value in raw:
            if value:
                values.update(
                    (value, quote(value, safe=""), base64.b64encode(value.encode()).decode())
                )
                values.add(base64.urlsafe_b64encode(value.encode()).decode())
    return tuple(sorted(values, key=len, reverse=True))


def redact(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        text = text.replace(pattern, "[REDACTED]")
    return text
