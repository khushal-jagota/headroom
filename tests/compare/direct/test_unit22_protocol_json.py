"""Fresh jiter parity at the executor's lexical boundary, without a connection.

Risk: native JSON differs in nonfinite admission, exact number custody, Unicode
refusals, diagnostic byte positions, and dictionary order. Existing Python tests
and generated-type checks do not exercise the TS parser. One small fixed exchange
covers distinct parser states. It is not a root codec or transport proof.
"""

from __future__ import annotations

import base64
import json
import math
import os
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[3]


def _snapshot(value: Any) -> Any:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        bits = struct.pack(">d", value).hex() if math.isfinite(value) else repr(value)
        return ["float", bits]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, list):
        return ["array", [_snapshot(item) for item in value]]
    return ["object", [[key, _snapshot(item)] for key, item in value.items()]]


def test_unit22_executor_json_matches_jiter(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    # One populated value proves exact integer custody through the source limit,
    # raw/escaped Unicode, dictionary order, and JavaScript-special property names.
    accepted = (
        b'{"2":"first","1":"second","2":"last","__proto__":{"safe":true},'
        b'"values":[null,false,true,0,-0,'
        + b"9" * 4300
        + b',1.0,-0.0,2.5e-7,'
        b'NaN,Infinity,-Infinity,1e999],"text":"\\u00e9 \\ud83d\\ude00",'
        b'"raw":"'
        + "é 😀".encode()
        + b'","escaped":"\\\\\\\"\\n\\t","empty":[],"object":{}}'
    )
    # Each named refusal protects a separate state, error kind, or location rule.
    cases = [
        ("populated", accepted),
        ("empty", b""),
        ("whitespace-line", b" \r\n\t"),
        ("object-eof", b"{"),
        ("object-colon-eof", b'{"a"'),
        ("object-colon", b'{"a" 1}'),
        ("object-value-eof", b'{"a":'),
        ("object-separator", b'{"a":1 "b":2}'),
        ("object-key", b"{1:2}"),
        ("array-eof", b"["),
        ("array-after-value-eof", b"[1"),
        ("array-after-comma-eof", b"[1,"),
        ("array-separator", b"[1 2]"),
        ("array-trailing-comma", b"[1,]"),
        ("literal-eof", b"tru"),
        ("literal-continuation", b"trux"),
        # The sign counts toward the integer-prefix bound even with a float suffix.
        ("signed-integer-overflow", b"-" + b"9" * 4300 + b".0"),
        ("number-leading-zero", b"01"),
        ("number-exponent-eof", b"1e+"),
        ("number-decimal", b"1.x"),
        ("negative-nan", b"-NaN"),
        ("positive-infinity", b"+Infinity"),
        ("lowercase-nan", b"nan"),
        ("string-eof", b'"abc'),
        ("string-escape-eof", b'"abc\\'),
        ("string-control", b'"a\x01b"'),
        ("string-escape", b'"a\\qb"'),
        ("string-unicode-eof", b'"\\u12'),
        ("string-unicode-escape", b'"\\u12x4"'),
        ("high-surrogate", b'"\\ud800"'),
        ("low-surrogate", b'"\\udc00"'),
        ("wrong-surrogate-pair", b'"\\ud800\\u0041"'),
        ("string-utf8", b'"\xff"'),
        ("outside-utf8", b"\xff"),
        ("bom", b"\xef\xbb\xbf{}"),
        ("trailing-data", b"{}x"),
        ("multibyte-column", '{"é":1,}'.encode()),
        ("astral-column", '{"😀":1,}'.encode()),
        ("crlf-location", b'{\r\n "a":1,\r\n}'),
        ("cr-location", b'{\r"a":1,}'),
        ("depth-accepted", b"[" * 200 + b"0" + b"]" * 200),
        ("depth-refused-array", b"[" * 201 + b"0" + b"]" * 201),
        ("depth-refused-object", b'{"k":' * 201 + b"0" + b"}" * 201),
    ]
    adapter = TypeAdapter(Any)
    reference = []
    for name, body in cases:
        try:
            observation = {"ok": True, "value": _snapshot(adapter.validate_json(body))}
        except ValidationError as error:
            issue = error.errors(include_url=False)[0]
            assert issue["type"] == "json_invalid", name
            observation = {"ok": False, "error": issue["ctx"]["error"]}
        reference.append({"name": name, **observation})
    assert [row["name"] for row in reference if row["ok"]] == [
        "populated",
        "depth-accepted",
    ]

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
        ["node", str(ROOT / "server/executor/testing/protocol_json.ts")],
        input=json.dumps(
            [
                {"name": name, "body": base64.b64encode(body).decode()}
                for name, body in cases
            ]
        ),
        env=environment,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    candidate = json.loads(run.stdout)
    assert candidate == reference
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report = Path(directory) / "unit-22-protocol-json.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "python": reference,
                    "typescript": candidate,
                    "elapsed_seconds": time.perf_counter() - started,
                    "differences": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
