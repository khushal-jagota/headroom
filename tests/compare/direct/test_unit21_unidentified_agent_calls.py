"""One source-comparison sequence: synthetic kernel evidence and admission wiring.

Python-only coverage cannot prove the TS port refuses the same missing identities.
No listener, live proc scan, database, provider or browser host is used here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_unidentified_agent_calls import (
    LIVE,
    PEER,
    SERVER,
    SESSION,
    SETTING,
    WORKER,
    holder,
    invoke,
    request_scope,
    socket_row,
)

from planner.core import unidentified_agent_calls as calls
from planner.core.authctx import AUTHENTICATED_OWNER_SCOPE_KEY
from planner.core.config import load_config
from planner.core.errors import PlannerError

ROOT = Path(__file__).resolve().parents[3]


def test_unit21_unidentified_agent_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    peers: list[dict[str, Any]] = []
    expected_peers = []
    variants = [
        ("127.0.0.1", "0100007F", "tcp", SESSION, None),
        ("::1", "00000000000000000000000001000000", "tcp6", SESSION, None),
        ("::ffff:127.0.0.1", "0000000000000000FFFF00000100007F", "tcp6", SESSION, None),
        ("127.0.0.1", "0100007F", "tcp", LIVE, None),
        ("127.0.0.1", "0100007F", "tcp", WORKER, None),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "shared_agent"),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "no_holder"),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "deny_outside"),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "deny_no_holder"),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "deny_agent"),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "deny_unknown"),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "race"),
        ("127.0.0.1", "0100007F", "tcp", "/panels-live.service/../session", None),
        ("127.0.0.1", "0100007F", "tcp", "/panels-typescript.service-extra", None),
        ("127.0.0.1", "0100007F", "tcp", SESSION, "feff_separator"),
        ("127.0.0.1", "0100007F", "tcp", "/panels-typescript.service", None),
    ]
    for index, (host, address, table, cgroup, fault) in enumerate(variants):
        root = tmp_path / str(index)
        (root / "net").mkdir(parents=True)
        for name in ("tcp", "tcp6"):
            row = (
                socket_row(address).replace(" ", "\ufeff")
                if fault == "feff_separator"
                else socket_row(address)
            )
            (root / "net" / name).write_text("header\n" + (row if name == table else ""))
        holder(root, 101, cgroup)
        if fault in {"no_holder", "deny_no_holder"}:
            (root / "101/fd/4").unlink()
        if fault and (fault.startswith("deny") or fault == "shared_agent"):
            holder(root, 202, WORKER if fault in {"deny_agent", "shared_agent"} else SESSION)
        real_list, real_text, real_link = Path.iterdir, Path.read_text, os.readlink
        reads = 0

        def listing(
            path: Path, fault: str | None = fault, root: Path = root, real_list: Any = real_list
        ) -> Any:
            if fault and fault.startswith("deny") and path == root / "202/fd":
                raise PermissionError("synthetic permission")
            return real_list(path)

        def text(
            path: Path,
            *args: Any,
            fault: str | None = fault,
            root: Path = root,
            real_text: Callable[..., str] = real_text,
            **kwargs: Any,
        ) -> str:
            if fault == "deny_unknown" and path == root / "202/cgroup":
                raise PermissionError("synthetic permission")
            return real_text(path, *args, **kwargs)

        def link(
            path: Any,
            *args: Any,
            fault: str | None = fault,
            root: Path = root,
            real_link: Any = real_link,
            **kwargs: Any,
        ) -> Any:
            nonlocal reads
            if fault == "race" and Path(path) == root / "101/fd/4":
                reads += 1
                if reads == 2:
                    return "socket:[99999]"
            return real_link(path, *args, **kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "iterdir", listing)
            scoped.setattr(Path, "read_text", text)
            scoped.setattr(os, "readlink", link)
            expected_peers.append(
                dataclasses.asdict(calls.resolve_loopback_peer((host, PEER[1]), SERVER, proc=root))
            )
        peers.append(dict(root=str(root), client=[host, PEER[1]], fault=fault))

    requests = []
    expected_requests = []
    vectors = [
        (False, "http", [], False, PEER),
        (True, "other", [], False, PEER),
        (True, "http", [], True, PEER),
        (True, "http", [["X-Plan-Actor", " unattributed "]], False, PEER),
        (True, "http", [["x-plan-actor", "\u00a0"], ["x-plan-actor", "chief"]], False, PEER),
        (True, "http", [["x-plan-ticket-id", "t_synthetic"]], False, PEER),
        (True, "http", [], False, ("192.0.2.1", PEER[1])),
        (True, "http", [], False, ("not-an-ip", PEER[1])),
        (True, "websocket", [], False, None),
    ]
    for enabled, kind, headers, owner, client in vectors:
        scope = request_scope(kind)
        scope.update(
            headers=[(k.encode("latin1"), v.encode("latin1")) for k, v in headers], client=client
        )
        scope[AUTHENTICATED_OWNER_SCOPE_KEY] = owner
        lookups = 0

        def lookup(*_args: Any) -> calls.PeerLookupResult:
            nonlocal lookups
            lookups += 1
            return calls.PeerLookupResult(False, 2)

        with monkeypatch.context() as scoped:
            scoped.setattr(calls, "resolve_loopback_peer", lookup)
            sent = asyncio.run(invoke(scope, enabled=enabled))
        response: Any = "forwarded"
        if sent[0]["type"] == "websocket.close":
            response = sent[0]
        elif sent[0]["type"] == "http.response.start":
            response = dict(status=sent[0]["status"], body=json.loads(sent[1]["body"]))
        expected_requests.append(dict(lookups=lookups, response=response))
        requests.append(
            dict(
                enabled=enabled,
                kind=kind,
                authenticated_owner=owner,
                headers=headers,
                client=client,
                server=SERVER,
            )
        )

    config = tmp_path / "config.yaml"
    config.write_text("refuse_unidentified_agent_calls: true\n")
    configs: list[dict[str, Any]] = []
    for value in (None, "yes", " OFF ", "\x1cyes\x1f", "invalid"):
        env = {} if value is None else {SETTING: value}
        try:
            configs.append(
                dict(
                    value=load_config(str(config), env=env).refuse_unidentified_agent_calls,
                    unchanged=env.get(SETTING) == value,
                )
            )
        except PlannerError as error:
            configs.append(dict(error=str(error), unchanged=env.get(SETTING) == value))
    with monkeypatch.context() as scoped:
        scoped.setenv(SETTING, "yes")
        consumed = (
            load_config(str(config)).refuse_unidentified_agent_calls and SETTING not in os.environ
        )
        scoped.setenv(SETTING, "invalid")
        with pytest.raises(PlannerError):
            load_config(str(config))
        invalid_retained = os.environ.get(SETTING) == "invalid"
    spec = tmp_path / "request.json"
    spec.write_text(
        json.dumps(
            dict(
                config=str(config),
                little_endian=sys.byteorder == "little",
                uid=str(os.getuid()),
                peers=peers,
                requests=requests,
            )
        )
    )
    launcher = tmp_path / "probe.mjs"
    launcher.write_text(
        "import{readFileSync}from'node:fs';"
        "const{refusal_probe}=await import(process.argv[2]);"
        "console.log(JSON.stringify(await refusal_probe("
        "JSON.parse(readFileSync(process.argv[3],'utf8')))));"
    )
    result = subprocess.run(
        [
            shutil.which("node") or "node",
            str(launcher),
            (ROOT / "server/core/unidentified_agent_calls/testing/compare.ts").as_uri(),
            str(spec),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    # The exact new unit is the one approved extension; never label it Python parity.
    assert expected_peers[-1] == dict(is_owner=True, skipped_unreadable_processes=0)
    assert actual["peers"][-1] == dict(is_owner=False, skipped_unreadable_processes=0)
    assert actual["peers"][:-1] == expected_peers[:-1]
    assert actual["requests"] == expected_requests
    assert actual["configs"] == configs
    assert actual["consumed"] == consumed is True
    assert actual["invalid_retained"] == invalid_retained is True
    refused = next(
        item["response"] for item in expected_requests if item["response"] != "forwarded"
    )
    assert actual["http"] == dict(
        refused={**refused, "type": "application/json"},
        owner=dict(status=200, body=dict(forwarded=True)),
        lookups=1,
    )
    report_dir = request.config.getoption("compare_report_dir", default=None)
    if report_dir:
        destination = Path(report_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-21-unidentified-agent-calls.json").write_text(
            json.dumps(
                dict(
                    seconds=time.perf_counter() - started,
                    boundary=(
                        "Synthetic proc decisions, source HTTP/WS values, ENV consumption "
                        "and Fastify injection; no kernel connection or WS transport proof."
                    ),
                    result=actual,
                ),
                indent=2,
            )
            + "\n"
        )
