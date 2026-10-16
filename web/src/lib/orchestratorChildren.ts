import type { OrchestratorChildFilter } from "./types";

/** Active retains all unfinished children. Backlog is an explicit subset. */
export function filterOrchestratorChildren<T extends { stage: string; is_backlogged: boolean }>(
  tickets: readonly T[], filter: OrchestratorChildFilter
): T[] {
  return tickets.filter((ticket) => {
    if (filter === "done") return ticket.stage === "done";
    if (filter === "retired") return ticket.stage === "cancelled";
    if (ticket.stage === "done" || ticket.stage === "cancelled") return false;
    return filter !== "backlogged" || ticket.is_backlogged;
  });
}
