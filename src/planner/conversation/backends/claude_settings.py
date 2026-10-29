"""Private Claude control snapshots and destination-local scope receipts.

Raw settings never leave this adapter. Opaque references name private equality,
not credential hashes or complete applied process state.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from claude_agent_sdk import Transport

from planner.conversation.backends.claude_settings_config import (
    CONTROL_TIMEOUT_SECONDS,
    MAX_RETAINED_SCOPES,
)
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendUserEmissionReceipt,
    UserEmissionRequirement,
)
from planner.conversation.model_resolution.diagnostics import offer_provider_comparison

_SOURCE_NAMES = (
    "userSettings", "projectSettings", "localSettings", "flagSettings", "policySettings",
)
_EFFORTS = frozenset(("low", "medium", "high", "xhigh", "max"))


class ClaudeSettingsUnavailable(Exception):
    """A sanitized failure to obtain a complete supported control snapshot."""


class ClaudeControlTransport(Transport):
    """Use the SDK's custom transport seam to own metadata response futures."""

    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self._metadata_responses: dict[str, asyncio.Future[dict[str, Any] | None]] = {}

    async def connect(self) -> None:
        await self._inner.connect()

    async def write(self, data: str) -> None:
        await self._inner.write(data)

    async def control_request(self, subtype: str) -> dict[str, Any]:
        if subtype not in {"get_settings", "list_models"}:
            raise ClaudeSettingsUnavailable("Unsupported Claude metadata request")
        identity = f"panels-metadata-{uuid4().hex}"
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        self._metadata_responses[identity] = future
        try:
            async with asyncio.timeout(CONTROL_TIMEOUT_SECONDS):
                await self.write(json.dumps({"type": "control_request", "request_id": identity,
                                             "request": {"subtype": subtype}}) + "\n")
                result = await asyncio.shield(future)
            if result is None:
                raise ClaudeSettingsUnavailable("Claude metadata response unavailable")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ClaudeSettingsUnavailable("Claude metadata response unavailable") from None
        finally:
            self._metadata_responses.pop(identity, None)
            if not future.done():
                future.cancel()

    def observe_control(self, message: dict[str, Any]) -> None:
        if message.get("type") != "control_response":
            return
        response = message.get("response")
        if not isinstance(response, dict):
            return
        identity = response.get("request_id")
        future = self._metadata_responses.get(identity) if isinstance(identity, str) else None
        if future is None or future.done():
            return
        payload = response.get("response")
        future.set_result(payload if response.get("subtype") == "success"
                          and isinstance(payload, dict) else None)

    def settle_control(self) -> None:
        for future in self._metadata_responses.values():
            if not future.done():
                future.set_result(None)
        self._metadata_responses.clear()

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        try:
            async for message in self._inner.read_messages():
                self.observe_control(message)
                yield message
        finally:
            self.settle_control()

    async def close(self) -> None:
        self.settle_control()
        await self._inner.close()

    async def end_input(self) -> None:
        await self._inner.end_input()

    def is_ready(self) -> bool:
        return self._inner.is_ready()


def _json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def _same_json(left: object, right: object) -> bool:
    # Python equality conflates a JSON boolean and number (True == 1).
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeSettingsSnapshot:
    effective: dict[str, Any]
    sources: tuple[tuple[str, dict[str, Any]], ...]
    applied_model: str
    applied_effort: str | None

    def same_sources(self, other: ClaudeSettingsSnapshot) -> bool:
        return (
            _same_json(self.effective, other.effective)
            and _same_json(self.sources, other.sources)
        )


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeCatalogSettingsSnapshot:
    settings: ClaudeSettingsSnapshot
    models: tuple[dict[str, Any], ...]


def _settings_snapshot(payload: dict[str, Any]) -> ClaudeSettingsSnapshot:
    if not _json_value(payload):
        raise ClaudeSettingsUnavailable("Claude settings response is incompatible")
    effective, sources, applied = (payload.get(key) for key in ("effective", "sources", "applied"))
    if (not isinstance(effective, dict) or not isinstance(sources, list)
            or not isinstance(applied, dict) or "effort" not in applied or payload.get("errors")):
        raise ClaudeSettingsUnavailable("Claude settings response is incompatible")
    parsed: list[tuple[str, dict[str, Any]]] = []
    last_source = -1
    for entry in sources:
        if not isinstance(entry, dict):
            raise ClaudeSettingsUnavailable("Claude settings sources are incompatible")
        name, settings = entry.get("source"), entry.get("settings")
        if name not in _SOURCE_NAMES or not isinstance(settings, dict):
            raise ClaudeSettingsUnavailable("Claude settings sources are incompatible")
        index = _SOURCE_NAMES.index(name)
        if index <= last_source:
            raise ClaudeSettingsUnavailable("Claude settings sources are incompatible")
        last_source = index
        parsed.append((name, copy.deepcopy(settings)))
    model, effort = applied.get("model"), applied.get("effort")
    if (not isinstance(model, str) or not model
            or effort is not None and (not isinstance(effort, str) or effort not in _EFFORTS)):
        raise ClaudeSettingsUnavailable("Claude applied selection is incompatible")
    return ClaudeSettingsSnapshot(copy.deepcopy(effective), tuple(parsed), model, effort)


