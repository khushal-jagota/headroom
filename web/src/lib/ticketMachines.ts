export type TicketLocale = "vps" | "macos";
export type ExecutorHealth = {
  mode: "local" | "remote";
  identity?: { executor_incarnation: string; connection_generation: number } | null;
  connection_state: "ready" | "reconciling" | "disconnected";
  last_contact_at_ms: number | null;
};
export const machineName = (locale: TicketLocale): string => locale === "macos" ? "Mac" : "VPS";
export function macActivityKnown(health: ExecutorHealth | undefined): boolean {
  return Boolean(health?.mode === "remote" && health.connection_state === "ready" && health.identity && health.identity.connection_generation > 0);
}
export function measuredContact(health: ExecutorHealth | undefined): string {
  if (health?.last_contact_at_ms == null) return "Last contact: unknown.";
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `Last contact: ${new Intl.DateTimeFormat(undefined, {dateStyle:"medium",timeStyle:"medium",timeZone:timezone}).format(health.last_contact_at_ms)} · ${timezone}.`;
}
