import { previewHashHref, sprintItemFileTarget } from "./filePreview";
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
  const done = tickets.filter((ticket) => ticket.stage === "done").length;
  return tickets.length ? `${done}/${tickets.length} Tickets done` : "No Tickets";
}

export function workspaceTicketSprintLabel(ticket: SprintItemWorkspaceTicket): string {
  if (ticket.sprint_id === null) return "Backlog";
  return ticket.sprint_name || ticket.sprint_id;
}

export type WorkspaceArtifactRow = {
  path: string;
  label: string;
  kind: string;
  href: string | null;
};

// A row's href is null only when the path itself cannot resolve to a real managed file —
// it must never be an empty string, which renders as a dead anchor.
export function workspaceArtifactRows(workspace: SprintItemWorkspace): WorkspaceArtifactRow[] {
  return workspace.artifacts.map((path) => {
    const target = sprintItemFileTarget(workspace.id, path);
    const dot = path.lastIndexOf(".");
    return {
      path,
      label: path.split("/").at(-1) || path,
      kind: dot < 0 ? "file" : path.slice(dot + 1),
      href: target ? previewHashHref(target) : null
    };
  });
}
