import type { ConversationSignal } from "./conversationSignal";
import { agentHoldsTicket, type AgentHoldFacts } from "./workAttentionPresentation";

export type TicketLocale = "vps" | "macos";
export type ExecutorIdentity = {
  protocol_version: number;
  application_revision: string;
  spool_format_version: number;
  executor_incarnation: string;
};
export type ExecutorRefusal = {
  reason: string;
  // An old WebSocket Mac is refused before it can name itself.
  peer_identity: ExecutorIdentity | null;
  server_identity: ExecutorIdentity;
  refused_at_ms: number;
};
// What the server last heard from the Mac's lease. `since_ms` is when that began:
// continuous presence, the going-to-sleep lease, the last lease, or the refusal.
export type MacPresence = "present" | "asleep" | "not_heard_from" | "never" | "refused";
export type MacStatus = {
  presence?: MacPresence;
  since_ms?: number | null;
  identity?: ExecutorIdentity | null;
  last_contact_at_ms: number | null;
  refusal?: ExecutorRefusal | null;
  update?: {
    request_id: string;
    target_revision: string;
    phase: "waiting" | "downloading" | "ready_to_restart" | "current" | "failed" | "reverted" | "unavailable";
    detail: string | null;
  } | null;
};
export type ExecutorHealth = MacStatus & {
  mode: "local" | "remote";
};
export const machineName = (locale: TicketLocale): string => locale === "macos" ? "Mac" : "VPS";
export function macActivityKnown(health: ExecutorHealth | undefined): boolean {
  return health?.mode === "remote" && health.presence === "present";
}
export function measuredContact(health: MacStatus | undefined): string {
  if (health?.last_contact_at_ms == null) return "Last contact: unknown.";
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `Last contact: ${new Intl.DateTimeFormat(undefined, {dateStyle:"medium",timeStyle:"medium",timeZone:timezone}).format(health.last_contact_at_ms)} · ${timezone}.`;
}

// "14:02" today, "7 Nov, 14:02" on another day, in the viewer's time zone unless one is given.
function macClockTime(at: number, now: number, timeZone?: string): string {
  const day = (time: number) => new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone }).format(time);
  const time = { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone } as const;
  return new Intl.DateTimeFormat("en-GB", day(at) === day(now) ? time : { day: "numeric", month: "short", ...time }).format(at);
}

// The sentence for a Mac that is not present. Present, or a presence this page does
// not know, has none: activity is shown as it is.
export function macPresenceLabel(health: MacStatus | undefined, now: number = Date.now(), timeZone?: string): string | null {
  const since = health?.since_ms == null ? "" : ` since ${macClockTime(health.since_ms, now, timeZone)}`;
  switch (health?.presence) {
    case "asleep": return `Asleep${since}`;
    case "not_heard_from": return `Not heard from${since}`;
    case "never": return "The Mac has not connected yet.";
    case "refused": return health.refusal?.reason ?? null;
    default: return null;
  }
}

export type MacTicketFacts = AgentHoldFacts & {
  locale?: TicketLocale;
  conversation_signal?: Partial<ConversationSignal> | null;
};
// What a Mac Ticket is doing while the Mac is away. Its turn is paused if it was
// working, and a Ticket the agent holds waits for the Mac to start. A refusal names
// itself instead, since the Mac will not return until it is updated.
export function macTicketActivityLabel(health: MacStatus | undefined, ticket: MacTicketFacts): string | null {
  if (ticket.locale !== "macos") return null;
  const presence = health?.presence;
  if (presence === "refused") return health?.refusal?.reason ?? null;
  if (presence !== "asleep" && presence !== "not_heard_from" && presence !== "never") return null;
  if (ticket.conversation_signal?.working) return "Paused: the Mac is away";
  return agentHoldsTicket(ticket) ? "Waiting for the Mac" : null;
}
export function macVersionDescription(identity: ExecutorIdentity): string {
  return `${identity.application_revision.slice(0, 12)} · protocol ${identity.protocol_version} · outbox ${identity.spool_format_version}`;
}
