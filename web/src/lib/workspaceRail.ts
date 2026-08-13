import type { BoardCard, Priority } from "./types";

export type WorkspaceRailMode = "attention" | "all";

export type WorkspaceRailItem = {
  id: string;
  title: string;
  priority: Priority;
  cards: BoardCard[];
  attentionCards: BoardCard[];
  liveCards: BoardCard[];
};

export type WorkspaceRail = {
  items: WorkspaceRailItem[];
  noItemCards: BoardCard[];
};

export type WorkspaceItemDisclosure = {
  expanded?: boolean;
  opened?: boolean;
  folded?: boolean;
};

const ATTENTION_STATUSES = new Set([
  "needs_user",
  "awaiting_approval",
  "awaiting_user_review",
  "paired",
  "user"
]);

const PRIORITY_ORDER: readonly Priority[] = ["P0", "P1", "P2", "P3"];

export function workspaceCardNeedsUser(card: BoardCard): boolean {
  return !card.is_done && !card.blocked && ATTENTION_STATUSES.has(card.ticket_status);
}

export function workspaceLiveCards(cards: readonly BoardCard[]): BoardCard[] {
  return cards.filter((card) => !card.is_done);
}

export function workspaceVisibleCards(
  cards: readonly BoardCard[],
  mode: WorkspaceRailMode,
  expanded = false
): BoardCard[] {
  const liveCards = workspaceLiveCards(cards);
  if (mode === "all" || expanded) return liveCards;
  return liveCards.filter(workspaceCardNeedsUser);
}

export function hiddenWorkspaceCardCount(
  item: WorkspaceRailItem,
  mode: WorkspaceRailMode,
  expanded = false
): number {
  return item.liveCards.length - workspaceVisibleCards(item.cards, mode, expanded).length;
}

export function workspaceItemIsOpen(
  item: WorkspaceRailItem,
  mode: WorkspaceRailMode,
  disclosure: WorkspaceItemDisclosure = {}
): boolean {
  if (item.liveCards.length === 0 || disclosure.folded) return false;
  return (
    mode === "all" ||
    Boolean(disclosure.opened) ||
    Boolean(disclosure.expanded) ||
    item.attentionCards.length > 0
  );
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

export function buildWorkspaceRail(cards: readonly BoardCard[]): WorkspaceRail {
  const itemCards = new Map<string, BoardCard[]>();
  const noItemCards: BoardCard[] = [];

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
    return {
      id,
      title: first.sprint_item_title ?? "Untitled Sprint Item",
      priority: first.sprint_item_priority ?? "P3",
      cards: cardsInOrder,
      attentionCards: cardsInOrder.filter(workspaceCardNeedsUser),
      liveCards: workspaceLiveCards(cardsInOrder)
    } satisfies WorkspaceRailItem;
  });

  items.sort(
    (left, right) =>
      Number(right.attentionCards.length > 0) - Number(left.attentionCards.length > 0) ||
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.title.localeCompare(right.title, undefined, { sensitivity: "base" }) ||
      left.id.localeCompare(right.id)
  );

  return { items, noItemCards: noItemCards.sort(cardOrder) };
}
