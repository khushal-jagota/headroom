import type { ConnectionStatus } from "./changeStream";
import type { ReleaseSnapshot } from "./releaseMonitor";
import type { DeploymentStatus, SuccessPresentation } from "./types";

export type VisibleDeploymentState =
  | "connected"
  | "reconnecting"
  | "preparing"
  | "restarting"
  | "back_up"
  | "problem";

function timestamp(value: string | null | undefined): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function expiryTime(status: DeploymentStatus): number | null {
  return timestamp(status.valid_until);
}

function isFresh(status: DeploymentStatus, now: number): boolean {
  const expiry = expiryTime(status);
  return expiry !== null && now < expiry;
}

/** The sole success presentation rule. A live record alone is not permission
 * to keep speaking. Both deadlines must be usable, and a disconnected tab reports
 * its connection instead. No browser bundle SHA participates in this decision. */
export function successPresentation(
  status: DeploymentStatus,
  connectionState: ConnectionStatus,
  now: number
): SuccessPresentation {
  const validUntil = timestamp(status.valid_until);
  const displayUntil = timestamp(status.success_display_until);
  const deadline = validUntil === null || displayUntil === null
    ? null : Math.min(validUntil, displayUntil);
  if (connectionState !== "connected" || deadline === null || now >= deadline) {
    return { state: connectionState, wakeAt: null };
  }
  return { state: "back_up", wakeAt: deadline };
}

export function resolveDeploymentStatus(
  status: DeploymentStatus | null | undefined,
  connectionState: ConnectionStatus,
  now = Date.now()
): VisibleDeploymentState {
  if (
    status?.state === "problem" ||
    status?.outcome === "failed" ||
    status?.outcome === "rolled_back"
  ) {
    return "problem";
  }
  if (!status) return "reconnecting";
  if (status.state === "preparing") {
    return isFresh(status, now) ? "preparing" : "reconnecting";
  }
  if (status.state === "restarting") {
    return isFresh(status, now) ? "restarting" : "reconnecting";
  }
  if (status.state === "back_up") {
    return successPresentation(status, connectionState, now).state;
  }
  if (status.state === "idle") {
    return connectionState;
  }
  return "reconnecting";
}

export function deploymentStatusExpiry(
  status: DeploymentStatus | null | undefined,
  now = Date.now(),
  connectionState: ConnectionStatus = "connected"
): number | null {
  if (
    !status ||
    !["preparing", "restarting", "back_up"].includes(status.state) ||
    status.outcome === "failed" ||
    status.outcome === "rolled_back"
  ) {
    return null;
  }
  if (status.state === "back_up") {
    return successPresentation(status, connectionState, now).wakeAt;
  }
  const expiry = expiryTime(status);
  return expiry !== null && expiry > now ? expiry : null;
}

export function deploymentStatusExpiryDelay(
  status: DeploymentStatus | null | undefined,
  now = Date.now(),
  connectionState: ConnectionStatus = "connected"
): number | null {
  const expiry = deploymentStatusExpiry(status, now, connectionState);
  return expiry === null ? null : Math.max(1, expiry - now + 1);
}

/** What the one status slot shows. The update prompt and the deploy card report the
 * same event from two ends, so they share the slot rather than stack. */
export type StatusCardKey =
  | "Deploying"
  | "Deployed"
  | "Reconnecting"
  | "Problem"
  | "update-ready"
  | "update-held"
  | "owner-update";

export function resolveStatusCard(
  statusState: VisibleDeploymentState,
  release: ReleaseSnapshot
): StatusCardKey | null {
  // A failed deploy keeps its explanation visible over the page refresh prompt.
  // ShellStatus retains owner update controls beside that failure explanation.
  if (statusState === "problem") return "Problem";
  if (release.updateAvailable) {
    return release.reloadRequested && release.compositionActive ? "update-held" : "update-ready";
  }
  if (statusState === "preparing" || statusState === "restarting") return "Deploying";
  if (statusState === "back_up") return "Deployed";
  if (statusState === "reconnecting") return "Reconnecting";
  return null;
}
