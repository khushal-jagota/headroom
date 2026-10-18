import type { OrchestratorChildFilter } from "./types";

/** Page sections are exclusive. Terminal history wins over a retained backlog flag. */
export function filterOrchestratorChildren<T extends { stage: string; is_backlogged: boolean }>(
  tickets: readonly T[], filter: OrchestratorChildFilter
): T[] {
  return tickets.filter((ticket) => {
    if (filter === "done") return ticket.stage === "done";
    if (ticket.stage === "done") return false;
    return filter === "backlogged" ? ticket.is_backlogged : !ticket.is_backlogged;
  });
}
