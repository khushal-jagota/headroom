import { fieldIsPassedFor, gatingFieldFor, type Lifecycle } from "./lifecycle";
import type { TicketDetail } from "./types";

/** Cancelled planning work keeps its last real stage and proposal as read-only history. */
export function workerPageHistory(lifecycle: Lifecycle, detail: TicketDetail): {
  stage: string; fields: string[]; completed: string[]; historicalProposal: { field: string; body: string } | null;
} {
  let stage = detail.stage;
  let historicalProposal: { field: string; body: string } | null = null;
  if (stage === "cancelled") {
    try {
      const record: unknown = JSON.parse(detail.field_values.retirement || "{}");
      if (record && typeof record === "object") {
        const retirement = record as Record<string, unknown>;
        if (typeof retirement.original_stage === "string") stage = retirement.original_stage;
        const raw: unknown = typeof retirement.original_pending_proposal_json === "string"
          ? JSON.parse(retirement.original_pending_proposal_json) : retirement.original_pending_proposal_json;
        if (raw && typeof raw === "object" && "field" in raw && "body" in raw && typeof raw.field === "string" && typeof raw.body === "string") {
          historicalProposal = { field: raw.field, body: raw.body };
        }
      }
    } catch { /* The original field values remain readable if provenance is malformed. */ }
  }
  const fields = lifecycle.fieldIds.filter((field) => field !== "retirement" && (
    fieldIsPassedFor(lifecycle, field, stage) || gatingFieldFor(lifecycle, stage) === field ||
    (detail.stage === "cancelled" && (Boolean(detail.field_values[field]) || historicalProposal?.field === field))
  ));
  return { stage, fields, completed: fields.filter((field) => fieldIsPassedFor(lifecycle, field, stage)), historicalProposal };
}
