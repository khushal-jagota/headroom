"""One paired receipt/frontier sequence; candidate Python is recorded, never executed."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.test_unit21_updates import _environment, _report
from tests.unit.test_update_prefetch import ZipProvider
from tests.unit.test_update_prefetch import published as published

from planner.environments.runtime_contracts import RuntimeContract
from planner.environments.update_contracts import PublishedUpdate
from planner.environments.update_install import (
    require_exact_schema_frontier,
    schema_change_pending,
)
from planner.environments.update_prefetch import stage_release

ROOT = Path(__file__).resolve().parents[3]


def test_unit21_receipt_staging_and_readonly_frontier(
    tmp_path: Path,
    published: tuple[ZipProvider, PublishedUpdate],
    request: pytest.FixtureRequest,
) -> None:
    began = time.perf_counter()
    provider, receipt = published
    for language in ("python", "typescript"):
        for name in ("current", "manifest", "admission"):
            (tmp_path / language / name).mkdir(parents=True)
        for name in (".app-next-old", ".app-download-old"):
            (tmp_path / language / "current" / name).mkdir()
        (tmp_path / language / "current/.app-next-alias").symlink_to(
            tmp_path / "outside"
        )
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside/sentinel").write_text("outside")
    admissions, refusing = 0, False

    def admit(_root: Path, _manifest: Any) -> None:
        nonlocal admissions
        admissions += 1
        if refusing:
            raise ValueError("runtime probe failed")

    rows = []
    for kind in ("fresh", "reuse", "retained-refusal", "manifest", "admission"):
        current = (
            tmp_path
            / "python"
            / ("current" if kind in ("fresh", "reuse", "retained-refusal") else kind)
        )
        refusing = kind in ("retained-refusal", "admission")
        changed = receipt
        if kind == "manifest":
            changed = replace(
                receipt, manifest=replace(receipt.manifest, source_digest="1" * 64)
            )
        error = None
        try:
            stage_release(provider, changed, current, admit=admit)
        except ValueError as cause:
            error = str(cause)
        rows.append(
            [
                kind,
                error,
                sorted(path.name for path in current.iterdir()),
                len(provider.downloads),
                admissions,
            ]
        )
    graph = tmp_path / "graph"
    shutil.copytree(
        ROOT / "src/planner/core/migrations/versions",
        graph / "src/planner/core/migrations/versions",
    )
    metadata = graph / "server/environments/release/migration_graph.json"
    metadata.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "server/environments/release/migration_graph.json", metadata)
    revisions = json.loads(metadata.read_text())["revisions"]
    heads = set(revisions) - {
        parent for parents in revisions.values() for parent in parents
    }
    databases: list[Path] = []
    frontier: list[str | None] = []
    (tmp_path / "~").mkdir()
    for name, versions in (
        ("~/schema.sqlite", heads),
        ("different.sqlite", {"unknown"}),
    ):
        database = tmp_path / name
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
            connection.executemany(
                "INSERT INTO alembic_version VALUES(?)",
                [(value,) for value in versions],
            )
        databases.append(database)
        try:
            require_exact_schema_frontier(database, graph)
            frontier.append(None)
        except ValueError as cause:
            frontier.append(str(cause))
    with pytest.raises(ValueError) as refusal:
        schema_change_pending(databases[1], graph)
    spec = tmp_path / "stage.json"
    spec.write_text(
        json.dumps(
            dict(
                root=str(tmp_path),
                artifact=str(provider.artifact),
                release=asdict(receipt),
                schema_error=str(refusal.value),
            )
        )
    )
    launcher = tmp_path / "stage.mjs"
    launcher.write_text(
        "import{readFileSync}from'node:fs';import{registerHooks}from'node:module';"
        "const probe={calls:[],response:null};globalThis.__schema=probe;"
        "const target=new URL('../../release/admission_process.ts',process.argv[2]).href;"
        "const hooks=registerHooks({load(url,ctx,next){if(url!==target)return next(url,ctx);"
        "return{format:'module',shortCircuit:true,source:'export * from '+"
        "JSON.stringify(target+'?actual')+';"
        "export async function run_runtime_json_probe(argv,state,deadline,owner){"
        "const p=globalThis.__schema;p.calls.push({argv,state,deadline,owner});"
        "p.entered?.();if(p.response instanceof Error)throw p.response;return p.response;}'};}});"
        "try{const{staging_parity}=await import(process.argv[2]);console.log(JSON.stringify("
        "await staging_parity(JSON.parse(readFileSync(process.argv[3],'utf8')),probe)));}"
        "finally{hooks.deregister();}"
    )
    environment = {
        **_environment(tmp_path),
        "TMPDIR": str(tmp_path),
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
    }
    before = [path.read_bytes() for path in databases]
    node_began = time.perf_counter()
    process = subprocess.Popen(
        [
            "node",
            str(launcher),
            (ROOT / "server/environments/updates/testing/staging.ts").as_uri(),
            str(spec),
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        output, errors = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
    assert process.returncode == 0, errors
    actual = json.loads(output)
    assert actual["rows"] == rows and actual["frontier"] == frontier
    assert actual["schema"] == [
        False,
        str(refusal.value),
        "Python schema probe returned an incompatible response",
    ]
    assert actual["spawned"] == actual["closed"] > 0 and actual["outside"] == "outside"
    candidate = (
        tmp_path / "typescript/current" / (".app-next-" + receipt.manifest.app_sha)
    )
    held = actual["held_cleanup"]
    assert held["fresh"] == {
        "held": True,
        "original_error": True,
        "candidate_after": False,
        "workspace_after": False,
    }
    assert held["retained"] == {
        "held": True,
        "original_error": True,
        "candidate_after": True,
        "workspace_after": True,
    }
    assert held["schema"] == {
        "held": True,
        "original_error": True,
        "cleanup_error": True,
        "state_removed": True,
    }
    argv = actual["argv"]
    assert isinstance(receipt.manifest.runtime_contract, RuntimeContract)
    assert argv[:4] == [str(candidate / ".venv/bin/python"), "-I", "-B", "-c"]
    assert argv[5:] == [
        str(candidate),
        receipt.manifest.runtime_contract.python_version,
        str(databases[0]),
    ]
    compile(argv[4], "trusted schema wrapper", "exec")  # Syntax only, never exec.
    assert [path.read_bytes() for path in databases] == before
    _report(
        request,
        "unit-21-staging.json",
        dict(
            seconds=time.perf_counter() - began,
            node_seconds=time.perf_counter() - node_began,
            held_cleanup_seconds=held["seconds"],
            held_cleanup_boundary=(
                "The existing host holds actual RuntimeProbeUnsettled cleanup. "
                "Real staging retains fresh and retained candidates until settlement. "
                "Real schema status retains private state and preserves both failure identities. "
                "Outer service adapter coverage does not prove consumer deletion order. "
                "No additional host, test case, or candidate process runs."
            ),
            result=actual,
            actual_candidate_python="HOSTED OPEN; recorded argv and compile-only wrapper here",
        ),
    )
