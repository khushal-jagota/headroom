import type { ConnectionStatus } from "../../changeStream";
import type { ReleaseSnapshot } from "../../releaseMonitor";
import type { UpdateExperienceState, UpdateStatus } from "../contracts";

export type UpdatePresentationState = UpdateExperienceState | "unreachable";

/** The server owns update facts. Only connection and page-local safety overlay them. */
export function updatePresentation(
  status: UpdateStatus | undefined,
  connection: ConnectionStatus,
  release: ReleaseSnapshot,
  now: number,
): UpdatePresentationState {
  if (connection !== "connected") {
    if (status?.state === "updating" || status?.state === "restarting") return "restarting";
    return "unreachable";
  }
  if (status && ["updating", "restarting", "failed"].includes(status.state)) return status.state;
  if (release.updateAvailable) {
    return "complete";
  }
  // The page only offers Refresh when the running SHA differs from its boot SHA.
  return status?.state === "complete" ? "up_to_date" : status?.state ?? "up_to_date";
}

export function dimsUpdatePage(state: UpdatePresentationState): boolean {
  return state === "restarting" || state === "unreachable";
}
