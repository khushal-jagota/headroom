"""Apply one pinned owner request through the existing deployment transaction.

Only the repository's deployment runner invokes this entry point. It neither builds
an app nor runs publication suites. A lost claim response never authorizes a stop.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict, replace
from pathlib import Path

import httpx
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError

from planner.core.db import MIGRATIONS_DIRECTORY
from planner.environments.app import validate_app_manifest
from planner.environments.backup import restore_database_snapshot
from planner.environments.deployment import (
    HttpHealthClient,
    SubprocessServiceController,
    deploy_app,
    run_current_app_backup,
)
from planner.environments.deployment_gate_contracts import PublicationGateEvidence
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore, lifecycle_path
from planner.environments.update_artifacts import extract_app_archive
from planner.environments.update_contracts import OwnerUpdateRequest, PublishedUpdate
from planner.environments.update_provider import GitHubUpdateProvider
from planner.environments.update_requests import (
    FileUpdateRequestStore,
    UpdateConflict,
    parse_release,
    parse_request,
)
from planner.environments.update_service import validate_release


def matches_pinned_release(evidence: PublishedUpdate | None, pinned: PublishedUpdate) -> bool:
    """Compare retained evidence with exactly what the owner request pinned.

    A server from before Mac publication pins no Mac part. Its request still
    adopts the VPS release; the Mac runtime is then outside that request.
    Every field the request does pin, including a pinned Mac part, stays exact.
    """
    if evidence is None:
        return False
    if pinned.mac is None:
        evidence = replace(evidence, mac=None)
    return evidence == pinned


def validate_schema_ancestry(database: Path, migrations: Path = MIGRATIONS_DIRECTORY) -> None:
    """Refuse unknown or pre-floor database revisions before service shutdown."""
    try:
        graph = ScriptDirectory(str(migrations))
    except CommandError as exc:
        raise ValueError("The pinned application has no usable migration history.") from exc
    floor = "video_upload_ledger"
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
        if floor not in ancestors and revision not in {"goal_holders", "canonical_manager_identities"}:
            raise ValueError("The installed database precedes the supported update schema floor.")


def apply(arguments: argparse.Namespace) -> dict[str, object]:
    service = SubprocessServiceController("systemctl", "panels-live.service")
    root = arguments.current_root.expanduser().resolve()
    database = root / "data" / "planner.db"
    store = FileUpdateRequestStore(root / "data" / "owner-update-request.json")
    request = store.read()
    recovery = getattr(arguments, "command", "apply") == "recover-legacy"
    if (
        request is None
        or request.request_id != arguments.request_id
        or request.state != ("applying" if recovery else "dispatched")
    ):
        raise ValueError("The pinned request is cancelled, already claimed, or missing.")
    release = request.release
    if (
        release.publication_run_id,
        release.artifact_id,
        release.proof_artifact_id,
        release.manifest.app_sha,
        release.manifest.artifact_digest,
        release.archive_sha256,
    ) != (
        arguments.publication_run_id,
        arguments.artifact_id,
        arguments.proof_artifact_id,
        arguments.app_sha,
        arguments.artifact_digest,
        arguments.archive_sha256,
    ):
        raise ValueError("Runner inputs differ from the pinned owner request.")
    validate_release(release)
    validate_schema_ancestry(database)
    validate_app_manifest(root / "app" / "manifest.json", require_runtime=False)
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
    # Artifact metadata and the proof bind IDs to a successful trusted main run.
    trusted = provider.publication(release.publication_run_id)
    if not matches_pinned_release(trusted, release):
        raise ValueError("The pinned publication no longer has trusted retained evidence.")
    lifecycle = DeploymentLifecycleStore(lifecycle_path(root))
    deployment_id = (
        arguments.deployment_id if recovery
        else os.environ.get("PANELS_DEPLOYMENT_ID", request.request_id)
    )

    def claim() -> None:
        validate_schema_ancestry(database, candidate_migrations)
        if recovery:
            # deploy_app holds the deployment lock; all download, proof, schema,
            # runtime and staged-byte checks have passed before recovery can write.
            recover_legacy_claim(arguments, store, lifecycle, request)
            return
        previous = lifecycle.read().lifecycle
        prior_manifest = validate_app_manifest(
            root / "app" / "manifest.json", require_runtime=False
        )
        with store.dispatch_guard(request.request_id):
            lifecycle.start(
                deployment_id,
                release.manifest.app_sha,
                expected_deployment_id=previous.deployment_id if previous else None,
                prior_sha=prior_manifest.app_sha,
            )
        # deploy_app holds its existing deployment lock here. The request-file lock
        # is acquired only inside the server's workspace admission callback.
        response = httpx.post(
            arguments.health_url.removesuffix("/api/health")
            + f"/api/updates/requests/{request.request_id}/claim",
            json={"deployment_id": deployment_id},
            timeout=30,
        )
        recorded = store.read()
        if (
            response.status_code != 200
            or recorded is None
            or parse_request(response.json()) != recorded
        ):
            raise ValueError(
                "Runner admission was refused or uncertain. The service remains unchanged."
            )
        if recorded.state != "applying":
            raise ValueError("Runner admission lacks an applying receipt.")
        if recorded.deployment_id not in {None, deployment_id}:
            raise ValueError("Runner admission belongs to another deployment attempt.")
        if recorded != replace(
            request, state="applying", deployment_id=recorded.deployment_id
        ):
            raise ValueError("Runner admission changed the pinned owner request.")
        if recorded.deployment_id is None:
            # Old servers ignore the claim body and omit optional receipt fields.
            # Bind that accepted claim to this runner without renewing its budget.
            store.transition(
                request.request_id, frozenset({"applying"}), "applying",
                deployment_id=deployment_id,
            )

    with tempfile.TemporaryDirectory(prefix="panels-owner-update-") as temporary:
        work = Path(temporary)
        provider.download_artifact(release.proof_artifact_id, work / "proof.zip")
        with zipfile.ZipFile(work / "proof.zip") as zipped:
            proof = json.loads(zipped.read("publication.json"))
        proof["proof_artifact_id"] = release.proof_artifact_id
        if not matches_pinned_release(parse_release(proof), release):
            raise ValueError("Downloaded proof differs from the pinned publication.")
        provider.download_artifact(release.artifact_id, work / "app.zip")
        with zipfile.ZipFile(work / "app.zip") as zipped:
            (work / "panels-app.tar.gz").write_bytes(zipped.read("panels-app.tar.gz"))
        manifest = extract_app_archive(
            work / "panels-app.tar.gz",
            work / "app",
            archive_sha256=release.archive_sha256,
            expected_sha=release.manifest.app_sha,
        )
        if manifest != release.manifest:
            raise ValueError("Downloaded application differs from the pinned manifest.")
        evidence: PublicationGateEvidence = {
            "requested_sha": release.manifest.app_sha,
            "artifact_digest": release.manifest.artifact_digest,
            "publication_run_id": release.publication_run_id,
            "archive_sha256": release.archive_sha256,
        }

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
            gate_evidence=evidence,
            deployment_id=deployment_id,
            before_stop=claim,
            lifecycle_transition=publish,
            installation_guard=lambda: store.installation_guard(request.request_id),
        )
    if result.status in {"succeeded", "unchanged"}:
        store.transition(request.request_id, frozenset({"applying", "dispatched"}), "applied")
    elif result.status == "rolled_back":
        store.transition(
            request.request_id, frozenset({"applying"}), "failed", detail=result.detail
        )
    return asdict(result)


def recover_legacy_claim(
    arguments: argparse.Namespace,
    store: FileUpdateRequestStore,
    lifecycle: DeploymentLifecycleStore,
    expected: OwnerUpdateRequest,
) -> None:
    """Prove an already-closed legacy admission, never repeat its HTTP claim.

    Called only from deploy_app's before_stop callback under its deployment lock.
    An operator must pin both the failed attempt and the still-installed prior app.
    """
    prior = validate_app_manifest(
        arguments.current_root / "app" / "manifest.json", require_runtime=False
    )
    if prior.app_sha != arguments.prior_sha:
        raise ValueError("Legacy recovery prior app no longer matches the installed app.")
    base_url = arguments.health_url.removesuffix("/api/health")
    health = httpx.get(
        arguments.health_url, params={"expected_sha": arguments.prior_sha}, timeout=30
    )
    if health.status_code != 200 or (
        health.json().get("ready") is not True
        or health.json().get("app_sha") != arguments.prior_sha
    ):
        raise ValueError("Legacy recovery requires exact-SHA prior app health.")
    status = httpx.get(base_url + "/api/deployment-status", timeout=30)
    if status.status_code != 200 or any(
        status.json().get(key) != value for key, value in {
            "state": "unknown", "deployed_sha": arguments.prior_sha,
            "target_sha": arguments.app_sha, "work_paused": True,
            "installation_confirmed": False,
        }.items()
    ):
        raise ValueError("Legacy recovery requires unchanged paused deployment evidence.")
    live = httpx.get(base_url + "/api/updates", timeout=30)
    if (
        live.status_code != 200
        or not isinstance(live.json().get("request"), dict)
        or parse_request(live.json()["request"]) != expected
    ):
        raise ValueError("Legacy recovery live request differs from the accepted receipt.")
    store.recover_legacy_attempt(
        expected, deployment_id=arguments.deployment_id,
        failed_deployment_id=arguments.failed_deployment_id,
        prior_sha=arguments.prior_sha, lifecycle=lifecycle,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("apply", "recover-legacy"):
        command = commands.add_parser(operation)
        for name in ("request-id", "app-sha", "artifact-digest", "archive-sha256"):
            command.add_argument("--" + name, required=True)
        for name in ("publication-run-id", "artifact-id", "proof-artifact-id"):
            command.add_argument("--" + name, type=int, required=True)
        command.add_argument(
            "--current-root", type=Path, default=Path.home() / "Deployments/Panels/current"
        )
        command.add_argument("--health-url", default="http://127.0.0.1:8767/api/health")
        if operation == "recover-legacy":
            for name in ("deployment-id", "failed-deployment-id", "prior-sha"):
                command.add_argument("--" + name, required=True)
    arguments = parser.parse_args()
    deployment_id = (
        arguments.deployment_id if arguments.command == "recover-legacy"
        else os.environ.get("PANELS_DEPLOYMENT_ID", arguments.request_id)
    )
    try:
        result = apply(arguments)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in {"succeeded", "unchanged"} else 1
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        # Persist a truthful outcome without replacing a cancellation or reopening
        # an uncertain applying candidate. The request lives outside DB restoration.
        root = arguments.current_root.expanduser().resolve()
        store = FileUpdateRequestStore(root / "data" / "owner-update-request.json")
        recorded = store.read()
        committed = DeploymentLifecycleStore(lifecycle_path(root)).read().lifecycle
        try:
            if recorded is not None and recorded.request_id == arguments.request_id:
                if recorded.state == "dispatched" and arguments.command == "apply":
                    store.transition(
                        recorded.request_id,
                        frozenset({"dispatched"}),
                        "failed",
                        detail="The runner refused this update before application began.",
                    )
                elif recorded.state == "applying":
                    if (
                        committed is not None
                        and committed.deployment_id
                        == deployment_id
                        and committed.requested_sha == arguments.app_sha
                        and committed.serving_sha == arguments.app_sha
                        and committed.phase in {"app_healthy", "succeeded", "failed"}
                    ):
                        store.transition(
                            recorded.request_id,
                            frozenset({"applying"}),
                            "applied",
                            detail=(
                                f"App {arguments.app_sha} is installed. Runner reporting failed."
                            ),
                        )
                    elif (
                        committed is not None
                        and committed.deployment_id
                        == (recorded.deployment_id or arguments.request_id)
                        and committed.phase in {"failed", "rolled_back"}
                    ):
                        store.transition(
                            recorded.request_id, frozenset({"applying"}), "failed",
                            detail=committed.detail or "The update did not install.",
                        )
                    # Otherwise the server settles from serving proof or deadline.
                    # A missing runner report never reopens or renews authority.
        except UpdateConflict:
            # Cancellation or another final receipt won the compare-and-swap.
            pass
        # Never infer a stop, restore, restart, or dispatch retry from uncertain I/O.
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
