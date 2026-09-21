import { labelize, type FieldStageVisualState } from "./ui";
import type { BoardCard, BoardSprintItem, Priority } from "./types";
import { primaryWorkAttention } from "./workAttentionPresentation";

const ATTENTION_GROUP_ORDER = [
  "awaiting_approval",
  "awaiting_answer",
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
  "empty",
  "blocked",
  "done"
];

const DEFAULT_COLLAPSED_GROUPS: ReadonlySet<string> = new Set([
  "blocked",
  "done"
]);

// Each of the three says whose the work is, in the word the code already uses for it:
// `awaiting_approval` is a proposal whose ceiling the owner holds, and `assigned` is a
// stage whose ownership mode is `user`, meaning his own to do. A proposal parked on an
// agent is a different fact and keeps its own quiet heading, so no label here can mean
// two things depending on the screen it is read on.
const GROUP_LABELS: Readonly<Record<string, string>> = {
  awaiting_approval: "Needs your approval",
  awaiting_answer: "Needs your answer",
  assigned: "Yours",
  awaiting_reply: "Messages",
  status_awaiting_approval: "Awaiting an agent's approval",
  waiting_to_closeout: "Waiting on Consequences"
};

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  defaultCollapsed: boolean;
  cards: BoardCard[];
};

export type WorkspaceRowMark = "answer" | "attention" | "working" | null;

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
  // The server says who holds a parked proposal. This reads that fact rather than the
  // status, which says a proposal is parked and not whose it is.
  if (card.awaiting_agent_approval) return "status_awaiting_approval";
  return String(card.ticket_status);
}

// Each Ticket gets one group. A broken worker leads, then attention wins in the shared
// canonical order. Tickets without attention retain the status group that made them
// reachable before.
//
// Errored is read from `agent_state`, which is the status *or* a last turn that ended
// failed. The raw status alone cannot carry it: nothing in production writes
// `ticket_status = 'errored'`, so a rail that grouped on the status alone named no
// broken worker at all. The Sprint Item page reads the same fact in the same place.
export function workspaceCardGroupKey(card: BoardCard): string {
  if (!card.is_done && card.agent_state === "errored") return "errored";
  return primaryWorkAttention(card) ?? workspaceRemainderGroupKey(card);
}

export function workspaceTicketRowMark(card: BoardCard): WorkspaceRowMark {
  if (card.awaiting_answer) return "answer";
  if (card.awaiting_reply) return "attention";
  if (card.agent_state === "working") return "working";
  return null;
}

export function workspaceRowMarkPresentation(
  mark: WorkspaceRowMark
): WorkspaceRowMarkPresentation {
  if (mark === "answer") {
    return { state: "needs-me", ariaLabel: "Needs an answer" };
  }
  if (mark === "attention") {
    return { state: "current-awaiting-approval", ariaLabel: "Message" };
  }
  if (mark === "working") {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  return { state: "upcoming", ariaLabel: "Nothing waiting" };
}

export function workspaceSprintItemRowMark(item: BoardSprintItem): WorkspaceRowMark {
  if (item.awaiting_answer || item.ticket_rollup.awaiting_answer) return "answer";
  if (item.awaiting_reply || item.ticket_rollup.awaiting_reply) return "attention";
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
  keys: readonly string[],
  keyOf: (card: BoardCard) => string = workspaceCardGroupKey
): WorkspaceTicketGroup[] {
  const byGroup = new Map<string, BoardCard[]>();
  const firstSeen: string[] = [];
  for (const card of cards) {
    const key = keyOf(card);
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
//
// An Item is read at rest, under a title the reader has not clicked, so it holds to the
// three groups and nothing else. It therefore names a Ticket by the attention it filtered
// on, not by `workspaceCardGroupKey`, whose broken-worker branch comes first and would put
// a Ticket the reader must approve under a fourth heading, Errored. The Tickets view is
// the screen that leads with a broken worker, and it still does.
export function workspaceAttentionGroups(
  cards: readonly BoardCard[]
): WorkspaceTicketGroup[] {
  return groupCards(
    cards.filter((card) => primaryWorkAttention(card) !== null),
    ATTENTION_GROUP_ORDER,
    (card) => primaryWorkAttention(card) ?? workspaceCardGroupKey(card)
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
