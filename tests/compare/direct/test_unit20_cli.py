"""Pin process admission, parser text and the first populated CLI contract.

Existing route lists do not execute a CLI. These checks reuse their host and seed
facilities. Only the same-host replay needs a reset, because CLI writes mutate state.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from tests.compare.direct import unit20_cli as driver
from tests.compare.runner import Normalizer, backup_database, capture_values, substitute
from tests.support.server import ServerHandle
from tests.support.server_fixtures import _start_server, _stop_server

LIST = driver.ROOT / "tests/compare/lists/unit-20-commands.work.json"


def _report(request: pytest.FixtureRequest, name: str, evidence: Any) -> None:
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        (root / (name + ".json")).write_text(json.dumps(evidence, indent=2) + "\n")


def _step(argv: list[str], code: int = 0) -> dict[str, Any]:
    return {
        "kind": "cli",
        "argv": argv,
        "identity": {"actor": "owner"},
        "exit_code": code,
        "proof": "Pins parser text or measures cold CLI startup.",
    }


def test_unit20_guard_refuses_before_subprocess_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "fixture.db"
    database.touch()
    handle = ServerHandle(
        "http://127.0.0.1:32123",
        cast(subprocess.Popen[bytes], SimpleNamespace(poll=lambda: None)),
        database,
        tmp_path / "log",
        32123,
        tmp_path / "socket",
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("guard refusal reached a process or a network request")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(httpx.Client, "get", forbidden)
    bad = [
        cast(ServerHandle, None),
        replace(handle, base="http://127.0.0.1:8767", port=8767),
        replace(handle, base="http://localhost:32123"),
        replace(handle, base="https://panels.example.invalid:32123"),
        replace(handle, base="http://127.0.0.1:32124"),
        replace(handle, db_path=tmp_path.parent / "foreign.db"),
    ]
    for foreign in bad:
        with pytest.raises((ValueError, FileNotFoundError)):
            driver.run_cli(
                driver.CliHost(foreign, tmp_path),
                _step(["--help"]),
                True,
                tmp_path / "input",
            )
    for step in (
        {
            **_step(["--help"]),
            "environment": {"PLAN_SERVER_URL": "http://127.0.0.1:8767"},
        },
        {**_step(["--help"]), "argv": "panels --help"},
    ):
        with pytest.raises(ValueError):
            driver.run_cli(
                driver.CliHost(handle, tmp_path), step, False, tmp_path / "input"
            )
    monkeypatch.setattr(driver, "LIVE_URL", handle.base)
    with pytest.raises(ValueError, match="live or foreign"):
        driver.run_cli(
            driver.CliHost(handle, tmp_path),
            _step(["--help"]),
            False,
            tmp_path / "input",
        )


def test_unit20_parser_smoke_and_startup(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """One existing host proves real subprocess output and startup without browser cost."""
    started = time.perf_counter()
    handle = _start_server(tmp_path / "host", server_module="tests.compare.host")
    evidence: dict[str, Any] = {"parser": [], "timings": {}}
    try:
        host = driver.CliHost(handle, tmp_path)
        for argv, code in (
            (["--help"], 0),
            (["project", "--help"], 0),
            (["manager", "set", "--help"], 0),
            (["stalls", "--help=no"], 2),
            (["project", "list", "--help=no"], 2),
            (["project", "list", "--limit"], 2),
            (["project", "list", "--json=no"], 2),
            (["project", "create"], 2),
            (["project", "list", "--limit", "bad"], 2),
            (["project", "list", "--unknown-option"], 2),
        ):
            answers = [
                driver.run_cli(
                    host,
                    _step(argv, code),
                    bool(side),
                    tmp_path / f"input-{side}",
                    "text",
                )
                for side in (0, 1)
            ]
            evidence["parser"].append({"argv": argv, "answers": answers})
            assert driver.comparable(answers[0]) == driver.comparable(
                answers[1]
            ), evidence
        for argv in (["--help"], ["project", "list", "--limit", "1"]):
            label = " ".join(argv)
            evidence["timings"][label] = {}
            for side, implementation in ((0, "python"), (1, "typescript")):
                answers = [
                    driver.run_cli(
                        host,
                        _step(argv),
                        bool(side),
                        tmp_path / f"input-{side}",
                        "text",
                    )
                    for _ in range(3)
                ]
                evidence["timings"][label][implementation] = {
                    "seconds": [answer["seconds"] for answer in answers],
                    "median_seconds": median(answer["seconds"] for answer in answers),
                }
        evidence["seconds"] = time.perf_counter() - started
        _report(request, "unit20-startup", evidence)
    finally:
        _stop_server(handle)


def _replay(
    handle: ServerHandle,
    root: Path,
    requests: list[dict[str, Any]],
    saved: dict[str, Any],
    candidate: bool,
    mode: str,
    rules: dict[str, Any],
) -> list[Any]:
    normalizer = Normalizer(rules, handle.db_path.parent)
    evidence = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
        for index, step in enumerate(requests):
            resolved = substitute(step, saved)
            if step.get("kind") == "cli":
                answer = driver.run_cli(
                    driver.CliHost(handle, root),
                    resolved,
                    candidate,
                    root / "client-input",
                    mode,
                )
                driver.cli_capture(answer, resolved, saved, mode)
                evidence.append(
                    {
                        "step": index,
                        "proof": step["proof"],
                        "answer": normalizer.normalize(driver.comparable(answer)),
                    }
                )
            else:
                response = client.request(
                    resolved["method"],
                    handle.base + resolved["path"],
                    json=resolved.get("json"),
                    headers=resolved.get("headers"),
                )
                assert response.status_code == resolved["status"], (
                    resolved,
                    response.text,
                )
                if step.get("capture"):
                    capture_values(response.json(), resolved, saved)
    return evidence


def test_unit20_same_python_host_populated_sequence(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Fresh database/files and process state make both CLI write sequences comparable."""
    started = time.perf_counter()
    spec = json.loads(LIST.read_text())
    rules = json.loads((LIST.parent.parent / "rules" / LIST.name).read_text())
    root = tmp_path / "same-host"
    handle = _start_server(root, server_module="tests.compare.host")
    captures: dict[str, Any] = {}
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            for step in spec["seed_requests"]:
                resolved = substitute(step, captures)
                answer = client.request(
                    resolved["method"],
                    handle.base + resolved["path"],
                    json=resolved.get("json"),
                    headers=resolved.get("headers"),
                )
                assert answer.status_code == step["status"], (resolved, answer.text)
                capture_values(answer.json(), step, captures)
    finally:
        _stop_server(handle)
    port = handle.port
    snapshot = tmp_path / "seed.db"
    backup_database(handle.db_path, snapshot)
    seed_files = tmp_path / "seed-files"
    seed_files.mkdir()
    for directory in ("skills", "files"):
        if (root / directory).is_dir():
            shutil.copytree(root / directory, seed_files / directory)
    proof: dict[str, Any] = {}
    for mode in ("json", "text"):
        answers = []
        for candidate in (False, True):
            # Never replace a database beneath a running server. The exact owned
            # process stopped above, so schema, caches and files all start fresh.
            shutil.rmtree(root)

            def restore(target: Path) -> None:
                backup_database(snapshot, target)
                for directory in seed_files.iterdir():
                    shutil.copytree(
                        directory, target.parent / directory.name, dirs_exist_ok=True
                    )

            handle = _start_server(
                root, port=port, server_module="tests.compare.host", seed_db=restore
            )
            try:
                answers.append(
                    _replay(
                        handle,
                        tmp_path,
                        spec["requests"],
                        dict(captures),
                        candidate,
                        mode,
                        rules,
                    )
                )
            finally:
                _stop_server(handle)
        proof[mode] = answers
        _report(
            request,
            "unit20-same-host",
            {
                "seconds": time.perf_counter() - started,
                "modes_completed": list(proof),
                "comparisons": proof,
            },
        )
        assert answers[0] == answers[1], proof


