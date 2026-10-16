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
};

export type WorkspaceRail = {
  groups: WorkspaceTicketGroup[];
  items: WorkspaceRailItem[];
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
  return railGroups(cards);
}

export function workspaceAttentionGroups(
  cards: readonly WorkItemTicketFacts[]
): WorkspaceTicketGroup[] {
  return railGroups(cards, true);
}

export function buildWorkspaceRail(
  cards: readonly BoardCard[],
  sprintItems: readonly BoardSprintItem[] = []
): WorkspaceRail {
  // The board owns rail membership; each listed Item supplies its complete Ticket set.
  // Selecting an Item only opens its pane and never changes this projection.
  const items = sprintItems.map((item) => ({
    id: item.id,
    title: item.title,
    priority: item.priority,
    createdAt: item.created_at,
    mark: workspaceSprintItemRowMark(item),
    groups: workspaceAttentionGroups(item.tickets),
    rested: item.tickets.every((ticket) => ticket.stage === "done")
  } satisfies WorkspaceRailItem));

  items.sort(
    (left, right) =>
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.createdAt - right.createdAt ||
      left.id.localeCompare(right.id)
  );

  return { groups: workspaceGroups(cards), items };
}
