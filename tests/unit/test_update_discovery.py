"""Discovery reads exactly two trusted workflows, each bound to one runtime contract version."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.update_apps import SHA_B, release

from planner.environments import update_provider
from planner.environments.runtime_contracts import NodeRuntimeContract
from planner.environments.update_attempts import parse_release
from planner.environments.update_contracts import PublishedLinuxExecutorUpdate

DEPLOY = ".github/workflows/deploy.yml"
queries: list[dict[str, str]] = []
REAL_CLIENT = httpx.Client
TYPESCRIPT = ".github/workflows/publish-typescript-release.yml"
PYTHON_RELEASE = release()
NODE_RELEASE = release(
    replace(PYTHON_RELEASE.manifest, runtime_contract=NodeRuntimeContract("f" * 64))
)


def provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    listed: dict[str, list[dict[str, Any]] | None],
    run_path: str = DEPLOY,
    published: Any = PYTHON_RELEASE,
) -> update_provider.GitHubUpdateProvider:
    """A fake GitHub API. A workflow listed as None is not on GitHub (404)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/repos/owner/panels/")
        if path.startswith("actions/workflows/"):
            queries.append(dict(request.url.params))
            runs = listed[path.split("/")[2]]
            if runs is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"workflow_runs": runs})
        if path == "actions/runs/1":
            return httpx.Response(200, json={
                "head_branch": "main", "event": "push", "conclusion": "success",
                "head_repository": {"full_name": "owner/panels"}, "path": run_path,
                "head_sha": SHA_B,
            })
        if path == "actions/runs/1/artifacts":
            return httpx.Response(200, json={"artifacts": [
                {"name": "panels-app", "id": 2, "expired": False},
                {"name": "panels-publication-proof", "id": 3, "expired": False},
            ]})
        if path == "actions/artifacts/3/zip":
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("publication.json", json.dumps(asdict(published)))
            return httpx.Response(200, content=archive.getvalue())
        raise AssertionError(f"unexpected request {path}")

    monkeypatch.setattr(
        httpx, "Client",
        lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )
    token = tmp_path / "token"
    token.write_text("secret\n")
    return update_provider.GitHubUpdateProvider("owner/panels", token)


def run(run_id: int, created_at: str) -> dict[str, Any]:
    return {"id": run_id, "created_at": created_at}


def test_runs_from_both_workflows_merge_newest_first_with_run_id_breaking_ties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: an older publication from one workflow shadows a newer one from the other."""
    github = provider(tmp_path, monkeypatch, listed={
        "deploy.yml": [run(10, "2026-11-12T09:00:00Z"), run(11, "2026-11-12T11:00:00Z")],
        "publish-typescript-release.yml": [
            run(20, "2026-11-12T10:00:00Z"), run(21, "2026-11-12T11:00:00Z"),
        ],
    })
    considered: list[int] = []
    monkeypatch.setattr(github, "publication", lambda run_id: considered.append(run_id))
    queries.clear()
    assert github.available() is None
    assert considered == [21, 11, 20, 10]
    eligible = {"branch": "main", "event": "push", "status": "success", "per_page": "20"}
    assert queries == [eligible, eligible]


def test_absent_optional_workflow_and_linux_artifact_leave_deploy_releases_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional workflow or Linux archive absence must not stop existing home updates."""
    published = replace(PYTHON_RELEASE, linux_executor=PublishedLinuxExecutorUpdate(
        99, "a" * 64, 123,
    ))
    github = provider(tmp_path, monkeypatch, published=published, listed={
        "deploy.yml": [run(1, "2026-11-12T09:00:00Z")],
        "publish-typescript-release.yml": None,
    })
    assert github.available() == published
    payload = json.loads(json.dumps(asdict(published)))
    payload["linux_executor"] = {"artifact_id": 99}
    assert parse_release(payload) == PYTHON_RELEASE
    payload["linux_executor"] = "malformed"
    assert parse_release(payload) == PYTHON_RELEASE
    # Required identities remain strict despite unavailable optional Linux metadata.
    payload["mac"] = "malformed"
    with pytest.raises(ValueError, match="Mac publication"):
        parse_release(payload)
    payload["mac"] = None
    payload["manifest"] = "malformed"
    with pytest.raises(ValueError, match="publication manifest"):
        parse_release(payload)
    missing_deploy = provider(tmp_path, monkeypatch, listed={
        "deploy.yml": None, "publish-typescript-release.yml": [],
    })
    with pytest.raises(RuntimeError, match="refused"):
        missing_deploy.available()


@pytest.mark.parametrize(
    ("run_path", "published", "outcome"),
    [
        (DEPLOY, PYTHON_RELEASE, "accepted"),
        (TYPESCRIPT, NODE_RELEASE, "accepted"),
        (DEPLOY, NODE_RELEASE, "refused"),
        (TYPESCRIPT, PYTHON_RELEASE, "refused"),
        (".github/workflows/typescript-backend-release.yml", NODE_RELEASE, "ignored"),
    ],
)
def test_each_workflow_publishes_only_its_own_contract_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    run_path: str, published: Any, outcome: str,
) -> None:
    """Risk: a trusted workflow's artifact is admitted under the other workflow's version,
    or an untrusted workflow is trusted at all."""
    github = provider(
        tmp_path, monkeypatch, listed={}, run_path=run_path, published=published
    )
    if outcome == "refused":
        with pytest.raises(ValueError, match="runtime contract version"):
            github.publication(1)
    else:
        assert github.publication(1) == (published if outcome == "accepted" else None)
