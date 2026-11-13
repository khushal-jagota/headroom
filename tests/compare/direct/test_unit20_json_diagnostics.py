"""Malformed-input messages need a Python oracle, not V8-message guesses.

No existing route comparison reaches decoder error positions. One pure Node call
compares the scanner against fresh CPython 3.12 without another server or host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.support.synthetic_homes import create_synthetic_homes


def test_unit20_json_diagnostics_match_fresh_python312(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    if sys.version_info[:2] != (3, 12):
        message = (
            "This diagnostic oracle requires CPython 3.12; "
            "later releases change trailing-comma messages."
        )
        if os.environ.get("CI") == "true":
            pytest.fail(message)
        pytest.skip(message)
    cases = [
        '{"a" 1}',
        '{"a":1,}',
        "[1,]",
        r'{"a":"\u12xz"}',
        '{"a":}',
        '{"a":1 "b":2}',
        '{"a":1}x',
        '{"a":"line\nbreak"}',
        r'{"a":"\q"}',
        '{"a":"unterminated}',
        '{"a":01}',
        '{"a":1.}',
        '{"a":1e+}',
        '{"😀":1,"b"2}',
        '\n{"a":1,}',
        "\ufeff{}",
        r'"\u1234',
        "{}",
        '{"a":1.0,"nested":[true,null,"😀"]}',
        "NaN",
        "Infinity",
        "-Infinity",
    ]
    expected: list[dict[str, object] | None] = []
    for text in cases:
        try:
            json.loads(text)
        except json.JSONDecodeError as error:
            expected.append(
                {"msg": error.msg, "pos": error.pos, "lineno": error.lineno, "colno": error.colno}
            )
        else:
            expected.append(None)
    root = Path(__file__).resolve().parents[3]
    module = (root / "server/cli/json_diagnostics.ts").as_uri()
    script = (
        f"import {{json_diagnostic}} from {json.dumps(module)};"
        "let source='';for await(const part of process.stdin)source+=part;"
        "process.stdout.write(JSON.stringify(JSON.parse(source).map(json_diagnostic)));"
    )
    environment = {"PATH": os.environ["PATH"], **create_synthetic_homes(tmp_path)}
    started = time.perf_counter()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        env=environment,
        check=True,
        timeout=10,
    )
    actual = json.loads(result.stdout)
    evidence = {
        "python": sys.version,
        "cases": cases,
        "expected": expected,
        "actual": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / "unit20-json-diagnostics.json").write_text(json.dumps(evidence, indent=2) + "\n")
    assert actual == expected, evidence


def test_unit20_nonfinite_file_input_refuses_before_httpx_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    refused = [
        '{"value":NaN}',
        '{"nested":[{"value":Infinity}]}',
        '{"value":-Infinity}',
        '{"value":1e999}',
    ]
    accepted = ['{"value":"NaN"}', '{"integer":1' + "0" * 310 + "}"]
    transported = []

    def forbidden(*args: object, **kwargs: object) -> None:
        transported.append(True)
        raise AssertionError("nonfinite JSON reached the HTTPX transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    for text in refused:
        with pytest.raises(ValueError):
            httpx.request(
                "POST", "http://127.0.0.1:1/api/tickets", json=json.loads(text), trust_env=False
            )
    assert not transported
    for text in accepted:
        httpx.Request("POST", "http://127.0.0.1:1/api/tickets", json=json.loads(text))
    paths = []
    for index, text in enumerate(refused + accepted):
        path = tmp_path / f"body-{index}.json"
        path.write_text(text)
        paths.append(str(path))
    root = Path(__file__).resolve().parents[3]
    input_module = (root / "server/cli/input.ts").as_uri()
    command_module = (root / "server/cli/commands.ts").as_uri()
    script = (
        f"import {{read_json_object}} from {json.dumps(input_module)};"
        f"import {{commands}} from {json.dumps(command_module)};"
        "let source='';for await(const part of process.stdin)source+=part;const results=[];"
        "for(const path of JSON.parse(source)){try{await read_json_object({"
        "command:commands.find(c=>c.path.join(' ')==='ticket create'),values:{as_json:true},"
        "environment:{PLAN_SERVER_URL:'http://127.0.0.1:1'},stdin:async()=>Buffer.alloc(0)},"
        "path,'Ticket create input');results.push({accepted:true});}catch(error){"
        "if(!error.result)throw error;results.push(error.result);}}"
        "process.stdout.write(JSON.stringify(results));"
    )
    (tmp_path / "homes").mkdir()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(paths),
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], **create_synthetic_homes(tmp_path / "homes")},
        check=True,
        timeout=10,
    )
    answers = json.loads(result.stdout)
    for answer in answers[: len(refused)]:
        assert answer["exit_code"] == 1 and answer["stdout"] == ""
        assert json.loads(answer["stderr"]) == {
            "error": {
                "code": "validation",
                "message": "non-finite JSON values are not allowed",
                "detail": {},
            }
        }
    assert answers[len(refused) :] == [{"accepted": True}, {"accepted": True}]
