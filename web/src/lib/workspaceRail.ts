import type { Lifecycle } from "./lifecycle";
import type {
  BoardCard,
  BoardSprintItem,
  Priority,
  WorkAttention
} from "./types";
import {
  WORK_ITEM_ATTENTION_GROUP_KEYS,
  workItemActivityMark,
  workItemActivityMarkPresentation,
  workItemTicketGroupKey,
  workItemTicketGroups,
  type WorkItemActivityMark,
  type WorkItemActivityMarkPresentation,
  type WorkItemTicketFacts
} from "./workItemPresentation";

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  defaultCollapsed: boolean;
  cards: WorkItemTicketFacts[];
};

export type WorkspaceRowMark = WorkItemActivityMark;
export type WorkspaceRowMarkPresentation = WorkItemActivityMarkPresentation;

export type WorkspaceRailItem = {
  id: string;
  title: string;
  priority: Priority;
  createdAt: number;
  mark: WorkspaceRowMark;
  groups: WorkspaceTicketGroup[];
  rested: boolean;
  settled: boolean;
  project: string | null;
  projectId: string | null;
  tickets: WorkItemTicketFacts[];
  hoverGroups: WorkspaceTicketGroup[];
};

export type WorkspaceRail = {
  groups: WorkspaceTicketGroup[];
  items: WorkspaceRailItem[];
  settledItems: WorkspaceRailItem[];
  chiefGroups: WorkspaceTicketGroup[];
  chiefHoverGroups: WorkspaceTicketGroup[];
};

const PRIORITY_ORDER: readonly Priority[] = ["P0", "P1", "P2", "P3"];

export const workspaceCardGroupKey = workItemTicketGroupKey;
export const workspaceTicketRowMark = workItemActivityMark;
export const workspaceRowMarkPresentation = workItemActivityMarkPresentation;

export function workspaceSprintItemRowMark(
  item: WorkAttention
): WorkspaceRowMark {
  return workItemActivityMark(item);
}

function priorityRank(priority: Priority): number {
  return PRIORITY_ORDER.indexOf(priority);
}

function railGroups(
  cards: readonly WorkItemTicketFacts[],
  attentionOnly = false
): WorkspaceTicketGroup[] {
  const groups = workItemTicketGroups(
    cards,
    attentionOnly ? WORK_ITEM_ATTENTION_GROUP_KEYS : undefined
  );
  return groups.map((group) => ({
    key: group.key,
    label: group.label,
    defaultCollapsed: group.quiet,
    cards: group.tickets
  }));
}

export function workspaceGroups(cards: readonly BoardCard[]): WorkspaceTicketGroup[] {
  return railGroups(cards.filter((card) => card.ticket_status !== "cancelled"));
}

export function workspaceAttentionGroups(
  cards: readonly WorkItemTicketFacts[]
): WorkspaceTicketGroup[] {
  return railGroups(cards, true);
}

export function workspaceHoverGroups(cards: readonly WorkItemTicketFacts[]): WorkspaceTicketGroup[] {
  return railGroups(cards.filter((card) => card.stage !== "done" && card.ticket_status !== "cancelled"));
}

/** Only an unread reply marks a row. An answer request remains a separate group fact. */
export function workspaceWorkerRowMarks(ticket: WorkAttention): { reply: boolean; answer: boolean } {
  return { reply: ticket.awaiting_reply, answer: false };
}

export type WorkspaceWorkerStage = { id: string; label: string; state: "done" | "now" | "todo" };

export function workspaceWorkerStages(
  ticket: Pick<WorkItemTicketFacts, "stage">,
  lifecycle: Lifecycle | null
): WorkspaceWorkerStage[] {
  if (!lifecycle) return [];
  const current = lifecycle.stageOrder.indexOf(ticket.stage);
  return lifecycle.stageOrder.map((id, index) => ({
    id,
    label: lifecycle.stageLabel[id],
    state: current < 0 ? "todo" : index < current ? "done" : index === current ? "now" : "todo"
  }));
}

export function buildWorkspaceRail(
  cards: readonly BoardCard[],
  sprintItems: readonly BoardSprintItem[] = []
): WorkspaceRail {
  // Complete children come from each orchestrator, never the currently open pane.
  const items = sprintItems.map((item) => ({
    id: item.id,
    title: item.title,
    priority: item.priority,
    createdAt: item.created_at,
    mark: workspaceSprintItemRowMark(item),
    groups: workspaceAttentionGroups(item.tickets.filter((ticket) => ticket.ticket_status !== "cancelled")),
    hoverGroups: workspaceHoverGroups(item.tickets),
    tickets: item.tickets,
    settled: item.settled === true,
    rested: item.settled === true,
    project: item.project ?? null,
    projectId: item.project_id ?? null
  } satisfies WorkspaceRailItem));

  items.sort(
    (left, right) =>
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.createdAt - right.createdAt ||
      left.id.localeCompare(right.id)
  );
  const chiefTickets = cards.filter((card) =>
    card.orchestrator?.kind === "chief" ||
    (card.orchestrator == null && card.sprint_item_id == null)
  );
  return {
    // Legacy projection remains available to references outside the new rail.
    groups: workspaceGroups(cards),
    items: items.filter((item) => !item.settled),
    settledItems: items.filter((item) => item.settled),
    chiefGroups: workspaceAttentionGroups(chiefTickets.filter((ticket) => ticket.ticket_status !== "cancelled")),
    chiefHoverGroups: workspaceHoverGroups(chiefTickets)
  };
}
