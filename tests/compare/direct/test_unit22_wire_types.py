"""Native schema and peer types agree before either executor starts.

This pure check protects stale generated types and required nullable fields.
The repository's native wire schema is authoritative. It needs no Python exporter.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from tests.support.synthetic_homes import create_synthetic_homes

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "server/executor/tools"


def test_unit22_shared_wire_types_are_current(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        **create_synthetic_homes(tmp_path),
        "PLAN_SERVER_URL": "http://127.0.0.1:1",
        "PLAN_TEST_MODE": "1",
    }

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            env=environment,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    schema_path = ROOT / "server/executor/wire.schema.json"
    types_path = ROOT / "server/executor/wire.generated.ts"
    schema = json.loads(schema_path.read_text())
    manifest = schema["manifest"]
    assert manifest["protocol_version"] == 22
    assert manifest["spool_format_version"] == 5
    assert manifest["command_journal_format_version"] == 1
    assert {"LeaseRequest", "QueuedEnvelope", "MacUpdateCheck", "ExecutorHealthRead"} <= set(
        schema["roots"]
    )
    current = run(["node", str(TOOLS / "generate_wire.ts"), "--check"])
    assert current.returncode == 0, current.stderr + current.stdout

    # A changed native schema must not silently leave both peers on old types.
    stale = run([
        "node", "--input-type=module", "-e",
        "import {readFileSync} from 'node:fs';"
        "import {check_wire} from './server/executor/tools/generate_wire.ts';"
        "const text=readFileSync(process.argv[1],'utf8'),schema=JSON.parse(text);"
        "schema.$defs.MacUpdateCheck.properties.schema_version.type='string';"
        "try{await check_wire(schema,{schema:text,types:readFileSync(process.argv[2],'utf8')});"
        "process.exitCode=1;}catch(error){"
        "if(!error.message.includes('Executor wire types are stale'))throw error;}",
        str(schema_path), str(types_path),
    ])
    assert stale.returncode == 0, stale.stderr + stale.stdout

    fixture = tmp_path / "wire-types.ts"
    fixture.write_text(
        f"import type {{ MacUpdateCheck, TurnToken }} from '{types_path.as_posix()}';\n"
        "const valid: MacUpdateCheck = {schema_version: 1, target: null};\n"
        "// @ts-expect-error Nullable without a default remains required.\n"
        "const missing: MacUpdateCheck = {schema_version: 1};\n"
        "// @ts-expect-error Integer fields use native numbers.\n"
        "const bigint: TurnToken = {conversation_id: 'c', turn_number: 1n};\n"
        "void [valid, missing, bigint];\n"
    )
    compiled = run([
        "node", str(ROOT / "server/node_modules/typescript/bin/tsc"),
        "--noEmit", "--strict", "--skipLibCheck", "--module", "nodenext",
        "--target", "es2022", "--allowImportingTsExtensions", "--resolveJsonModule", str(fixture),
    ])
    assert compiled.returncode == 0, compiled.stderr + compiled.stdout
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination) / "unit-22-wire-types.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "manifest": manifest, "roots": sorted(schema["roots"]),
            "stale_types_refused": True, "required_nullable_compiled": True,
            "native_number_types": True, "seconds": time.perf_counter() - started,
        }, indent=2) + "\n")
