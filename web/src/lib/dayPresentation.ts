import type { DayTicket } from "./types";
import type { FieldStageVisualState } from "./ui";
import { primaryWorkAttention } from "./workAttentionPresentation";
import {
  workItemActivityMark,
  workItemActivityMarkPresentation
} from "./workItemPresentation";

export type DayVisualTicket = {
  ticket: DayTicket;
  state: FieldStageVisualState | null;
  ariaLabel: string;
  group: string;
};

export type DayActionTile = {
  key: "needs-me" | "review" | "working" | "assigned" | "done";
  label: string;
  count: number;
  href: string;
};

function groupKeyFor(ticket: DayTicket): string {
  if (ticket.is_done || ticket.stage === "done") return "done";
  if (ticket.waiting_to_closeout) return "waiting_to_closeout";
  const attention = primaryWorkAttention({
    awaiting_reply: Boolean(ticket.awaiting_reply),
    awaiting_answer: Boolean(ticket.awaiting_answer),
    awaiting_approval: Boolean(ticket.awaiting_approval),
    assigned: Boolean(ticket.assigned)
  });
  if (attention === "awaiting_approval" && ticket.gating_field === "brief") {
    return "waiting_for_kickoff";
  }
  if (attention !== null) return attention;
  return String(ticket.ticket_status);
}

export function dayVisualTicket(
  ticket: DayTicket
): DayVisualTicket {
  const group = groupKeyFor(ticket);
  const presentation = workItemActivityMarkPresentation(workItemActivityMark({
    awaiting_reply: Boolean(ticket.awaiting_reply),
    awaiting_answer: Boolean(ticket.awaiting_answer),
    awaiting_approval: Boolean(ticket.awaiting_approval),
    agent_state: ticket.agent_state ?? "idle"
  }));
  return { ticket, state: presentation.state, ariaLabel: presentation.ariaLabel, group };
}

/** Show the exact conversations needing message attention in roster order. */
export function dayDotOrder(
  visualTickets: readonly DayVisualTicket[]
): DayVisualTicket[] {
  return visualTickets.filter((visual) => visual.state !== null);
}

export function dayActionTiles(visualTickets: readonly DayVisualTicket[]): DayActionTile[] {
  const counts: Record<DayActionTile["key"], number> = {
    "needs-me": 0,
    review: 0,
    working: 0,
    assigned: 0,
    done: 0
  };

  // Counts follow attention and activity facts, independently of conversation dots.
  for (const visual of visualTickets) {
    if (visual.group === "awaiting_answer" || visual.group === "awaiting_reply") {
      counts["needs-me"] += 1;
    }
    else if (visual.group === "awaiting_approval" || visual.group === "waiting_for_kickoff") {
      counts.review += 1;
    }
    else if (visual.ticket.agent_state === "working") counts.working += 1;
    else if (visual.group === "assigned") counts.assigned += 1;
    else if (visual.group === "done") counts.done += 1;
  }

  const definitions: Array<{
    key: DayActionTile["key"];
    label: string;
    href: string;
  }> = [
    { key: "needs-me", label: "Need you", href: "#/workspace" },
    { key: "review", label: "To review", href: "#/review" },
    { key: "working", label: "Working", href: "#/workspace" },
    { key: "assigned", label: "Assigned", href: "#/workspace" },
    { key: "done", label: "Done", href: "#/workspace" }
  ];

  return definitions
    .filter((definition) => counts[definition.key] > 0)
    .map((definition) => ({ ...definition, count: counts[definition.key] }));
}

export function dayPageState(visualTickets: readonly DayVisualTicket[]): "populated" | "calm" {
  return visualTickets.some(
    (visual) => visual.group === "awaiting_answer" || visual.group === "awaiting_reply"
  )
    ? "populated"
    : "calm";
}
