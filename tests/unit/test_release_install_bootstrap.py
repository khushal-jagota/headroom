"""First-install authority boundaries before any downloaded interpreter executes."""

import hashlib
import io
import json
import signal
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from http.client import HTTPMessage
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import panels_install as bootstrap


@pytest.mark.parametrize(
    "target,command,version",
    [
        ("linux", "home", 1),
        ("linux", "home", 2),
        ("darwin", "home", 1),
        ("darwin", "home", 2),
        ("linux", "join", 1),
        ("darwin", "join", 1),
    ],
)
def test_local_receipt_routes_native_home_and_preserves_legacy_installer_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    command: str,
    version: int,
) -> None:
    archive = tmp_path / "release.tar.gz"
    archive.write_bytes(b"synthetic archive")
    receipt_file, root = tmp_path / "publication.json", tmp_path / "new-home"
    selected: dict[str, Any] = {
        "archive_sha256": "a" * 64,
        "manifest": {
            "app_sha": "b" * 40,
            "runtime_contract": {
                "version": version,
                "system": target,
                "architecture": "arm64" if target == "darwin" else "x86_64",
                "role": "home",
            },
        },
    }
    receipt: dict[str, Any] = (
        {"mac_home": selected} if target == "darwin" and command == "home" else selected
    )
    if command == "join":
        receipt = {
            "manifest": {"app_sha": "b" * 40},
            "mac" if target == "darwin" else "linux_executor": {"archive_sha256": "a" * 64},
        }
    receipt_file.write_text(json.dumps(receipt))
    args = [
        "panels_install.py",
        command,
        "--root",
        str(root),
        "--archive",
        str(archive),
        "--receipt",
        str(receipt_file),
    ]
    if command == "join":
        args += ["--home", "https://home.invalid", "--machine-id", "synthetic-machine"]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(bootstrap, "host_target", lambda: target)
    monkeypatch.setattr(bootstrap, "preflight", lambda *_: None)
    monkeypatch.setattr("scripts.panels_install.shutil.which", lambda _: "/synthetic/host/node")
    monkeypatch.setattr("scripts.panels_install.getpass.getpass", lambda _: "synthetic-secret")
    extracted: list[Path] = []
    monkeypatch.setattr(
        bootstrap,
        "extract_bootstrap",
        lambda _archive, destination, _digest: extracted.append(destination),
    )
    monkeypatch.setattr(bootstrap, "check_linux_contract", lambda _: None)
    invoked: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(
        bootstrap,
        "run_installer",
        lambda argv, value, _env: invoked.append((argv, json.loads(value))),
    )
    bootstrap.main()
    argv, value = invoked[0]
    payload = extracted[0]
    if command == "home":
        expected: dict[str, Any] = {
            "root": str(root),
            "service": "com.panels.home",
            "port": 8767,
            "isolated_providers": False,
            "archive": str(archive),
            "archive_sha256": "a" * 64,
            "revision": "b" * 40,
        }
        if version == 2:
            assert argv == [
                "/opt/homebrew/bin/node" if target == "darwin" else "/synthetic/host/node",
                str(payload / "server/installation/home_main.ts"),
            ]
            wrong_target = json.loads(json.dumps(receipt))
            (wrong_target["mac_home"] if target == "darwin" else wrong_target)["manifest"][
                "runtime_contract"
            ]["architecture"] = "wrong"
            with pytest.raises(ValueError, match="target"):
                bootstrap.select_receipt(wrong_target, target, command)
            if target == "darwin":
                selected["manifest"]["runtime_contract"]["role"] = "executor"
                with pytest.raises(ValueError, match="home artifact"):
                    bootstrap.select_receipt(receipt, target, command)
        else:
            assert argv == [
                str(payload / ".venv/bin/python"),
                "-I",
                "-B",
                "-c",
                "from planner.installation.home import main; main()",
            ]
            expected["installer_python"] = str(Path(sys.executable).resolve())
    else:
        assert argv == [
            str(payload / ("python/bin/python3" if target == "darwin" else ".venv/bin/python")),
            "-I",
            "-B",
            "-c",
            "from planner.installation.join import main; main()",
        ]
        expected = {
            "root": str(root),
            "label": "com.panels.executor" if target == "darwin" else "panels-executor.service",
            "home_url": "https://home.invalid",
            "machine_id": "synthetic-machine",
            "credential": "synthetic-secret",
            "isolated_providers": False,
            "archive": str(archive),
            "archive_sha256": "a" * 64,
            "archive_size": archive.stat().st_size,
            "revision": "b" * 40,
        }
    assert value == expected


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


