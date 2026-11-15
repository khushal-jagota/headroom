"""Download, check once and unpack the next release beside the live app, before any press.

The staged folder `<current_root>/.app-next-<sha>` sits on the same filesystem as
`<current_root>/app`, so the installer swaps them by renaming. Only one is kept.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from planner.environments.app import AppManifest, validate_app_manifest
from planner.environments.runtime_admission import admit_runtime
from planner.environments.update_artifacts import APP_ARCHIVE_NAME, extract_app_archive
from planner.environments.update_configuration import STAGED_APP_PREFIX
from planner.environments.update_contracts import PublishedUpdate, UpdateProvider


def staged_app_path(current_root: Path, app_sha: str) -> Path:
    return current_root / f"{STAGED_APP_PREFIX}{app_sha}"


def stage_release(
    provider: UpdateProvider,
    release: PublishedUpdate,
    current_root: Path,
    *,
    admit: Callable[[Path, AppManifest], None] = admit_runtime,
) -> Path:
    """Return the staged app for `release`, downloading and checking it when absent."""
    app_sha = release.manifest.app_sha
    staged = staged_app_path(current_root, app_sha)
    # One staged release at a time; a download a stopped process left is garbage.
    for other in (*current_root.glob(f"{STAGED_APP_PREFIX}*"),
                  *current_root.glob(".app-download-*")):
        if other != staged and other.is_dir() and not other.is_symlink():
            shutil.rmtree(other)
    if staged.is_dir():
        # A staged folder is renamed into place only after every check below passed;
        # a failed install renames it back. Its manifest still has to be this release.
        if _manifest(staged, app_sha) != release.manifest:
            shutil.rmtree(staged)
        else:
            admit(staged, release.manifest)
            return staged
    with tempfile.TemporaryDirectory(prefix=".app-download-", dir=current_root) as temporary:
        work = Path(temporary)
        artifact = work / "artifact.zip"
        provider.download_artifact(release.artifact_id, artifact)
        archive = work / APP_ARCHIVE_NAME
        with zipfile.ZipFile(artifact) as zipped, zipped.open(APP_ARCHIVE_NAME) as source:
            with archive.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
        artifact.unlink()
        unpacked = work / "app"
        manifest = extract_app_archive(
            archive, unpacked, archive_sha256=release.archive_sha256, expected_sha=app_sha
        )
        archive.unlink()
        if manifest != release.manifest:
            raise ValueError("The downloaded app differs from its publication receipt.")
        admit(unpacked, manifest)
        unpacked.rename(staged)
    return staged


def _manifest(app: Path, app_sha: str) -> AppManifest | None:
    try:
        return validate_app_manifest(
            app / "manifest.json", expected_sha=app_sha, require_runtime=True,
            verify_artifact_digest=False,
        )
    except (OSError, ValueError):
        return None
