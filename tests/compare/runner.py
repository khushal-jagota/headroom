"""Differential HTTP and source-row oracle, independent of either server language."""

from __future__ import annotations

import base64
import json
import re
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
from tests.support.server_fixtures import _start_server, _stop_server
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.db import connect, create_schema

ID = re.compile(
    r"(?<![a-zA-Z0-9])(?:(?:si|t|schedule|idea|run|credential|measure|dataset|source|source_run|"
    r"goal|block|chart)_[a-z0-9]{8}|claim_[a-z0-9]{12}|"
    r"(?:vu_|f_|held_|manager_delivery_wake_|conv_|conversation_|worker_step_message_|worker_step_|stored_file_)"
    r"[0-9a-f]{32}|[0-9a-f]{32}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![\w])"
)
HEADERS = (
    "content-type",
    "cache-control",
    "etag",
    "last-modified",
    "content-disposition",
    "x-content-type-options",
    "accept-ranges",
    "content-range",
)


class Normalizer:
    def __init__(self, rules: dict[str, Any], root: Path) -> None:
        self.rules = rules
        self.root = str(root)
        self.ids: dict[str, str] = {}
        self.tokens: dict[str, dict[str, int]] = {}

    def token(self, value: Any, context: str) -> Any:
        if value is None:
            return None
        seen = self.tokens.setdefault(context, {})
        key = json.dumps(value, sort_keys=True)
        if key not in seen:
            seen[key] = len(seen)
        return seen[key]

    def normalize(
        self,
        value: Any,
        context: str = "",
        table: str = "",
        path: str = "",
        apply_rules: bool = True,
    ) -> Any:
        if isinstance(value, dict):
            out = {}
            for key in sorted(value):
                child_path = f"{path}.{key}" if path else key
                rule_key = f"{table}.{key}" if table else child_path
                if apply_rules and rule_key in self.rules.get(
                    "ignore" if table else "response_ignore", {}
                ):
                    continue
                item = value[key]
                if apply_rules and rule_key in self.rules.get(
                    "tokens" if table else "response_tokens", {}
                ):
                    out[str(self.normalize(key))] = self.token(item, f"{context}.{key}")
                else:
                    out[str(self.normalize(key))] = self.normalize(
                        item,
                        f"{context}.{key}",
                        path=child_path,
                        apply_rules=apply_rules and not table,
                    )
            return out
        if isinstance(value, list):
            return [
                self.normalize(v, f"{context}.{i}", path=f"{path}.*", apply_rules=apply_rules)
                for i, v in enumerate(value)
            ]
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, str):
            value = value.replace(self.root, "<server-root>")

            def replace(match: re.Match[str]) -> str:
                raw = match.group()
                if raw not in self.ids:
                    self.ids[raw] = f"<id:{len(self.ids)}>"
                return self.ids[raw]

            return ID.sub(replace, value)
        return value


def substitute(value: Any, captures: dict[str, Any]) -> Any:
    if isinstance(value, str):
        for name, replacement in captures.items():
            if value == "${" + name + "}":
                return replacement
            value = value.replace("${" + name + "}", str(replacement))
        return value
    if isinstance(value, dict):
        return {k: substitute(v, captures) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, captures) for v in value]
    return value


