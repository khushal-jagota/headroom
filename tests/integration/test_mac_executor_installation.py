"""Standard pip installation proof; no Mac, launchd, or provider evidence."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from planner.environments.mac_executor.contracts import ExecutorLaunchOptions, ServiceConfiguration
from planner.environments.mac_executor.launch_agent import render_launch_agent
from planner.environments.mac_executor.paths import mac_executor_paths


def test_standard_install_replaces_same_version_and_preserves_full_worker_cli(
    tmp_path: Path,
) -> None:
    """Protect the gap between checkout imports and installed executable contents."""
    repository = Path(__file__).resolve().parents[2]
    paths = mac_executor_paths(tmp_path / "Account With Spaces")
    source = paths.source
    source.mkdir(parents=True)
    shutil.copytree(
        repository / "src/planner",
        source / "src/planner",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in ("pyproject.toml", "requirements-executor.txt", "requirements-build.txt"):
        shutil.copyfile(repository / name, source / name)
    subprocess.run([sys.executable, "-m", "venv", str(paths.venv)], check=True)
    python = str(paths.venv / "bin/python")
    clean_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key != "PYTHONPATH"
    }

    def pip(*arguments: str) -> None:
        subprocess.run(
            [python, "-m", "pip", *arguments],
            env=clean_environment,
            text=True,
            capture_output=True,
            check=True,
        )

    pip("install", "-r", str(source / "requirements-build.txt"))
    pip(
        "install",
        "--no-build-isolation",
        "--only-binary=:all:",
        "--no-binary=http-ece,PyICU",
        "-r",
        str(source / "requirements-executor.txt"),
    )
    pip("install", "--force-reinstall", "--no-deps", "--no-build-isolation", str(source))
    # Same project version must replace existing installed contents.
    marker = source / "src/planner/installation_fixture.py"
    marker.write_text("VALUE = 'replacement'\n")
    pip("install", "--force-reinstall", "--no-deps", "--no-build-isolation", str(source))
    pip("check")
    sites = subprocess.check_output(
        [python, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
    ).strip()
    installed = Path(sites) / "planner"
    for name in (
        "executor/entrypoint.py",
        "cli/main.py",
        "core/migrations/env.py",
        "core/migrations/script.py.mako",
        "core/migrations/planning_retirement_cohort.json",
        "worker_types/shipped_worker_types.json",
        "py.typed",
        "skills/panels/SKILL.md",
    ):
        assert (installed / name).is_file()
    assert list((installed / "core/migrations/versions").glob("*.py"))
    assert (installed / "installation_fixture.py").read_text() == "VALUE = 'replacement'\n"
    # Setuptools reuses build/lib. Clear only generated build output before an update.
    marker.unlink()
    shutil.rmtree(source / "build")
    pip("install", "--force-reinstall", "--no-deps", "--no-build-isolation", str(source))
    assert not (installed / "installation_fixture.py").exists()
    sha = "a" * 40
    outside_source = tmp_path / "outside_source"
    outside_source.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key != "PYTHONPATH"
    }
    environment.update(
        HOME=str(paths.home),
        PLAN_TICKET_ID="t_fixture",
        PLAN_ACTOR="worker",
    )
    # Eager CLI help never imports the executor's actual runtime or the transport client.
    # Resolve that dependency closure from installed wheels under an isolated
    # interpreter, without a checkout path, connection, or provider instance.
    import_proof = subprocess.run(
        [
            str(paths.venv / "bin/python"),
            "-I",
            "-B",
            "-c",
            """import importlib, json, sys, sysconfig
from pathlib import Path
names = (
    'planner.executor.mac_service', 'planner.executor.app_transport', 'planner.executor.link_files',
    'planner.executor.mac_outbox', 'planner.cli.main', 'aiohttp',
)
modules = {name: importlib.import_module(name) for name in names}
assert (
    modules['planner.executor.mac_service'].MacService.__module__
    == 'planner.executor.mac_service'
)
assert (
    modules['planner.executor.link_files'].GenerationTransfers.__module__
    == 'planner.executor.link_files'
)
sites = {Path(sysconfig.get_path(key)).resolve() for key in ('purelib', 'platlib')}
for name, module in tuple(sys.modules.items()):
    if name.split('.')[0] in {'planner', 'aiohttp', 'cryptography', 'cffi'}:
        # Namespace packages have search paths instead of a module file.
        origins = (
            (module.__file__,) if module.__file__ is not None
            else tuple(module.__spec__.submodule_search_locations or ())
        )
        assert origins, name
        for value in origins:
            origin = Path(value).resolve()
            assert any(origin.is_relative_to(site) for site in sites), (name, str(origin))