def test_unit20_every_help_and_group_usage(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Fresh Click metadata selects cases; no frozen help snapshot hides Python drift."""
    started = time.perf_counter()
    import click

    from planner.cli.main import main

    declarations: list[tuple[list[str], click.Command]] = []

    def visit(command: click.Command, path: list[str]) -> None:
        declarations.append((path, command))
        if isinstance(command, click.Group):
            for name, child in command.commands.items():
                visit(child, [*path, name])

    visit(main, [])
    cases = [(path + ["--help"], 0) for path, _ in declarations]
    cases.extend(
        (
            (["projec"], 2),
            (["project", "shwo"], 2),
            (["manager", "delte"], 2),
            (["unit20-no-such-command"], 2),
        )
    )
    for path, command in declarations:
        if not isinstance(command, click.Group):
            continue
        descendants = [
            (p, c)
            for p, c in declarations
            if p[: len(path)] == path
            and len(p) > len(path)
            and not isinstance(c, click.Group)
        ]
        missing = next(
            (
                p
                for p, c in descendants
                if any(parameter.required for parameter in c.params)
            ),
            path,
        )
        # Every group has a child with --json. A value for a boolean option is a
        # concrete bad value even where the group has no numeric/choice options.
        bad = next(
            (
                p + ["--json=no"]
                for p, c in descendants
                if any(
                    isinstance(parameter, click.Option) and "--json" in parameter.opts
                    for parameter in c.params
                )
            ),
            path + ["--help=no"],
        )
        typed = next(
            (
                p + [parameter.opts[0], "not-an-integer"]
                for p, c in descendants
                for parameter in c.params
                if isinstance(parameter, click.Option)
                and isinstance(parameter.type, click.types.IntParamType)
            ),
            None,
        )
        cases.extend(
            ((missing, 2), (path + ["--unknown-unit20-option"], 2), (typed or bad, 2))
        )
    handle = _start_server(tmp_path / "host", server_module="tests.compare.host")
    evidence = []
    try:
        for argv, code in cases:
            answers = [
                driver.run_cli(
                    driver.CliHost(handle, tmp_path),
                    _step(argv, code),
                    bool(side),
                    tmp_path / f"client-{side}",
                    "text",
                )
                for side in (0, 1)
            ]
            evidence.append({"argv": argv, "answers": answers})
            assert driver.comparable(answers[0]) == driver.comparable(
                answers[1]
            ), evidence[-1]
        _report(
            request,
            "unit20-help-and-usage",
            {
                "seconds": time.perf_counter() - started,
                "cases": len(cases),
                "comparisons": evidence,
            },
        )
    finally:
        _stop_server(handle)
