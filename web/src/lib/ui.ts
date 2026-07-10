import type { TicketDetail, TicketField } from "./types";

export const FIELD_NAMES = ["success", "approach", "plan", "implementation", "closeout"] as const;
export const PRIORITIES = ["P0", "P1", "P2", "P3"];
export const PRIORITY_ORDER = ["P0", "P1", "P2", "P3"];

export type FieldStageVisualState =
  | "completed"
  | "current-running"
  | "current-waiting"
  | "current-awaiting-approval"
  | "errored"
  | "upcoming";

export const STATE_ORDER = [
  "needs_success",
  "needs_approach",
  "needs_plan",
  "needs_implementation",
  "needs_closeout",
  "done"
];

const GATING_FIELD: Record<string, string> = {
  needs_success: "success",
  needs_approach: "approach",
  needs_plan: "plan",
  needs_implementation: "implementation",
  needs_closeout: "closeout"
};

const GATED_STATE: Record<string, string> = {
  success: "needs_success",
  approach: "needs_approach",
  plan: "needs_plan",
  implementation: "needs_implementation",
  closeout: "needs_closeout"
};

const ADVANCE: Record<string, string> = {
  needs_success: "needs_approach",
  needs_approach: "needs_plan",
  needs_plan: "needs_implementation",
  needs_implementation: "needs_closeout",
  needs_closeout: "done"
};

export function stateLabel(value: string): string {
  return String(value).replace(/_/g, " ");
}

export function gatingField(state: string): string | null {
  return GATING_FIELD[state] || null;
}

export function advanceTarget(state: string, ceiling: string): string | null {
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

export type TicketStageVisualInput = {
  ticketState: string;
  ticketStatus?: string | null;
  fieldName: string;
  fieldHasProposal?: boolean;
};

export function ticketStageVisualState({
  ticketState,
  ticketStatus,
  fieldName,
  fieldHasProposal = false
}: TicketStageVisualInput): FieldStageVisualState {
  if (ticketState === "done") return "completed";


  if (fieldIsPassed(fieldName, ticketState)) return "completed";

  if (gatingField(ticketState) === fieldName) {
    if (ticketStatus === "agent_running_step") return "current-running";
    if (ticketStatus === "errored") return "errored";
    if (fieldHasProposal || ticketStatus === "awaiting_approval") {
      return "current-awaiting-approval";
    }
    return "current-waiting";
  }

  return "upcoming";
}

export function fieldStageVisualState(
  detail: TicketDetail,
  fieldName: string
): FieldStageVisualState {
  return ticketStageVisualState({
    ticketState: detail.state,
    ticketStatus: detail.ticket_status,
    fieldName,
    fieldHasProposal: Boolean(fieldSlot(detail, fieldName).proposal)
  });
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
