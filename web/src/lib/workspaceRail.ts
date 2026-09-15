import { labelize, type FieldStageVisualState } from "./ui";
import type { BoardCard, BoardSprintItem, Priority, WorkAttention } from "./types";
import { primaryWorkAttention } from "./workAttentionPresentation";

const ATTENTION_GROUP_ORDER = [
  "awaiting_approval",
  "assigned",
  "awaiting_reply"
] as const;

// Tickets outside the three attention groups keep the status order the Workspace
// already used. Unknown statuses still follow these in the order first seen.
const REMAINDER_GROUP_ORDER: readonly string[] = [
  "errored",
  "agent",
  "waiting_to_closeout",
  "status_awaiting_approval",
  "waiting_for_kickoff",
  "empty",
  "blocked",
  "done"
];

const DEFAULT_COLLAPSED_GROUPS: ReadonlySet<string> = new Set([
  "waiting_for_kickoff",
  "blocked",
  "done"
]);

const GROUP_LABELS: Readonly<Record<string, string>> = {
  awaiting_approval: "Awaiting approval",
  assigned: "Paired",
  awaiting_reply: "Messages",
  status_awaiting_approval: "Awaiting approval",
  waiting_to_closeout: "Waiting to Closeout",
  waiting_for_kickoff: "Waiting for Kickoff"
};

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  defaultCollapsed: boolean;
  cards: BoardCard[];
};

export type WorkspaceRowMark = "attention" | "working" | null;

export type WorkspaceRowMarkPresentation = {
  state: FieldStageVisualState;
  ariaLabel: string;
};

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

function workspaceRemainderGroupKey(card: BoardCard): string {
  if (card.is_done) return "done";
  if (card.waiting_to_closeout) return "waiting_to_closeout";
  if (card.ticket_status === "awaiting_approval") return "status_awaiting_approval";
  return String(card.ticket_status);
}

// Each Ticket gets one group. Attention wins in the shared canonical order. Tickets
// without attention retain the status group that made them reachable before.
export function workspaceCardGroupKey(card: BoardCard): string {
  return primaryWorkAttention(card) ?? workspaceRemainderGroupKey(card);
}

export function workspaceTicketRowMark(card: BoardCard): WorkspaceRowMark {
  if (card.awaiting_reply) return "attention";
  if (card.agent_state === "working") return "working";
  return null;
}

export function workspaceRowMarkPresentation(
  mark: WorkspaceRowMark,
  attentionLabel: "Message" | "Needs you"
): WorkspaceRowMarkPresentation {
  if (mark === "attention") {
    return { state: "current-awaiting-approval", ariaLabel: attentionLabel };
  }
  if (mark === "working") {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  return { state: "upcoming", ariaLabel: "Nothing waiting" };
}

function hasAttention(facts: WorkAttention | BoardSprintItem): boolean {
  return primaryWorkAttention(facts) !== null;
}

export function workspaceSprintItemRowMark(item: BoardSprintItem): WorkspaceRowMark {
  if (hasAttention(item) || hasAttention(item.ticket_rollup)) return "attention";
  if (item.agent_state === "working" || item.ticket_rollup.agent_state === "working") {
    return "working";
  }
  return null;
}

function priorityRank(priority: Priority): number {
  return PRIORITY_ORDER.indexOf(priority);
}

function cardOrder(left: BoardCard, right: BoardCard): number {
  return right.activity_at - left.activity_at || left.id.localeCompare(right.id);
}

function groupCards(
  cards: readonly BoardCard[],
  keys: readonly string[]
): WorkspaceTicketGroup[] {
  const byGroup = new Map<string, BoardCard[]>();
  const firstSeen: string[] = [];
  for (const card of cards) {
    const key = workspaceCardGroupKey(card);
    if (!byGroup.has(key)) {
      byGroup.set(key, []);
      firstSeen.push(key);
    }
    byGroup.get(key)?.push(card);
  }
  const orderedKeys = keys.filter((key) => byGroup.has(key)).concat(
    firstSeen.filter((key) => !keys.includes(key))
  );
  return orderedKeys.map((key) => ({
    key,
    label: GROUP_LABELS[key] ?? labelize(key),
    defaultCollapsed: DEFAULT_COLLAPSED_GROUPS.has(key),
    cards: [...(byGroup.get(key) ?? [])].sort(cardOrder)
  }));
}

// The Tickets view starts with attention, then preserves every remaining status group.
export function workspaceGroups(cards: readonly BoardCard[]): WorkspaceTicketGroup[] {
  return groupCards(cards, [...ATTENTION_GROUP_ORDER, ...REMAINDER_GROUP_ORDER]);
}

// An Item exposes only work that needs the owner. Quiet child Tickets remain available
// from the Tickets view and from the Item workspace.
export function workspaceAttentionGroups(
  cards: readonly BoardCard[]
): WorkspaceTicketGroup[] {
  return groupCards(
    cards.filter((card) => primaryWorkAttention(card) !== null),
    ATTENTION_GROUP_ORDER
  );
}

export function buildWorkspaceRail(
  cards: readonly BoardCard[],
  sprintItems: readonly BoardSprintItem[] = []
): WorkspaceRail {
  const itemCards = new Map<string, BoardCard[]>();
  const summaries = new Map(sprintItems.map((item) => [item.id, item]));

  for (const card of cards) {
    if (!card.sprint_item_id) continue;
    const grouped = itemCards.get(card.sprint_item_id) ?? [];
    grouped.push(card);
    itemCards.set(card.sprint_item_id, grouped);
  }

  const items = [...itemCards.entries()].map(([id, groupedCards]) => {
    const first = groupedCards[0];
    const summary = summaries.get(id);
    return {
      id,
      title: first.sprint_item_title ?? "Untitled Sprint Item",
      priority: first.sprint_item_priority ?? "P3",
      createdAt: summary?.created_at ?? 0,
      mark: summary ? workspaceSprintItemRowMark(summary) : null,
      groups: workspaceAttentionGroups(groupedCards),
      rested: groupedCards.every((groupedCard) => groupedCard.is_done)
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
