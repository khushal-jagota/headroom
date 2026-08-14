import type { FieldStageVisualState } from "./ui";
import type { Priority, ProjectSummary } from "./types";

export type SprintTicket = {
  id: string;
  title: string;
  stage: string;
  priority: Priority;
  ticket_status: string;
  has_pending_proposal?: boolean;
};

export type SprintItem = {
  id: string;
  title: string;
  body?: string | null;
  priority: Priority;
  deadline?: string | null;
  project_id?: string | null;
  project?: string | null;
  kind: string;
  status: string;
  tickets?: SprintTicket[];
};

export type SprintProjectGroup = {
  key: string;
  label: string;
  priority: Priority | null;
  items: SprintItem[];
};

export type SprintTicketCondition = {
  mark: FieldStageVisualState;
  word: string;
};

// The three facts a Ticket's condition is read from. Sprint tickets, Sprint Item
// workspace tickets and Workspace board cards all carry them, so all three screens
// name a Ticket's condition the same way.
export type TicketConditionFacts = {
  stage: string;
  ticket_status: string;
  has_pending_proposal?: boolean;
};

export type SprintTicketSections = {
  today: SprintTicket[];
  later: SprintTicket[];
  done: SprintTicket[];
};

export type SprintDateRange = {
  date_start: string;
  date_end: string;
};

const millisecondsPerDay = 86_400_000;

function isoCalendarDay(isoDate: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const timestamp = Date.UTC(year, month - 1, day);
  const parsed = new Date(timestamp);
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) return null;
  return timestamp / millisecondsPerDay;
}

export function sprintDayLabel(sprint: SprintDateRange, planningDate: string): string {
  const start = isoCalendarDay(sprint.date_start);
  const end = isoCalendarDay(sprint.date_end);
  const current = isoCalendarDay(planningDate);
  if (start === null || end === null || current === null || end < start) return "";
  const total = end - start + 1;
  const day = current - start + 1;
  if (day < 1 || day > total) return "";
  return `day ${day} of ${total}`;
}

const priorityRank = new Map<Priority, number>([
  ["P0", 0],
  ["P1", 1],
  ["P2", 2],
  ["P3", 3]
]);

function rankPriority(priority: Priority | null | undefined): number {
  return priority ? (priorityRank.get(priority) ?? priorityRank.size) : priorityRank.size;
}

export function sprintItems(groups: Record<string, SprintItem[]>): SprintItem[] {
  return Object.values(groups || {}).flat();
}

export function sprintTicketCondition(ticket: TicketConditionFacts): SprintTicketCondition {
  if (ticket.stage === "done") return { mark: "completed", word: "done" };
  if (ticket.ticket_status === "blocked" || ticket.ticket_status === "errored") {
    return { mark: "errored", word: "blocked" };
  }
  if (
    ticket.has_pending_proposal ||
    ticket.ticket_status === "awaiting_agent_review" ||
    ticket.ticket_status === "awaiting_user_review"
  ) {
    return { mark: "current-awaiting-approval", word: "to review" };
  }
  if (ticket.ticket_status === "needs_user") return { mark: "needs-me", word: "need you" };
  if (ticket.ticket_status === "user") return { mark: "needs-me", word: "yours" };
  if (ticket.ticket_status === "agent") return { mark: "current-running", word: "working" };
  if (ticket.ticket_status === "paired") return { mark: "current-paired", word: "paired" };
  return { mark: "upcoming", word: "to do" };
}

function sortedTickets(tickets: SprintTicket[], blockedLast: boolean): SprintTicket[] {
  return [...tickets].sort((left, right) => {
    if (blockedLast) {
      const blockedDifference =
        Number(sprintTicketCondition(left).word === "blocked") -
        Number(sprintTicketCondition(right).word === "blocked");
      if (blockedDifference !== 0) return blockedDifference;
    }
    return rankPriority(left.priority) - rankPriority(right.priority) ||
      left.title.localeCompare(right.title, undefined, { sensitivity: "base" }) ||
      left.id.localeCompare(right.id);
  });
}

export function sprintTicketSectionsForTickets(
  tickets: SprintTicket[],
  todayTicketIds: ReadonlySet<string>
): SprintTicketSections {
  const today: SprintTicket[] = [];
  const later: SprintTicket[] = [];
  const done: SprintTicket[] = [];
  for (const ticket of tickets) {
    if (ticket.stage === "dropped") continue;
    if (ticket.stage === "done") done.push(ticket);
    else if (todayTicketIds.has(ticket.id)) today.push(ticket);
    else later.push(ticket);
  }
  return {
    today: sortedTickets(today, true),
    later: sortedTickets(later, false),
    done: sortedTickets(done, false)
  };
}

export function sprintItemRollup(item: SprintItem): string {
  const tickets = (item.tickets || []).filter((ticket) => ticket.stage !== "dropped");
  const done = tickets.filter((ticket) => ticket.stage === "done").length;
  if (tickets.length === 0 || done === 0) return "to do";
  if (done === tickets.length) return "done";
  return `${done}/${tickets.length}`;
}

export function sprintItemIsDone(item: SprintItem): boolean {
  const tickets = (item.tickets || []).filter((ticket) => ticket.stage !== "dropped");
  return tickets.length > 0 && tickets.every((ticket) => ticket.stage === "done");
}

function fallbackProjectRank(label: string): number {
  if (label.localeCompare("Vylo", undefined, { sensitivity: "base" }) === 0) return 0;
  if (label.localeCompare("Other", undefined, { sensitivity: "base" }) === 0) return 2;
  return 1;
}

export function sprintProjectGroups(
  items: SprintItem[],
  projects: ProjectSummary[]
): SprintProjectGroup[] {
  const projectsById = new Map(projects.map((project) => [project.id, project]));
  const grouped = new Map<string, SprintProjectGroup>();
  for (const item of items) {
    const key = item.project_id || "__other__";
    const project = item.project_id ? projectsById.get(item.project_id) : undefined;
    const label = item.project || project?.name || "Other";
    const group = grouped.get(key) || { key, label, priority: project?.priority || null, items: [] };
    group.items.push(item);
    grouped.set(key, group);
  }

  for (const group of grouped.values()) {
    group.items.sort((left, right) =>
      Number(sprintItemIsDone(left)) - Number(sprintItemIsDone(right)) ||
      rankPriority(left.priority) - rankPriority(right.priority) ||
      left.title.localeCompare(right.title, undefined, { sensitivity: "base" }) ||
      left.id.localeCompare(right.id)
    );
  }

  return [...grouped.values()].sort((left, right) =>
    rankPriority(left.priority) - rankPriority(right.priority) ||
    fallbackProjectRank(left.label) - fallbackProjectRank(right.label) ||
    left.label.localeCompare(right.label, undefined, { sensitivity: "base" }) ||
    left.key.localeCompare(right.key)
  );
}
