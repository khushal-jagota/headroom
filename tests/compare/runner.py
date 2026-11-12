"""Differential HTTP and source-row oracle, independent of either server language."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from functools import partial
from pathlib import Path
from typing import Any

import httpx
from tests.support.server_fixtures import _start_server, _stop_server
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.db import connect, create_schema

# Unit-01's reached bare 32-hex values trace to UUID4, not content digests.
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


def backup_database(source: Path, target: Path) -> None:
    """Copy an isolated SQLite snapshot, including committed WAL content."""
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)


def capture_values(body: Any, call: dict[str, Any], captures: dict[str, Any]) -> None:
    for name, pointer in call.get("capture", {}).items():
        value = body
        for part in pointer.split(".") if pointer else []:
            value = value[int(part)] if isinstance(value, list) else value[part]
        captures[name] = value


async def stream_answer(base: str, call: dict[str, Any]) -> dict[str, Any]:
    """Read complete SSE frames under one deadline, including response headers."""
    count = call["frames"]
    deadline = call["deadline_seconds"]
    assert isinstance(count, int) and not isinstance(count, bool) and count > 0
    assert (
        isinstance(deadline, (int, float)) and not isinstance(deadline, bool)
        and math.isfinite(deadline) and deadline > 0
    )
    frames: list[dict[str, Any]] = []
    answer: dict[str, Any] = {}

    async def consume() -> None:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                call.get("method", "GET"), base + call["path"], headers=call.get("headers")
            ) as response:
                answer.update({
                    "status": response.status_code,
                    "headers": {h: response.headers[h] for h in HEADERS if h in response.headers},
                    "body": frames,
                })
                assert response.status_code == call["status"], (call, response.status_code)
                assert "text/event-stream" in response.headers.get("content-type", ""), call
                fields: dict[str, str] = {}
                reconnect: dict[str, str | int] = {}
                data: list[str] = []
                async for line in response.aiter_lines():
                    if not line:
                        if data:
                            raw = "\n".join(data)
                            try:
                                payload: Any = json.loads(raw)
                            except ValueError:
                                payload = raw
                            frames.append({
                                "event": fields.get("event") or "message", "data": payload,
                                **reconnect,
                            })
                            if len(frames) == count:
                                return
                        fields = {}
                        data = []
                    elif not line.startswith(":"):
                        key, separator, value = line.partition(":")
                        if separator and value.startswith(" "):
                            value = value[1:]
                        if key == "data":
                            data.append(value)
                        elif key == "event":
                            fields[key] = value
                        elif key == "id" and "\0" not in value:
                            reconnect["id"] = value
                        elif key == "retry" and re.fullmatch(r"[0-9]+", value):
                            reconnect["retry"] = int(value)
    try:
        await asyncio.wait_for(consume(), timeout=deadline)
    except TimeoutError:
        pass
    assert len(frames) == count, (
        "stream did not deliver the required complete frames", call, len(frames), answer
    )
    return answer


async def stream_answers(bases: list[str], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reads = [stream_answer(base, call) for base, call in zip(bases, calls, strict=True)]
    return list(await asyncio.gather(*reads))


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
    seed_captures: dict[str, Any] = {}
    if spec.get("seed_requests"):
        seed_server = _start_server(
            root / "seed-source", seed_db=lambda target: backup_database(seed, target),
            server_module="tests.compare.host",
        )
        try:
            with httpx.Client(timeout=30) as client:
                for request in spec["seed_requests"]:
                    call = substitute(request, seed_captures)
                    response = client.request(
                        call["method"], seed_server.base + call["path"],
                        json=call.get("json"), headers=call.get("headers"),
                    )
                    assert response.status_code == call["status"], (
                        call, response.status_code, response.text,
                    )
                    capture_values(response.json(), request, seed_captures)
        finally:
            _stop_server(seed_server)
        backup_database(seed_server.db_path, seed)
    servers = []
    modules = ["tests.compare.host", right_module]
    differences: list[dict[str, Any]] = []
    reach: dict[str, set[str]] = {}
    try:
        for name, module in zip(("python", "candidate"), modules, strict=True):

            def copy_seed(target: Path) -> None:
                backup_database(seed, target)

            host_environment = (
                {"PLAN_COMPARE_CONVERSATION_SYSTEM": "in_memory"}
                if spec.get("conversation_system") == "in_memory"
                else {}
            )
            servers.append(
                _start_server(
                    root / name,
                    seed_db=copy_seed,
                    server_module=module,
                    trusted_ingress_env=host_environment,
                )
            )
        norms = [Normalizer(rules, root / name) for name in ("python", "candidate")]
        captures: list[dict[str, Any]] = [dict(seed_captures), dict(seed_captures)]
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
                if request.get("kind") == "restart":
                    for side, server in enumerate(servers):
                        _stop_server(server)
                        snapshot = root / f"restart-{side}.db"
                        backup_database(server.db_path, snapshot)
                        servers[side] = _start_server(
                            server.db_path.parent, port=server.port,
                            server_module=modules[side],
                            trusted_ingress_env=host_environment,
                            seed_db=partial(backup_database, snapshot),
                        )
                        snapshot.unlink()
                    continue
                calls = [request]
                if (
                    request.get("kind") != "stream"
                    and request["method"].upper() not in ("GET", "HEAD")
                ):
                    calls += [
                        {"method": "GET", "path": p, "area": "probes", "status": 200}
                        for p in spec.get("probes", [])
                    ]
                for call in calls:
                    answers = []
                    streamed = None
                    if call.get("kind") == "stream":
                        streamed = asyncio.run(stream_answers(
                            [server.base for server in servers],
                            [substitute(call, saved) for saved in captures],
                        ))
                    for side, server in enumerate(servers):
                        resolved = substitute(call, captures[side])
                        if streamed is not None:
                            answer = streamed[side]
                            body = answer["body"]
                        else:
                            response = client.request(
                                resolved["method"],
                                server.base + resolved["path"],
                                json=resolved.get("json"),
                                headers=resolved.get("headers"),
                            )
                            if "status" in resolved:
                                assert response.status_code == resolved["status"], (
                                    index, side, resolved, response.status_code, response.text,
                                )
                            if "application/json" in response.headers.get("content-type", ""):
                                body = response.json()
                            else:
                                body = response.text
                            answer = {
                                "status": response.status_code,
                                "headers": {
                                    h: response.headers[h]
                                    for h in HEADERS if h in response.headers
                                },
                                "body": body,
                            }
                        if side == 0 and resolved["path"].startswith("/api/test/poll/"):
                            loop_results[resolved["path"]] = body
                        capture_values(body, call, captures[side])
                        answers.append(
                            norms[side].normalize(
                                answer,
                                str(
                                    norms[side].normalize(
                                        resolved.get("method", "GET") + " " + resolved["path"]
                                    )
                                ),
                            )
                        )
                    actual_path = resolved["path"].split("?")[0]
                    matches = [
                        (method, path)
                        for method, path in declared
                        if method == call.get("method", "GET").upper()
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
        skill_files: list[dict[str, bytes]] = []
        if spec.get("compare_skill_files") is True:
            for server in servers:
                skills = server.db_path.parent / "skills"
                skill_files.append({
                    path.relative_to(skills).as_posix(): path.read_bytes()
                    for path in sorted(skills.rglob("*")) if path.is_file()
                })
            for name in sorted(set(skill_files[0]) | set(skill_files[1])):
                contents = [files.get(name) for files in skill_files]
                if contents[0] != contents[1]:
                    differences.append({
                        "skill_file": name,
                        "bytes": [content.hex() if content is not None else None
                                  for content in contents],
                    })
        return {
            "list": list_path.name,
            "seconds": time.monotonic() - started,
            "requests": len(spec["requests"]),
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
            **({"skill_files": sorted(skill_files[0])} if skill_files else {}),
        }
    finally:
        for server in reversed(servers):
            _stop_server(server)
