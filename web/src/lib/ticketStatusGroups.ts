import { sprintTicketCondition, type TicketConditionFacts } from "./sprintPresentation";

// The Sprint Item page's order of Ticket status groups, and which of them arrive shut.
// `quiet` is the single fact that a group is not what the reader came for: the page
// names and counts it, and arrives with it collapsed.
//
// The workspace rail groups by raw `ticket_status` and holds its own order in
// `workspaceRail.ts`. These were meant to be one rule. They are not, because the two
// screens were designed apart and each design was approved on its own terms: this page
// reads a Ticket's condition, the rail reads its status. Anyone unifying them is
// changing an approved design on one screen or the other, so do it deliberately.
export type TicketStatusGroupDefinition = {
  key: string;
  label: string;
  quiet: boolean;
};

export const TICKET_STATUS_GROUPS: readonly TicketStatusGroupDefinition[] = [
  { key: "needs-me", label: "Needs you", quiet: false },
  { key: "waiting-for-kickoff", label: "Waiting for kickoff", quiet: false },
  { key: "current-awaiting-approval", label: "Awaiting approval", quiet: false },
  { key: "current-paired", label: "Paired", quiet: false },
  { key: "current-running", label: "Agent", quiet: true },
  { key: "errored", label: "Blocked", quiet: true },
  { key: "current-waiting", label: "Waiting for closeout", quiet: true },
  { key: "upcoming", label: "Not started", quiet: true },
  { key: "completed", label: "Done", quiet: true }
] as const;

// The facts a group is read from: the Ticket's condition, plus the two the condition
// cannot see — an open blocker link, and the field the Ticket is gated on. A Ticket is
// finished at stage `done`, which the registry requires every Worker type to end on.
export type TicketStatusGroupFacts = TicketConditionFacts & {
  blocked?: boolean;
  gating_field?: string | null;
};

// A Ticket's own two facts win first: it is finished, or a blocker holds it. Then the
// shared awaiting-approval mark splits once: a Ticket still gated on its kickoff is the
// one worth naming on its own. Everything else waiting is simply awaiting approval.
export function ticketStatusGroupKey(ticket: TicketStatusGroupFacts): string {
  if (ticket.stage === "done") return "completed";
  if (ticket.blocked) return "errored";
  const mark = sprintTicketCondition(ticket).mark;
  if (mark !== "current-awaiting-approval") return mark;
  return ticket.gating_field === "kickoff" ? "waiting-for-kickoff" : mark;
}
