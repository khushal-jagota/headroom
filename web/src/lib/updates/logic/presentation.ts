import type { ConnectionStatus } from "../../changeStream";
import type { MacStatus } from "../../ticketMachines";
import type { UpdateStatus } from "../contracts";

/** What the one update card shows. `null` is no card. */
export type UpdateCardState =
  | "available" | "waiting" | "updating" | "complete"
  | "failed" | "mac_failed" | "mac_unreachable" | "unreachable";
export type UpdateFailureState = "failed" | "mac_failed" | "mac_unreachable";
export type UpdatePresentation = { state: UpdateCardState | null; dimmed: boolean };

/** What the card's action button does. */
export type UpdateCardAction = "offer" | "waiting" | "refresh" | "retry" | "retry_mac" | "reconnect";
export type UpdateCard = {
  word: string;
  tone: "changing" | "settled" | "wrong" | "done";
  action?: { label: string; does: UpdateCardAction };
  info?: UpdateFailureState;
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
  // Panels moved on. A page left behind cannot write, so it refreshes first and then
  // shows a Mac failure, which the server keeps.
  if (pageIsBehind) return { state: "complete", dimmed: false };
  if (!status || status.state === "up_to_date") return { state: null, dimmed: false };
  return { state: status.state, dimmed: false };
}

/** The card for a state. Actions appear only when the server allows them. */
export function updateCard(state: UpdateCardState, status: UpdateStatus | undefined): UpdateCard {
  const allows = (action: UpdateStatus["allowed_actions"][number]) => Boolean(status?.allowed_actions.includes(action));
  const attempt = Boolean(status?.attempt);
  switch (state) {
    case "available":
      return { word: "Update available", tone: "settled",
        action: allows("update") && status?.available ? { label: "Update", does: "offer" } : undefined };
    case "waiting":
      return { word: "Update waits for work to finish", tone: "changing", sentence: true, action: { label: "View", does: "waiting" } };
    case "updating":
      return { word: "Updating…", tone: "changing", track: true };
    case "complete":
      return { word: "Update complete", tone: "done", action: { label: "Refresh", does: "refresh" } };
    case "failed":
      return { word: "Update didn't install", tone: "wrong", info: state,
        action: allows("retry") && attempt ? { label: "Try again", does: "retry" } : undefined };
    case "mac_failed":
    case "mac_unreachable":
      return { word: state === "mac_failed" ? "Mac didn't update" : "Can't reach the Mac", tone: "wrong", info: state,
        action: allows("retry_mac") && attempt ? { label: "Try again", does: "retry_mac" } : undefined };
    case "unreachable":
      return { word: "Can't reach Panels", tone: "wrong", detail: "Trying again every few seconds", action: { label: "Try now", does: "reconnect" } };
  }
}

export type UpdateDetails = { title: string; facts: [label: string, value: string][] };

const DETAIL_TITLES: Record<UpdateFailureState, string> = {
  failed: "Update didn't install",
  mac_failed: "Mac didn't update",
  mac_unreachable: "Can't reach the Mac",
};

const short = (sha: string) => sha.slice(0, 8);

/** The plain facts behind the info button. Mac diagnostics appear only here. */
export function updateDetails(state: UpdateFailureState, status: UpdateStatus, now: number): UpdateDetails {
  const attempt = status.attempt;
  if (state === "failed") {
    return { title: DETAIL_TITLES[state], facts: [
      ["What happened", status.detail ?? attempt?.detail ?? "Panels went back to the previous version."],
      ["Panels", `${short(attempt?.from_sha ?? status.app_sha)} (previous)`],
      ...(attempt ? [["Tried", short(attempt.to_sha)] as [string, string]] : []),
      ["Mac", "Not changed"],
    ] };
  }
  const mac = status.mac_details;
  const fallback = state === "mac_failed"
    ? mac?.update?.detail ?? "The Mac went back to its previous version."
    : "The Mac did not check in during the update. It can be asleep or offline.";
  const facts: [string, string][] = [
    ["What happened", status.detail ?? fallback],
    ["Panels", `${short(status.app_sha)} (updated)`],
  ];
  const refusal = mac?.connection_state === "disconnected" ? mac.refusal ?? null : null;
  const identity = mac?.identity ?? refusal?.peer_identity ?? null;
  if (identity) facts.push(["Mac", `${short(identity.application_revision)}${state === "mac_failed" ? " (previous)" : ""}`]);
  facts.push(["Last contact", lastContact(mac, now)]);
  if (identity) {
    const server = refusal?.server_identity;
    facts.push(["Protocol", `Mac ${identity.protocol_version}${server ? ` · server ${server.protocol_version}` : ""}`]);
    facts.push(["Outbox format", `Mac ${identity.spool_format_version}${server ? ` · server ${server.spool_format_version}` : ""}`]);
  }
  if (refusal) facts.push(["Refused", refusal.reason]);
  return { title: DETAIL_TITLES[state], facts };
}

function lastContact(mac: MacStatus | null, now: number): string {
  const at = mac?.last_contact_at_ms;
  if (at == null) return "Unknown";
  const minutes = Math.floor(Math.max(0, now - at) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(at);
}
