import type { ConversationSignals } from "./conversationSignalPresentation";
import { labelize } from "./ui";
import type { BoardCard, BoardSprintItem, Priority } from "./types";
import { primaryWorkAttention } from "./workAttentionPresentation";

// Presentation only: the top-to-bottom order of the status groups. A group with no
// Tickets is not drawn, and a status not named here appends as its own group after
// these, in the order first seen.
const GROUP_ORDER: readonly string[] = [
  "errored",
  "awaiting_reply",
  "assigned",
  "agent",
  "waiting_to_closeout",
  "awaiting_approval",
  "waiting_for_kickoff",
  "empty",
  "blocked",
  "done"
];

// The three the reader opens for themselves. They are still their own group: nothing
// hides behind a count. The rail carries every group a Ticket lands in — a quiet group
// arrives shut, never absent — so no status is ever unreachable from the rail.
const DEFAULT_COLLAPSED_GROUPS: ReadonlySet<string> = new Set([
  "waiting_for_kickoff",
  "blocked",
  "done"
]);

const GROUP_LABELS: Readonly<Record<string, string>> = {
  awaiting_reply: "Needs you",
  assigned: "Assigned",
  waiting_to_closeout: "Waiting to Closeout",
  waiting_for_kickoff: "Waiting for Kickoff"
};

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  defaultCollapsed: boolean;
  cards: BoardCard[];
};

export type WorkspaceRailItem = {
  id: string;
  title: string;
  priority: Priority;
  createdAt: number;
  signals: ConversationSignals;
  groups: WorkspaceTicketGroup[];
  // Every one of the Item's Tickets on today is done. Read from all of the Item's
  // Tickets, so a Blocked, Empty, or Waiting to Closeout Ticket keeps the Item awake.
  rested: boolean;
};

// The two views over one board: every Ticket in its status group, and every Sprint
// Item with the same groups over its own Tickets.
export type WorkspaceRail = {
  groups: WorkspaceTicketGroup[];
  items: WorkspaceRailItem[];
};

const PRIORITY_ORDER: readonly Priority[] = ["P0", "P1", "P2", "P3"];

// Every Ticket sits in exactly one group: Done wins, a resting Closeout Ticket that the
// server says is runnable gets its server-projected semantic exception, and a Kickoff
// approval uses the existing gating field to split from later approvals. Every other
// Ticket uses its own status.
export function workspaceCardGroupKey(card: BoardCard): string {
  if (card.is_done) return "done";
  if (card.waiting_to_closeout) return "waiting_to_closeout";
  const attention = primaryWorkAttention(card);
  if (attention === "awaiting_approval" && card.gating_field === "kickoff") {
    return "waiting_for_kickoff";
  }
  if (attention !== null) return attention;
  return String(card.ticket_status);
}

function priorityRank(priority: Priority): number {
  return PRIORITY_ORDER.indexOf(priority);
}

// Newest activity first. The rail answers "what moved", so nothing else orders a row.
function cardOrder(left: BoardCard, right: BoardCard): number {
  return right.activity_at - left.activity_at || left.id.localeCompare(right.id);
}

export function workspaceGroups(
  cards: readonly BoardCard[]
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
  const orderedKeys = GROUP_ORDER.filter((key) => byGroup.has(key)).concat(
    firstSeen.filter((key) => !GROUP_ORDER.includes(key))
  );
  return orderedKeys.map((key) => ({
    key,
    label: GROUP_LABELS[key] ?? labelize(key),
    defaultCollapsed: DEFAULT_COLLAPSED_GROUPS.has(key),
    cards: [...(byGroup.get(key) ?? [])].sort(cardOrder)
  }));
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
      // The Item's own conversation, read exactly as a card's is. Its reply only ever
      // follows a message of the reader's, because nothing else starts one.
      signals: {
        awaiting_reply: Boolean(
          summary?.awaiting_reply || summary?.ticket_rollup?.awaiting_reply
        ),
        agent_state:
          summary?.agent_state === "working" || summary?.ticket_rollup?.agent_state === "working"
            ? "working"
            : summary?.agent_state === "errored" || summary?.ticket_rollup?.agent_state === "errored"
              ? "errored"
              : "idle"
      },
      groups: workspaceGroups(groupedCards),
      rested: groupedCards.every((groupedCard) => groupedCard.is_done)
    } satisfies WorkspaceRailItem;
  });

  // Priority, then age. An Item holds its place while its Tickets move under it.
  items.sort(
    (left, right) =>
      priorityRank(left.priority) - priorityRank(right.priority) ||
      left.createdAt - right.createdAt ||
      left.id.localeCompare(right.id)
  );

  return { groups: workspaceGroups(cards), items };
}
