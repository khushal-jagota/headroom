"""Fresh Python schema guards the executor types shared by both TS peers.

Risk: an envelope schema changes while either peer still compiles against stale
TS types. Python's existing manifest check does not inspect the generated TS file.
This pure contract check needs no database, listener, provider, or Mac process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from planner.executor.protocol_schema import protocol_manifest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "server/executor/tools"


def test_unit22_shared_wire_types_are_current(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "PLAN_TEST_MODE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def run(
        arguments: list[str], body: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            input=body,
            env=environment,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    exported = run([sys.executable, str(TOOLS / "export_wire.py")])
    assert exported.returncode == 0, exported.stderr
    schema = json.loads(exported.stdout)
    manifest = schema["manifest"]
    assert manifest == protocol_manifest()
    assert manifest["protocol_version"] == 22
    assert manifest["spool_format_version"] == 5
    assert manifest["command_journal_format_version"] == 1
    assert {
        "LeaseRequest",
        "QueuedEnvelope",
        "BackendSnapshot",
        "MacUpdateCheck",
        "MacUpdateReport",
        "ExecutorHealthRead",
    } <= set(schema["roots"])

    generator = ["node", str(TOOLS / "generate_wire.ts"), "--check"]
    current = run(generator, exported.stdout)
    assert current.returncode == 0, current.stderr + current.stdout

    # A fresh Python schema with a changed field must fail before a peer uses it.
    schema["$defs"]["MacUpdateCheck"]["properties"]["schema_version"]["type"] = "string"
    stale = run(generator, json.dumps(schema))
    assert stale.returncode != 0, "A changed Python schema passed the check"
    assert "Executor wire output is stale" in stale.stderr

    # Check a changed type file in memory, without modifying the repository tree.
    changed_types = run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "import {pathToFileURL} from 'node:url'; "
            "const {check_wire}=await import(pathToFileURL(process.argv[2]).href); "
            "const source=JSON.parse(readFileSync(0,'utf8')); "
            "try { await check_wire(source, {"
            "schema:readFileSync(process.argv[3],'utf8'),"
            "types:readFileSync(process.argv[4],'utf8')+'\\n'}); process.exitCode=1; } "
            "catch(error) { "
            "if(!error.message.includes('Executor wire output is stale')) "
            "throw error; }",
            "unit22-wire-check",
            str(TOOLS / "generate_wire.ts"),
            str(ROOT / "server/executor/wire.schema.json"),
            str(ROOT / "server/executor/wire.generated.ts"),
        ],
        exported.stdout,
    )
    assert changed_types.returncode == 0, changed_types.stderr

    fixture = tmp_path / "wire-types.ts"
    generated = (ROOT / "server/executor/wire.generated.ts").as_posix()
    root_names = " | ".join(json.dumps(name) for name in schema["roots"])
    fixture.write_text(
        f"import type {{ MacUpdateCheck, WireTypes }} from '{generated}';\n"
        f"type AllPythonRoots = Pick<WireTypes, {root_names}>;\n"
        "const valid: MacUpdateCheck = {schema_version: 1, target: null};\n"
        "// @ts-expect-error A nullable field without a default remains required.\n"
        "const missing: MacUpdateCheck = {schema_version: 1};\n"
        "void [valid, missing];\n"
    )
    compiled = run(
        [
            "node",
            str(ROOT / "server/node_modules/typescript/bin/tsc"),
            "--noEmit",
            "--strict",
            "--skipLibCheck",
            "--module",
            "nodenext",
            "--target",
            "es2022",
            "--allowImportingTsExtensions",
            str(fixture),
        ]
    )
    assert compiled.returncode == 0, compiled.stderr + compiled.stdout

    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report = Path(directory) / "unit-22-wire-types.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "manifest": manifest,
                    "roots": sorted(schema["roots"]),
                    "stale_schema_refused": True,
                    "stale_types_refused": True,
                    "required_nullable_compiled": True,
                    "elapsed_seconds": time.perf_counter() - started,
                    "differences": [],
                },
                indent=2,
            )
            + "\n"
        )
