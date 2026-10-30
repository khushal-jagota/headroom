export type TicketLocale = "vps" | "macos";
export type ExecutorHealth = {
  mode: "local" | "remote";
  identity?: { executor_incarnation: string; connection_generation: number } | null;
  connection_state: "ready" | "reconciling" | "disconnected";
  reconciliation_ready: boolean;
  observed_at_ms?: number | null;
  lost_at_ms?: number | null;
  last_contact_at_ms: number | null;
  last_contact_source: "vps_received_valid_executor_frame";
};
export type TicketCreationDraft = {
  worker_type: string;
  project_id?: string;
  project?: string;
  manager?: {kind: "chief" | "manager"; id: string};
  employee_backend?: string;
  employee_launch_model?: string;
  employee_launch_reasoning_effort?: string | null;
  locale?: TicketLocale;
};
export type DraftCreationContext = {
  resolved_locale: TicketLocale;
  employee_backend: string;
  project_id: string | null;
  locale_choices: { locale: TicketLocale; compatible: boolean; reason: string | null }[];
};
export const machineName = (locale: TicketLocale): string => locale === "macos" ? "Mac" : "VPS";
export function macActivityKnown(health: ExecutorHealth | undefined): boolean {
  return Boolean(health?.mode === "remote" && health.connection_state === "ready" && health.reconciliation_ready && health.identity && health.identity.connection_generation > 0);
}
export function measuredContact(health: ExecutorHealth | undefined): string {
  if (health?.last_contact_at_ms == null || health.last_contact_source !== "vps_received_valid_executor_frame") return "Last contact: unknown.";
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `Last contact: ${new Intl.DateTimeFormat(undefined, {dateStyle:"medium",timeStyle:"medium",timeZone:timezone}).format(health.last_contact_at_ms)} · ${timezone}.`;
}
