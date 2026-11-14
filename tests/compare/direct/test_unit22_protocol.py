"""Complete fixed-root exchange and source-mode proof for the executor codecs.

The type freshness check proves no runtime behavior. This single pure exchange
protects every root, required nulls/defaults, numeric modes, custom validators,
smart piece selection, and actual event classes. It constructs no database,
listener, provider, or Mac peer. Lexical parity has its separate existing proof.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError
from tests.compare.direct.export_unit22_wire import export_wire

from planner import execution_identity as identity_contract
from planner.conversation.snapshot import BackendSnapshot
from planner.execution_identity import ProtocolRefused
from planner.executor import protocol as wire
from planner.executor import update_contracts
from planner.executor.contracts import ExecutorHealthRead

ROOT = Path(__file__).resolve().parents[3]


def _root(name: str) -> Any:
    return (
        {
            "BackendSnapshot": BackendSnapshot,
            "ExecutorHealthRead": ExecutorHealthRead,
            "JsonObject": dict,
        }.get(name)
        or getattr(wire, name, None)
        or getattr(update_contracts, name)
    )


def _body(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _fixture_value(tree: list[Any]) -> Any:
    tag, value = tree
    if tag == "primitive":
        return value
    if tag == "int":
        return int(value)
    if tag == "float":
        return struct.unpack(">d", bytes.fromhex(value))[0]
    if tag == "array":
        return [_fixture_value(item) for item in value]
    assert tag == "object", tag
    return {key: _fixture_value(item) for key, item in value}


class _Reader:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.position = 0

    async def readexactly(self, length: int) -> bytes:
        chunk = self.body[self.position : self.position + length]
        self.position += length
        if len(chunk) != length:
            raise EOFError("fixture frame ended early")
        return chunk


def _cases(fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    def case(name: str, root: str, value: Any, **options: Any) -> dict[str, Any]:
        return {"name": name, "root": root, "dto": value, **options}

    command = deepcopy(fixtures["ChildCommand"])
    command["arguments"]["sender_message_count"] = "2.0"
    command["arguments"].pop("kind")
    command.pop("file_dependencies")
    changed = deepcopy(fixtures["ChildCommand"])
    changed["arguments"]["kind"] = "write_prompt"
    changed["operation"] = "stop"
    answer_command = {
        "command_id": "wrong",
        "child": fixtures["ChildReference"],
        "turn": fixtures["TurnToken"],
        "operation": "answer_permission_ask",
        "arguments": fixtures["AnswerPermission"],
    }
    ambiguous = {
        "content": [
            {
                "text": "extra",
                "stored_file_id": "image",
                "media_type": "image/png",
                "file_name": "a",
                "byte_count": "bad",
            }
        ]
    }
    deferred = deepcopy(fixtures["ItemBatch"])
    deferred["items"][0]["envelope"]["payload"] = {"malformed": True}
    tuple_start = deepcopy(fixtures["ResolvedConversationStart"])
    tuple_start["role_materials"]["identity_environment_variables"] = [["PLAN_ACTOR"]]
    nested = deepcopy(fixtures["LeaseRequest"])
    nested["identity"]["protocol_version"] = "wrong"
    cases = [
        case(
            "lax-http-bool-extra",
            "ModelListRequest",
            {"backend_key": "codex", "refresh": "YES", "future": 7},
        ),
        case(
            "lax-journal-int",
            "TurnToken",
            {"conversation_id": "conversation", "turn_number": " 2.0 "},
            operation="json",
        ),
        case("lax-float", "LeaseReply", {**fixtures["LeaseReply"], "away_after_seconds": "1_2.5"}),
        case(
            "long-space-number", "TokenUsageReported", {"cost_usd": "1" + " " * (500 * 1024) + "1"}
        ),
        case(
            "lax-int-float-overflow",
            "WaitingConversation",
            {"conversation_id": "conversation", "oldest_sequence": 1e19},
        ),
        case(
            "int-positive-bound",
            "WaitingConversation",
            {"conversation_id": "conversation", "oldest_sequence": float(2**63)},
        ),
        case(
            "int-negative-bound",
            "WaitingConversation",
            {"conversation_id": "conversation", "oldest_sequence": -float(2**63)},
        ),
        case(
            "structured-unsafe-int",
            "WaitingConversation",
            {"conversation_id": "conversation", "oldest_sequence": 10000000000000000000},
        ),
        case("tuple-wrong-input", "LiveEventBatch", {"events": 1}),
        case("live-read-reply-not-object", "JsonObject", []),
        case("path-wrong-input", "FileSnapshot", {**fixtures["FileSnapshot"], "local_path": 7}),
        case("dataclass-wrong-input", "ChildReference", []),
        case(
            "enum-and-literal-errors",
            "WritePrompt",
            {**fixtures["WritePrompt"], "kind": "future", "mode": "future"},
        ),
        case(
            "lax-float-negative-zero-string",
            "LeaseReply",
            {**fixtures["LeaseReply"], "away_after_seconds": "-0.0"},
        ),
        case(
            "integer-negative-zero-float",
            "LeaseReply",
            {**fixtures["LeaseReply"], "away_after_seconds": 0},
        ),
        case(
            "strict-json-scalar",
            "TurnToken",
            {"conversation_id": "conversation", "turn_number": 3.0},
            strict=True,
        ),
        case(
            "strict-update",
            "MacUpdateReport",
            {
                **fixtures["MacUpdateReport"],
                "identity": {**fixtures["ExecutorIdentity"], "protocol_version": 22.0},
            },
            strict=True,
        ),
        case("strict-frame-lax-arguments", "ChildCommand", command, operation="frame"),
        case(
            "strict-frame-tuple-refusal",
            "ChildCommand",
            {**command, "file_dependencies": fixtures["ChildCommand"]["file_dependencies"]},
            operation="frame",
        ),
        case(
            "strict-frame-refusal",
            "TurnToken",
            {"conversation_id": "conversation", "turn_number": 3.0},
            operation="frame",
        ),
        case(
            "explicit-null", "IpcResponse", {"request_id": "request", "result": None, "error": None}
        ),
        case("missing-nullable", "IpcResponse", {"request_id": "request", "result": {}}),
        case("forbidden-extra", "IpcRetryPayload", {"submission_id": "submission", "future": 7}),
        case("ordered-scalar-errors", "ModelListRequest", {"backend_key": 7, "refresh": "maybe"}),
        case("nested-error", "LeaseRequest", nested),
        case("fixed-tuple", "ResolvedConversationStart", tuple_start),
        case("smart-piece", "AgentMessageCompleted", ambiguous),
        case(
            "command-missing-operation",
            "ChildCommand",
            {key: value for key, value in command.items() if key != "operation"},
        ),
        case(
            "command-missing-arguments",
            "ChildCommand",
            {key: value for key, value in command.items() if key != "arguments"},
        ),
        case("command-conflicting-kind", "ChildCommand", changed),
        case(
            "command-null-arguments",
            "ChildCommand",
            {**fixtures["ChildCommand"], "arguments": None},
        ),
        case(
            "command-list-arguments", "ChildCommand", {**fixtures["ChildCommand"], "arguments": []}
        ),
        case(
            "command-text-arguments",
            "ChildCommand",
            {**fixtures["ChildCommand"], "arguments": "bad"},
        ),
        case("command-missing-turn", "ChildCommand", {**fixtures["ChildCommand"], "turn": None}),
        case("durable-answer-conflict", "ChildCommand", answer_command),
        case(
            "durable-user-answer-command",
            "ChildCommand",
            {
                "command_id": "answer",
                "child": fixtures["ChildReference"],
                "turn": fixtures["TurnToken"],
                "operation": "answer_user_input",
                "arguments": fixtures["AnswerUserInput"],
            },
        ),
        case(
            "mailbox-no-payload",
            "MailboxCommand",
            {"command_sequence": 1, "command_id": "prompt", "created_at_vps_ms": 100},
        ),
        case(
            "mailbox-two-payloads",
            "MailboxCommand",
            {**fixtures["MailboxCommand"], "model_list_request": fixtures["ModelListRequest"]},
        ),
        case(
            "mailbox-conflicting-id",
            "MailboxCommand",
            {**fixtures["MailboxCommand"], "command_id": "wrong"},
        ),
        case(
            "enum-literal-success",
            "MacUpdateStatus",
            {**fixtures["MacUpdateStatus"], "phase": "unavailable"},
        ),
        case(
            "enum-literal-failure",
            "MacUpdateStatus",
            {**fixtures["MacUpdateStatus"], "phase": "future"},
        ),
        case("deferred-open-payload", "ItemBatch", deferred),
        case("consumer-value-refusal", "BackendEvent", {"malformed": True}, operation="value"),
        case("python-value-path", "FileSnapshot", fixtures["FileSnapshot"], operation="value"),
        case(
            "nonfinite-json",
            "IpcRequest",
            {**fixtures["IpcRequest"], "response_deadline": float("inf")},
        ),
        case("nonfinite-lax-string", "TokenUsageReported", {"cost_usd": "NaN"}),
        case(
            "bounded-body",
            "CanonicalSkillSource",
            fixtures["CanonicalSkillSource"],
            limit=len(_body(fixtures["CanonicalSkillSource"])) - 1,
        ),
        case(
            "bounded-encode",
            "CanonicalSkillSource",
            fixtures["CanonicalSkillSource"],
            operation="encode",
            limit=1,
        ),
        case(
            "encode-missing-required",
            "LeaseRequest",
            {
                key: value
                for key, value in fixtures["LeaseRequest"].items()
                if key != "oldest_item_age_ms"
            },
            operation="encode",
        ),
        case(
            "encode-mailbox-semantic",
            "MailboxCommand",
            {"command_sequence": 1, "command_id": "prompt", "created_at_vps_ms": 100},
            operation="encode",
        ),
        case(
            "encode-command-semantic",
            "ChildCommand",
            {**fixtures["ChildCommand"], "turn": None},
            operation="encode",
        ),
        case(
            "native-default-undefined",
            "ExecutorIdentity",
            {
                key: value
                for key, value in fixtures["ExecutorIdentity"].items()
                if key != "connection_generation"
            },
            operation="native-default",
        ),
        case(
            "native-surrogate",
            "CanonicalSkillSource",
            fixtures["CanonicalSkillSource"],
            operation="native-surrogate",
        ),
        case(
            "native-int-overflow",
            "WaitingConversation",
            {"conversation_id": "conversation", "oldest_sequence": 1e19},
            operation="native-int-overflow",
        ),
        case(
            "encode-nonnullable-nonfinite",
            "LeaseReply",
            {**fixtures["LeaseReply"], "away_after_seconds": float("nan")},
            operation="encode",
        ),
        case("native-deep-output", "JsonObject", {}, operation="native-deep-output"),
        case(
            "native-exact-int-overflow",
            "WaitingConversation",
            {},
            operation="native-exact-int-overflow",
        ),
        case(
            "encode-frame-peer-refusal",
            "ChildCommand",
            fixtures["ChildCommand"],
            operation="native-frame",
        ),
    ]
    for entry in cases:
        body = _body(entry["dto"])
        if entry["name"] == "integer-negative-zero-float":
            # Python's integer value cannot retain the JSON token's minus sign.
            body = body.replace(b'"away_after_seconds":0,', b'"away_after_seconds":-0,', 1)
            assert b'"away_after_seconds":-0,' in body
        if entry.get("operation") == "frame":
            body = struct.pack("!I", len(body)) + body
        entry["body"] = base64.b64encode(body).decode()
    cases.append(
        {
            "name": "frame-length-refusal",
            "root": "IpcRequest",
            "dto": {},
            "operation": "frame",
            "body": base64.b64encode(struct.pack("!I", 1024 * 1024 + 1)).decode(),
        }
    )
    return cases


def _reference_case(case: dict[str, Any]) -> dict[str, Any]:
    cls = _root(case["root"])
    operation = case.get("operation", "decode")
    body = base64.b64decode(case["body"])
    try:
        if operation == "frame":
            value = asyncio.run(wire.read_typed_frame(_Reader(body), cls))
        elif operation == "native-exact-int-overflow":
            # The oversized marker never crosses the control parser or Python int serializer.
            raw = b'{"conversation_id":"conversation","oldest_sequence":1' + b"0" * 4300 + b"}"
            value = wire.decode_bounded(raw, cls)
        elif operation == "value" or operation == "encode" or operation.startswith("native-"):
            dto = case["dto"]
            if operation == "native-surrogate":
                dto = {**dto, "source_text": "\ud800"}
            if operation == "native-deep-output":
                nested: Any = None
                for _ in range(201):
                    nested = [nested]
                dto = {"nested": nested}
            value = wire.type_adapter(cls).validate_python(dto)
        else:
            if not body or len(body) > case.get("limit", 1024 * 1024):
                raise ProtocolRefused("body exceeds its size limit")
            value = wire.type_adapter(cls).validate_json(body, strict=case.get("strict", False))
        if operation == "native-frame":
            value = asyncio.run(wire.read_typed_frame(_Reader(wire.encode_frame(value)), cls))
        encoded = wire.encode_bounded(
            value, limit=case.get("limit", 1024 * 1024) if operation == "encode" else 1024 * 1024
        )
        if operation == "encode" or operation.startswith("native-"):
            # Manager admission: a sender never emits a value that its peer refuses.
            wire.decode_bounded(encoded, cls)
        return {"name": case["name"], "ok": True, "body": base64.b64encode(encoded).decode()}
    except Exception as error:
        return {
            "name": case["name"],
            "ok": False,
            "type": type(error).__name__,
            "message": str(error),
            "value_error": isinstance(error, ValueError),
            **(
                {
                    "issues": [
                        {"type": issue["type"], "loc": list(issue["loc"])}
                        for issue in error.errors(include_url=False)
                    ]
                }
                if isinstance(error, ValidationError)
                else {}
            ),
        }


def _identity_cases(identity: dict[str, Any]) -> list[dict[str, Any]]:
    identity = {key: value for key, value in identity.items() if key != "connection_generation"}
    native = {**identity, "connection_generation": 0}
    return [
        {"name": "parse-valid", "operation": "parse", "value": identity},
        {"name": "parse-extra", "operation": "parse", "value": {**identity, "future": 7}},
        {"name": "parse-missing", "operation": "parse", "value": {}},
        {
            "name": "parse-integer-first",
            "operation": "parse",
            "value": {**identity, "protocol_version": True, "executor_incarnation": "bad"},
        },
        {
            "name": "uuid-prefix-underscore",
            "operation": "parse",
            "value": {**identity, "executor_incarnation": "0x_45678123442348234123456789abc"},
        },
        {
            "name": "uuid-invalid-control",
            "operation": "parse",
            "value": {**identity, "executor_incarnation": "\x1c2345678123442348234123456789abc"},
        },
        {
            "name": "validation-uuid-first",
            "operation": "validate",
            "value": {**native, "protocol_version": True, "executor_incarnation": "bad"},
        },
        {
            "name": "revision-python-space",
            "operation": "validate",
            "value": {**native, "application_revision": "\u0085"},
        },
        {
            "name": "revision-bom",
            "operation": "validate",
            "value": {**native, "application_revision": "\ufeff"},
        },
        {
            "name": "old-version",
            "operation": "validate",
            "value": {**native, "protocol_version": 21},
        },
        {
            "name": "old-version-shape-only",
            "operation": "validate",
            "value": {**native, "protocol_version": 21},
            "check_versions": False,
        },
        {"name": "supports-safe", "operation": "supports", "versions": [22, 5]},
        {"name": "supports-bool", "operation": "supports", "versions": [True, 5]},
        {"name": "supports-float-token", "operation": "supports", "versions": [22.0, 5]},
        {"name": "reason-old-version", "operation": "reason", "versions": [21, 5]},
        {"name": "reason-old-spool", "operation": "reason", "versions": [22, 4]},
        {"name": "reason-future", "operation": "reason", "versions": [23, 5]},
        {
            "name": "identity-long-braces",
            "operation": "validate",
            "value": {**native, "executor_incarnation": "1" + "{" * 500000 + "1"},
        },
        {
            "name": "identity-long-interior-space",
            "operation": "validate",
            "value": {**native, "application_revision": "1" + " " * 500000 + "1"},
        },
    ]


def _reference_identity(case: dict[str, Any]) -> dict[str, Any]:
    try:
        operation = case["operation"]
        if operation == "parse":
            result: Any = identity_contract.parse_identity(case["value"])
        elif operation == "validate":
            identity_contract.validate_identity(
                identity_contract.ExecutorIdentity(**case["value"]),
                check_versions=case.get("check_versions", True),
            )
            result = None
        elif operation == "supports":
            result = identity_contract.supports_executor_versions(*case["versions"])
        else:
            result = identity_contract.refusal_reason_for_versions(*case["versions"])
        return {"name": case["name"], "ok": True, "value": wire.encode_value(result)}
    except Exception as error:
        return {
            "name": case["name"],
            "ok": False,
            "type": type(error).__name__,
            "message": str(error),
            "value_error": isinstance(error, ValueError),
        }


def test_unit22_shared_protocol_exchange(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "PLAN_TEST_MODE": "1",
    }
    export_started = time.perf_counter()
    exported = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/native_protocol_fixtures.ts"), "--export"],
        env=environment,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
    )
    fixture_export_seconds = time.perf_counter() - export_started
    raw_fixtures = _fixture_value(json.loads(exported.stdout))
    # These independent anchors refuse a bridge that loses key order or numeric kinds.
    document = raw_fixtures["IpcResponse"]["result"]
    assert list(document)[:2] == ["2", "1"]
    counts = document["counts"]
    assert type(counts[1]) is float and counts[1] == 1.0
    assert type(counts[3]) is int and counts[3] == 9007199254740993
    assert type(counts[5]) is float and struct.pack(">d", counts[5]).hex() == "8000000000000000"
    schema = export_wire()
    assert set(raw_fixtures) == set(schema["roots"])
    events = {
        qualified.rsplit(".", 1)[-1]: method for method, qualified in schema["event_codecs"].items()
    }
    fixtures = {}
    reference = {}
    payload = []
    for root, dto in raw_fixtures.items():
        value = wire.type_adapter(_root(root)).validate_json(_body(dto))
        encoded = wire.encode_bounded(value)
        fixtures[root] = json.loads(encoded)
        reference[root] = encoded
        payload.append({"root": root, "python": base64.b64encode(encoded).decode()})
    cases = _cases(fixtures)
    expected_cases = [_reference_case(case) for case in cases]
    assert {case["name"] for case in expected_cases if case["ok"]} == {
        "lax-http-bool-extra",
        "lax-journal-int",
        "lax-float",
        "structured-unsafe-int",
        "lax-float-negative-zero-string",
        "integer-negative-zero-float",
        "strict-frame-lax-arguments",
        "explicit-null",
        "smart-piece",
        "enum-literal-success",
        "deferred-open-payload",
        "python-value-path",
        "nonfinite-json",
        "nonfinite-lax-string",
        "native-default-undefined",
        "durable-user-answer-command",
    }
    identities = _identity_cases(fixtures["ExecutorIdentity"])
    expected_identities = [_reference_identity(case) for case in identities]
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/protocol.ts")],
        input=json.dumps(
            {"fixtures": payload, "cases": cases, "events": events, "identities": identities},
            ensure_ascii=False,
        ),
        env=environment,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    candidate = json.loads(run.stdout)
    assert set(candidate["fixtures"]) == set(fixtures)
    for root, result in candidate["fixtures"].items():
        # Journal messages retain compact spelling, field order, and opaque key order.
        assert base64.b64decode(result["read"]) == reference[root], root
        assert result["value_bytes"].encode() == reference[root], root
        written = base64.b64decode(result["write"])
        assert written == reference[root], root
        Draft202012Validator({"$ref": f"#/$defs/{root}", "$defs": schema["$defs"]}).validate(
            json.loads(written)
        )
        wire.decode_bounded(written, _root(root))
    assert candidate["object_bytes"].encode() == reference["JsonObject"]
    assert len(candidate["cases"]) == len(expected_cases)
    sender_depth = None
    sender_integer = None
    for actual, expected in zip(candidate["cases"], expected_cases, strict=True):
        elapsed = actual.pop("elapsed_ms", None)
        if actual["name"] == "long-space-number":
            assert elapsed is not None and elapsed < 2000
        assert actual["name"] == expected["name"]
        assert actual["ok"] == expected["ok"], (actual, expected)
        if actual["name"] == "native-deep-output":
            assert not actual["ok"] and actual["value_error"] is expected["value_error"] is True
            sender_depth = {"source_peer": expected, "sender": actual}
            continue
        if actual["name"] == "native-exact-int-overflow":
            assert expected["type"] == "ValidationError"
            assert "Invalid JSON: number out of range" in expected["message"]
            assert not actual["ok"] and actual["value_error"] is expected["value_error"] is True
            assert "body" not in actual
            sender_integer = {"source_peer": expected, "sender": actual}
            continue
        if expected["ok"]:
            assert base64.b64decode(actual["body"]) == base64.b64decode(expected["body"]), actual[
                "name"
            ]
        else:
            # Native diagnostics differ. Refusal classes, field paths and issue kinds stay exact.
            assert isinstance(actual["message"], str) and actual["message"]
            assert {key: value for key, value in actual.items() if key != "message"} == {
                key: value for key, value in expected.items() if key != "message"
            }
    assert set(candidate["events"]) == set(events)
    for root, result in candidate["events"].items():
        assert result["instance"] is True and result["exact_constructor"] is True
        event = wire.type_adapter(_root(root)).validate_json(_body(fixtures[root]))
        method, arguments = wire.encode_backend_event(event)
        assert result["method"] == method.value == events[root]
        assert result["argument_bytes"].encode() == wire.type_adapter(object).dump_json(
            arguments
        ), root
        if root == "AgentMessageCompleted":
            assert result["pieces"] == ["MessageText", "MessageImage", "MessageFile"]
        else:
            assert result["pieces"] is None
    # The framing prefix is judged independently of the TS reader.
    framed = base64.b64decode(candidate["frame"])
    assert struct.unpack("!I", framed[:4])[0] == len(framed) - 4
    value = asyncio.run(wire.read_typed_frame(_Reader(framed), _root("IpcResponse")))
    assert wire.encode_bounded(value) == reference["IpcResponse"]
    for actual, expected in zip(candidate["identities"], expected_identities, strict=True):
        elapsed = actual.pop("elapsed_ms", None)
        if actual["name"].startswith("identity-long-"):
            assert elapsed is not None and elapsed < 2000
        assert _body(actual) == _body(expected)
    assert candidate["numbers"] == {
        "safe_integer": True,
        "float": True,
        "supports_decoded_identity": True,
        "unsafe_exact": "10000000000000000000",
    }
    # The timed hot-path message uses the exact configured batch byte budget.
    event_count = 256
    large = {
        "events": [deepcopy(fixtures["LiveEventBatch"]["events"][0]) for _ in range(event_count)]
    }
    limit = 4 * 1024 * 1024
    initial = wire.encode_bounded(
        wire.type_adapter(wire.LiveEventBatch).validate_python(large), limit=limit
    )
    padding, remainder = divmod(limit - len(initial), event_count)
    for index, event in enumerate(large["events"]):
        event["arguments"]["text_delta"] += "x" * (padding + (index < remainder))
    expected_large = wire.encode_bounded(
        wire.type_adapter(wire.LiveEventBatch).validate_python(large), limit=limit
    )
    performance = candidate["performance"]
    assert performance.pop("body") == base64.b64encode(expected_large).decode()
    assert performance["bytes"] == len(expected_large) == limit and performance["exact"] is True
    assert performance["event_count"] == event_count
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report = Path(directory) / "unit-22-protocol.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "roots": sorted(fixtures),
                    "events": sorted(events),
                    "cases": expected_cases,
                    "candidate_cases": candidate["cases"],
                    "diagnostic_rule": "Compare refusal classes, issue kinds and paths.",
                    "identity_cases": expected_identities,
                    "sender_depth": sender_depth,
                    "sender_integer": sender_integer,
                    "hot_path": performance,
                    "fixture_export_seconds": fixture_export_seconds,
                    "field_kinds": {
                        "boolean": ["lax-http-bool-extra", "ordered-scalar-errors"],
                        "integer": ["lax-journal-int", "lax-int-float-overflow"],
                        "float": ["lax-float", "long-space-number"],
                        "string": ["root/CanonicalSkillSource", "ordered-scalar-errors"],
                        "enum": ["root/WritePrompt", "enum-and-literal-errors"],
                        "literal": ["root/WritePrompt", "enum-and-literal-errors"],
                        "tuple": ["root/LiveEventBatch", "tuple-wrong-input"],
                        "dataclass": ["root/ChildReference", "dataclass-wrong-input"],
                        "dictionary": ["root/JsonObject", "live-read-reply-not-object"],
                        "path": ["root/FileSnapshot", "path-wrong-input"],
                    },
                    "elapsed_seconds": time.perf_counter() - started,
                    "differences": [],
                },
                indent=2,
            )
            + "\n"
        )
