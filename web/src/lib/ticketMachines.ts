import { MACHINE_ACTIVITY_UNKNOWN } from "./machines/config";

export type TicketLocale = "vps" | "macos";
export type ExecutorIdentity = {
  protocol_version: number;
  application_revision: string;
  spool_format_version: number;
  executor_incarnation: string;
  connection_generation: number;
};
export type ExecutorRefusal = {
  reason: string;
  peer_identity: ExecutorIdentity;
  server_identity: ExecutorIdentity;
  refused_at_ms: number;
};
export type MacStatus = {
  identity?: { executor_incarnation: string; connection_generation: number } | null;
  connection_state: "ready" | "reconciling" | "disconnected";
  last_contact_at_ms: number | null;
  refusal?: ExecutorRefusal | null;
};
export type ExecutorHealth = MacStatus & {
  mode: "local" | "remote";
};
export const machineName = (locale: TicketLocale): string => locale === "macos" ? "Mac" : "VPS";
export function macActivityKnown(health: ExecutorHealth | undefined): boolean {
  return Boolean(health?.mode === "remote" && health.connection_state === "ready" && health.identity && health.identity.connection_generation > 0);
}
export function measuredContact(health: MacStatus | undefined): string {
  if (health?.last_contact_at_ms == null) return "Last contact: unknown.";
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `Last contact: ${new Intl.DateTimeFormat(undefined, {dateStyle:"medium",timeStyle:"medium",timeZone:timezone}).format(health.last_contact_at_ms)} · ${timezone}.`;
}

export function macAvailability(health: MacStatus | undefined): string {
  return (health?.connection_state === "disconnected" ? health.refusal?.reason : null) ?? MACHINE_ACTIVITY_UNKNOWN;
}
export function macVersionDescription(identity: ExecutorIdentity): string {
  return `${identity.application_revision.slice(0, 12)} · protocol ${identity.protocol_version} · outbox ${identity.spool_format_version}`;
}
