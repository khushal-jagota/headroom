import { sprintTicketCondition } from "./sprintPresentation";
import type { BoardCard, BoardSprintItem, Priority } from "./types";

// One order of Ticket groups, with the quiet four hidden until the reader asks for
// them. The labels are the ones the Sprint screen and the Sprint Item page already use.
const GROUP_ORDER = [
  { key: "needs-me", label: "Needs user", hidden: false },
  { key: "waiting-for-kickoff", label: "Waiting for kickoff", hidden: false },
  { key: "awaiting-user-review", label: "User review", hidden: false },
  { key: "awaiting-agent-review", label: "Agent review", hidden: true },
  { key: "current-paired", label: "Paired", hidden: false },
  { key: "current-running", label: "Agent", hidden: true },
  { key: "errored", label: "Blocked", hidden: true },
  { key: "current-waiting", label: "Waiting for closeout", hidden: false },
  { key: "upcoming", label: "To do", hidden: false },
  { key: "completed", label: "Done", hidden: true }
] as const;

// The groups that hold work the user owns. They decide which Items lead the rail.
// Agent review is deliberately left out: it is the agent's own review, not the user's.
const USER_GROUP_KEYS = new Set([
  "needs-me",
  "waiting-for-kickoff",
  "awaiting-user-review",
  "current-paired"
]);

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  hidden: boolean;
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

// A card's own two facts win first: it is finished, or a blocker holds it. Then the
// shared awaiting-approval mark splits: a Ticket still gated on its kickoff is the one
// worth naming on its own, and the rest split by which review route they are parked on,
// so User review and Agent review land in their own groups.
export function workspaceCardGroupKey(card: BoardCard): string {
  if (card.is_done) return "completed";
  if (card.blocked) return "errored";
  const mark = sprintTicketCondition(card).mark;
  if (mark !== "current-awaiting-approval") return mark;
  if (card.gating_field === "kickoff") return "waiting-for-kickoff";
  return card.ticket_status === "awaiting_agent_review"
    ? "awaiting-agent-review"
    : "awaiting-user-review";
}

export function workspaceItemGroups(
  groups: readonly WorkspaceTicketGroup[],
  revealed: boolean
): WorkspaceTicketGroup[] {
  return groups.filter((group) => revealed || !group.hidden);
}

export function hiddenWorkspaceCardCount(groups: readonly WorkspaceTicketGroup[]): number {
  return groups.reduce(
    (total, group) => total + (group.hidden ? group.cards.length : 0),
    0
  );
}

export function workspaceGroupsHaveShownCards(
  groups: readonly WorkspaceTicketGroup[]
): boolean {
  return groups.some((group) => !group.hidden && group.cards.length > 0);
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
  return GROUP_ORDER.flatMap((group) => {
    const grouped = cards
      .filter((card) => workspaceCardGroupKey(card) === group.key)
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
