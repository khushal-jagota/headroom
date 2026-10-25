"""Real isolated wheel/runtime proof; no Mac, launchd or provider evidence."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    ExecutorSourceClosure,
    ResidentError,
    ResidentPackage,
    ServiceConfiguration,
)
from planner.environments.resident.launch_agent import render_launch_agent
from planner.environments.resident.native_builder import NativeRuntimeBuilder
from planner.environments.resident.packaging import (
    build_source_package,
    prepare_runtime,
    validate_runtime,
)
from planner.environments.resident.paths import resident_paths
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import SshAuthenticationMode


@pytest.mark.parametrize(
    "arguments",
    [
        ["serve"],
        ["--", "serve"],
        ["executor", "run", "--application-revision=untrusted"],
        ["--", "executor", "run", "--application-revision=untrusted"],
        ["executor", "--", "run", "--application-revision=untrusted"],
        ["executor", "--", "run"],
    ],
)
def test_resident_restrictions_precede_cli_routing(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.executor import entrypoint

    monkeypatch.setattr(sys, "argv", ["panels", *arguments])
    monkeypatch.setattr(entrypoint, "main", lambda: pytest.fail("restricted argv reached CLI"))
    with pytest.raises(SystemExit):
        entrypoint.resident_main("a" * 40)


def test_real_wheel_installs_full_cli_data_and_resident_identity(tmp_path: Path) -> None:
    """Protect the gap between checkout imports and installed executable contents."""
    repository = Path(__file__).resolve().parents[2]
    source = tmp_path / "source with spaces"
    source.mkdir()
    shutil.copytree(
        repository / "src/planner",
        source / "src/planner",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in ("pyproject.toml", "requirements-executor.txt", "requirements-build.txt"):
        shutil.copyfile(repository / name, source / name)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "artifact fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    package = ResidentPackage(
        sha,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        sys.platform,
        platform.machine(),
        platform.python_version(),
        ExecutorSourceClosure(
            (
                "src/planner",
                "pyproject.toml",
                "requirements-executor.txt",
                "requirements-build.txt",
            ),
            "planner.executor.entrypoint",
            "requirements-executor.txt",
        ),
    )
    # Dirty source can never affect the exact committed wheel.
    (source / "src/planner/executor/entrypoint.py").write_text("raise RuntimeError('dirty')")
    paths = resident_paths(tmp_path / "Account With Spaces")
    app = build_source_package(source, package, paths.app)
    builder = NativeRuntimeBuilder(Path(sys.executable))
    prepared = prepare_runtime(app, package, paths, builder)
    assert not (app / "executor-bootstrap.py").exists()
    assert not list(app.rglob("*.egg-info"))
    wheel = next((prepared.runtime / "wheels").glob("planner-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for name in (
            "planner/executor/entrypoint.py",
            "planner/cli/main.py",
            "planner/core/migrations/env.py",
            "planner/core/migrations/script.py.mako",
            "planner/core/migrations/planning_retirement_cohort.json",
            "planner/worker_types/shipped_worker_types.json",
            "planner/py.typed",
            "planner/skills/panels/SKILL.md",
        ):
            assert name in names
        assert any(name.startswith("planner/core/migrations/versions/") for name in names)
    receipt = json.loads((prepared.runtime / "resident-installed.json").read_text())
    assert receipt["built_dependencies"][0]["source_sha256"] == (
        "8c6ab23116bbf6affda894acfd5f2ca0fb8facbcbb72121c11c75c33e7ce8cff"
    )
    hostile = tmp_path / "hostile"
    (hostile / "planner").mkdir(parents=True)
    (hostile / "planner/__init__.py").write_text("raise RuntimeError('hostile cwd')")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(
        HOME=str(paths.home),
        PYTHONPATH=str(hostile),
        PLAN_TICKET_ID="t_fixture",
        PLAN_ACTOR="worker",
    )
    # Eager CLI help never imports the executor's actual runtime or AsyncSSH.
    # Resolve that dependency closure from installed wheels under an isolated
    # interpreter, without a checkout path, connection, or provider instance.
    import_proof = subprocess.run(
        [
            str(prepared.runtime / "bin/python"),
            "-I",
            "-B",
            "-c",
            """import importlib, json, sys, sysconfig
from pathlib import Path
names = (
    'planner.executor.mac', 'planner.executor.ssh', 'planner.executor.sftp',
    'planner.executor.preflight', 'planner.cli.main', 'asyncssh',
)
modules = {name: importlib.import_module(name) for name in names}
assert modules['planner.executor.mac'].MacExecutor.__module__ == 'planner.executor.mac'
sites = {Path(sysconfig.get_path(key)).resolve() for key in ('purelib', 'platlib')}
for name, module in tuple(sys.modules.items()):
    if name.split('.')[0] in {'planner', 'asyncssh', 'cryptography', 'cffi'}:
        origin = Path(module.__file__).resolve()
        assert any(origin.is_relative_to(site) for site in sites), (name, str(origin))
print(json.dumps({name: module.__file__ for name, module in modules.items()}))
""",
        ],
        cwd=hostile,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    installed_origins = json.loads(import_proof.stdout)
    assert set(installed_origins) == {
        "planner.executor.mac",
        "planner.executor.ssh",
        "planner.executor.sftp",
        "planner.executor.preflight",
        "planner.cli.main",
        "asyncssh",
    }
    assert all(
        Path(origin).is_relative_to(prepared.runtime) for origin in installed_origins.values()
    )
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
            [str(app / "bin/panels"), "worker", "my-ticket", "brief", "--json"],
            cwd=hostile,
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
    # Both the standard generated console and minimal isolated wrapper work.
    subprocess.run(
        [
            str(prepared.runtime / "bin/python"),
            "-I",
            "-B",
            str(prepared.runtime / "bin/panels"),
            "executor",
            "--help",
        ],
        cwd=hostile,
        env=environment,
        capture_output=True,
        check=True,
    )
    for arguments, error in (
        (["serve"], "resident package cannot run"),
        (["--", "serve"], "resident command separators"),
        (["executor", "run", "--application-revision=" + sha], "application revision is owned"),
        (["--", "executor", "run", "--application-revision=" + sha], "resident command separators"),
        (["executor", "--", "run", "--application-revision=" + sha], "resident command separators"),
    ):
        refused = subprocess.run(
            [str(app / "bin/panels"), *arguments],
            cwd=hostile,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert refused.returncode != 0
        assert not refused.stdout
        assert error in refused.stderr
    validate_runtime(prepared)
    # Certification must run before the separately reviewed transport CLI boundary.
    target = prepared.runtime / "bin/panels"
    console_bytes = target.read_bytes()
    target.write_bytes(console_bytes + b"# altered\n")
    with pytest.raises(ResidentError, match="artifact"):
        validate_runtime(prepared)
    target.write_bytes(console_bytes)
    key, hosts = tmp_path / "executor key", tmp_path / "known hosts"
    key.write_text("isolated fixture private key")
    key.chmod(0o600)
    hosts.write_text("vps.example ssh-ed25519 isolated-fixture-key\n")
    launch = ExecutorLaunchOptions(
        "vps.example",
        sha,
        "https://panels.example",
        22,
        "owner",
        key,
        hosts,
        SshAuthenticationMode.public_key,
    )
    plist = plistlib.loads(
        render_launch_agent(ServiceConfiguration(paths, os.getuid(), launch=launch))
    )
    # The real rendered service argv traverses the installed revision wrapper.
    # Eager help stops before transport, credentials, services, or providers execute.
    subprocess.run(
        [*plist["ProgramArguments"], "--help"],
        cwd=hostile,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
