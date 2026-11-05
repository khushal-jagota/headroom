import { fetchJson, PlannerFetchError } from "../api";
import type { OwnerUpdateRequest, PublishedUpdate, UpdateAction, UpdateMode, UpdateStatus } from "./contracts";
import { createUpdatePayload } from "./logic/actions";

/** These owner controls also recover an uncommitted candidate from an older page.
 * Ordinary app writes retain the boot-SHA preflight. Update controls instead read
 * the current authoritative action and pinned identity before their narrow POST.
 */
async function ownerUpdateAction(
  actions: UpdateAction[],
  operation: (status: UpdateStatus) => { path: string; body?: unknown },
): Promise<OwnerUpdateRequest> {
  const status = await fetchJson<UpdateStatus>("/api/updates");
  if (!status.app_sha || !actions.some(action => status.allowed_actions.includes(action))) {
    throw new Error("This update action is no longer available. Close this dialog and try again.");
  }
  const { path, body } = operation(status);
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Panels-App-SHA": status.app_sha },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch { throw new PlannerFetchError("network error", "network"); }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail ?? payload.error?.message ?? `HTTP ${response.status}`);
  return payload as OwnerUpdateRequest;
}

export function requestUpdate(release: PublishedUpdate, mode: UpdateMode, confirmActivity = false): Promise<OwnerUpdateRequest> {
  return ownerUpdateAction(["update", "retry"], status => {
    const selected = status.available;
    if (!selected || selected.publication_run_id !== release.publication_run_id ||
      selected.artifact_id !== release.artifact_id || selected.proof_artifact_id !== release.proof_artifact_id ||
      selected.manifest.app_sha !== release.manifest.app_sha || selected.manifest.artifact_digest !== release.manifest.artifact_digest ||
      selected.archive_sha256 !== release.archive_sha256) throw new Error("The selected update changed. Close this dialog and try again.");
    return { path: "/api/updates/requests", body: createUpdatePayload(release, mode, confirmActivity) };
  });
}

export function cancelUpdate(requestId: string): Promise<OwnerUpdateRequest> {
  return ownerUpdateAction(["cancel"], status => {
    if (status.request?.request_id !== requestId) throw new Error("The update request changed.");
    return { path: `/api/updates/requests/${encodeURIComponent(requestId)}/cancel` };
  });
}

/** Promote the pinned pending request without replacing its publication or identity. */
export function promoteUpdate(requestId: string): Promise<OwnerUpdateRequest> {
  return ownerUpdateAction(["update_now"], status => {
    if (status.request?.request_id !== requestId) throw new Error("The update request changed.");
    return { path: `/api/updates/requests/${encodeURIComponent(requestId)}/now`, body: { confirm_activity: true } };
  });
}
