import type { CreateUpdateRequest, OwnerUpdateRequest, PublishedUpdate, UpdateActivity, UpdateMode } from "../contracts";

export function usableUpdate(release: PublishedUpdate | null | undefined, now: number): release is PublishedUpdate {
  return !!release && Number.isFinite(Date.parse(release.expires_at)) && Date.parse(release.expires_at) > now;
}

export function needsActivityConfirmation(activity: UpdateActivity): boolean {
  return activity !== "idle";
}

export function canCancelUpdate(request: OwnerUpdateRequest): boolean {
  return request.state === "pending" || request.state === "dispatched";
}

export function activeUpdateRequest(request: OwnerUpdateRequest | null | undefined): boolean {
  return !!request && ["pending", "dispatched", "applying"].includes(request.state);
}

export function createUpdatePayload(release: PublishedUpdate, mode: UpdateMode, confirmActivity: boolean): CreateUpdateRequest {
  return {
    publication_run_id: release.publication_run_id,
    artifact_id: release.artifact_id,
    proof_artifact_id: release.proof_artifact_id,
    app_sha: release.manifest.app_sha,
    artifact_digest: release.manifest.artifact_digest,
    archive_sha256: release.archive_sha256,
    mode,
    confirm_activity: confirmActivity
  };
}
