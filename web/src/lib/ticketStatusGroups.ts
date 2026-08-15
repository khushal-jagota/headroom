import { sprintTicketCondition, type TicketConditionFacts } from "./sprintPresentation";

// The Sprint Item page's order of Ticket status groups, and which of them arrive shut.
// `quiet` is the single fact that a group is not what the reader came for: the page
// names and counts it, and arrives with it collapsed.
//
// The workspace rail groups by raw `ticket_status` and holds its own order in
// `workspaceRail.ts`. The two screens split a Ticket up differently, but they call the
// same thing by the same name: a group here and a group there that hold the same
// Tickets carry one label, and `GROUP_LABELS` in the rail is where the other half of
// each pair lives. A label changed on one side is changed on both.
export type TicketStatusGroupDefinition = {
  key: string;
  label: string;
  quiet: boolean;
};

export const TICKET_STATUS_GROUPS: readonly TicketStatusGroupDefinition[] = [
  { key: "needs-me", label: "Needs you", quiet: false },
  { key: "user", label: "User", quiet: false },
  { key: "waiting-for-kickoff", label: "Waiting for kickoff", quiet: false },
  { key: "current-awaiting-approval", label: "Awaiting approval", quiet: false },
  { key: "current-paired", label: "Paired", quiet: false },
  { key: "current-running", label: "Agent", quiet: true },
  { key: "errored", label: "Blocked", quiet: true },
  { key: "current-waiting", label: "Waiting for closeout", quiet: true },
  { key: "upcoming", label: "Empty", quiet: true },
  { key: "completed", label: "Done", quiet: true }
] as const;

// The facts a group is read from: the Ticket's condition, plus the two the condition
// cannot see — an open blocker link, and the field the Ticket is gated on. A Ticket is
// finished at stage `done`, which the registry requires every Worker type to end on.
export type TicketStatusGroupFacts = TicketConditionFacts & {
  blocked?: boolean;
  gating_field?: string | null;
};

// A Ticket's own two facts win first: it is finished, or a blocker holds it. Then two
// marks split, each because the rail splits them and the two screens name the same
// Tickets the same way: a Ticket waiting on the user is not a Ticket that is the user's
// own to do, and a Ticket gated on its kickoff is worth naming apart from later
// approvals.
export function ticketStatusGroupKey(ticket: TicketStatusGroupFacts): string {
  if (ticket.stage === "done") return "completed";
  if (ticket.blocked) return "errored";
  const mark = sprintTicketCondition(ticket).mark;
  if (mark === "needs-me") return ticket.ticket_status === "user" ? "user" : "needs-me";
  if (mark !== "current-awaiting-approval") return mark;
  return ticket.gating_field === "kickoff" ? "waiting-for-kickoff" : mark;
}
