"""Protect consumed receipt storage and package mismatch before an arm."""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from click.testing import CliRunner

from planner.conversation.model_resolution.diagnostic_contracts import unavailable_record
from planner.executor.catalog_diagnostic_operator import (
    ReviewedBinding,
    read_binding,
    retrieve_once,
    run_once,
)
from planner.executor.contracts import IpcOperation, IpcResponse, JsonObject

REVISION = "a" * 40
REQUEST_ID = "773efcbd-04ac-4c7a-bd17-c59a03ddfe89"
RESERVATION_ID = "bd0c969c-ab75-495d-8b79-04b3ef838009"
SOURCES = {"operator_script": b"script", "operator_module": b"module",
           "receipt_contract": b"contract"}
WORKSPACE: JsonObject = {
    "workspace_folder": "/synthetic", "workspace_path_policy": "native_absolute",
    "workspace_home_relative": None,
}


def _binding() -> ReviewedBinding:
    return read_binding({
        "application_revision": REVISION,
        "operator_revision": REVISION,
        "source_sha256": {name: hashlib.sha256(value).hexdigest()
                          for name, value in SOURCES.items()},
    })


class _Transport:
    def __init__(self, record: object) -> None:
        self.record = record
        self.calls: list[str] = []
        self.revision = REVISION
        self.identity: JsonObject = {
            "application_revision": REVISION, "protocol_version": 12, "spool_format_version": 2,
        }

    def http(
        self, path: str, body: JsonObject | None, headers: Mapping[str, str],
    ) -> tuple[int, JsonObject]:
        if path.startswith("/api/health?"):
            self.calls.append("app_health")
            return 200, {"ready": True, "app_sha": self.revision}
        if path == "/api/executor/health":
            self.calls.append("native_health")
            return 200, {"reconciliation_ready": True, "identity": self.identity}
        if path.endswith("diagnostic-reservation"):
            self.calls.append("reserve")
            return 200, {"reservation": RESERVATION_ID, "arm": {
                "request_id": REQUEST_ID, "backend_key": "claude", "expected_context": {
                    "executor_identity": self.identity, "workspace": WORKSPACE,
                },
            }}
        assert path == "/api/conversation/backends/model-choices"
        assert headers == {"X-Panels-Catalog-Diagnostic-Reservation": RESERVATION_ID}
        self.calls.append("preview")
        return 200, {"ordinary": "unchanged"}

    def native(self, operation: str, payload: JsonObject) -> object:
        self.calls.append(operation)
        if operation == "arm":
            return {"armed": True}
        assert operation == "result"
        assert payload == {"request_id": REQUEST_ID}
        return self.record


def _run(transport: _Transport, output: Path, sources: Mapping[str, bytes] = SOURCES) -> JsonObject:
    def preserve(value: JsonObject) -> None:
        with output.open("x") as stream:
            json.dump(value, stream)

    return run_once(
        binding=_binding(), sources=sources, http=transport.http, native_once=transport.native,
        body={"ticket_id": "synthetic"}, expected_workspace=WORKSPACE,
        preserve_ordinary=lambda value: transport.calls.append("preserve_ordinary"),
        preserve_finite=preserve,
    )


def test_operator_retrieval_validates_new_receipt_and_retains_it(tmp_path: Path) -> None:
    """The old script consumed a valid new receipt and then rejected its extra key."""
    record = unavailable_record("capture_missing")
    record["provider_source_snapshot"] = "changed"
    transport = _Transport(record)
    output = tmp_path / "finite.json"
    assert _run(transport, output) == record
    assert json.loads(output.read_text()) == record
    assert transport.calls == ["app_health", "native_health", "reserve", "arm", "preview",
                               "preserve_ordinary", "result"]


@pytest.mark.parametrize("failure", ["application_revision", "native_revision", "source_bytes"])
def test_operator_rejects_mismatched_packages_before_reservation(
    tmp_path: Path, failure: str,
) -> None:
    transport = _Transport(unavailable_record())
    sources = SOURCES.copy()
    if failure == "application_revision":
        transport.revision = "b" * 40
    elif failure == "native_revision":
        transport.identity["application_revision"] = "b" * 40
    else:
        sources["receipt_contract"] = b"old validator"
    with pytest.raises(ValueError, match="match"):
        _run(transport, tmp_path / "finite.json", sources)
    assert "reserve" not in transport.calls
    assert "arm" not in transport.calls


@pytest.mark.parametrize("failure", ["old_receipt", "unexpected_field", "unexpected_role"])
def test_operator_rejects_unclassified_or_extended_receipt_without_retry(
    tmp_path: Path, failure: str,
) -> None:
    record = unavailable_record()
    if failure == "old_receipt":
        del record["diagnostic_status"]
    elif failure == "unexpected_field":
        record["secret"] = "never retain"
    else:
        roles = cast(JsonObject, record["roles"])
        roles["unexpected_role"] = "never retain"
    transport = _Transport(record)
    output = tmp_path / "finite.json"
    with pytest.raises(ValueError, match="Invalid diagnostic record"):
        _run(transport, output)
    assert transport.calls.count("result") == 1
    assert transport.calls.count("arm") == 1
    assert not output.exists()


def test_operator_storage_failure_does_not_repeat_consumed_retrieval() -> None:
    calls: list[str] = []

    def retrieve() -> object:
        calls.append("retrieve")
        return unavailable_record("cancelled")

    def preserve(value: JsonObject) -> None:
        calls.append("preserve")
        raise OSError("storage unavailable")

    with pytest.raises(OSError, match="storage unavailable"):
        retrieve_once(retrieve, preserve)
    assert calls == ["retrieve", "preserve"]


def test_operator_binding_rejects_mixed_revisions() -> None:
    with pytest.raises(ValueError, match="revisions must match"):
        read_binding({"application_revision": REVISION, "operator_revision": "b" * 40,
                      "source_sha256": {}})


def _legacy_module(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = (Path(__file__).parents[1] / "fixtures" / "catalog_diagnostic_684f29d5"
            / (name + ".py.txt"))
    digests = {
        "cli": "fa66d0d262f8fe307c1624183512841ce4e92b23ec8bf18233d0cbd972ec9f93",
        "diagnostics": "aa2761ade08b54f53dc5c91926d51b014aa8ab8542b3e9b4c1a1a3c0dc922a85",
    }
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == digests[name]
    module = ModuleType("_retained_684f29d5_" + name)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def test_exact_old_validator_rejects_new_receipt_and_keeps_old_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _legacy_module("diagnostics", monkeypatch)
    historical: object = old.unavailable_record()
    assert isinstance(historical, dict)
    assert "diagnostic_status" not in historical
    assert old.validate_record(historical) == historical
    new = unavailable_record("capture_missing")
    with pytest.raises(ValueError, match="Invalid diagnostic record"):
        old.validate_record(new)
    with pytest.raises(ValueError, match="Invalid diagnostic record"):
        retrieve_once(lambda: historical, lambda value: pytest.fail("Old evidence reclassified"))
    assert "diagnostic_status" not in historical


def test_exact_old_generic_cli_prints_new_finite_result_without_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _legacy_module("cli", monkeypatch)
    record = unavailable_record("capture_missing")
    calls: list[IpcOperation] = []

    def request(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
        assert payload == {"request_id": REQUEST_ID}
        calls.append(operation)
        return IpcResponse(REQUEST_ID, record, None)

    monkeypatch.setattr(old, "request", request)
    result = CliRunner().invoke(old.catalog_diagnostic_result, ["--request-id", REQUEST_ID])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == record
    assert calls == [IpcOperation.catalog_diagnostic_result]
