import { conversationSignalPresentation } from "./conversationSignalPresentation";
import type { DayTicket } from "./types";
import type { FieldStageVisualState } from "./ui";

export type DayVisualTicket = {
  ticket: DayTicket;
  state: FieldStageVisualState;
  ariaLabel: string;
  group: string;
};

export type DayActionTile = {
  key: "needs-me" | "review" | "working" | "paired" | "done";
  label: string;
  count: number;
  href: string;
};

function groupKeyFor(ticket: DayTicket): string {
  if (ticket.is_done || ticket.stage === "done") return "done";
  if (ticket.waiting_to_closeout) return "waiting_to_closeout";
  if (ticket.ticket_status === "awaiting_approval" && ticket.gating_field === "kickoff") {
    return "waiting_for_kickoff";
  }
  return String(ticket.ticket_status);
}

export function dayVisualTicket(
  ticket: DayTicket,
  replyWatermarks: Readonly<Record<string, number>>
): DayVisualTicket {
  const group = groupKeyFor(ticket);
  const presentation = conversationSignalPresentation(
    {
      conversation_id:
        typeof ticket.conversation_id === "string" ? ticket.conversation_id : null,
      needs_me: Boolean(ticket.needs_me),
      agent_working: Boolean(ticket.agent_working),
      latest_turn_ended_sequence: Number(ticket.latest_turn_ended_sequence ?? 0)
    },
    replyWatermarks
  );

  if (ticket.is_done || ticket.stage === "done") {
    return { ticket, state: "completed", ariaLabel: "Done", group };
  }
  if (presentation.state === "reply-seen") {
    return { ticket, state: "upcoming", ariaLabel: "Nothing waiting", group };
  }
  if (presentation.state === "upcoming" && group === "paired") {
    return { ticket, state: "current-paired", ariaLabel: "Paired", group };
  }
  if (
    presentation.state === "upcoming" &&
    (group === "awaiting_approval" ||
      group === "waiting_for_kickoff" ||
      group === "needs_user")
  ) {
    return { ticket, state: "current-awaiting-approval", ariaLabel: "To review", group };
  }
  return { ticket, state: presentation.state, ariaLabel: presentation.ariaLabel, group };
}

/**
 * Where each dot state sits in the Day progress row, most urgent first.
 *
 * The Day row only produces `needs-me`, `current-awaiting-approval`,
 * `current-paired`, `current-running`, `upcoming`, and `completed`. The rest are
 * ranked so the sort stays total. `errored` sits beside `needs-me`: both mean the
 * ticket stopped and wants the user.
 */
const dotOrder: Record<FieldStageVisualState, number> = {
  "needs-me": 0,
  errored: 1,
  "current-awaiting-approval": 2,
  "current-paired": 3,
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
    paired: 0,
    done: 0
  };

  for (const visual of visualTickets) {
    if (visual.state === "needs-me") counts["needs-me"] += 1;
    else if (visual.state === "current-running") counts.working += 1;
    else if (
      visual.state === "current-awaiting-approval" &&
      (visual.group === "awaiting_approval" || visual.group === "waiting_for_kickoff")
    ) counts.review += 1;
    else if (visual.state === "current-paired") counts.paired += 1;
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
    { key: "paired", label: "Paired", href: "#/workspace" },
    { key: "done", label: "Done", href: "#/workspace" }
  ];

  return definitions
    .filter((definition) => counts[definition.key] > 0)
    .map((definition) => ({ ...definition, count: counts[definition.key] }));
}

export function dayPageState(visualTickets: readonly DayVisualTicket[]): "populated" | "calm" {
  return visualTickets.some((visual) => visual.state === "needs-me") ? "populated" : "calm";
}
