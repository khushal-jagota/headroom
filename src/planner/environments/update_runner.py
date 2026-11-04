"""Apply one pinned owner request through the existing deployment transaction.

Only the repository's deployment runner invokes this entry point. It neither builds
an app nor runs publication suites. The owner request supplies the selected archive.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

import httpx
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError

from planner.core.db import BASELINE_REVISION, MIGRATIONS_DIRECTORY
from planner.environments.app import validate_app_manifest
from planner.environments.backup import restore_database_snapshot
from planner.environments.deployment import (
    HttpHealthClient,
    SubprocessServiceController,
    deploy_app,
    run_current_app_backup,
)
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore, lifecycle_path
from planner.environments.update_artifacts import extract_app_archive
from planner.environments.update_provider import GitHubUpdateProvider
from planner.environments.update_requests import (
    FileUpdateRequestStore,
    UpdateConflict,
)
from planner.environments.update_service import validate_release


def validate_schema_ancestry(database: Path, migrations: Path = MIGRATIONS_DIRECTORY) -> None:
    """Refuse unknown or pre-floor database revisions before service shutdown."""
    try:
        graph = ScriptDirectory(str(migrations))
    except CommandError as exc:
        raise ValueError("The pinned application has no usable migration history.") from exc
    floor = BASELINE_REVISION if graph.get_bases() == [BASELINE_REVISION] else "video_upload_ledger"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            revisions = [
                str(row[0]) for row in conn.execute("SELECT version_num FROM alembic_version")
            ]
    except sqlite3.DatabaseError as exc:
        raise ValueError("The installed database has no readable schema ancestry.") from exc
    if not revisions:
        raise ValueError("The installed database has no known schema revision.")
    for revision in revisions:
        try:
            ancestors = {node.revision for node in graph.iterate_revisions(revision, "base")}
        except (CommandError, ResolutionError) as exc:
            raise ValueError("The installed database has unknown schema ancestry.") from exc
        if floor not in ancestors:
            raise ValueError("The installed database precedes the supported update schema floor.")


def apply(arguments: argparse.Namespace) -> dict[str, object]:
    service = SubprocessServiceController("systemctl", "panels-live.service")
    root = arguments.current_root.expanduser().resolve()
    database = root / "data" / "planner.db"
    store = FileUpdateRequestStore(root / "data" / "owner-update-request.json")
    request = store.read()
    if (
        request is None
        or request.request_id != arguments.request_id
        or request.state != "installing"
    ):
        raise ValueError("The pinned request is cancelled, already claimed, or missing.")
    release = request.release
    validate_release(release)
    installed = validate_app_manifest(root / "app" / "manifest.json", require_runtime=False)
    if installed.app_sha == release.manifest.app_sha:
        store.transition(
            request.request_id,
            frozenset({"installing"}),
            "failed",
            detail="The pinned application is already installed.",
        )
        raise ValueError("The pinned application is already installed. No service action occurred.")
    repository = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("PLAN_UPDATE_REPOSITORY")
    if repository is None:
        raise ValueError("The update repository is not configured.")
    token_file = os.environ.get("PLAN_UPDATE_TOKEN_FILE")
    runner_token = os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    provider = GitHubUpdateProvider(
        repository,
        Path(token_file) if token_file else None,
        runner_token=runner_token,
    )
    lifecycle = DeploymentLifecycleStore(lifecycle_path(root))
    deployment_id = os.environ.get("PANELS_DEPLOYMENT_ID", request.request_id)

    def prepare_install() -> None:
        validate_schema_ancestry(database, candidate_migrations)
        previous = lifecycle.read().lifecycle
        prior_manifest = validate_app_manifest(
            root / "app" / "manifest.json", require_runtime=False
        )
        lifecycle.start(
            deployment_id,
            release.manifest.app_sha,
            expected_deployment_id=previous.deployment_id if previous else None,
            prior_sha=prior_manifest.app_sha,
        )

    with tempfile.TemporaryDirectory(prefix="panels-owner-update-") as temporary:
        work = Path(temporary)
        provider.download_artifact(release.artifact_id, work / "app.zip")
        with zipfile.ZipFile(work / "app.zip") as zipped:
            (work / "panels-app.tar.gz").write_bytes(zipped.read("panels-app.tar.gz"))
        extract_app_archive(
            work / "panels-app.tar.gz",
            work / "app",
            archive_sha256=release.archive_sha256,
            expected_sha=release.manifest.app_sha,
        )

        def publish(phase: str, **kwargs: object) -> None:
            lifecycle.transition(deployment_id, phase, **kwargs)  # type: ignore[arg-type]

        candidate_migrations = work / "app" / "src" / "planner" / "core" / "migrations"
        result = deploy_app(
            candidate_app=work / "app",
            current_root=root,
            source_db=database,
            backup=lambda revision: run_current_app_backup(
                root, database, root / "data" / "backups", revision
            ),
            restore=lambda snapshot: restore_database_snapshot(
                snapshot, database, live_stopped=True
            ),
            service=service,
            health=HttpHealthClient(arguments.health_url),
            deployment_id=deployment_id,
            before_stop=prepare_install,
            lifecycle_transition=publish,
        )
    if result.status in {"succeeded", "unchanged"}:
        store.transition(request.request_id, frozenset({"installing"}), "done")
    elif result.status == "rolled_back":
        store.transition(
            request.request_id, frozenset({"installing"}), "failed", detail=result.detail
        )
    return asdict(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("apply")
    for name in ("request-id",):
        command.add_argument("--" + name, required=True)
    command.add_argument(
        "--current-root", type=Path, default=Path.home() / "Deployments/Panels/current"
    )
    command.add_argument("--health-url", default="http://127.0.0.1:8767/api/health")
    arguments = parser.parse_args()
    try:
        result = apply(arguments)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"succeeded", "unchanged"} else 1
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        root = arguments.current_root.expanduser().resolve()
        store = FileUpdateRequestStore(root / "data" / "owner-update-request.json")
        try:
            store.transition(
                arguments.request_id, frozenset({"installing"}), "failed", detail=str(exc)
            )
        except UpdateConflict:
            pass
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "detail": "Inspect the recorded request and lifecycle before owner recovery.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
