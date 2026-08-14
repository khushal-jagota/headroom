import { sprintTicketCondition } from "./sprintPresentation";
import type {
  SprintItemWorkspace,
  SprintItemWorkspaceObligation,
  SprintItemWorkspaceTicket
} from "./types";

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  tickets: SprintItemWorkspaceTicket[];
};

const groupOrder = [
  ["needs-me", "Needs user"],
  ["current-awaiting-approval", "Awaiting approval"],
  ["current-paired", "Paired"],
  ["current-running", "Agent"],
  ["errored", "Blocked"],
  ["current-waiting", "Waiting for closeout"],
  ["upcoming", "To do"],
  ["completed", "Done"]
] as const;

function ticketOrder(left: SprintItemWorkspaceTicket, right: SprintItemWorkspaceTicket): number {
  return (
    Number(left.priority.slice(1)) - Number(right.priority.slice(1)) ||
    left.title.localeCompare(right.title, undefined, { sensitivity: "base" }) ||
    left.id.localeCompare(right.id)
  );
}

// Today and Remaining are the same structure. The only thing the split says is whether
// the Ticket is on the Day, so both sections group their own Tickets the same way and
// both carry a Done group.
function groupTickets(tickets: SprintItemWorkspaceTicket[]): WorkspaceTicketGroup[] {
  return groupOrder.flatMap(([key, label]) => {
    const grouped = tickets
      .filter((ticket) => sprintTicketCondition(ticket).mark === key)
      .sort(ticketOrder);
    return grouped.length ? [{ key, label, tickets: grouped }] : [];
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
  const done = tickets.filter((ticket) => ticket.stage === "done").length;
  return `${done} of ${tickets.length} done`;
}

export function failedWorkspaceDeliveries(
  workspace: SprintItemWorkspace
): SprintItemWorkspaceObligation[] {
  return workspace.obligations.filter(
    (obligation) => obligation.lifecycle === "failed" || obligation.last_error !== null
  );
}
