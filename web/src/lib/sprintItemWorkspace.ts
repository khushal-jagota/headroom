import {
  TICKET_STATUS_GROUPS,
  ticketStatusGroupKey,
  type TicketStatusGroupDefinition
} from "./ticketStatusGroups";
import type { SprintItemWorkspace, SprintItemWorkspaceTicket } from "./types";

export type WorkspaceTicketGroup = TicketStatusGroupDefinition & {
  tickets: SprintItemWorkspaceTicket[];
};

function ticketOrder(left: SprintItemWorkspaceTicket, right: SprintItemWorkspaceTicket): number {
  return (
    Number(left.priority.slice(1)) - Number(right.priority.slice(1)) ||
    left.title.localeCompare(right.title, undefined, { sensitivity: "base" }) ||
    left.id.localeCompare(right.id)
  );
}

// Today and Remaining are the same structure. The only thing the split says is whether
// the Ticket is on the Day, so both blocks group their own Tickets the same way and
// both carry a Done group.
function groupTickets(tickets: SprintItemWorkspaceTicket[]): WorkspaceTicketGroup[] {
  return TICKET_STATUS_GROUPS.flatMap((group) => {
    const grouped = tickets
      .filter((ticket) => ticketStatusGroupKey(ticket) === group.key)
      .sort(ticketOrder);
    return grouped.length ? [{ ...group, tickets: grouped }] : [];
  });
}

export function todayWorkspaceTicketGroups(workspace: SprintItemWorkspace): WorkspaceTicketGroup[] {
  const today = new Set(workspace.today_ticket_ids);
  return groupTickets(
    workspace.tickets.filter((ticket) => today.has(ticket.id) && ticket.stage !== "dropped")
  );
}

export function remainingWorkspaceTicketGroups(
  workspace: SprintItemWorkspace
): WorkspaceTicketGroup[] {
  const today = new Set(workspace.today_ticket_ids);
  return groupTickets(
    workspace.tickets.filter((ticket) => !today.has(ticket.id) && ticket.stage !== "dropped")
  );
}

export function workspaceProgress(workspace: SprintItemWorkspace): string {
  const tickets = workspace.tickets.filter((ticket) => ticket.stage !== "dropped");
  if (!tickets.length) return "No Tickets";
  const open = tickets.filter((ticket) => ticket.stage !== "done");
  if (!open.length) return `All ${tickets.length} done`;
  const needsYou = open.filter((ticket) => ticketStatusGroupKey(ticket) === "needs-me").length;
  return `${open.length} open${needsYou ? ` · ${needsYou} needs you` : ""}`;
}

export function workspaceTicketIsBacklog(ticket: SprintItemWorkspaceTicket): boolean {
  return ticket.sprint_id === null && ticket.stage !== "done" && ticket.stage !== "dropped";
}
