import { fetchJson } from "../api";
import type { OwnerUpdateRequest, PublishedUpdate, UpdateMode } from "./contracts";
import { createUpdatePayload } from "./logic/actions";

export function requestUpdate(release: PublishedUpdate, mode: UpdateMode, confirmActivity = false): Promise<OwnerUpdateRequest> {
  return fetchJson("/api/updates/requests", { method: "POST", body: createUpdatePayload(release, mode, confirmActivity) });
}

export function cancelUpdate(requestId: string): Promise<OwnerUpdateRequest> {
  return fetchJson(`/api/updates/requests/${encodeURIComponent(requestId)}/cancel`, { method: "POST" });
}
