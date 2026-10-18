"""Cheap proof of observations and credential boundaries; no candidate server."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from planner.environments import deployment_gate as gate


def test_unavailable_observation_is_concrete_and_unready_sha_is_not_a_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "a" * 40
    manifest_path = tmp_path / "app" / "manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text('{"app_sha":"' + sha + '"}')
    from planner.environments.app import AppManifest

    monkeypatch.setattr(
        gate, "validate_app_manifest", lambda _: AppManifest(sha, "b" * 64, "c" * 64)
    )
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(200, json={"app_sha": sha, "ready": False}),
    )
    observed = gate.observe_served(tmp_path, "http://unused/api/health")
    assert observed["base_sha"] is None
    assert observed["manifest"] == manifest_path.read_text()
    assert observed["health"]["status"] == 200
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(200, json={"app_sha": sha, "ready": True}),
    )
    assert gate.observe_served(tmp_path, "http://unused/api/health")["base_sha"] == sha


def test_smoke_environment_is_explicit_and_never_inherits_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross")
    monkeypatch.setenv("PLAN_DB_PATH", "/production/planning.db")
    env = gate.smoke_environment(tmp_path, 12345)
    assert env["PLAN_DISPATCH_ENABLED"] == "0"
    assert env["PLAN_PORT"] == "12345"
    assert "OPENAI_API_KEY" not in env
    for key, value in env.items():
        if key.endswith(("PATH", "HOME", "DIR", "SOCKET")) and key != "PATH":
            assert Path(value).is_relative_to(tmp_path)


def test_malformed_nested_proof_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            {
                "requested_sha": "a" * 40,
                "artifact_digest": "b" * 64,
                "observation": {
                    "current_root": str(tmp_path),
                    "health_url": "http://unused",
                    "manifest_state": "missing",
                    "manifest": None,
                    "base_sha": None,
                    "health": {"status": True, "body": None, "error": None},
                },
            }
        )
    )
    with pytest.raises(ValueError, match="health status"):
        gate.read_gate_evidence(path)
