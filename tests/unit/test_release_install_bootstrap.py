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
    args = bootstrap.parser().parse_args(["home", "--run-id", "5"])
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

    def api(
        repository: str,
        path: str,
        token_file: Path | None,
        destination: Path | None = None,
        *,
        github_cli: bool = False,
    ) -> Any:
        if path == "actions/runs/5":
            return {
                "id": 5,
                "path": ".github/workflows/deploy.yml",
                "head_branch": "main",
                "event": "push",
                "conclusion": "success",
                "head_sha": revision,
                "head_repository": {"full_name": args.repository},
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


def test_registered_platform_refuses_before_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    """An owner-issued Linux command must not use its credential on a Mac runtime."""
    monkeypatch.setattr("sys.argv", ["panels_install.py", "join", "--platform", "linux"])
    monkeypatch.setattr(bootstrap, "host_target", lambda: "darwin")
    with pytest.raises(SystemExit, match="registered --platform"):
        bootstrap.main()