print(json.dumps({name: module.__file__ for name, module in modules.items()}))
""",
        ],
        cwd=outside_source,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    # Executor command bootstrap must not open SQLite or contact a peer.
    subprocess.run(
        [
            python,
            "-I",
            "-B",
            "-c",
            """
import socket, sqlite3, sys
from unittest.mock import patch
from planner.executor.entrypoint import main
sys.argv = ['panels', 'executor', '--help']
with patch.object(sqlite3, 'connect', side_effect=AssertionError('database access')), \
     patch.object(socket.socket, 'connect', side_effect=AssertionError('network access')):
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0
assert 'planner.core.db' not in sys.modules
""",
        ],
        cwd=outside_source,
        env=environment,
        capture_output=True,
        check=True,
    )
    installed_origins = json.loads(import_proof.stdout)
    assert set(installed_origins) == {
        "planner.executor.mac_service",
        "planner.executor.app_transport",
        "planner.executor.link_files",
        "planner.executor.mac_outbox",
        "planner.cli.main",
        "aiohttp",
    }
    assert all(Path(origin).is_relative_to(paths.venv) for origin in installed_origins.values())
    requests: list[str] = []

    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            assert self.headers["X-Plan-Actor"] == "worker"
            assert self.headers["X-Plan-Ticket-ID"] == "t_fixture"
            response: dict[str, object]
            if self.path == "/api/tickets/t_fixture/worker-self":
                response = {
                    "id": "t_fixture",
                    "title": "Installed wheel",
                    "worker_type": "coding",
                    "field_values": {"brief": "Read from the isolated fixture."},
                }
            else:
                assert self.path == "/api/worker-types"
                response = {
                    "worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]
                }
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment["PLAN_SERVER_URL"] = f"http://127.0.0.1:{server.server_port}"
    try:
        result = subprocess.run(
            [str(paths.venv / "bin/panels"), "worker", "my-ticket", "brief", "--json"],
            cwd=outside_source,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert json.loads(result.stdout)["header"]["title"] == "Installed wheel"
    assert requests == ["/api/tickets/t_fixture/worker-self", "/api/worker-types"]
    # The ordinary generated console supports executor commands.
    subprocess.run(
        [
            str(paths.venv / "bin/python"),
            "-I",
            "-B",
            str(paths.venv / "bin/panels"),
            "executor",
            "--help",
        ],
        cwd=outside_source,
        env=environment,
        capture_output=True,
        check=True,
    )
    launch = ExecutorLaunchOptions("owner-mac", sha, "https://panels.example")
    plist = plistlib.loads(
        render_launch_agent(ServiceConfiguration(paths, os.getuid(), launch=launch))
    )
    # The rendered service argv uses the ordinary installed console.
    # Eager help stops before transport, credentials, services, or providers execute.
    subprocess.run(
        [*plist["ProgramArguments"], "--help"],
        cwd=outside_source,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    # A noneditable installation must use the explicit backend under retained source.
    claude = Path(plist["EnvironmentVariables"]["PLAN_CLAUDE_EXECUTABLE"])
    claude.parent.mkdir(parents=True)
    claude.write_text("#!/bin/sh\nprintf 'fixture backend'\n")
    claude.chmod(0o755)
    subprocess.run(
        [
            python,
            "-I",
            "-c",
            """
import os, shutil, subprocess
from planner.conversation.production_backends import _claude_launch
launch = _claude_launch('https://panels.example')
assert str(launch.claude_executable) == os.environ['PLAN_CLAUDE_EXECUTABLE']
assert subprocess.check_output([str(launch.claude_executable)], text=True) == 'fixture backend'
assert shutil.which('panels') == os.environ['PATH'].split(':')[0] + '/panels'
""",
        ],
        cwd=outside_source,
        env={**environment, **plist["EnvironmentVariables"]},
        capture_output=True,
        check=True,
    )
