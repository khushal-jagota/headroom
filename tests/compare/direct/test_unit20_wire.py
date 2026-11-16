"""CLI comparisons preserve JSON meaning and authored file bytes."""

import base64
from pathlib import Path

import pytest
from tests.compare.direct.unit20_wire import _body, wire_view
from tests.compare.runner import Normalizer


def test_wire_compares_json_fields_and_keeps_authored_bytes() -> None:
    rules = {
        "response_tokens": {"body.opaque": "existing opaque identity"},
        "response_ignore": {"body.epoch": "existing process epoch"},
    }

    def body(text: str) -> str:
        return _body(text, Normalizer(rules, Path("/unused-wire-root")), "response", None)

    left = '{"opaque":"left","epoch":17,"values":[1.0,2]}'
    right = '{"values":[1,2.0],"epoch":19,"opaque":"right"}'
    assert body(left) == body(right)
    assert body(left) != body(right.replace("[1,2.0]", "[2.0,1]"))
    assert body(left) != body(right.replace('"epoch":19', '"epoch":"19"'))
    assert body(left) != body(right.replace('"opaque":"right"', '"opaque":17'))
    assert body(left) != body(right.replace('"values":', '"extra":true,"values":'))
    with pytest.raises(AssertionError):
        body('{"error":{"code":"validation","message":null,"detail":{}}}')

    authored = b'{"epoch":17,"opaque":"authored","values":[1.0,2]}'
    delivery = {
        "wire": [
            {
                "method": "GET",
                "path": "/files/authored.json",
                "status": 200,
                "complete": True,
                "encoding": "",
                "body_base64": base64.b64encode(authored).decode(),
            }
        ],
    }
    assert (
        wire_view(delivery, Normalizer(rules, Path("/unused-wire-root")))[0]["body"]
        == authored.decode()
    )
