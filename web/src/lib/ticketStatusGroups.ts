import { sprintTicketCondition, type TicketConditionFacts } from "./sprintPresentation";

// One order of Ticket status groups, for every screen that lists Tickets by status.
// `quiet` is the single fact that a group is not what the reader came for. Each screen
// renders that fact its own way: the Sprint Item page arrives collapsed but still named
// and counted, and the workspace rail holds the group behind "+n more".
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
