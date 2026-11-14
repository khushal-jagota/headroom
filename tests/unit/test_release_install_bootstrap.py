"""First-install authority boundaries before any downloaded interpreter executes."""

import hashlib
import io
import json
import tarfile
import urllib.request
import zipfile
from http.client import HTTPMessage
from pathlib import Path
from typing import Any

import pytest
from scripts import panels_install as bootstrap


def test_archive_checksum_precedes_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "app.tar.gz"
    archive.write_bytes(b"wrong release")
    target = tmp_path / "payload"
    with pytest.raises(ValueError, match="checksum"):
        bootstrap.extract_bootstrap(archive, target, "0" * 64)
    assert not target.exists()


def test_checked_archive_still_refuses_link_escape(tmp_path: Path) -> None:
    archive = tmp_path / "app.tar.gz"
    with tarfile.open(archive, "w:gz") as packed:
        link = tarfile.TarInfo("outside")
        link.type = tarfile.SYMTYPE
        link.linkname = "../"
        packed.addfile(link)
        content = tarfile.TarInfo("outside/canary")
        content.size = 1
        packed.addfile(content, io.BytesIO(b"x"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(tarfile.FilterError):
        bootstrap.extract_bootstrap(archive, tmp_path / "payload", digest)
    assert not (tmp_path / "canary").exists()


def test_artifact_redirect_removes_github_token() -> None:
    request = urllib.request.Request("https://api.github.com/repos/a/b/artifacts/1/zip")
    request.add_header("Authorization", "Bearer private")
    redirected = bootstrap.DownloadRedirect().redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "https://blob.example/archive.zip",
    )
    assert redirected is not None
    assert not redirected.has_header("Authorization")


def test_publication_refuses_receipt_from_another_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = bootstrap.parser().parse_args(["home"])
    revision = "a" * 40
    receipt = {
        "publication_run_id": 6,
        "artifact_id": 10,
        "manifest": {
            "app_sha": revision,
            "runtime_contract": {
                "version": 1,
                "system": "linux",
                "architecture": "x86_64",
            },
        },
        "archive_sha256": "0" * 64,
    }

    def api(path: str, destination: Path | None = None) -> Any:
        if path.startswith("actions/workflows/deploy.yml/runs?"):
            return {
                "workflow_runs": [
                    {
                        "id": 5,
                        "path": ".github/workflows/deploy.yml",
                        "head_branch": "main",
                        "event": "push",
                        "conclusion": "success",
                        "head_sha": revision,
                        "head_repository": {"full_name": bootstrap.REPOSITORY},
                    }
                ]
            }
        if path.endswith("/artifacts?per_page=100"):
            return {
                "artifacts": [
                    {"name": "panels-publication-proof", "id": 9, "expired": False},
                    {"name": "panels-app", "id": 10, "expired": False},
                ]
            }
        assert path == "actions/artifacts/9/zip" and destination is not None
        with zipfile.ZipFile(destination, "w") as zipped:
            zipped.writestr("publication.json", json.dumps(receipt))
        return None

    monkeypatch.setattr(bootstrap, "_api", api)
    with pytest.raises(ValueError, match="trusted publication"):
        bootstrap.select_publication(args, "linux", tmp_path)


def test_unsupported_loader_host_refuses_before_candidate_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "app.tar.gz"
    revision = "a" * 40
    manifest = {
        "runtime_contract": {
            "platforms": [
                {"distribution": "debian", "version": "13", "minimum_glibc": "2.41"},
            ]
        }
    }
    content = json.dumps(manifest).encode()
    with tarfile.open(archive, "w:gz") as packed:
        member = tarfile.TarInfo("manifest.json")
        member.size = len(content)
        packed.addfile(member, io.BytesIO(content))
    receipt = {
        "revision": revision,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr("sys.argv", ["panels_install.py", "home", "--root", str(tmp_path / "home")])
    monkeypatch.setattr(bootstrap, "host_target", lambda: "linux")
    monkeypatch.setattr(bootstrap, "preflight", lambda *_: None)
    monkeypatch.setattr(bootstrap, "select_publication", lambda *_: (archive, receipt))
    monkeypatch.setattr(
        "scripts.panels_install.platform.freedesktop_os_release", lambda: {"ID": "unsupported"}
    )

    def never_execute(*arguments: Any, **keywords: Any) -> None:
        raise AssertionError("The candidate interpreter must not execute on an unsupported host")

    monkeypatch.setattr("scripts.panels_install.subprocess.run", never_execute)
    with pytest.raises(SystemExit, match="supported Linux distribution"):
        bootstrap.main()
    assert not (tmp_path / "home").exists()


def test_interrupt_waits_for_real_installer_cleanup_through_repeated_ctrl_c(tmp_path: Path) -> None:
    """A real child must finish rollback beyond subprocess.run's .25-second interrupt grace."""
    import signal
    import subprocess
    import sys
    import time

    child = tmp_path / "installer.py"
    child.write_text(
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "assert sys.stdin.read() == 'private synthetic input'\n"
        "def cleanup(signum, frame):\n"
        "    (root / 'cleanup-started').touch()\n"
        "    time.sleep(.6)\n"
        "    (root / 'cleanup-complete').touch()\n"
        "    raise SystemExit(17)\n"
        "signal.signal(signal.SIGINT, cleanup)\n"
        "(root / 'ready').write_text(str(os.getpgrp()))\n"
        "time.sleep(5)\n"
    )
    wrapper = (
        "import os, runpy, sys; "
        "run = runpy.run_path(sys.argv[1], run_name='bootstrap_test')['run_installer']; "
        "run([sys.executable, sys.argv[2], sys.argv[3]], "
        "'private synthetic input', dict(os.environ))"
    )
    wrapper_path = tmp_path / "bootstrap.py"
    wrapper_path.write_text(wrapper)
    process = subprocess.Popen(
        [sys.executable, str(wrapper_path), bootstrap.__file__, str(child), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    def wait_for(path: Path) -> None:
        deadline = time.monotonic() + 5
        while not path.exists():
            assert process.poll() is None, "The bootstrap exited before the installer checkpoint"
            assert time.monotonic() < deadline, "The installer did not reach its checkpoint"
            time.sleep(0.01)

    try:
        wait_for(tmp_path / "ready")
        assert int((tmp_path / "ready").read_text()) != process.pid
        process.send_signal(signal.SIGINT)
        wait_for(tmp_path / "cleanup-started")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130
        assert (tmp_path / "cleanup-complete").exists()
        assert "private synthetic input" not in stdout + stderr
        assert "Traceback" not in stderr
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("major", [21, 25])
def test_install_preflight_accepts_current_node_and_refuses_old_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, major: int
) -> None:
    """Bootstrap must accept newer Node before any release download or service write."""
    import shutil
    import subprocess

    args = bootstrap.parser().parse_args(
        ["join", "--root", str(tmp_path / "install"), "--service", "test-executor"]
    )
    node = tmp_path / "node"
    node.touch()
    monkeypatch.setattr(shutil, "which", lambda *_: str(node))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def run(*command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = f"v{major}.0.0" if command[-1] == "--version" else "not-found"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(bootstrap, "_run", run)
    if major < 22:
        with pytest.raises(ValueError, match="Node.js 22 or newer"):
            bootstrap.preflight(args, "linux")
    else:
        bootstrap.preflight(args, "linux")
