import { ticketStatusGroupKey } from "./ticketStatusGroups";
import type { SprintItemWorkspace, SprintItemWorkspaceTicket } from "./types";
import {
  workItemTicketGroups,
  type WorkItemTicketGroupDefinition
} from "./workItemPresentation";

export type WorkspaceTicketGroup = WorkItemTicketGroupDefinition & {
  tickets: SprintItemWorkspaceTicket[];
};

// Today and Remaining are the same structure. The only thing the split says is whether
// the Ticket is on the Day, so both blocks group their own Tickets the same way and
// both carry a Done group.
function groupTickets(tickets: SprintItemWorkspaceTicket[]): WorkspaceTicketGroup[] {
  return workItemTicketGroups(tickets);
}

export function todayWorkspaceTicketGroups(workspace: SprintItemWorkspace): WorkspaceTicketGroup[] {
  const today = new Set(workspace.today_ticket_ids);
  return groupTickets(workspace.tickets.filter((ticket) => today.has(ticket.id)));
}

export function remainingWorkspaceTicketGroups(
  workspace: SprintItemWorkspace
): WorkspaceTicketGroup[] {
  const today = new Set(workspace.today_ticket_ids);
  return groupTickets(workspace.tickets.filter((ticket) => !today.has(ticket.id)));
}

export function workspaceProgress(workspace: SprintItemWorkspace): string {
  const tickets = workspace.tickets;
  if (!tickets.length) return "No Tickets";
  const open = tickets.filter((ticket) => ticket.stage !== "done");
  if (!open.length) return `All ${tickets.length} done`;
  const needsYou = open.filter((ticket) => {
    const group = ticketStatusGroupKey(ticket);
    return group === "awaiting_approval" || group === "awaiting_answer";
  }).length;
  return `${open.length} open${needsYou ? ` · ${needsYou} needs you` : ""}`;
}

export function workspaceTicketIsBacklog(ticket: SprintItemWorkspaceTicket): boolean {
  return ticket.sprint_id === null && ticket.stage !== "done";
}
