"""Mac home role and receipt admission without a native Mac execution claim."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts.update_publication import validate_mac_home_publication
from tests.support.update_apps import SHA_B, build_app, release, rewrite_artifact_digest

from planner.environments.update_artifacts import APP_ARCHIVE_NAME, create_app_archive
from planner.environments.update_provider import GitHubUpdateProvider
from planner.platform_runtime.darwin.runtime_contracts import DarwinRuntimeContract
from planner.platform_runtime.selection import parse_optional_runtime_contract, select_home_runtime
from planner.platform_runtime.targets import MAC_EXECUTOR, MAC_HOME


def home_publication(tmp_path: Path) -> dict[str, Any]:
    app = build_app(tmp_path / "app", SHA_B, "native-home")
    # Bind the fixture's real inventory rather than an invented native audit.
    from planner.environments.runtime_contracts import runtime_bytes_digest

    contract = DarwinRuntimeContract(
        "3.12.12", runtime_bytes_digest((app / "runtime/inventory.json").read_bytes())
    )
    manifest = json.loads((app / "manifest.json").read_text())
    manifest["runtime_contract"] = contract.as_dict()
    (app / "runtime/contract.json").write_text(json.dumps(contract.as_dict()))
    (app / "manifest.json").write_text(json.dumps(manifest))
    rewrite_artifact_digest(app)
    manifest = json.loads((app / "manifest.json").read_text())
    archive = tmp_path / APP_ARCHIVE_NAME
    digest = create_app_archive(app, archive)
    value = {
        "schema_version": 1,
        "publication_run_id": 1,
        "application_revision": SHA_B,
        "manifest": manifest,
        "archive_sha256": digest,
        "archive_size": archive.stat().st_size,
        "native": [{"path": ".venv/bin/python3", "architectures": ["arm64"]}],
        "proof": {
            "passed": True,
            "application_revision": SHA_B,
            "relocation": True,
            "health_assets_api": True,
            "archive_sha256": digest,
            "contract_sha256": contract.digest,
            "duration_seconds": 0.1,
        },
    }
    (tmp_path / "mac-home-publication.json").write_text(json.dumps(value))
    return value


def test_mac_home_receipt_requires_same_native_identity_and_home_proof(tmp_path: Path) -> None:
    value = home_publication(tmp_path)
    admitted = validate_mac_home_publication(
        tmp_path, requested=SHA_B, run_id=1, artifact_id=4, build_result="success"
    )
    assert admitted["artifact_id"] == 4
    assert admitted["manifest"] == value["manifest"]
    value["proof"]["health_assets_api"] = False
    (tmp_path / "mac-home-publication.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="native home execution"):
        validate_mac_home_publication(
            tmp_path, requested=SHA_B, run_id=1, artifact_id=4, build_result="success"
        )


def test_mac_contract_roundtrip_refuses_executor_role() -> None:
    contract = DarwinRuntimeContract("3.12.12", "f" * 64)
    assert parse_optional_runtime_contract(contract.as_dict()) == contract
    assert select_home_runtime(MAC_HOME).target == MAC_HOME
    with pytest.raises(ValueError, match="does not match"):
        select_home_runtime(MAC_EXECUTOR)
    with pytest.raises(ValueError, match="home artifact"):
        parse_optional_runtime_contract({**contract.as_dict(), "role": "executor"})


def test_provider_selects_authenticated_mac_home_without_changing_linux_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = home_publication(tmp_path)
    payload = asdict(release())
    payload["mac"] = {"artifact_id": 5, "archive_sha256": "e" * 64, "archive_size": 100}
    payload["mac_home"] = {
        "artifact_id": 4,
        "manifest": value["manifest"],
        "archive_sha256": value["archive_sha256"],
    }
    provider = GitHubUpdateProvider("owner/panels", None, target=MAC_HOME)

    def response(method: str, path: str, **kwargs: object) -> httpx.Response:
        if path.endswith("/artifacts"):
            return httpx.Response(
                200,
                json={
                    "artifacts": [
                        {"id": identity, "name": name, "expired": False}
                        for identity, name in (
                            (2, "panels-app"),
                            (3, "panels-publication-proof"),
                            (4, "panels-mac-home"),
                            (5, "panels-mac-runtime"),
                        )
                    ]
                },
            )
        if path.endswith("/zip"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as packed:
                packed.writestr("publication.json", json.dumps(payload))
            return httpx.Response(200, content=stream.getvalue())
        return httpx.Response(
            200,
            json={
                "head_branch": "main",
                "event": "push",
                "conclusion": "success",
                "head_repository": {"full_name": "owner/panels"},
                "path": ".github/workflows/deploy.yml",
                "head_sha": SHA_B,
            },
        )

    monkeypatch.setattr(provider, "_request", response)
    selected = provider.publication(1)
    assert selected is not None and selected.artifact_id == 4
    assert selected.mac is None
    assert selected.manifest.runtime_contract is not None
    assert selected.manifest.runtime_contract.system == "darwin"
    linux = GitHubUpdateProvider("owner/panels", None)
    monkeypatch.setattr(linux, "_request", response)
    selected_linux = linux.publication(1)
    assert selected_linux is not None and selected_linux.artifact_id == 2
    provider._publication_cache = None
    payload["mac_home"]["artifact_id"] = 999
    with pytest.raises(ValueError, match="identity differs"):
        provider.publication(1)
    payload.pop("mac_home")
    assert provider.publication(1) is None


def test_native_admission_uses_hosted_node_outside_default_system_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform
    import shutil
    import sys

    from planner.environments import runtime_admission
    from planner.platform_runtime.darwin.runtime import DarwinRuntime

    node = tmp_path / "hosted-toolcache/node/bin/node"
    node.parent.mkdir(parents=True)
    node.touch()
    monkeypatch.setattr(shutil, "which", lambda name: str(node))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("15.0", (), ""))
    monkeypatch.setattr(sys, "platform", "darwin")
    commands = []

    def probe(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return "v22.12.0"

    monkeypatch.setattr(runtime_admission, "_run_probe", probe)
    contract = DarwinRuntimeContract("3.12.12", "f" * 64)
    runtime = DarwinRuntime()
    runtime.verify_host(contract, state=tmp_path)
    assert commands == [[str(node), "--version"]]
    assert runtime.native_tool_commands(tmp_path, contract)[0][0] == str(node)
    assert runtime_admission.probe_environment(tmp_path)["PATH"].split(":")[0] == str(node.parent)
    monkeypatch.setattr(sys, "platform", "linux")
    assert runtime_admission.probe_environment(tmp_path)["PATH"] == "/usr/local/bin:/usr/bin:/bin"
