import type { TicketDetail, TicketField } from "./types";

export const PRIORITIES = ["P0", "P1", "P2", "P3"];
export const PRIORITY_ORDER = ["P0", "P1", "P2", "P3"];

export type FieldStageVisualState =
  | "completed"
  | "current-running"
  | "current-waiting"
  | "current-awaiting-approval"
  | "errored"
  | "upcoming";

export function stageLabel(value: string): string {
  return String(value).replace(/_/g, " ");
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
