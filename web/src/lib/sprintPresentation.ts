import type { FieldStageVisualState } from "./ui";
import type { Priority, ProjectSummary, SprintOutcomeGroup } from "./types";
import { primaryWorkAttention } from "./workAttentionPresentation";

export type SprintTicket = {
  id: string;
  title: string;
  stage: string;
  priority: Priority;
  ticket_status: string;
  waiting_to_closeout?: boolean;
  has_pending_proposal?: boolean;
  awaiting_reply: boolean;
  awaiting_approval: boolean;
  assigned: boolean;
  agent_state: "working" | "idle" | "errored";
};

export type SprintProjectGroup = {
  key: string;
  label: string;
  priority: Priority | null;
  outcomes: SprintOutcomeGroup[];
};

export type SprintTicketCondition = {
  mark: FieldStageVisualState;
  word: string;
};

// The three facts a Ticket's condition is read from. Sprint tickets, Sprint Item
// workspace tickets and Workspace board cards all carry them, so all three screens
// name a Ticket's condition the same way. A filed proposal is not one of them: a
// The shared projection is authoritative even while a proposal stays filed.
export type TicketConditionFacts = {
  stage: string;
  ticket_status: string;
  waiting_to_closeout?: boolean;
  awaiting_reply: boolean;
  awaiting_approval: boolean;
  assigned: boolean;
  agent_state: "working" | "idle" | "errored";
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

export function sprintTicketCondition(ticket: TicketConditionFacts): SprintTicketCondition {
  if (ticket.stage === "done") return { mark: "completed", word: "done" };
  // Two states, one mark, two words. Errored is a worker that broke; blocked is a
  // Ticket another Ticket holds. They read the same red, and never the same word.
  if (ticket.agent_state === "errored") return { mark: "errored", word: "errored" };
  if (ticket.ticket_status === "blocked") return { mark: "errored", word: "blocked" };
  const attention = primaryWorkAttention(ticket);
  if (attention === "awaiting_approval") {
    return { mark: "current-awaiting-approval", word: "to review" };
  }
  if (attention === "assigned") return { mark: "current-assigned", word: "assigned" };
  if (attention === "awaiting_reply") return { mark: "needs-me", word: "need you" };
  if (ticket.agent_state === "working") return { mark: "current-running", word: "working" };
  if (ticket.waiting_to_closeout) return { mark: "current-waiting", word: "waiting for closeout" };
  return { mark: "upcoming", word: "to do" };
}

function sortedTickets(tickets: SprintTicket[], blockedLast: boolean): SprintTicket[] {
  return [...tickets].sort((left, right) => {
    if (blockedLast) {
      // The mark, not the word: errored and blocked read as different words and both
      // belong at the end of the section.
      const blockedDifference =
        Number(sprintTicketCondition(left).mark === "errored") -
        Number(sprintTicketCondition(right).mark === "errored");
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

export function outcomeTicketProgress(item: SprintOutcomeGroup): string {
  const tickets = item.tickets;
  const done = tickets.filter((ticket) => ticket.stage === "done").length;
  return `${done}/${tickets.length}`;
}

function fallbackProjectRank(label: string): number {
  if (label.localeCompare("Vylo", undefined, { sensitivity: "base" }) === 0) return 0;
  if (label.localeCompare("Other", undefined, { sensitivity: "base" }) === 0) return 2;
  return 1;
}

export function sprintProjectGroups(
  items: SprintOutcomeGroup[],
  projects: ProjectSummary[]
): SprintProjectGroup[] {
  const projectsById = new Map(projects.map((project) => [project.id, project]));
  const grouped = new Map<string, SprintProjectGroup>();
  for (const item of items) {
    const outcome = item.outcome;
    const key = outcome.project_id;
    const project = projectsById.get(outcome.project_id);
    const label = outcome.project || project?.name || "Other";
    const group = grouped.get(key) || { key, label, priority: project?.priority || null, outcomes: [] };
    group.outcomes.push(item);
    grouped.set(key, group);
  }

  for (const group of grouped.values()) {
    group.outcomes.sort((left, right) =>
      rankPriority(left.outcome.priority) - rankPriority(right.outcome.priority) ||
      left.outcome.created_at - right.outcome.created_at ||
      left.outcome.id.localeCompare(right.outcome.id)
    );
  }

  return [...grouped.values()].sort((left, right) =>
    rankPriority(left.priority) - rankPriority(right.priority) ||
    fallbackProjectRank(left.label) - fallbackProjectRank(right.label) ||
    left.label.localeCompare(right.label, undefined, { sensitivity: "base" }) ||
    left.key.localeCompare(right.key)
  );
}
