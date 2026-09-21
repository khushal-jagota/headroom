import { sprintTicketCondition, type TicketConditionFacts } from "./sprintPresentation";

// The Sprint Item page's order of Ticket status groups, and which of them arrive shut.
// `quiet` is the single fact that a group is not what the reader came for: the page
// names and counts it, and arrives with it collapsed. This flag is this page's own.
// The rail carries its own groups too, and collapses two of them in
// `DEFAULT_COLLAPSED_GROUPS`. The two lists are not the same, because the two screens
// are read for different reasons, so neither one follows the other. Awaiting kickoff is
// this page's alone: the rail shows only the three attention groups, so it never has one.
//
// The workspace rail groups by the shared attention projection and holds its own order in
// `workspaceRail.ts`. The two screens split a Ticket up differently, but they call the
// same thing by the same name: where a group here and a group there hold the same
// Tickets they carry one label, and `GROUP_LABELS` in the rail is where the other half of
// such a pair lives. A label changed on one side is changed on both.
//
// `errored` and `blocked` go further than a shared label: they carry the rail's own
// keys. A worker that broke wants the reader, so Errored arrives open on both screens
// and sits first under the three that are his own, where the rail also puts it. Work
// another Ticket holds does not, so Blocked sits late and arrives shut on both. Neither
// state is ever named as the other.
export type TicketStatusGroupDefinition = {
  key: string;
  label: string;
  quiet: boolean;
};

// The three groups that say the work is the reader's own lead, in the rail's order —
// approval, then his own stage, then a message — and everything else keeps the order it
// already had beneath them. The page shows every Ticket and the rail shows only these
// three, so leading with them is what makes one heading mean one thing on both.
export const TICKET_STATUS_GROUPS: readonly TicketStatusGroupDefinition[] = [
  { key: "current-awaiting-approval", label: "Needs your approval", quiet: false },
  { key: "current-assigned", label: "Yours", quiet: false },
  { key: "needs-me", label: "Messages", quiet: false },
  { key: "errored", label: "Errored", quiet: false },
  { key: "waiting-for-kickoff", label: "Awaiting kickoff", quiet: false },
  { key: "current-running", label: "Agent", quiet: false },
  { key: "status_awaiting_approval", label: "Awaiting an agent's approval", quiet: true },
  { key: "current-waiting", label: "Waiting on Consequences", quiet: true },
  { key: "upcoming", label: "Empty", quiet: true },
  { key: "blocked", label: "Blocked", quiet: true },
  { key: "completed", label: "Done", quiet: true }
] as const;

// The facts a group is read from: the Ticket's condition, plus the two the condition
// cannot see — the field the Ticket is gated on, and whether a parked proposal is held
// by an agent rather than by the reader. A Ticket is finished at stage `done`, which the
// registry requires every Worker type to end on.
export type TicketStatusGroupFacts = TicketConditionFacts & {
  gating_field?: string | null;
  awaiting_agent_approval?: boolean;
};

// The Ticket is finished, or one of the two red states holds it. Errored and blocked
// are read from the status, which is the fact the rail reads: the server writes
// `blocked` only while a resting Ticket has a live blocker, and a Ticket doing
// something owns its own status. Then two marks split, each because the rail splits
// them and the two screens name the same Tickets the same way: a Ticket waiting on the
// user is not a Ticket that is the user's own to do, and a Ticket gated on its kickoff
// is worth naming apart from later approvals. Awaiting kickoff is the one case of
// awaiting approval that sets the Ticket going, so it is its own group and it keeps its
// place in the order.
export function ticketStatusGroupKey(ticket: TicketStatusGroupFacts): string {
  if (ticket.stage === "done") return "completed";
  if (ticket.ticket_status === "errored") return "errored";
  if (ticket.ticket_status === "blocked") return "blocked";
  const mark = sprintTicketCondition(ticket).mark;
  if (mark === "needs-me") return "needs-me";
  if (mark !== "current-awaiting-approval") {
    // A proposal is parked, but not for the reader: the server splits a parked proposal
    // by who holds its ceiling, and this is the half nobody is asking the reader about.
    // Read from that fact, not from the status, which says only that a proposal is
    // parked.
    //
    // Only out of `upcoming`. The rail reaches its own `status_awaiting_approval` only
    // after every attention group, so a broken worker still reads Errored and a Ticket
    // that is the user's own still reads Yours — a parked proposal never renames them.
    if (mark === "upcoming" && ticket.awaiting_agent_approval) {
      return "status_awaiting_approval";
    }
    return mark;
  }
  return ticket.gating_field === "brief" ? "waiting-for-kickoff" : mark;
}
