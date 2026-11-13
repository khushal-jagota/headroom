"""Nonfinite input fails before a transport call, with a structured CLI error."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.compare.direct import unit20_cli as driver
from tests.support.synthetic_homes import create_synthetic_homes


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
                "POST",
                "http://127.0.0.1:1/api/tickets",
                json=json.loads(text),
                trust_env=False,
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
    result = driver.raw_process(
        ["node", "--input-type=module", "-e", script],
        {"PATH": os.environ["PATH"], **create_synthetic_homes(tmp_path / "homes")},
        json.dumps(paths).encode(),
        timeout=10,
        check=True,
        cwd=None,
    )
    answers = json.loads(result.stdout)
    for answer in answers[: len(refused)]:
        assert answer["exit_code"] == 1 and answer["stdout"] == ""
        error = json.loads(answer["stderr"])["error"]
        assert error["code"] == "validation" and error["detail"] == {}
        assert isinstance(error["message"], str) and error["message"]

    assert answers[len(refused) :] == [{"accepted": True}, {"accepted": True}]
