import type { ConnectionStatus } from "../../changeStream";
import type { UpdateStatus, MachineUpdateState, UpdateInterruption } from "../contracts";

/** What the one update card shows. `null` is no card. */
export type UpdateCardState =
  | "check_unavailable" | "available" | "updating" | "refresh_needed"
  | "failed" | "mac_failed" | "mac_unreachable" | "unreachable" | "machines";
export type UpdatePresentation = { state: UpdateCardState | null; dimmed: boolean };

/** What the card's action button does. */
export type UpdateCardAction = "install" | "refresh" | "retry" | "reconnect";
export type UpdateCard = {
  word: string;
  tone: "changing" | "settled" | "wrong" | "done";
  action?: { label: string; does: UpdateCardAction };
  detail?: string;
  track?: boolean;
  sentence?: boolean;
};

export type UpdatePresentationInput = {
  status: UpdateStatus | undefined;
  connection: ConnectionStatus;
  /** The running SHA differs from the SHA this page booted with. */
  pageIsBehind: boolean;
  /** The owner pressed an action that starts an update and the server has not answered yet. */
  starting: boolean;
  now: number;
};

/** The server owns update facts. Only the connection and this page's version overlay them. */
export function updatePresentation({ status, connection, pageIsBehind, starting, now }: UpdatePresentationInput): UpdatePresentation {
  if (connection !== "connected") {
    // A restart drops the connection. Until the server's deadline that is still the update.
    const deadline = Date.parse(status?.deadline_at ?? "");
    const updating = (starting || status?.state === "updating") && Number.isFinite(deadline) && now < deadline;
    return { state: updating ? "updating" : "unreachable", dimmed: true };
  }
  if (starting || status?.state === "updating") return { state: "updating", dimmed: false };
  // Panels rolled back, so its failure is the news.
  if (status?.state === "failed") return { state: "failed", dimmed: false };
  if (!status) return { state: null, dimmed: false };
  if (status.state === "up_to_date") {
    const hasTarget = status.machines.some(machine => machine.attempt_id !== null || machine.state !== "current");
    const installed = status.attempt?.state === "done" && status.attempt.to_sha === status.app_sha;
    return { state: hasTarget || installed ? "machines" : pageIsBehind ? "refresh_needed" : null, dimmed: false };
  }
  return { state: status.state, dimmed: false };
}

/** The card for a state. Actions appear only when the server allows them. */
export function updateCard(state: UpdateCardState, status: UpdateStatus | undefined): UpdateCard {
  const allows = (action: UpdateStatus["allowed_actions"][number]) => Boolean(status?.allowed_actions.includes(action));
  const attempt = Boolean(status?.attempt);
  switch (state) {
    case "available":
      return { word: "Update available", tone: "settled",
        action: allows("update") && status?.available ? { label: "Update", does: "install" } : undefined };
    case "updating":
      return { word: "Updating…", tone: "changing", track: true };
    case "check_unavailable":
      return { word: "Couldn’t check for updates", tone: "wrong", detail: status?.detail ?? undefined };
    case "refresh_needed":
      return { word: "Refresh to load this version", tone: "settled", action: { label: "Refresh", does: "refresh" } };
    case "failed":
      return { word: "Update didn't install", tone: "wrong", detail: status?.detail ?? status?.attempt?.detail ?? undefined,
        action: allows("retry") && attempt ? { label: "Try again", does: "retry" } : undefined };
    case "mac_failed":
    case "mac_unreachable":
      return { word: "Machine update needs attention", tone: "wrong" };
    case "machines": {
      const rows = status?.machines ?? [];
      const failed = rows.some(row => row.state === "failed" || row.state === "unavailable");
      const updating = rows.some(row => row.state === "updating");
      return { word: failed ? "Machine update needs attention" : updating ? "Updating machines…" : rows.some(row => row.state === "waiting") ? "Machine updates" : "Up to date",
        tone: failed ? "wrong" : updating ? "changing" : "done" };
    }
    case "unreachable":
      return { word: "Can't reach Panels", tone: "wrong", detail: "Trying again every few seconds", action: { label: "Try now", does: "reconnect" } };
  }
}

/** Keep current targets visible while a newer release is offered. */
export function cardMachines(status: UpdateStatus, presentation: UpdateCardState | UpdateStatus["state"] = status.state): readonly MachineUpdateState[] {
  if (presentation !== "available" || !status.available) return status.machines;
  return status.machines.map(machine => machine.attempt_id !== null ? machine
    : status.available_machines.find(offered => offered.machine_id === machine.machine_id) ?? machine);
}

export function machineUpdateLabel(machine: MachineUpdateState): string {
  switch (machine.state) {
    case "current": return machine.attempt_id ? "Done" : "Up to date";
    case "available": return machine.present ? "Will update" : "Updates when it is back";
    case "waiting": return "Updates when it is back";
    case "updating": return "Updating…";
    case "failed": return "Update failed";
    case "unavailable": return "Needs an update to continue";
  }
}

export function interruptionText(interruption: UpdateInterruption | null): string | null {
  if (!interruption) return null;
  return interruption.stops_agents ? "This update stops your agents" : "Your agents keep running";
}

/** An available offer is not evidence of what an earlier press interrupted. */
export function cardInterruption(status: UpdateStatus, presentation: UpdateCardState | UpdateStatus["state"] = status.state): UpdateInterruption | null {
  return presentation === "available" ? status.interruption : status.attempt?.interruption ?? null;
}

/** Offer requirements can differ from the requirements of the running server. */
export function deviceUpdateWarnings(status: UpdateStatus): string[] {
  const compatibility = status.available?.compatibility;
  if (!compatibility) return [];
  const warnings = new Set<string>();
  for (const identity of status.client_versions) {
    if (identity.kind === "ios" && identity.build !== undefined && identity.build < compatibility.ios.minimum_build) {
      warnings.add("Your iPhone needs the TestFlight update first.");
    } else if (identity.kind === "desktop" && identity.bridge_version !== undefined
      && identity.bridge_version < compatibility.desktop_shell.minimum_bridge_version) {
      warnings.add("Your desktop app needs an update first.");
    } else if (identity.kind === "machine" && (
      identity.protocol_version !== undefined && (identity.protocol_version < compatibility.machine.minimum_protocol_version
        || identity.protocol_version > compatibility.machine.maximum_protocol_version)
      || identity.outbox_format !== undefined && identity.outbox_format !== compatibility.outbox_format)) {
      const machine = status.machines.find(row => row.machine_id === identity.device_id);
      warnings.add(`${machine?.name ?? "A machine"} needs an update to continue.`);
    }
  }
  return [...warnings];
}
