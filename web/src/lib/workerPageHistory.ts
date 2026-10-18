import { fieldIsPassedFor, gatingFieldFor, type Lifecycle } from "./lifecycle";
import type { TicketDetail } from "./types";

/** Show completed steps and the current step without exposing future steps. */
export function workerPageHistory(lifecycle: Lifecycle, detail: TicketDetail): {
  fields: string[]; completed: string[];
} {
  const fields = lifecycle.fieldIds.filter((field) =>
    fieldIsPassedFor(lifecycle, field, detail.stage) || gatingFieldFor(lifecycle, detail.stage) === field
  );
  return { fields, completed: fields.filter((field) => fieldIsPassedFor(lifecycle, field, detail.stage)) };
}
