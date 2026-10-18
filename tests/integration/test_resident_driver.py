"""Clean-process bootstrap instruments, without native install or host contact."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tarfile
import textwrap
from dataclasses import asdict
from pathlib import Path

from planner.environments.resident.contracts import ExecutorSourceClosure, ResidentPackage
from planner.environments.resident.transport import (
    _REMOTE_BOOTSTRAP,
    _TOOL_SHARED,
    build_delivery_archive,
)
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION


def test_exact_bundle_bootstraps_without_preinstalled_panels_or_hidden_checkout(
    tmp_path: Path,
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    paths = set(_TOOL_SHARED) | {
        str(path.relative_to(checkout))
        for path in (checkout / "src/planner/environments/resident").rglob("*.py")
    }
    for name in paths:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((checkout / name).read_bytes())
    (source / "src/example").mkdir(parents=True)
    (source / "src/example/runner.py").write_text("pass\n")
    (source / "requirements.lock").write_text("")
    (source / "requirements-build.txt").write_text("")
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
            "exact bootstrap fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    package = ResidentPackage(
        revision,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        "darwin",
        "arm64",
        "3.14.3",
        ExecutorSourceClosure(
            ("src/example/runner.py", "requirements.lock", "requirements-build.txt"),
            "example.runner",
            "requirements.lock",
        ),
    )
    manifest = tmp_path / "spec"
    manifest.write_text(json.dumps(asdict(package)))
    # Dirty checkout bytes cannot enter the committed tooling or app bundle.
    (source / "src/example/runner.py").write_text("raise RuntimeError('dirty')\n")
    archive = build_delivery_archive(source, manifest, revision)
    tree = tmp_path / "delivered"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(tree, filter="data")
    assert (tree / "app/src/example/runner.py").read_text() == "pass\n"
    tools = tree / "tools/src"
    # First stage genuinely imports using no third-party site-packages.
    imports = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from planner.environments.resident import cli,driver,native_builder,packaging;"
        "print(driver.__file__)"
    )
    first = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", imports, str(tools)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert first.stdout.strip() == str(tools / "planner/environments/resident/driver.py")
    # Once target dependencies exist, owner IPC and the production compatibility
    # checker resolve only from delivered tooling, not the local checkout.
    imports = textwrap.dedent(
        '''
        import sqlite3
        import subprocess
        import sys
        from pathlib import Path
        from uuid import uuid4

        tools = Path(sys.argv[1])
        sys.path.insert(0, str(tools))
        from planner.environments.resident import control, service
        from planner.executor import files, legacy_preflight, outbox_storage, preflight

        for module in (files, legacy_preflight, outbox_storage, preflight):
            assert Path(module.__file__).is_relative_to(tools)
        root = Path(sys.argv[2])
        preflight.check_spool_compatibility(root, 2)
        assert not root.exists()
        spool = root / 'spool'
        (spool / 'entries').mkdir(parents=True)
        database = spool / outbox_storage.DATABASE_NAME
        sender = str(uuid4())
        connection = outbox_storage.connect_database(database)
        outbox_storage.initialize_database(connection, sender)
        connection.close()
        outbox_storage.publish_metadata(spool, sender)

        def snapshot():
            return {
                str(path.relative_to(root)): (
                    path.stat().st_mode, path.stat().st_mtime_ns,
                    None if path.is_dir() else path.read_bytes(),
                )
                for path in (root, *root.rglob('*'))
            }

        connections = []
        def audit(event, args):
            if event == 'sqlite3.connect':
                name = str(args[0])
                assert name == ':memory:' or name.endswith('?mode=ro'), name
                connections.append(name)
        sys.addaudithook(audit)
        before = snapshot()
        preflight.check_spool_compatibility(root, 2)
        assert snapshot() == before
        assert database.resolve().as_uri() + '?mode=ro' in connections

        # Crash a real SQLite writer after page spill. The delivered reader must
        # refuse recovery and preserve the database and journal exactly.
        crash = """import os, sqlite3, sys
        c = sqlite3.connect(sys.argv[1])
        c.execute('PRAGMA cache_size=1')
        c.execute('BEGIN IMMEDIATE')
        c.execute('UPDATE outbox_metadata SET sender_id=randomblob(2000000)')
        os._exit(0)
        """
        subprocess.run(
            [sys.executable, '-I', '-B', '-c', crash, str(database)], check=True,
        )
        assert database.with_name(database.name + '-journal').stat().st_size > 0
        before = snapshot()
        connections.clear()
        try:
            preflight.check_spool_compatibility(root, 2)
        except ValueError as error:
            assert 'recovery required' in str(error), str(error)
        else:
            raise AssertionError('preflight accepted recovery-required SQLite state')
        assert not connections
        assert snapshot() == before
        print(preflight.__file__)
        '''
    )
    second = subprocess.run(
        [sys.executable, "-I", "-B", "-c", imports, str(tools), str(tmp_path / "executor")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert second.stdout.strip() == str(tools / "planner/executor/preflight.py")
    # Execute the exact SSH bootstrap byte stream on this Linux instrument. It
    # extracts into the test account root, imports cleanly and refuses native work.
    home = tmp_path / "instrument-home"
    home.mkdir()
    config = [
        "--python",
        sys.executable,
        "--requested-sha",
        revision,
        "--executor-ssh-host",
        "vps.example",
        "--executor-ssh-port",
        "2222",
        "--executor-ssh-user",
        "owner",
        "--executor-ssh-identity-file",
        str(home / "key"),
        "--executor-ssh-known-hosts",
        str(home / "known_hosts"),
        "--panels-server-url",
        "https://panels.example",
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            _REMOTE_BOOTSTRAP,
            base64.b64encode(json.dumps(config).encode()).decode(),
        ],
        input=archive,
        cwd=tmp_path,
        env=dict(os.environ, HOME=str(home)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr.decode()
    assert "actual Mac owner's HOME" in json.loads(result.stdout)["error"]
    assert len(list((home / "Deployments/PanelsExecutor/staging").iterdir())) == 1
