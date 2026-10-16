import type { TicketDetail } from "./types";

/** Retirement is provenance, not a completed lifecycle step. */
export function retirementReason(ticket: Pick<TicketDetail, "stage" | "field_values">): string | null {
  if (ticket.stage !== "cancelled") return null;
  try {
    const record: unknown = JSON.parse(ticket.field_values.retirement || "{}");
    return record !== null && typeof record === "object" && "reason" in record && typeof record.reason === "string"
      ? record.reason : null;
  } catch {
    return null;
  }
}
