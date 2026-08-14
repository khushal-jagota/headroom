import { previewHashHref, sprintItemFileTarget } from "./filePreview";
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
  ["awaiting-user-review", "User review"],
  ["awaiting-agent-review", "Agent review"],
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

// The shared awaiting-approval mark covers both review routes; split it by which one a
// Ticket is actually parked on so each gets its own header.
function ticketGroupKey(ticket: SprintItemWorkspaceTicket): string {
  const mark = sprintTicketCondition(ticket).mark;
  if (mark !== "current-awaiting-approval") return mark;
  return ticket.ticket_status === "awaiting_agent_review"
    ? "awaiting-agent-review"
    : "awaiting-user-review";
}

// Today and Remaining are the same structure. The only thing the split says is whether
// the Ticket is on the Day, so both sections group their own Tickets the same way and
// both carry a Done group.
function groupTickets(tickets: SprintItemWorkspaceTicket[]): WorkspaceTicketGroup[] {
  return groupOrder.flatMap(([key, label]) => {
    const grouped = tickets
      .filter((ticket) => ticketGroupKey(ticket) === key)
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
