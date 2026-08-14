import type { AtCap, TicketDetail, TicketField } from "./types";

export const PRIORITIES = ["P0", "P1", "P2", "P3"];
export const PRIORITY_ORDER = ["P0", "P1", "P2", "P3"];

export type FieldStageVisualState =
  | "completed"
  | "current-running"
  | "current-waiting"
  | "current-paired"
  | "current-awaiting-approval"
  | "errored"
  | "upcoming"
  | "reply-seen"
  // A worker waiting on a permission ask only the user can answer: the pure white dot.
  | "needs-me";

export function stageLabel(value: string): string {
  return String(value).replace(/_/g, " ");
}

export function atCapLabel(value: AtCap): string {
  const labels: Record<AtCap, string> = {
    stop: "stop",
    propose: "propose"
  };
  return labels[value];
}

// Underscores to spaces; capitalizes the first letter by default. Pass
// { capitalize: false } to leave the case untouched (e.g. sprint state tags that
// are lowercased via CSS).
export function labelize(value: string, options?: { capitalize?: boolean }): string {
  if (options?.capitalize === false) return String(value).replace(/_/g, " ");
  const text = String(value).replace(/_/g, " ").trim();
  if (!text) return "";
  return text[0].toUpperCase() + text.slice(1);
}

export function fieldSlot(detail: TicketDetail, name: string): TicketField {
  return detail.fields?.[name] || {};
}

// The per-field visual-state input. The classifier that consumes it now lives in
// lifecycle.ts (ticketStageVisualStateFor), keyed on the Worker type's manifest-derived
// lifecycle; this shape stays here as the shared input contract.
export type TicketStageVisualInput = {
  ticketStage: string;
  ticketStatus?: string | null;
  fieldName: string;
  fieldHasProposal?: boolean;
};

export function markerLabel(value: string): string {
  const labels: Record<string, string> = {
    "pending-proposal": "proposal pending",
    "blockers-cleared": "blockers cleared",
    frozen: "frozen"
  };
  return labels[value] || value;
}

// The ticket status names are the labels: underscores become spaces.
export function ticketStatusText(value: string): string {
  return value.replace(/_/g, " ");
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
