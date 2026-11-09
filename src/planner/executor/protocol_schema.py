"""Fingerprint the concrete executor contract, including nested event payloads.

WireMessage.payload and envelope payloads are JSON dictionaries, so their schemas
alone cannot detect changes to the concrete values that the codecs put in them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import is_dataclass
from typing import Any, TypedDict, cast

from planner.conversation.snapshot import BackendSnapshot
from planner.execution_configuration import (
    COMMAND_JOURNAL_FORMAT_VERSION,
    PROTOCOL_VERSION,
    SPOOL_FORMAT_VERSION,
)
from planner.executor import protocol
from planner.executor.contracts import COMMAND_JOURNAL_SCHEMA
from planner.executor.mac_outbox import SCHEMA


class ProtocolManifest(TypedDict):
    protocol_version: int
    schema_sha256: str
    spool_format_version: int
    spool_schema_sha256: str
    command_journal_format_version: int
    command_journal_schema_sha256: str


# A previous manifest from before a versioned schema existed is its starting point: the
# first recorded digest needs no increase.
_VERSIONED_SCHEMAS = (
    ("protocol_version", "schema_sha256"),
    ("spool_format_version", "spool_schema_sha256"),
    ("command_journal_format_version", "command_journal_schema_sha256"),
)


def _without_documentation(value: object, *, named_schemas: bool = False) -> object:
    if isinstance(value, dict):
        # Properties named "title" or "description" are contract fields, not
        # documentation. Strip annotations only from actual schema nodes.
        return {
            key: _without_documentation(
                child,
                named_schemas=not named_schemas and key in ("properties", "$defs"),
            )
            for key, child in value.items()
            if named_schemas or key not in ("description", "title")
        }
    if isinstance(value, list):
        return [_without_documentation(child) for child in value]
    return value


def protocol_schemas() -> dict[str, object]:
    """Include all protocol dataclasses, imported nested types and event codecs."""
    roots = {
        name: value
        for name, value in vars(protocol).items()
        if isinstance(value, type) and is_dataclass(value)
    }
    roots["BackendSnapshot"] = BackendSnapshot
    return {
        "types": {
            name: _without_documentation(protocol.type_adapter(value).json_schema())
            for name, value in sorted(roots.items())
        },
        "event_codecs": {
            method.value: f"{value.__module__}.{value.__qualname__}"
            for method, value in protocol._BACKEND_EVENT_TYPES.items()
        },
    }


def protocol_manifest() -> ProtocolManifest:
    encoded = json.dumps(protocol_schemas(), sort_keys=True, separators=(",", ":")).encode()
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_sha256": hashlib.sha256(encoded).hexdigest(),
        "spool_format_version": SPOOL_FORMAT_VERSION,
        "spool_schema_sha256": hashlib.sha256(SCHEMA.encode()).hexdigest(),
        "command_journal_format_version": COMMAND_JOURNAL_FORMAT_VERSION,
        "command_journal_schema_sha256": hashlib.sha256(
            COMMAND_JOURNAL_SCHEMA.encode()
        ).hexdigest(),
    }


def check_protocol_manifest(
    actual: ProtocolManifest,
    recorded: ProtocolManifest,
    previous: ProtocolManifest | None = None,
) -> None:
    if actual != recorded:
        raise ValueError("Executor schema changed. Update the version and protocol manifest.")
    if previous is None:
        return
    old, new = cast(Mapping[str, Any], previous), cast(Mapping[str, Any], actual)
    for version_name, digest_name in _VERSIONED_SCHEMAS:
        if version_name not in old or digest_name not in old:
            continue
        old_version, new_version = old[version_name], new[version_name]
        old_digest, new_digest = old[digest_name], new[digest_name]
        if new_version < old_version or (old_digest != new_digest and new_version <= old_version):
            raise ValueError(f"Executor schema changed without an increase of {version_name}.")
