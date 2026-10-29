"""Temporary one-attempt operator entry path, separate from native authority.

Callbacks use the existing reservation, arm, preview and result transports. Nothing
here retries, rearms, refreshes provider input, or changes those wire contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from planner.conversation.model_resolution.diagnostic_contracts import validate_record
from planner.executor.contracts import JsonObject

_SOURCE_NAMES = frozenset({"operator_script", "operator_module", "receipt_contract"})


@dataclass(frozen=True)
class ReviewedBinding:
    application_revision: str
    operator_revision: str
    source_sha256: Mapping[str, str]


def read_binding(value: object) -> ReviewedBinding:
    """Read handoff metadata, not a diagnostic receipt or a new native protocol."""
    if type(value) is not dict or value.keys() != {
        "application_revision", "operator_revision", "source_sha256",
    }:
        raise ValueError("Invalid reviewed operator binding")
    application = value["application_revision"]
    operator = value["operator_revision"]
    if (type(application) is not str or not re.fullmatch(r"[0-9a-f]{40}", application)
            or operator != application):
        raise ValueError("Application and operator revisions must match")
    sources = value["source_sha256"]
    if type(sources) is not dict or sources.keys() != _SOURCE_NAMES:
        raise ValueError("Incomplete reviewed operator sources")
    checked: dict[str, str] = {}
    for name, digest in sources.items():
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid reviewed operator source digest")
        checked[name] = digest
    return ReviewedBinding(application, application, checked)


def verify_sources(binding: ReviewedBinding, sources: Mapping[str, bytes]) -> None:
    if (binding.application_revision != binding.operator_revision
            or set(binding.source_sha256) != _SOURCE_NAMES or set(sources) != _SOURCE_NAMES):
        raise ValueError("Mismatched reviewed operator package")
    for name, content in sources.items():
        if hashlib.sha256(content).hexdigest() != binding.source_sha256[name]:
            raise ValueError("Mismatched reviewed operator source")


def retrieve_once(
    retrieve: Callable[[], object], preserve: Callable[[JsonObject], None],
) -> JsonObject:
    """Consume once, validate closed finite output, then retain only that output.

    Any exception stops the caller. A failed transport can leave consumption
    uncertain. A validation or storage failure cannot authorize a second read.
    """
    value = validate_record(retrieve())
    if len(json.dumps(value).encode()) > 4096:
        raise ValueError("Diagnostic receipt exceeds bound")
    preserve(value)
    return value


def run_once(
    *,
    binding: ReviewedBinding,
    sources: Mapping[str, bytes],
    http: Callable[[str, JsonObject | None, Mapping[str, str]], tuple[int, JsonObject]],
    native_once: Callable[[str, JsonObject], object],
    body: JsonObject,
    expected_workspace: JsonObject,
    preserve_ordinary: Callable[[JsonObject], None],
    preserve_finite: Callable[[JsonObject], None],
    clock: Callable[[], float] = time.monotonic,
) -> JsonObject:
    """Run the retained operator's one reservation/arm/preview/retrieval path.

    Source verification and both installed revision checks precede reservation
    and arm. The application Worker must independently verify package identity
    before it authorizes this function in a later window.
    """
    verify_sources(binding, sources)
    _, health = http("/api/health?expected_sha=" + binding.application_revision, None, {})
    _, native = http("/api/executor/health", None, {})
    identity = native.get("identity")
    if (health.get("ready") is not True
            or health.get("app_sha") != binding.application_revision
            or native.get("reconciliation_ready") is not True
            or not isinstance(identity, dict)
            or identity.get("application_revision") != binding.application_revision
            or identity.get("protocol_version") != 12
            or identity.get("spool_format_version") != 2):
        raise ValueError("Installed application and operator packages do not match")
    _, reservation = http(
        "/api/conversation/backends/model-choices/diagnostic-reservation", body, {},
    )
    reserved_at = clock()
    if reservation.keys() != {"reservation", "arm"}:
        raise ValueError("Invalid diagnostic reservation")
    reservation_id = reservation["reservation"]
    arm = reservation["arm"]
    if not isinstance(reservation_id, str) or not isinstance(arm, dict):
        raise ValueError("Invalid diagnostic reservation")
    UUID(reservation_id)
    if arm.keys() != {"request_id", "backend_key", "expected_context"}:
        raise ValueError("Invalid diagnostic arm")
    request_id = arm["request_id"]
    context = arm["expected_context"]
    if not isinstance(request_id, str) or not isinstance(context, dict):
        raise ValueError("Invalid diagnostic arm")
    UUID(request_id)
    if (arm["backend_key"] != "claude" or context.get("executor_identity") != identity
            or context.get("workspace") != expected_workspace
            or len(json.dumps(arm).encode()) > 4096):
        raise ValueError("Diagnostic arm differs from reviewed context")
    if native_once("arm", {"arm": arm}) != {"armed": True}:
        raise ValueError("Diagnostic arm failed")
    if clock() - reserved_at >= 50:
        raise ValueError("Reservation deadline expired")
    _, ordinary = http(
        "/api/conversation/backends/model-choices", body,
        {"X-Panels-Catalog-Diagnostic-Reservation": reservation_id},
    )
    settled_at = clock()
    preserve_ordinary(ordinary)
    if clock() - settled_at >= 50:
        raise ValueError("Receipt retrieval deadline expired")
    return retrieve_once(
        lambda: native_once("result", {"request_id": request_id}), preserve_finite,
    )
