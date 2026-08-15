import {
  TICKET_STATUS_GROUPS,
  ticketStatusGroupKey,
  type TicketStatusGroupDefinition
} from "./ticketStatusGroups";
import type { BoardCard, BoardSprintItem, Priority } from "./types";

// The groups that hold work the user owns. They decide which Items lead the rail.
const USER_GROUP_KEYS = new Set([
  "needs-me",
  "waiting-for-kickoff",
  "current-awaiting-approval",
  "current-paired"
]);

export type WorkspaceTicketGroup = TicketStatusGroupDefinition & {
  cards: BoardCard[];
};

export type WorkspaceRailItem = {
  id: string;
  title: string;
  priority: Priority;
  createdAt: number;
  project: string | null;
  progress: { done: number; total: number } | null;
  cards: BoardCard[];
  groups: WorkspaceTicketGroup[];
  needsUser: boolean;
};

export type WorkspaceRail = {
  items: WorkspaceRailItem[];
  noItemGroups: WorkspaceTicketGroup[];
};

const PRIORITY_ORDER: readonly Priority[] = ["P0", "P1", "P2", "P3"];

export function workspaceItemGroups(
  groups: readonly WorkspaceTicketGroup[],
  revealed: boolean
): WorkspaceTicketGroup[] {
  return groups.filter((group) => revealed || !group.quiet);
}

export function quietWorkspaceCardCount(groups: readonly WorkspaceTicketGroup[]): number {
  return groups.reduce((total, group) => total + (group.quiet ? group.cards.length : 0), 0);
}

export function workspaceGroupsHaveShownCards(
  groups: readonly WorkspaceTicketGroup[]
): boolean {
  return groups.some((group) => !group.quiet && group.cards.length > 0);
}

function priorityRank(priority: Priority): number {
  return PRIORITY_ORDER.indexOf(priority);
}

function cardOrder(left: BoardCard, right: BoardCard): number {
  return (
    priorityRank(left.priority) - priorityRank(right.priority) ||
    right.activity_at - left.activity_at ||
    left.id.localeCompare(right.id)
  );
}

function groupCards(cards: readonly BoardCard[]): WorkspaceTicketGroup[] {
  return TICKET_STATUS_GROUPS.flatMap((group) => {
    const grouped = cards
      .filter((card) => ticketStatusGroupKey(card) === group.key)
      .sort(cardOrder);
    return grouped.length ? [{ ...group, cards: grouped }] : [];
  });
}

export function buildWorkspaceRail(
  cards: readonly BoardCard[],
  sprintItems: readonly BoardSprintItem[] = []
): WorkspaceRail {
  const itemCards = new Map<string, BoardCard[]>();
  const noItemCards: BoardCard[] = [];
  const summaries = new Map(sprintItems.map((item) => [item.id, item]));

  for (const card of cards) {
    if (!card.sprint_item_id) {
      noItemCards.push(card);
      continue;
    }
    const grouped = itemCards.get(card.sprint_item_id) ?? [];
    grouped.push(card);
    itemCards.set(card.sprint_item_id, grouped);
  }

  const items = [...itemCards.entries()].map(([id, groupedCards]) => {
    const cardsInOrder = [...groupedCards].sort(cardOrder);
    const first = cardsInOrder[0];
    const summary = summaries.get(id);
    const groups = groupCards(cardsInOrder);
    return {
      id,
      title: first.sprint_item_title ?? "Untitled Sprint Item",
      priority: first.sprint_item_priority ?? "P3",
      createdAt: summary?.created_at ?? 0,
      project: summary?.project ?? null,
      progress: summary
        ? { done: summary.done_ticket_count, total: summary.total_ticket_count }
        : null,
      cards: cardsInOrder,
      groups,
      needsUser: groups.some(
        (group) => USER_GROUP_KEYS.has(group.key) && group.cards.length > 0
      )
    } satisfies WorkspaceRailItem;
  });

  items.sort(
    (left, right) =>
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.createdAt - right.createdAt ||
      left.id.localeCompare(right.id)
  );

  return { items, noItemGroups: groupCards(noItemCards) };
}
