"""Bounded fixtures for the accepted Unit 20 comparison rules."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import httpx
from tests.compare.direct import unit20_cli as cli
from tests.compare.runner import Normalizer, rows
from tests.support.server import ServerHandle

ROOT = Path(__file__).resolve().parents[3]
LIST = ROOT / "tests/compare/lists/unit-20-commands.domain.json"
RULES = LIST.parent.parent / "rules" / LIST.name


def direct_steps(group: str) -> list[dict[str, Any]]:
    return [
        step for step in json.loads(LIST.read_text())["direct_requests"] if step["argv"][0] == group
    ]


def rule(name: str) -> dict[str, Any]:
    return json.loads(RULES.read_text())["cli_direct_rules"][name]


def node_export(host: cli.CliHost, root: Path, code: str, payload: Any = None) -> Any:
    # Code is a fixed test constant, never list data. Only factory arguments use stdin.
    cli._admit(host, root)
    env = cli.environment(host, {"actor": "owner"}, root)
    result = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        cwd=ROOT,
        env=env,
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    return json.loads(result.stdout)


def differences(left: Any, right: Any, path: str = "", *, indexed_arrays: bool = False) -> set[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        assert list(left) == list(right), (path, list(left), list(right))
        return set().union(
            *(
                differences(
                    left[key], right[key], f"{path}.{key}".strip("."), indexed_arrays=indexed_arrays
                )
                for key in left
            )
        )
    if indexed_arrays and isinstance(left, list) and isinstance(right, list):
        assert len(left) == len(right), (path, len(left), len(right))
        return set().union(
            *(
                differences(a, b, f"{path}.{index}", indexed_arrays=True)
                for index, (a, b) in enumerate(zip(left, right, strict=True))
            )
        )
    return set() if type(left) is type(right) and left == right else {path}


def api(
    handle: ServerHandle,
    method: str,
    path: str,
    body: Any = None,
    identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = (
        {}
        if identity is None
        else {
            "X-Plan-Actor": "worker",
            "X-Plan-Ticket-ID": identity["ticket_id"],
        }
    )
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
        response = client.request(method, handle.base + path, json=body, headers=headers)
    assert response.status_code == 200, (method, path, response.status_code, response.text)
    value: dict[str, Any] = response.json()
    return value


def attention_state(
    handle: ServerHandle, returned: dict[str, Any], expected_script: str, *, root: Path
) -> dict[str, Any]:
    host = cli.CliHost(handle, root)
    host.check()
    assert rule("attention_scripts")["scope"] == {"account": "panels-attention-history", "rows": 1}
    with closing(sqlite3.connect(f"file:{handle.db_path}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        sources = conn.execute(
            "SELECT * FROM measure_sources WHERE account='panels-attention-history'"
        ).fetchall()
        assert len(sources) == 1
        source = dict(sources[0])
    assert source["id"] == returned["source_id"]
    assert source["active_script"] == expected_script
    assert source["active_script_sha256"] == hashlib.sha256(expected_script.encode()).hexdigest()
    rules = json.loads(RULES.read_text())
    normalizer = Normalizer(rules, handle.db_path.parent)
    normalizer.ids[returned["source_id"]] = "<attention-source>"
    for kind in ("dataset_ids", "measure_ids"):
        for metric, identifier in returned[kind].items():
            normalizer.ids[identifier] = f"<attention-{kind}-{metric}>"
    state = rows(handle.db_path, normalizer)
    # Only the single verified attention row receives the two accepted substitutions.
    scoped = [
        item for item in state["measure_sources"] if item["account"] == "panels-attention-history"
    ]
    assert len(scoped) == 1
    for path in rule("attention_scripts")["paths"]:
        table, column = path.split(".")
        assert table == "measure_sources" and column in ("active_script", "active_script_sha256")
        scoped[0][column] = f"<verified-attention-{column}>"
    return {
        "returned": normalizer.normalize(returned),
        "rows": state,
        "script": source["active_script"],
        "sha256": source["active_script_sha256"],
    }


def preview_answer(
    answer: dict[str, Any],
    current: dict[str, Any],
    handle: ServerHandle,
    interval: tuple[int, int],
    normalizer: Normalizer,
    mode: str,
    *,
    first: bool,
) -> dict[str, Any]:
    assert rule("preview_creation")["paths"] == [
        "json.created_at",
        "text.created_at",
        "ticket_application_previews.created_at",
    ]
    timestamp = current["created_at"]
    assert type(timestamp) is int
    if first:
        assert interval[0] <= timestamp <= interval[1], (interval, timestamp)
    with closing(sqlite3.connect(f"file:{handle.db_path}?mode=ro", uri=True)) as conn:
        stored = conn.execute(
            "SELECT created_at FROM ticket_application_previews WHERE generation=?",
            (current["generation"],),
        ).fetchone()
        assert stored is not None and stored[0] == timestamp
    normalized = cli.comparable(answer)
    text = normalized["stdout"]
    expression = (
        r'("created_at"\s*:\s*)(-?\d+)(?=\s*[,}])' if mode == "json" else r"(^created_at: )(-?\d+)$"
    )
    matches = list(re.finditer(expression, text, re.MULTILINE))
    # Human stop prints only a completion line. Every other response exposes the field.
    if mode == "text" and text == "Application preview ended.\n":
        assert not matches
    else:
        assert len(matches) == 1 and int(matches[0].group(2)) == timestamp
        start, end = matches[0].span(2)
        normalized["stdout"] = text[:start] + "<preview-created-at>" + text[end:]
    return cast(dict[str, Any], normalizer.normalize(normalized))
