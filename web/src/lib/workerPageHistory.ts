import { fieldIsPassedFor, type Lifecycle } from "./lifecycle";
import type { TicketDetail } from "./types";

/** Show every lifecycle step and report which steps are complete. */
export function workerPageHistory(lifecycle: Lifecycle, detail: TicketDetail): {
  fields: string[]; completed: string[];
} {
  const fields = [...lifecycle.fieldIds];
  return { fields, completed: fields.filter((field) => fieldIsPassedFor(lifecycle, field, detail.stage)) };
}
