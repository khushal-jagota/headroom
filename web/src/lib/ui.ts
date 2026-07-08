import type { TicketDetail, TicketField } from "./types";

export const FIELD_NAMES = ["success", "approach", "plan", "result"] as const;
export const PROJECTS = ["Vylo", "Tribe", "Learning", "Other"];
export const PRIORITIES = ["P0", "P1", "P2", "P3"];
export const PRIORITY_ORDER = ["P0", "P1", "P2", "P3"];

export const STATE_ORDER = [
  "needs_success",
  "needs_approach",
  "needs_plan",
  "in_progress",
  "needs_review",
  "done"
];

const GATING_FIELD: Record<string, string> = {
  needs_success: "success",
  needs_approach: "approach",
  needs_plan: "plan",
  in_progress: "result"
};

const GATED_STATE: Record<string, string> = {
  success: "needs_success",
  approach: "needs_approach",
  plan: "needs_plan",
  result: "in_progress"
};

const ADVANCE: Record<string, string> = {
  needs_success: "needs_approach",
  needs_approach: "needs_plan",
  needs_plan: "in_progress",
  in_progress: "needs_review"
};

export function stateLabel(value: string): string {
  return String(value).replace(/_/g, " ");
}

export function gatingField(state: string): string | null {
  return GATING_FIELD[state] || null;
}

export function advanceTarget(state: string, ceiling: string): string | null {
  if (state === "in_progress" && ceiling === "done") return "done";
  return ADVANCE[state] || null;
}

export function ceilingOptions(floorState: string): Array<{ value: string; label: string }> {
  let start = STATE_ORDER.indexOf(floorState);
  if (start < 0) start = 0;
  return STATE_ORDER.slice(start).map((state) => ({ value: state, label: stateLabel(state) }));
}

export function fieldIsPassed(field: string, state: string): boolean {
  return STATE_ORDER.indexOf(state) > STATE_ORDER.indexOf(GATED_STATE[field]);
}

export function fieldSlot(detail: TicketDetail, name: string): TicketField {
  return detail.fields?.[name] || {};
}

export function markerLabel(value: string): string {
  const labels: Record<string, string> = {
    "pending-proposal": "proposal pending",
    "agent-running-step": "running step",
    "blockers-cleared": "blockers cleared",
    errored: "errored",
    frozen: "frozen",
    "user-takeover": "user takeover"
  };
  return labels[value] || value;
}

export function ticketStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    empty: "empty",
    agent_running_step: "running step",
    awaiting_approval: "awaiting approval",
    user_takeover: "user takeover",
    errored: "errored"
  };
  return `status ${labels[value] || value.replace(/_/g, " ")}`;
}

export function formatUnix(seconds: unknown): string {
  if (seconds === null || seconds === undefined) return "";
  return new Date(Number(seconds) * 1000).toLocaleString();
}

export function errorCode(error: unknown): string {
  return error && typeof error === "object" && "code" in error
    ? String((error as { code?: unknown }).code || "error")
    : "error";
}

export function errorMessage(error: unknown): string {
  return error && typeof error === "object" && "message" in error
    ? String((error as { message?: unknown }).message || "request failed")
    : "request failed";
}