@pytest.mark.parametrize("command", ["home", "join"])
def test_publication_refuses_receipt_from_another_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    args = bootstrap.parser().parse_args([command])
    workflow = "publish-typescript-release.yml" if command == "join" else "deploy.yml"
    artifact = "panels-linux-executor" if command == "join" else "panels-app"
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
        if path.startswith(f"actions/workflows/{workflow}/runs?"):
            return {
                "workflow_runs": [
                    {
                        "id": 5,
                        "path": f".github/workflows/{workflow}",
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
                    {"name": artifact, "id": 10, "expired": False},
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


@pytest.mark.parametrize(
    "interrupt_signal", [signal.SIGINT, signal.SIGTERM], ids=["legacy-int", "linux-term"]
)
def test_interrupt_waits_for_real_installer_cleanup_through_repeated_ctrl_c(
    tmp_path: Path, interrupt_signal: signal.Signals
) -> None:
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
        "    (root / 'cleanup-started').write_text(str(signum))\n"
        "    time.sleep(.6)\n"
        "    (root / 'cleanup-complete').touch()\n"
        "    raise SystemExit(17)\n"
        "signal.signal(signal.SIGINT, cleanup)\n"
        "signal.signal(signal.SIGTERM, cleanup)\n"
        "(root / 'ready').write_text(str(os.getpgrp()))\n"
        "time.sleep(5)\n"
    )
    wrapper = (
        "import os, runpy, sys; "
        "run = runpy.run_path(sys.argv[1], run_name='bootstrap_test')['run_installer']; "
        "options = {'forward_termination': True} if sys.argv[4] == 'term' else {}; "
        "run([sys.executable, sys.argv[2], sys.argv[3]], "
        "'private synthetic input', dict(os.environ), **options)"
    )
    wrapper_path = tmp_path / "bootstrap.py"
    wrapper_path.write_text(wrapper)
    process = subprocess.Popen(
        [
            sys.executable,
            str(wrapper_path),
            bootstrap.__file__,
            str(child),
            str(tmp_path),
            "term" if interrupt_signal == signal.SIGTERM else "int",
        ],
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
        process.send_signal(interrupt_signal)
        wait_for(tmp_path / "cleanup-started")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 128 + interrupt_signal
        assert int((tmp_path / "cleanup-started").read_text()) == interrupt_signal
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


@pytest.mark.parametrize("version", ["21.0.0", "22.17.0", "22.18.0", "25.0.0"])
def test_linux_join_preflight_requires_the_native_node_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """The native Linux join needs the exact admitted Node major and minimum version."""
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
        output = f"v{version}" if command[-1] == "--version" else "not-found"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(bootstrap, "_run", run)
    if version != "22.18.0":
        with pytest.raises(ValueError, match=r"Node(?:\.js)? 22"):
            bootstrap.preflight(args, "linux")
    else:
        bootstrap.preflight(args, "linux")


@pytest.mark.parametrize("contract_version,role", [(1, "executor"), (2, "home"), (2, "executor")])
def test_linux_join_recipe_checks_target_then_passes_private_stdin_to_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    contract_version: int,
    role: str,
) -> None:
    """This new Node handoff must reject legacy/home payloads before credential or helper use."""
    revision, credential = "a" * 40, "x" * 43
    contract = dict(
        version=contract_version,
        system="linux",
        architecture="x86_64",
        node_major=22,
        minimum_node_version="22.18.0",
        platforms=[dict(distribution="debian", version="13", minimum_glibc="2.41")],
        cpu_features=["sse2"],
        external_libraries=[],
        external_executables=["node"],
    )
    marker = dict(
        schema_version=1,
        application_revision=revision,
        platform="linux",
        architecture="x86_64",
        role=role,
    )
    files = {
        "manifest.json": json.dumps(dict(app_sha=revision, runtime_contract=contract)),
        "mac-runtime.json": json.dumps(marker),
        "server/environments/linux_executor/join_main.ts": "inert entry, never executed",
    }
    archive = tmp_path / "executor.tar.gz"
    with tarfile.open(archive, "w:gz") as packed:
        for name, text in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(text.encode())
            packed.addfile(entry, io.BytesIO(text.encode()))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                **marker,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "archive_size": archive.stat().st_size,
            }
        )
    )
    home, xdg, node = tmp_path / "account", tmp_path / "xdg", tmp_path / "node"
    home.mkdir()
    node.touch()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setenv("NODE_OPTIONS", "--require=/foreign/override")
    monkeypatch.setattr(bootstrap, "host_target", lambda: "linux")
    monkeypatch.setattr("scripts.panels_install.shutil.which", lambda *_args, **_kwargs: str(node))
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, "v22.18.0" if args[-1] == "--version" else "not-found", ""
        ),
    )
    monkeypatch.setattr(
        "scripts.panels_install.platform.freedesktop_os_release",
        lambda: dict(ID="debian", VERSION_ID="13"),
    )
    monkeypatch.setattr("scripts.panels_install.platform.libc_ver", lambda: ("glibc", "2.41"))
    read_text = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            "flags: sse2\n" if str(path) == "/proc/cpuinfo" else read_text(path, *args, **kwargs)
        ),
    )
    prompt, installer = Mock(return_value=credential), Mock()
    monkeypatch.setattr("scripts.panels_install.getpass.getpass", prompt)
    monkeypatch.setattr(bootstrap, "run_installer", installer)
    monkeypatch.setattr(
        "sys.argv",
        [
            "panels_install.py",
            "join",
            "--home",
            "https://home.example",
            "--machine-id",
            "ad1f66de-bcaa-45b7-918a-8d0e7a018ae1",
            "--archive",
            str(archive),
            "--receipt",
            str(receipt),
        ],
    )
    root = xdg / "panels/executor-typescript"
    if contract_version != 2 or role != "executor":
        with pytest.raises(SystemExit, match="native version 2|target or revision"):
            bootstrap.main()
        installer.assert_not_called()
        prompt.assert_not_called()
    else:
        bootstrap.main()
        installer.assert_called_once()
        assert installer.call_args.kwargs == {"forward_termination": True}
        prompt.assert_called_once()
        command, encoded, environment = installer.call_args.args
        value = json.loads(encoded)
        assert command[0] == str(node.resolve()) and command[1].endswith(
            "/server/environments/linux_executor/join_main.ts"
        )
        assert credential not in " ".join(command) and value["credential"] == credential
        assert value["root"] == str(root) and value["home"] == str(home)
        assert value["node_executable"] == str(node.resolve()) and "label" not in value
        assert "NODE_OPTIONS" not in environment and not any(
            key.startswith("PLAN_") for key in environment
        )
    captured = capsys.readouterr()
    assert credential not in captured.out + captured.err and not root.exists()
