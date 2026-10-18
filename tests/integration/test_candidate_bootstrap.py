"""One installed-runtime boundary proof, sharing the production builder and launcher."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from planner.environments.app import build_exported_app
from planner.environments.deployment_gate import smoke_candidate


def test_installed_candidate_bootstraps_with_clean_state(tmp_path: Path) -> None:
    """Fake deployment interpreters cannot prove server startup, assets, or DB migration."""
    repository = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    candidate = tmp_path / "candidate"
    started = time.monotonic()
    manifest = build_exported_app(
        repository, requested_sha=sha, candidate_app=candidate, install_dependencies=True
    )
    built = time.monotonic()
    assert smoke_candidate(candidate, sha) == manifest.artifact_digest
    finished = time.monotonic()
    print(f"candidate build={built - started:.2f}s smoke={finished - built:.2f}s")
