import type { ConnectionStatus } from "./changeStream";
import type { ReleaseSnapshot } from "./releaseMonitor";
import type { DeploymentStatus, SuccessPresentation } from "./types";

export type VisibleDeploymentState =
  | "connected"
  | "reconnecting"
  | "preparing"
  | "restarting"
  | "back_up"
  | "problem"
  | "unknown";

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

function unknownPresentation(
  status: DeploymentStatus, connectionState: ConnectionStatus, now: number
): VisibleDeploymentState {
  if (!status.installation_confirmed) return "unknown";
  if (connectionState !== "connected") return connectionState;
  const deadline = timestamp(status.unknown_display_until);
  return deadline !== null && now >= deadline ? connectionState : "unknown";
}

/** A stale cached progress body uses the same truth as a fresh unknown body. */
export function deploymentStatusDetail(
  status: DeploymentStatus | null | undefined, state: VisibleDeploymentState
): string | null {
  if (!status) return null;
  if (state === "unknown") {
    if (status.installation_confirmed) {
      return "The requested app is installed and available. Its final report is unconfirmed. No action is needed from you.";
    }
    if (status.state === "unknown" && status.detail) return status.detail;
    return status.work_paused
      ? "Installation is unconfirmed. Work remains paused. Chief review is required."
      : "Installation is unconfirmed. Chief review is required.";
  }
  return ["preparing", "restarting", "problem"].includes(state) ? status.detail : null;
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
    return isFresh(status, now) ? "preparing" : unknownPresentation(status, connectionState, now);
  }
  if (status.state === "restarting") {
    return isFresh(status, now) ? "restarting" : unknownPresentation(status, connectionState, now);
  }
  if (status.state === "back_up") {
    return successPresentation(status, connectionState, now).state;
  }
  if (status.state === "idle") {
    return connectionState;
  }
  return unknownPresentation(status, connectionState, now);
}

export function deploymentStatusExpiry(
  status: DeploymentStatus | null | undefined,
  now = Date.now(),
  connectionState: ConnectionStatus = "connected"
): number | null {
  if (
    !status ||
    !["preparing", "restarting", "back_up", "unknown"].includes(status.state) ||
    status.outcome === "failed" ||
    status.outcome === "rolled_back"
  ) {
    return null;
  }
  if (status.state === "back_up") {
    return successPresentation(status, connectionState, now).wakeAt;
  }
  const expiry = expiryTime(status);
  if (status.state !== "unknown" && expiry !== null && expiry > now) return expiry;
  const unknownDeadline = status.installation_confirmed && connectionState === "connected"
    ? timestamp(status.unknown_display_until) : null;
  return unknownDeadline !== null && unknownDeadline > now ? unknownDeadline : null;
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
  | "Deployment outcome unknown"
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
  if (statusState === "unknown") return "Deployment outcome unknown";
  if (release.updateAvailable) {
    return release.reloadRequested && release.compositionActive ? "update-held" : "update-ready";
  }
  if (statusState === "preparing" || statusState === "restarting") return "Deploying";
  if (statusState === "back_up") return "Deployed";
  if (statusState === "reconnecting") return "Reconnecting";
  return null;
}
