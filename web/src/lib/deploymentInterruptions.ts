import { workspaceAddress } from "./workspaceAddress";

export type InterruptionEntry = {
  interruption_id: string;
  deployment_id: string;
  conversation_id: string | null;
  display_name: string;
  owner_kind: "ticket" | "manager" | "chief" | "unknown";
  owner_id: string | null;
  original_status: string;
  recovery_state: "awaiting_action" | "resumed" | "unable_to_resume" | "closed" | "historical";
  detail: string;
  can_restart: boolean;
};
export type InterruptionSummary = { accounts: {
  deployment_id: string;
  capture_state: "captured" | "unavailable";
  detail: string | null;
  entries: InterruptionEntry[];
}[] };
export const recoveryLabel: Record<InterruptionEntry["recovery_state"], string> = {
  awaiting_action: "Awaiting action", resumed: "Resumed", unable_to_resume: "Unable to resume",
  closed: "Closed", historical: "Historical"
};
export function interruptionHref(entry: InterruptionEntry): string | null {
  if (!entry.conversation_id) return null;
  if (entry.owner_kind === "chief") return workspaceAddress({ kind: "chief" });
  if ((entry.owner_kind === "ticket" || entry.owner_kind === "manager") && entry.owner_id) {
    return workspaceAddress({ kind: entry.owner_kind, id: entry.owner_id });
  }
  return null;
}
