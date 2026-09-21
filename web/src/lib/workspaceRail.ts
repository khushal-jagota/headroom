import type {
  BoardCard,
  BoardSprintItem,
  Priority,
  SprintItemWorkspace,
  WorkAttention
} from "./types";
import {
  WORK_ITEM_ATTENTION_GROUP_KEYS,
  workItemActivityMark,
  workItemActivityMarkPresentation,
  workItemRollupActivityMark,
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
  item: WorkAttention & { ticket_rollup: WorkAttention }
): WorkspaceRowMark {
  return workItemRollupActivityMark(item, item.ticket_rollup);
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
  sprintItems: readonly BoardSprintItem[] = [],
  selectedWorkspace: SprintItemWorkspace | undefined = undefined
): WorkspaceRail {
  const itemCards = new Map<string, WorkItemTicketFacts[]>();
  const summaries = new Map<string, BoardSprintItem | SprintItemWorkspace>(
    sprintItems.map((item) => [item.id, item])
  );

  for (const card of cards) {
    if (!card.sprint_item_id) continue;
    const grouped = itemCards.get(card.sprint_item_id) ?? [];
    grouped.push(card);
    itemCards.set(card.sprint_item_id, grouped);
  }

  if (selectedWorkspace) {
    itemCards.set(selectedWorkspace.id, selectedWorkspace.tickets);
    summaries.set(selectedWorkspace.id, selectedWorkspace);
  }

  const items = [...itemCards.entries()].map(([id, groupedCards]) => {
    const first = cards.find((card) => card.sprint_item_id === id);
    const summary = summaries.get(id);
    const selected = selectedWorkspace?.id === id ? selectedWorkspace : undefined;
    return {
      id,
      title: selected?.title ?? first?.sprint_item_title ?? "Untitled Sprint Item",
      priority: selected?.priority ?? first?.sprint_item_priority ?? "P3",
      createdAt: summary?.created_at ?? 0,
      mark: summary ? workspaceSprintItemRowMark(summary) : null,
      groups: workspaceAttentionGroups(groupedCards),
      rested: groupedCards.every((groupedCard) => groupedCard.stage === "done")
    } satisfies WorkspaceRailItem;
  });

  items.sort(
    (left, right) =>
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.createdAt - right.createdAt ||
      left.id.localeCompare(right.id)
  );

  return { groups: workspaceGroups(cards), items };
}