async def read_claude_catalog_settings_snapshot(
    transport: ClaudeControlTransport,
) -> ClaudeCatalogSettingsSnapshot:
    try:
        return await _read_claude_catalog_settings_snapshot(transport)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ClaudeSettingsUnavailable("Claude metadata snapshot unavailable") from None


async def _read_claude_catalog_settings_snapshot(
    transport: ClaudeControlTransport,
) -> ClaudeCatalogSettingsSnapshot:
    before = _settings_snapshot(await transport.control_request("get_settings"))
    catalog = await transport.control_request("list_models")
    rows = catalog.get("models")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ClaudeSettingsUnavailable("Claude model response is incompatible")
    after = _settings_snapshot(await transport.control_request("get_settings"))
    sources_equal = before.same_sources(after)
    offer_provider_comparison(sources_equal)
    if not sources_equal:
        raise ClaudeSettingsUnavailable("Claude settings changed during observation")
    return ClaudeCatalogSettingsSnapshot(after, tuple(copy.deepcopy(rows)))


class ClaudeSettingsScopeRegistry:
    """One destination's bounded private equality map and single-use receipts."""

    def __init__(self) -> None:
        self._scopes: OrderedDict[str, ClaudeSettingsSnapshot] = OrderedDict()
        self._receipts: dict[str, BackendUserEmissionReceipt] = {}

    def contains(self, scope_ref: str) -> bool:
        return scope_ref in self._scopes

    def retain(self, snapshot: ClaudeSettingsSnapshot) -> str:
        for reference, previous in self._scopes.items():
            if previous.same_sources(snapshot):
                self._scopes.move_to_end(reference)
                return reference
        reference = uuid4().hex
        self._scopes[reference] = snapshot
        if len(self._scopes) > MAX_RETAINED_SCOPES:
            self._scopes.popitem(last=False)
        return reference

    def matches(self, scope_ref: str, snapshot: ClaudeSettingsSnapshot) -> bool:
        retained = self._scopes.get(scope_ref)
        return retained is not None and retained.same_sources(snapshot)

    def issue_receipt(
        self, sample: ClaudeCatalogSettingsSnapshot, requirement: UserEmissionRequirement,
        *, child_lifetime: str, message_uuid: str,
    ) -> BackendUserEmissionReceipt:
        if not self.matches(requirement.scope_ref, sample.settings):
            raise AdmissionRefused("model verification Claude settings changed")
        rows = [row for row in sample.models if row.get("value") == requirement.submitted_token]
        if not rows:
            raise AdmissionRefused("model verification Claude selection unproved")
        facts = [(row.get("resolvedModel"), row.get("disabled"), row.get("hidden"),
                  row.get("supportedEffortLevels")) for row in rows]
        if any(fact != facts[0] for fact in facts[1:]):
            raise AdmissionRefused("model verification Claude selection conflicts")
        for row in rows:
            if (row.get("hidden") is True or row.get("resolvedModel") != requirement.resolved_model
                    or row.get("disabled") is not False and row.get("disabled") is not None):
                raise AdmissionRefused("model verification Claude selection changed")
            if requirement.reasoning_effort is not None:
                efforts = row.get("supportedEffortLevels")
                if not isinstance(efforts, list) or requirement.reasoning_effort not in efforts:
                    raise AdmissionRefused("model verification Claude effort unproved")
        if (sample.settings.applied_model != requirement.resolved_model
                or requirement.reasoning_effort is not None
                and sample.settings.applied_effort != requirement.reasoning_effort):
            raise AdmissionRefused("model verification Claude applied selection changed")
        receipt = BackendUserEmissionReceipt(
            uuid4().hex, requirement.scope_ref, child_lifetime, message_uuid,
            requirement.attempt_id, requirement.turn, requirement.resolved_model,
            requirement.reasoning_effort,
        )
        self._receipts[receipt.receipt_ref] = receipt
        return receipt

    def consume(
        self, receipt: BackendUserEmissionReceipt, *, child_lifetime: str, message_uuid: str,
    ) -> None:
        kept = self._receipts.pop(receipt.receipt_ref, None)
        if (kept is not receipt or receipt.child_lifetime != child_lifetime
                or receipt.message_uuid != message_uuid or not self.contains(receipt.scope_ref)):
            raise AdmissionRefused("model verification Claude user receipt unproved")

    def discard(self, receipt: BackendUserEmissionReceipt) -> None:
        self._receipts.pop(receipt.receipt_ref, None)

    def discard_child(self, child_lifetime: str) -> None:
        self._receipts = {key: value for key, value in self._receipts.items()
                          if value.child_lifetime != child_lifetime}
