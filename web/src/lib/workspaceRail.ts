import type { ConversationSignals } from "./conversationSignalPresentation";
import { labelize } from "./ui";
import type { BoardCard, BoardSprintItem, Priority } from "./types";

// Presentation only: the top-to-bottom order of the status groups. A group with no
// Tickets is not drawn, and a status not named here appends as its own group after
// these, in the order first seen.
const GROUP_ORDER: readonly string[] = [
  "errored",
  "needs_user",
  "user",
  "paired",
  "agent",
  "waiting_to_closeout",
  "awaiting_approval",
  "waiting_for_kickoff",
  "empty",
  "blocked",
  "done"
];

// The groups the rail holds: the ones that want the reader, live or waiting on them.
// A group outside this set is not drawn shut in the rail — it is not in the rail at
// all, in either view and in the count line under a shut Sprint Item. The quiet
// states (Waiting to Closeout, Empty, Blocked, Done) are read on the Sprint Item
// page, which still lists every group. Everything the rail draws arrives open, so
// nothing hides behind a count.
const RAIL_GROUPS: ReadonlySet<string> = new Set([
  "errored",
  "needs_user",
  "user",
  "paired",
  "agent",
  "awaiting_approval",
  "waiting_for_kickoff"
]);

const GROUP_LABELS: Readonly<Record<string, string>> = {
  needs_user: "Needs you",
  waiting_to_closeout: "Waiting to Closeout",
  waiting_for_kickoff: "Waiting for Kickoff"
};

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  cards: BoardCard[];
};

export type WorkspaceRailItem = {
  id: string;
  title: string;
  priority: Priority;
  createdAt: number;
  signals: ConversationSignals;
  groups: WorkspaceTicketGroup[];
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
  if (card.ticket_status === "awaiting_approval" && card.gating_field === "kickoff") {
    return "waiting_for_kickoff";
  }
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
    if (!RAIL_GROUPS.has(key)) continue;
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
      // The Item's own supervisor, marked the way every other row is marked.
      signals: {
        conversation_id: summary?.conversation_id ?? null,
        needs_me: summary?.needs_me ?? false,
        agent_working: summary?.agent_working ?? false,
        latest_turn_ended_sequence: summary?.latest_turn_ended_sequence ?? 0
      },
      groups: workspaceGroups(groupedCards)
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
