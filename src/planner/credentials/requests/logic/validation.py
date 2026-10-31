"""Bounded public metadata and canonical snapshot decoding."""

import re
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from planner.credentials.logic.validation import invalid, text
from planner.credentials.requests.contracts import (
    CreateCredentialRequestBody,
    CredentialRequestRowBody,
)


def decode_snapshot(value: object) -> CredentialRequestRowBody:
    try:
        return TypeAdapter(CredentialRequestRowBody).validate_python(value)
    except ValidationError:
        raise ValueError("invalid credential request snapshot") from None


def validate_request(value: object) -> CreateCredentialRequestBody:
    if not isinstance(value, dict) or set(value) != {
        "request_id",
        "name",
        "provider",
        "use",
        "auth_kind",
        "header_name",
        "help",
        "help_url",
        "replacement",
    }:
        raise invalid("invalid credential request metadata")
    try:
        request_id = str(UUID(text(value["request_id"], maximum=36)))
    except (ValueError, AttributeError):
        raise invalid("request_id must be a UUID") from None
    kind = value["auth_kind"]
    if kind not in ("bearer", "header"):
        raise invalid("API key requests support bearer and header credentials")
    header = value["header_name"]
    if kind == "header":
        if not isinstance(header, str) or not re.fullmatch(
            r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,200}", header
        ):
            raise invalid("a header request needs its header name")
    elif header is not None:
        raise invalid("bearer requests do not name a header")
    hint = None if value["help"] is None else text(value["help"], maximum=1000).strip()
    url = None if value["help_url"] is None else text(value["help_url"], maximum=2000).strip()
    if url is not None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise invalid("credential help links require an HTTPS URL")
    replacement = value["replacement"]
    if replacement is not None:
        if not isinstance(replacement, dict) or set(replacement) != {
            "credential_id",
            "name",
            "revision",
        }:
            raise invalid("invalid replacement metadata")
        text(replacement["credential_id"], maximum=200)
        text(replacement["name"], maximum=200)
        if type(replacement["revision"]) is not int or replacement["revision"] < 1:
            raise invalid("invalid credential revision")
    return {
        "request_id": request_id,
        "name": text(value["name"], maximum=200).strip(),
        "provider": text(value["provider"], maximum=200).strip(),
        "use": text(value["use"], maximum=2000).strip(),
        "auth_kind": kind,
        "header_name": header,
        "help": hint,
        "help_url": url,
        "replacement": replacement,
    }
