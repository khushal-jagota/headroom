import { sprintTicketCondition, type TicketConditionFacts } from "./sprintPresentation";

// The Sprint Item page's order of Ticket status groups, and which of them arrive shut.
// `quiet` is the single fact that a group is not what the reader came for: the page
// names and counts it, and arrives with it collapsed. This flag is this page's own.
// The rail carries every group too, and collapses its own three in
// `DEFAULT_COLLAPSED_GROUPS`. The two lists are not the same, because the two screens
// are read for different reasons, so neither one follows the other.
//
// The workspace rail groups by the shared attention projection and holds its own order in
// `workspaceRail.ts`. The two screens split a Ticket up differently, but they call the
// same thing by the same name: a group here and a group there that hold the same
// Tickets carry one label, and `GROUP_LABELS` in the rail is where the other half of
// each pair lives. A label changed on one side is changed on both.
//
// `errored` and `blocked` go further than a shared label: they carry the rail's own
// keys. A worker that broke wants the reader now, so Errored leads and arrives open on
// both screens. Work another Ticket holds does not, so Blocked sits late and arrives
// shut on both. Neither state is ever named as the other.
export type TicketStatusGroupDefinition = {
  key: string;
  label: string;
  quiet: boolean;
};

export const TICKET_STATUS_GROUPS: readonly TicketStatusGroupDefinition[] = [
  { key: "errored", label: "Errored", quiet: false },
  { key: "needs-me", label: "Needs you", quiet: false },
  { key: "waiting-for-kickoff", label: "Waiting for kickoff", quiet: false },
  { key: "current-awaiting-approval", label: "Awaiting approval", quiet: false },
  { key: "current-assigned", label: "Assigned", quiet: false },
  { key: "current-running", label: "Agent", quiet: false },
  { key: "status_awaiting_approval", label: "Awaiting an agent's approval", quiet: true },
  { key: "current-waiting", label: "Waiting for closeout", quiet: true },
  { key: "upcoming", label: "Empty", quiet: true },
  { key: "blocked", label: "Blocked", quiet: true },
  { key: "completed", label: "Done", quiet: true }
] as const;

// The facts a group is read from: the Ticket's condition, plus the one the condition
// cannot see — the field the Ticket is gated on. A Ticket is finished at stage `done`,
// which the registry requires every Worker type to end on.
export type TicketStatusGroupFacts = TicketConditionFacts & {
  gating_field?: string | null;
};

// The Ticket is finished, or one of the two red states holds it. Errored and blocked
// are read from the status, which is the fact the rail reads: the server writes
// `blocked` only while a resting Ticket has a live blocker, and a Ticket doing
// something owns its own status. Then two marks split, each because the rail splits
// them and the two screens name the same Tickets the same way: a Ticket waiting on the
// user is not a Ticket that is the user's own to do, and a Ticket gated on its kickoff
// is worth naming apart from later approvals.
export function ticketStatusGroupKey(ticket: TicketStatusGroupFacts): string {
  if (ticket.stage === "done") return "completed";
  if (ticket.ticket_status === "errored") return "errored";
  if (ticket.ticket_status === "blocked") return "blocked";
  const mark = sprintTicketCondition(ticket).mark;
  if (mark === "needs-me") return "needs-me";
  if (mark !== "current-awaiting-approval") {
    // A proposal is parked, but not for the reader: `awaiting_approval` on the condition
    // is true only when the viewer holds the ceiling, so a Ticket held by an agent used
    // to fall through to Empty. The rail names these, and this page names them the same.
    //
    // Only out of `upcoming`. The rail reaches its own `status_awaiting_approval` only
    // after every attention group, so a broken worker still reads Errored and a Ticket
    // that is the user's own still reads Assigned — a parked proposal never renames them.
    if (mark === "upcoming" && ticket.ticket_status === "awaiting_approval") {
      return "status_awaiting_approval";
    }
    return mark;
  }
  return ticket.gating_field === "kickoff" ? "waiting-for-kickoff" : mark;
}