def rows(path: Path, normalizer: Normalizer) -> dict[str, list[Any]]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        tables = sorted(
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        result = {}
        for table in tables:
            if table in normalizer.rules.get("derived_tables", {}):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            raw = [dict(r) for r in conn.execute(f"SELECT * FROM {quoted}")]
            for row in raw:
                for key, value in row.items():
                    if isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                        except ValueError:
                            continue
                        if isinstance(parsed, (dict, list)):
                            row[key] = parsed

            def semantic_order(row: dict[str, Any], table_name: str = table) -> str:
                stable = {
                    k: v
                    for k, v in row.items()
                    if f"{table_name}.{k}" not in normalizer.rules.get("ignore", {})
                    and f"{table_name}.{k}" not in normalizer.rules.get("tokens", {})
                }
                return ID.sub("<id>", json.dumps(stable, sort_keys=True, default=str))

            raw.sort(key=semantic_order)
            primary = [r[1] for r in conn.execute(f"PRAGMA table_info({quoted})") if r[5]]
            normalized = []
            for row in raw:
                identity = (
                    {k: row[k] for k in primary}
                    if primary
                    else {
                        k: v
                        for k, v in row.items()
                        if f"{table}.{k}" not in normalizer.rules.get("tokens", {})
                    }
                )
                context = json.dumps(normalizer.normalize(identity), sort_keys=True)
                normalized.append(normalizer.normalize(row, f"table.{table}.{context}", table))
            result[table] = sorted(normalized, key=lambda r: json.dumps(r, sort_keys=True))
        return result


def run_list(
    list_path: Path, root: Path, right_module: str = "tests.compare.host"
) -> dict[str, Any]:
    started = time.monotonic()
    spec = json.loads(list_path.read_text())
    rule_path = list_path.parent.parent / "rules" / list_path.name
    rules = json.loads(rule_path.read_text())
    root.mkdir(parents=True, exist_ok=True)
    seed = root / "seed.db"
    (root / "seed-home").mkdir()
    homes = create_synthetic_homes(root / "seed-home")
    with synthetic_home_environment(homes):
        with closing(connect(str(seed))) as conn:
            create_schema(conn)
    servers = []
    differences: list[dict[str, Any]] = []
    reach: dict[str, set[str]] = {}
    response_statuses: list[dict[str, Any]] = []
    try:
        for name, module in [("python", "tests.compare.host"), ("candidate", right_module)]:

            def copy_seed(target: Path) -> None:
                shutil.copyfile(seed, target)

            servers.append(_start_server(root / name, seed_db=copy_seed, server_module=module))
        norms = [Normalizer(rules, root / name) for name in ("python", "candidate")]
        captures: list[dict[str, Any]] = [{}, {}]
        # Establish changed/unchanged baselines for opaque source columns.
        baselines = [
            rows(server.db_path, norm) for server, norm in zip(servers, norms, strict=True)
        ]
        loop_results: dict[str, Any] = {}
        with httpx.Client(timeout=30) as client:
            schema = client.get(servers[0].base + "/openapi.json").json()
            declared = [
                (method.upper(), path)
                for path, methods in schema["paths"].items()
                for method in methods
                if method in ("get", "post", "put", "patch", "delete", "head", "options")
                and not path.startswith("/api/test/")
            ]
            for index, request in enumerate(spec["requests"]):
                calls = [request]
                if request["method"].upper() not in ("GET", "HEAD"):
                    calls += [
                        {"method": "GET", "path": p, "area": "probes"}
                        for p in spec.get("probes", [])
                    ]
                for call in calls:
                    answers = []
                    for side, server in enumerate(servers):
                        resolved = substitute(call, captures[side])
                        content = resolved.get("content")
                        if isinstance(content, dict):
                            content = base64.b64decode(content["base64"], validate=True)
                        response = client.request(
                            resolved["method"],
                            server.base + resolved["path"],
                            json=resolved.get("json"),
                            content=content,
                            headers=resolved.get("headers"),
                        )
                        if "application/json" in response.headers.get("content-type", ""):
                            body = response.json()
                        elif response.headers.get("content-type", "").startswith("text/"):
                            body = response.text
                        else:
                            body = response.content
                        if side == 0 and resolved["path"].startswith("/api/test/poll/"):
                            loop_results[resolved["path"]] = body
                        for name, pointer in call.get("capture", {}).items():
                            captured = body
                            for part in pointer.split(".") if pointer else []:
                                captured = (
                                    captured[int(part)]
                                    if isinstance(captured, list)
                                    else captured[part]
                                )
                            captures[side][name] = captured
                        answer = {
                            "status": response.status_code,
                            "headers": {
                                h: response.headers[h] for h in HEADERS if h in response.headers
                            },
                            "body": body,
                        }
                        answers.append(
                            norms[side].normalize(
                                answer,
                                str(
                                    norms[side].normalize(
                                        resolved["method"] + " " + resolved["path"]
                                    )
                                ),
                            )
                        )
                    if call is request:
                        response_statuses.append(
                            {
                                "step": index,
                                "method": call["method"],
                                "path": call["path"],
                                "statuses": [answer["status"] for answer in answers],
                            }
                        )
                    actual_path = resolved["path"].split("?")[0]
                    matches = [
                        (method, path)
                        for method, path in declared
                        if method == call["method"].upper()
                        and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", path), actual_path)
                    ]
                    if matches:
                        method, path = max(
                            matches,
                            key=lambda item: (
                                item[1] == actual_path,
                                -item[1].count("{"),
                                len(item[1]),
                            ),
                        )
                        reach.setdefault(call.get("area", "unspecified"), set()).add(
                            method + " " + path
                        )
                    if answers[0] != answers[1]:
                        differences.append({"step": index, "call": call, "answers": answers})
        snapshots = [rows(s.db_path, n) for s, n in zip(servers, norms, strict=True)]
        for table in sorted(set(snapshots[0]) | set(snapshots[1])):
            if snapshots[0].get(table) != snapshots[1].get(table):
                differences.append({"table": table, "rows": [s.get(table) for s in snapshots]})
        return {
            "list": list_path.name,
            "seconds": time.monotonic() - started,
            "requests": len(spec["requests"]),
            "response_statuses": response_statuses,
            "http_inventory": len(declared),
            "http_routes": [method + " " + path for method, path in declared],
            "differences": differences,
            "routes": {k: sorted(v) for k, v in reach.items()},
            "tables": {table: len(records) for table, records in snapshots[0].items()},
            "seed_tables": {table: len(records) for table, records in baselines[0].items()},
            "mutated_tables": [
                table for table in snapshots[0] if snapshots[0][table] != baselines[0].get(table)
            ],
            "loop_results": loop_results,
            "rules": rules,
        }
    finally:
        for server in reversed(servers):
            _stop_server(server)
