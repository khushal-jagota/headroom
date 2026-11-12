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
from tests.compare.direct.export_unit22_wire import export_wire
from tests.compare.direct.unit22_protocol_fixtures import wire_fixtures

from planner.conversation.snapshot import BackendSnapshot
from planner.execution_identity import ProtocolRefused
from planner.executor import protocol as wire
from planner.executor import update_contracts
from planner.executor.contracts import ExecutorHealthRead

ROOT = Path(__file__).resolve().parents[3]


def _root(name: str) -> Any:
    return (
        {"BackendSnapshot": BackendSnapshot, "ExecutorHealthRead": ExecutorHealthRead}.get(name)
        or getattr(wire, name, None)
        or getattr(update_contracts, name)
    )


def _body(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _snapshot(value: Any) -> Any:
    if isinstance(value, float):
        return ["float", struct.pack(">d", value).hex()]
    if isinstance(value, int) and not isinstance(value, bool):
        return ["int", str(value)]
    if isinstance(value, list):
        return [_snapshot(item) for item in value]
    if isinstance(value, dict):
        return {key: _snapshot(item) for key, item in value.items()}
    return value


def _canonical(body: bytes) -> Any:
    return _snapshot(json.loads(body))


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
        case("command-missing-turn", "ChildCommand", {**fixtures["ChildCommand"], "turn": None}),
        case("durable-answer-conflict", "ChildCommand", answer_command),
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
        elif operation in ("value", "encode"):
            value = wire.type_adapter(cls).validate_python(case["dto"])
        else:
            if not body or len(body) > case.get("limit", 1024 * 1024):
                raise ProtocolRefused("body exceeds its size limit")
            value = wire.type_adapter(cls).validate_json(body, strict=case.get("strict", False))
        encoded = wire.encode_bounded(
            value, limit=case.get("limit", 1024 * 1024) if operation == "encode" else 1024 * 1024
        )
        return {"name": case["name"], "ok": True, "body": base64.b64encode(encoded).decode()}
    except Exception as error:
        return {
            "name": case["name"],
            "ok": False,
            "type": type(error).__name__,
            "message": str(error),
        }


def test_unit22_shared_protocol_exchange(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    fixtures, events = wire_fixtures()
    schema = export_wire()
    assert set(fixtures) == set(schema["roots"])
    reference = {}
    payload = []
    for root, dto in fixtures.items():
        value = wire.type_adapter(_root(root)).validate_json(_body(dto))
        encoded = wire.encode_bounded(value)
        reference[root] = encoded
        payload.append({"root": root, "dto": dto, "python": base64.b64encode(encoded).decode()})
    cases = _cases(fixtures)
    expected_cases = [_reference_case(case) for case in cases]
    assert {case["name"] for case in expected_cases if case["ok"]} == {
        "lax-http-bool-extra",
        "lax-journal-int",
        "lax-float",
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
    }
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
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/protocol.ts")],
        input=json.dumps(
            {"fixtures": payload, "cases": cases, "events": events}, ensure_ascii=False
        ),
        env=environment,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    candidate = json.loads(run.stdout)
    assert set(candidate["fixtures"]) == set(fixtures)
    for root, result in candidate["fixtures"].items():
        # Journal messages retain compact spelling, field order, and opaque key order.
        assert base64.b64decode(result["read"]) == reference[root], root
        assert result["value_bytes"].encode() == reference[root], root
        expected_value = json.loads(reference[root])
        assert _snapshot(result["object"]) == _snapshot(expected_value), root
        assert _snapshot(result["value"]) == _snapshot(expected_value), root
        written = base64.b64decode(result["write"])
        if root == "CanonicalSkillSource":
            # Independent DTO encoding also proves exact compact UTF-8 output.
            assert written == reference[root]
        Draft202012Validator({"$ref": f"#/$defs/{root}", "$defs": schema["$defs"]}).validate(
            json.loads(written)
        )
        reread = wire.decode_bounded(written, _root(root))
        assert _canonical(wire.encode_bounded(reread)) == _canonical(reference[root]), root
    assert len(candidate["cases"]) == len(expected_cases)
    for actual, expected in zip(candidate["cases"], expected_cases, strict=True):
        assert actual["name"] == expected["name"]
        assert actual["ok"] == expected["ok"], (actual, expected)
        if expected["ok"]:
            assert base64.b64decode(actual["body"]) == base64.b64decode(expected["body"]), actual[
                "name"
            ]
        else:
            assert actual == expected
    assert set(candidate["events"]) == set(events)
    for root, result in candidate["events"].items():
        assert result["class"] == root and result["instance"] is True
        event = wire.type_adapter(_root(root)).validate_json(_body(fixtures[root]))
        method, arguments = wire.encode_backend_event(event)
        assert result["method"] == method.value == events[root]
        assert _snapshot(result["arguments"]) == _snapshot(arguments), root
        if root == "AgentMessageCompleted":
            assert result["pieces"] == ["MessageText", "MessageImage", "MessageFile"]
        else:
            assert result["pieces"] is None
    # The framing prefix is judged independently of the TS reader.
    framed = base64.b64decode(candidate["frame"])
    assert struct.unpack("!I", framed[:4])[0] == len(framed) - 4
    value = asyncio.run(wire.read_typed_frame(_Reader(framed), _root("IpcResponse")))
    assert _canonical(wire.encode_bounded(value)) == _canonical(reference["IpcResponse"])
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
                    "elapsed_seconds": time.perf_counter() - started,
                    "differences": [],
                },
                indent=2,
            )
            + "\n"
        )
