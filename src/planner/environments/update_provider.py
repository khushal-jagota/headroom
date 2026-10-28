"""Repository-scoped GitHub publication and dispatch adapter.

The operator supplies a token file. Token contents never enter process environments,
request receipts, employee homes, or public error messages.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from planner.environments.update_contracts import OwnerUpdateRequest, PublishedUpdate
from planner.environments.update_requests import parse_release


class GitHubUpdateProvider:
    def __init__(
        self, repository: str, token_file: Path | None, *, runner_token: str | None = None
    ) -> None:
        if re.fullmatch(r"[\w.-]+/[\w.-]+", repository) is None:
            raise ValueError("Invalid update repository")
        self.repository = repository
        self.token_file = token_file
        self._runner_token = runner_token
        self._publication_cache: PublishedUpdate | None = None

    @classmethod
    def configured(cls) -> GitHubUpdateProvider | None:
        repository, token_file = (
            os.getenv("PLAN_UPDATE_REPOSITORY"),
            os.getenv("PLAN_UPDATE_TOKEN_FILE"),
        )
        return cls(repository, Path(token_file)) if repository and token_file else None

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = self._runner_token or (
            self.token_file.read_text().strip() if self.token_file else ""
        )
        if not token:
            raise RuntimeError("The repository update credential is unavailable.")
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.request(
                    method,
                    f"https://api.github.com/repos/{self.repository}/{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "The repository update service is unavailable or its result is uncertain."
            ) from exc
        if response.is_error:
            raise RuntimeError("The repository update service refused the operation.")
        return response

    def available(self) -> PublishedUpdate | None:
        runs = self._request(
            "GET",
            "actions/workflows/deploy.yml/runs",
            params={
                "branch": "main",
                "event": "push",
                "status": "success",
                "per_page": 20,
            },
        ).json()["workflow_runs"]
        for run in runs:
            release = self.publication(int(run["id"]))
            if release is not None:
                return release
        return None

    def publication(self, run_id: int) -> PublishedUpdate | None:
        run = self._request("GET", f"actions/runs/{run_id}").json()
        if (
            run["head_branch"] != "main"
            or run["event"] != "push"
            or run["conclusion"] != "success"
            or run.get("head_repository", {}).get("full_name") != self.repository
            or run.get("path") != ".github/workflows/deploy.yml"
        ):
            return None
        artifacts = self._request(
            "GET", f"actions/runs/{run_id}/artifacts", params={"per_page": 100}
        ).json()["artifacts"]
        by_name = {a["name"]: a for a in artifacts if not a["expired"]}
        proof, app = by_name.get("panels-publication-proof"), by_name.get("panels-app")
        if proof is None or app is None:
            return None
        cached = self._publication_cache
        if (
            cached is not None
            and cached.publication_run_id == run_id
            and cached.artifact_id == app["id"]
            and cached.proof_artifact_id == proof["id"]
        ):
            release = cached
        else:
            archive = self._request("GET", f"actions/artifacts/{proof['id']}/zip").content
            with zipfile.ZipFile(BytesIO(archive)) as zipped:
                payload = json.loads(zipped.read("publication.json"))
            payload["proof_artifact_id"] = proof["id"]
            release = parse_release(payload)
        if (
            release.publication_run_id != run_id
            or release.artifact_id != app["id"]
            or release.manifest.app_sha != run["head_sha"]
        ):
            raise ValueError("Publication identity differs from trusted repository evidence.")
        if datetime.fromisoformat(release.expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            return None
        self._publication_cache = release
        return release

    def dispatch(self, request: OwnerUpdateRequest) -> None:
        release = request.release
        self._request(
            "POST",
            "actions/workflows/deploy.yml/dispatches",
            json={
                "ref": "main",
                "inputs": {
                    "request_id": request.request_id,
                    "publication_run_id": str(release.publication_run_id),
                    "artifact_id": str(release.artifact_id),
                    "proof_artifact_id": str(release.proof_artifact_id),
                    "commit_sha": release.manifest.app_sha,
                    "operation": "adopt",
                    "rehearsal": "false",
                    "artifact_digest": release.manifest.artifact_digest,
                    "archive_sha256": release.archive_sha256,
                },
            },
        )

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        metadata = self._request("GET", f"actions/artifacts/{artifact_id}").json()
        if metadata["expired"]:
            raise ValueError("The pinned update artifact expired.")
        destination.write_bytes(
            self._request("GET", f"actions/artifacts/{artifact_id}/zip").content
        )
