"""Opaque field replacement must preserve server key order and numeric spelling."""

import base64
from pathlib import Path

from tests.compare.direct.unit20_wire import _body, wire_view
from tests.compare.runner import Normalizer


def test_wire_spans_preserve_unapproved_lexemes() -> None:
    rules = {
        "response_tokens": {"body.items.*.opaque": "existing opaque identity"},
        "response_ignore": {"body.epoch": "existing process epoch"},
    }
    left = '{"items":[{"opaque":"left","value":1.0}],"epoch":17}'
    right = '{"items":[{"opaque":"right","value":1.0}],"epoch":19}'

    def body(text: str) -> str:
        return _body(text, Normalizer(rules, Path("/unused-wire-root")), "response", None)

    assert body(left) == body(right)
    assert body(left) != body(right.replace('"epoch":19', '"epoch":19.0'))
    assert body(left) != body(right.replace('"opaque":"right"', '"opaque":17'))
    assert '"value":1.0' in body(left)
    assert body(left) != body(right.replace("1.0", "1"))
    assert body(left) != body('{"epoch":19,"items":[{"opaque":"right","value":1.0}]}')
    assert body(left) != body(right.replace('"value":1.0', '"value":1.0,"extra":true'))
    assert body("1.0 plain artifact") == "1.0 plain artifact"
    assert (
        body('{"text":"escaped \\" } [ remains","value":1e3}')
        == '{"text":"escaped \\" } [ remains","value":1e3}'
    )

    authored = b'{"epoch":17,"items":[{"opaque":"authored","value":1.0}]}'
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
        ]
    }
    assert (
        wire_view(delivery, Normalizer(rules, Path("/unused-wire-root")))[0]["body"]
        == authored.decode()
    )

    def catalog(text: str) -> str:
        return _body(text, Normalizer({}, Path("/unused-wire-root")), "catalog", None, True)

    revision = '{"revision":"' + "a" * 64 + ':0","value":1.0}'
    assert catalog(revision) == catalog(revision.replace("a" * 64, "b" * 64))
    assert catalog(revision) != catalog(revision.replace(':0"', ':1"'))

    years = '{"results":[{"rows":[{"values":{"2025":7.25,"2024":3.0},"strict":1.0}]}]}'
    year_rules = {"cli_direct_rules": {"dataset_year_fields": {"fields": ["2025", "2024"]}}}

    def year_view(text: str) -> str:
        return _body(
            text, Normalizer(year_rules, Path("/unused-wire-root")), "years", None, year_fields=True
        )

    assert year_view(years) == year_view(
        years.replace('"2025":7.25,"2024":3.0', '"2024":3.0,"2025":7.25')
    )
    assert year_view(years) != year_view(years.replace('"2024":3.0', '"2024":3'))
    assert year_view(years) != year_view(years.replace('"strict":1.0', '"strict":1'))

    charts = (
        '{"charts":{"20":7.25,"10":6.5,"__proto__":3.0,"constructor":4.0,'
        '"toString":5.0},"values":[7.25,6.5]}'
    )
    chart_rules = {
        "cli_direct_rules": {
            "goal_numeric_chart_keys": {
                "fields": ["20", "10", "__proto__", "constructor", "toString"]
            }
        }
    }

    def chart_view(text: str) -> str:
        return _body(
            text,
            Normalizer(chart_rules, Path("/unused-wire-root")),
            "charts",
            None,
            chart_keys=True,
        )

    assert chart_view(charts) == chart_view(
        charts.replace('"20":7.25,"10":6.5', '"10":6.5,"20":7.25')
    )
    assert chart_view(charts) != chart_view(charts.replace("[7.25,6.5]", "[6.5,7.25]"))
    assert chart_view(charts) != chart_view(
        charts.replace('"constructor":4.0,"toString":5.0', '"toString":5.0,"constructor":4.0')
    )
