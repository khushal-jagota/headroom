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
from tests.compare.direct.unit20_wire import wire_view
from tests.compare.runner import Normalizer, backup_database, capture_values, substitute
from tests.support.server import ServerHandle
from tests.support.server_fixtures import _start_server, _stop_server

LIST = driver.ROOT / "tests/compare/lists/unit-20-commands.work.json"


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
            driver.run_cli(driver.CliHost(handle, tmp_path), step, False, tmp_path / "input")
    monkeypatch.setattr(driver, "LIVE_URL", handle.base)
    with pytest.raises(ValueError, match="live or foreign"):
        driver.run_cli(
            driver.CliHost(handle, tmp_path),
            _step(["--help"]),
            False,
            tmp_path / "input",
        )


def test_unit20_parser_smoke_and_startup(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """One existing host proves real subprocess output and startup without browser cost."""
    started = time.perf_counter()
    handle = _start_server(tmp_path / "host", server_module="tests.compare.host")
    evidence: dict[str, Any] = {"timings": {}, "reference_crosschecks": []}
    try:
        host = driver.CliHost(handle, tmp_path)
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
                if side == 0:
                    library = driver.run_cli(
                        host,
                        _step(argv),
                        False,
                        tmp_path / "library-input",
                        "text",
                        python_in_process=True,
                    )
                    evidence["reference_crosschecks"].append(
                        {"argv": argv, "subprocess": answers[0], "library": library}
                    )
                    assert driver.comparable(library) == driver.comparable(answers[0])
        evidence["seconds"] = time.perf_counter() - started
        driver.write_report(request, "unit20-startup", evidence)
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
    result: dict[str, Any],
) -> None:
    normalizer = Normalizer(rules, handle.db_path.parent)
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
                    python_in_process=not candidate,
                )
                driver.cli_capture(answer, resolved, saved, mode)
                result["raw"].append(
                    {"argv": resolved["argv"], "proof": step["proof"], "outcome": answer}
                )
                result["normalized"].append(
                    {
                        "step": index,
                        "proof": step["proof"],
                        "answer": normalizer.normalize(driver.comparable(answer)),
                        "wire": wire_view(answer, normalizer),
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
                result["controls"].append(
                    {
                        "step": index,
                        "method": resolved["method"],
                        "path": resolved["path"],
                        "status": response.status_code,
                    }
                )
                if step.get("capture"):
                    capture_values(response.json(), resolved, saved)


def compare_cli_sequence(
    list_path: Path, backend: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """One reset route proves CLI isolation and actual TS-backend human/JSON bytes."""
    started = time.perf_counter()
    spec = json.loads(list_path.read_text())
    rules = json.loads((list_path.parent.parent / "rules" / list_path.name).read_text())
    root = tmp_path / "same-host"
    handle = _start_server(root, server_module="tests.compare.host")
    captures: dict[str, Any] = {}
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
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
    for name in ("skills", "files", "credential-key"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, seed_files / name)
        elif source.is_file():
            shutil.copy2(source, seed_files / name)
    report_name = list_path.stem + (
        ".same-python" if backend == "tests.compare.host" else ".typescript-backend"
    )
    proof: dict[str, Any] = {
        "list": list_path.name,
        "candidate_backend": backend,
        "boundary": ("Python baseline vs TS CLI: both modes on Python; human text on TS"),
        "strategy": {"python": "CliRunner.invoke", "typescript": "subprocess"},
        "seed_capture_names": sorted(captures),
        "comparisons": {},
    }
    # The ordinary list already compares JSON CLI text and HTTP bytes on both backends.
    modes = ("text",) if backend == "tests.compare.ts_launcher" else ("json", "text")
    for mode in modes:
        answers: list[dict[str, Any]] = []
        for candidate in (False, True):
            # Never replace a database beneath a running server. The exact owned
            # process stopped above, so schema, caches and files all start fresh.
            shutil.rmtree(root)

            def restore(target: Path) -> None:
                backup_database(snapshot, target)
                for source in seed_files.iterdir():
                    if source.is_dir():
                        shutil.copytree(source, target.parent / source.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(source, target.parent / source.name)

            handle = _start_server(
                root,
                port=port,
                server_module=backend if candidate else "tests.compare.host",
                seed_db=restore,
            )
            saved = dict(captures)
            result: dict[str, Any] = {
                "raw": [],
                "normalized": [],
                "controls": [],
                "captures": saved,
            }
            try:
                _replay(handle, tmp_path, spec["requests"], saved, candidate, mode, rules, result)
            finally:
                _stop_server(handle)
                answers.append(result)
                proof["comparisons"][mode] = answers
                proof["seconds"] = time.perf_counter() - started
                driver.write_report(request, report_name, proof)
        assert answers[0]["normalized"] == answers[1]["normalized"], proof


@pytest.mark.parametrize(
    "backend", ["tests.compare.host", "tests.compare.ts_launcher"], ids=["python", "typescript"]
)
def test_unit20_same_python_host_populated_sequence(
    tmp_path: Path, request: pytest.FixtureRequest, backend: str
) -> None:
    compare_cli_sequence(LIST, backend, tmp_path, request)


def test_unit20_every_help_and_group_usage(tmp_path: Path, request: pytest.FixtureRequest) -> None:
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
            (["--", "project", "--help"], 0),
            (["project", "list", "--help=no"], 2),
            (["project", "list", "--limit"], 2),
            (["project", "list", "--json=no"], 2),
            (["project", "list", "--limit", "bad"], 2),
            (["project", "list", "--limit", "a'b"], 2),
            (["project", "list", "-xy"], 2),
            (["project", "list", "--limit", "bad", "--bogus"], 2),
            (["project", "list", "--limit", "bad", "--help"], 0),
            (["ticket", "show", "--help", "--bogus"], 2),
            (["ticket", "block", "t1", "extra"], 2),
            (["sprint", "outcome", "add"], 2),
            (["sprint", "outcome", "otherx"], 2),
            (["projec"], 2),
            (["project", "shwo"], 2),
            (["manager", "delte"], 2),
            (["-"], 2),
        )
    )
    for path, command in declarations:
        if not isinstance(command, click.Group):
            continue
        descendants = [
            (p, c)
            for p, c in declarations
            if p[: len(path)] == path and len(p) > len(path) and not isinstance(c, click.Group)
        ]
        missing = next(
            (p for p, c in descendants if any(parameter.required for parameter in c.params)),
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
        cases.extend(((missing, 2), (path + ["--unknown-unit20-option"], 2), (typed or bad, 2)))
    handle = _start_server(tmp_path / "host", server_module="tests.compare.host")
    evidence = []
    differences = []
    try:
        batch = driver.help_batch(driver.CliHost(handle, tmp_path), cases, tmp_path / "help-input")
        for (argv, _), left, right in zip(cases, batch["python"], batch["typescript"], strict=True):
            answers = [left, right]
            evidence.append({"argv": argv, "answers": answers})
            if driver.comparable(left) != driver.comparable(right):
                differences.append(evidence[-1])
        driver.write_report(
            request,
            "unit20-help-and-usage",
            {
                "seconds": time.perf_counter() - started,
                "cases": len(cases),
                "strategy": batch["strategy"],
                "batch_seconds": batch["seconds"],
                "comparisons": evidence,
                "differences": differences,
            },
        )
        assert not differences, differences
    finally:
        _stop_server(handle)


def test_unit20_declarations_match_fresh_python(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Pin functional parser metadata without requiring Python help presentation."""
    import os
    import sys

    from tests.support.synthetic_homes import create_synthetic_homes

    started = time.perf_counter()
    environment = {name: os.environ[name] for name in ("PATH", "LANG", "TZ") if name in os.environ}
    environment.update(create_synthetic_homes(tmp_path))
    environment = {
        name: value for name, value in environment.items() if not name.startswith("PLAN_")
    }
    environment["PYTHONPATH"] = str(driver.ROOT / "src")
    python = driver.raw_process(
        [
            sys.executable,
            "-c",
            "import json; from tests.compare.direct.unit20_metadata import declarations; "
            "print(json.dumps(declarations(), ensure_ascii=False))",
        ],
        environment,
    )
    assert python.returncode == 0, python.stderr
    typescript = driver.raw_process(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {commands} from './server/cli/commands.ts'; "
            "process.stdout.write(JSON.stringify(commands));",
        ],
        environment,
    )
    assert typescript.returncode == 0, typescript.stderr
    left, right = json.loads(python.stdout), json.loads(typescript.stdout)
    driver.write_report(
        request,
        "unit20-declaration-drift",
        {
            "seconds": time.perf_counter() - started,
            "scope": "Functional declarations: command/parameter order, options, "
            "types, defaults, choices, path rules, nargs and environment variables. "
            "Human help and layout metadata are excluded.",
            "python": left,
            "typescript": right,
        },
    )
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        from tests.compare.direct.unit20_metadata import render_declarations

        # This is review evidence, never a repository mutation or a test bypass.
        (Path(destination) / "unit20-fresh-commands.ts").write_text(render_declarations(left))

    def functional(value: Any, metadata: bool = True) -> Any:
        if isinstance(value, list):
            return [functional(item, metadata) for item in value]
        if isinstance(value, dict):
            return {
                key: functional(item, metadata and key == "parameters")
                for key, item in value.items()
                if not metadata
                or key not in {"help", "short_help", "epilog", "metavar", "show_default"}
            }
        if type(value) is float and value.is_integer():
            return int(value)
        return value

    # JSON keeps boolean/number distinctions while allowing equivalent numeric spellings.
    assert json.dumps(functional(left), sort_keys=True) == json.dumps(
        functional(right), sort_keys=True
    )
