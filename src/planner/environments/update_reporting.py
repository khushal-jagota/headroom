"""Finalize one owner adoption report without any deployment actions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from planner.environments.deployment_lifecycle import (
    DeploymentLifecycleConflict,
    DeploymentLifecycleStore,
    _exclusive_lock,
    lifecycle_path,
)
from planner.environments.logic.update_reporting import final_report
from planner.environments.update_requests import FileUpdateRequestStore


def finalize(
    *, current_root: Path, request_id: str, deployment_id: str, app_sha: str,
    apply_outcome: str, provision_outcome: str, published_route_outcome: str,
    serving_sha: str | None = None,
) -> dict[str, object]:
    """Existing request and lifecycle identities prevent stale shared writes."""
    requests = FileUpdateRequestStore(current_root / "data" / "owner-update-request.json")
    lifecycle = DeploymentLifecycleStore(lifecycle_path(current_root))
    with _exclusive_lock(requests.lock_path):
        current = requests.read()
        request = requests.read_for_reporting(request_id)
        record = lifecycle.read().lifecycle
        cancelled = (request is not None and request.request_id == request_id
                     and request.release.manifest.app_sha == app_sha
                     and request.state == "cancelled")
        detail = final_report(
            cancelled=cancelled, committed=False, serving_matches=False,
            apply_outcome=apply_outcome, provision_outcome=provision_outcome,
            published_route_outcome=published_route_outcome,
        )[2]
        if current is not None and current.request_id != request_id:
            return {"status": "superseded", "detail": detail}
        if record is None or record.deployment_id != deployment_id:
            return {"status": "not_current", "detail": detail}
        if record.requested_sha != app_sha:
            return {"status": "uncertain", "detail": "Attempt target evidence does not match."}
        if record.phase in {"succeeded", "failed", "rolled_back", "cancelled", "unknown"}:
            return {"status": "preserved", **record.to_public_dict()}
        # Cancellation is legal only before installation. Conflicting evidence is uncertainty.
        cancelled = cancelled and record.phase == "preparing" and record.serving_sha is None
        committed = record.phase == "app_healthy" and record.serving_sha == app_sha
        phase, code, detail = final_report(
            cancelled=cancelled, committed=committed,
            serving_matches=serving_sha == app_sha,
            apply_outcome=apply_outcome, provision_outcome=provision_outcome,
            published_route_outcome=published_route_outcome,
        )
        try:
            result = lifecycle.transition(deployment_id, phase, code=code, detail=detail,
                                          preserve_terminal=True)
        except DeploymentLifecycleConflict:
            return {"status": "superseded", "detail": "A later attempt owns the lifecycle."}
        return {"status": "recorded", **result.to_public_dict()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-root", type=Path, required=True)
    for name in ("request-id", "deployment-id", "app-sha", "apply-outcome",
                 "provision-outcome", "published-route-outcome"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--serving-sha", default="")
    args = parser.parse_args()
    exit_code = 0
    try:
        result = finalize(**vars(args))
        if result["status"] == "uncertain":
            exit_code = 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        # Reporting cannot act on the service or replace missing evidence with a guess.
        result = {"status": "uncertain", "detail": f"Update report unavailable: {exc}"}
        exit_code = 1
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
