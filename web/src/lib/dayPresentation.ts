import { conversationSignalPresentation } from "./conversationSignalPresentation";
import type { DayTicket } from "./types";
import type { FieldStageVisualState } from "./ui";
import { primaryWorkAttention } from "./workAttentionPresentation";

export type DayVisualTicket = {
  ticket: DayTicket;
  state: FieldStageVisualState;
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
  const presentation = conversationSignalPresentation(
    {
      awaiting_reply: Boolean(ticket.awaiting_reply),
      awaiting_answer: Boolean(ticket.awaiting_answer),
      agent_state: ticket.agent_state ?? "idle"
    }
  );

  if (ticket.is_done || ticket.stage === "done") {
    return { ticket, state: "completed", ariaLabel: "Done", group };
  }
  if (presentation.state === "upcoming" && group === "assigned") {
    return { ticket, state: "current-assigned", ariaLabel: "Assigned", group };
  }
  if (
    presentation.state === "upcoming" &&
    (group === "awaiting_approval" ||
      group === "waiting_for_kickoff" ||
      group === "awaiting_reply")
  ) {
    return { ticket, state: "current-awaiting-approval", ariaLabel: "To review", group };
  }
  return { ticket, state: presentation.state, ariaLabel: presentation.ariaLabel, group };
}

/**
 * Where each dot state sits in the Day progress row, most urgent first.
 *
 * The Day row only produces `needs-me`, `current-awaiting-approval`,
 * `current-assigned`, `current-running`, `upcoming`, and `completed`. The rest are
 * ranked so the sort stays total. `errored` sits beside `needs-me`: both mean the
 * ticket stopped and wants the user.
 */
const dotOrder: Record<FieldStageVisualState, number> = {
  "needs-me": 0,
  errored: 1,
  "current-awaiting-approval": 2,
  "current-assigned": 3,
  "current-running": 4,
  "current-waiting": 5,
  upcoming: 6,
  "reply-seen": 7,
  completed: 8
};

/**
 * The Day's tickets in progress-row order: one unbroken run per state, roster
 * order inside a run. The sort is stable, and the given list is left alone.
 */
export function dayDotOrder(
  visualTickets: readonly DayVisualTicket[]
): DayVisualTicket[] {
  return [...visualTickets].sort((left, right) => dotOrder[left.state] - dotOrder[right.state]);
}

export function dayActionTiles(visualTickets: readonly DayVisualTicket[]): DayActionTile[] {
  const counts: Record<DayActionTile["key"], number> = {
    "needs-me": 0,
    review: 0,
    working: 0,
    assigned: 0,
    done: 0
  };

  // "Need you" is every ticket stopped on the owner: a worker waiting on an answer,
  // which carries the `needs-me` dot, and an unread message, which carries its own.
  for (const visual of visualTickets) {
    if (visual.state === "needs-me" || visual.group === "awaiting_reply") {
      counts["needs-me"] += 1;
    }
    else if (visual.state === "current-running") counts.working += 1;
    else if (
      visual.state === "current-awaiting-approval" &&
      (visual.group === "awaiting_approval" || visual.group === "waiting_for_kickoff")
    ) counts.review += 1;
    else if (visual.state === "current-assigned") counts.assigned += 1;
    else if (visual.state === "completed") counts.done += 1;
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
    (visual) => visual.state === "needs-me" || visual.group === "awaiting_reply"
  )
    ? "populated"
    : "calm";
}
