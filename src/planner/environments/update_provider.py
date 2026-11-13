"""Repository-scoped GitHub publication adapter.

The operator supplies a token file. Token contents never enter process environments,
request receipts, employee homes, or public error messages.
"""

from __future__ import annotations

import json
import os
import platform
import re
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from planner.environments.runtime_contracts import (
    NODE_RUNTIME_CONTRACT_VERSION,
    RUNTIME_CONTRACT_VERSION,
)
from planner.environments.update_attempts import parse_release
from planner.environments.update_contracts import PublishedUpdate
from planner.platform_runtime.targets import LINUX_HOME, MAC_HOME, RuntimeTarget

# Each trusted publishing workflow may publish exactly one runtime contract version.
TRUSTED_RELEASE_WORKFLOWS = {
    ".github/workflows/deploy.yml": RUNTIME_CONTRACT_VERSION,
    ".github/workflows/publish-typescript-release.yml": NODE_RUNTIME_CONTRACT_VERSION,
}


class GitHubUpdateProvider:
    def __init__(
        self, repository: str, token_file: Path | None, *, target: RuntimeTarget | None = None
    ) -> None:
        if re.fullmatch(r"[\w.-]+/[\w.-]+", repository) is None:
            raise ValueError("Invalid update repository")
        self.target = target or (
            MAC_HOME
            if platform.system().lower() == "darwin" and platform.machine() == "arm64"
            else LINUX_HOME
        )
        if self.target not in (LINUX_HOME, MAC_HOME):
            raise ValueError("Updates require a supported home runtime target")
        self.repository = repository
        self.token_file = token_file
        self._publication_cache: PublishedUpdate | None = None

    @classmethod
    def configured(cls) -> GitHubUpdateProvider | None:
        repository, token_file = (
            os.getenv("PLAN_UPDATE_REPOSITORY"),
            os.getenv("PLAN_UPDATE_TOKEN_FILE"),
        )
        return cls(repository, Path(token_file)) if repository and token_file else None

    def _headers(self) -> dict[str, str]:
        token = self.token_file.read_text().strip() if self.token_file else ""
        if not token:
            raise RuntimeError("The repository update credential is unavailable.")
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def _request(
        self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any
    ) -> httpx.Response | None:
        headers = self._headers()
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.request(
                    method,
                    f"https://api.github.com/repos/{self.repository}/{path}",
                    headers=headers,
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "The repository update service is unavailable or its result is uncertain."
            ) from exc
        if missing_ok and response.status_code == 404:
            return None
        if response.is_error:
            raise RuntimeError("The repository update service refused the operation.")
        return response

    def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        response = self._request("GET", path, **kwargs)
        assert response is not None
        return response

    def available(self) -> PublishedUpdate | None:
        """The newest acceptable publication across every trusted workflow."""
        runs: list[dict[str, Any]] = []
        for workflow, contract_version in TRUSTED_RELEASE_WORKFLOWS.items():
            # Until the TypeScript workflow is on GitHub its runs are a 404: no runs yet.
            # A 404 for deploy.yml still fails, as before; it can mean a lost credential.
            response = self._request(
                "GET",
                f"actions/workflows/{workflow.rsplit('/', 1)[-1]}/runs",
                missing_ok=contract_version == NODE_RUNTIME_CONTRACT_VERSION,
                params={
                    "branch": "main",
                    "event": "push",
                    "status": "success",
                    "per_page": 20,
                },
            )
            if response is not None:
                runs.extend(response.json()["workflow_runs"])
        runs.sort(
            key=lambda run: (
                datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")),
                int(run["id"]),
            ),
            reverse=True,
        )
        for run in runs:
            release = self.publication(int(run["id"]))
            if release is not None:
                return release
        return None

    def publication(self, run_id: int) -> PublishedUpdate | None:
        run = self._get(f"actions/runs/{run_id}").json()
        contract_version = TRUSTED_RELEASE_WORKFLOWS.get(run.get("path"))
        if (
            run["head_branch"] != "main"
            or run["event"] != "push"
            or run["conclusion"] != "success"
            or run.get("head_repository", {}).get("full_name") != self.repository
            or contract_version is None
        ):
            return None
        artifacts = self._get(
            f"actions/runs/{run_id}/artifacts", params={"per_page": 100}
        ).json()["artifacts"]
        by_name = {a["name"]: a for a in artifacts if not a["expired"]}
        proof = by_name.get("panels-publication-proof")
        app = by_name.get("panels-mac-home" if self.target == MAC_HOME else "panels-app")
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
            archive = self._get(f"actions/artifacts/{proof['id']}/zip").content
            with zipfile.ZipFile(BytesIO(archive)) as zipped:
                payload = json.loads(zipped.read("publication.json"))
            if self.target == MAC_HOME:
                home = payload.get("mac_home")
                if not isinstance(home, dict):
                    return None
                payload = {**payload, **home, "mac": None}
            payload["proof_artifact_id"] = proof["id"]
            release = parse_release(payload)
        if (
            release.publication_run_id != run_id
            or release.artifact_id != app["id"]
            or release.manifest.app_sha != run["head_sha"]
            or (
                release.linux_executor is not None
                and (by_name.get("panels-linux-executor", {}).get("id")
                     != release.linux_executor.artifact_id)
            )
            or (
                release.mac is not None
                and (by_name.get("panels-mac-runtime", {}).get("id") != release.mac.artifact_id)
            )
        ):
            raise ValueError("Publication identity differs from trusted repository evidence.")
        if datetime.fromisoformat(release.expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            return None
        contract = release.manifest.runtime_contract
        if contract is None:
            return None
        if (contract.system, contract.architecture) != (
            self.target.system,
            self.target.architecture,
        ):
            raise ValueError("Publication runtime target differs from this home")
        if contract.version != contract_version:
            raise ValueError("This workflow does not publish this runtime contract version.")
        self._publication_cache = release
        return release

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        """Stream the artifact ZIP to disk; a release is about 1 GB."""
        metadata = self._get(f"actions/artifacts/{artifact_id}").json()
        if metadata["expired"]:
            raise ValueError("The pinned update artifact expired.")
        try:
            with (
                httpx.Client(timeout=60, follow_redirects=True) as client,
                client.stream(
                    "GET",
                    f"https://api.github.com/repos/{self.repository}/actions/artifacts/"
                    f"{artifact_id}/zip",
                    headers=self._headers(),
                ) as response,
            ):
                if response.is_error:
                    raise RuntimeError("The repository update service refused the download.")
                with destination.open("xb") as stream:
                    for chunk in response.iter_bytes(1024 * 1024):
                        stream.write(chunk)
        except httpx.HTTPError as exc:
            raise RuntimeError("The repository update download did not complete.") from exc
