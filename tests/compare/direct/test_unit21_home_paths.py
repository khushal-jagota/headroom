"""Fresh Python path contracts. No server, socket, service or database starts."""

import json
import subprocess
from pathlib import Path

import pytest

from planner.platform_runtime.darwin import paths

ROOT = Path(__file__).resolve().parents[3]


def test_unit21_darwin_home_paths_match_python(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Path("/var/tmp/panels-123")
    monkeypatch.setattr(paths, "_runtime_directory", lambda: runtime)
    values = [
        "/short/control.sock",
        "/source/" + "ü" * 64 + "/control.sock",
        "/source/" + "ü" * 64 + "/control.sockx",
        "/" + "x" * 102,
        "/" + "x" * 103,
    ]
    expected = [str(paths._socket_path(Path(value))) for value in values]
    values += expected
    expected += [str(paths._socket_path(Path(value))) for value in expected]
    script = (
        "import{darwin_control_socket_path}from'./server/lifecycle/logic/paths.ts';"
        "console.log(JSON.stringify(JSON.parse(process.argv[1]).map("
        "path=>darwin_control_socket_path(path,process.argv[2]))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(values), str(runtime)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == expected
