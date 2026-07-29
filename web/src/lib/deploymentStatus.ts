import type { ConnectionStatus } from "./changeStream";
import type { DeploymentStatus } from "./types";

export type VisibleDeploymentState =
  | "connected"
  | "reconnecting"
  | "preparing"
  | "restarting"
  | "back_up"
  | "problem";

function expiryTime(status: DeploymentStatus): number | null {
  if (status.valid_until === null) return null;
  const value = Date.parse(status.valid_until);
  return Number.isFinite(value) ? value : null;
}

function isFresh(status: DeploymentStatus, now: number): boolean {
  const expiry = expiryTime(status);
  return expiry !== null && now < expiry;
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
    return connectionState === "connected" && isFresh(status, now)
      ? "back_up"
      : connectionState;
  }
  if (status.state === "idle") {
    return connectionState;
  }
  return "reconnecting";
}

export function deploymentStatusExpiry(
  status: DeploymentStatus | null | undefined,
  now = Date.now()
): number | null {
  if (
    !status ||
    !["preparing", "restarting", "back_up"].includes(status.state) ||
    status.outcome === "failed" ||
    status.outcome === "rolled_back"
  ) {
    return null;
  }
  const expiry = expiryTime(status);
  return expiry !== null && expiry > now ? expiry : null;
}

export function deploymentStatusExpiryDelay(
  status: DeploymentStatus | null | undefined,
  now = Date.now()
): number | null {
  const expiry = deploymentStatusExpiry(status, now);
  return expiry === null ? null : Math.max(1, expiry - now + 1);
}
