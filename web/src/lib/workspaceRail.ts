import type { Lifecycle } from "./lifecycle";
import { workspaceChildMessageConversations } from "./workspaceMessageDiscovery";
import type {
  BoardCard,
  BoardSprintItem,
  Priority,
  WorkspaceMessageConversation,
} from "./types";
import {
  workItemTicketGroupKey,
  workItemTicketGroups,
  type WorkItemTicketFacts
} from "./workItemPresentation";

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  defaultCollapsed: boolean;
  cards: WorkItemTicketFacts[];
};

export type WorkspaceRailItem = {
  id: string;
  title: string;
  priority: Priority;
  createdAt: number;
  conversationSignal: BoardSprintItem["conversation_signal"];
  conversationId: string | null;
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
function priorityRank(priority: Priority): number {
  return PRIORITY_ORDER.indexOf(priority);
}

function railGroups(
  cards: readonly WorkItemTicketFacts[],
  railLabel = false
): WorkspaceTicketGroup[] {
  const groups = workItemTicketGroups(
    cards,
    undefined
  );
  return groups.map((group) => ({
    key: group.key,
    label: railLabel && group.key === "empty" ? "Other" : group.label,
    defaultCollapsed: group.quiet,
    cards: group.tickets
  }));
}

export function workspaceGroups(cards: readonly BoardCard[]): WorkspaceTicketGroup[] {
  return railGroups(cards.filter((card) => card.ticket_status !== "cancelled"));
}

export function railTicketEligible(card: WorkItemTicketFacts): boolean {
  return !card.is_backlogged && !card.blocked && card.ticket_status !== "blocked"
    && card.stage !== "done" && card.stage !== "cancelled" && card.ticket_status !== "cancelled";
}

/** Keep survivors in their presented order, then append arrivals and re-entries. */
export function stabilizeRailGroups(next: WorkspaceTicketGroup[], previous: WorkspaceTicketGroup[]): WorkspaceTicketGroup[] {
  return next.map((group) => {
    const byId = new Map(group.cards.map((card) => [card.id, card]));
    const survivors = (previous.find((old) => old.key === group.key)?.cards ?? [])
      .flatMap((card) => { const fresh = byId.get(card.id); byId.delete(card.id); return fresh ? [fresh] : []; });
    return {...group, cards: [...survivors, ...byId.values()]};
  });
}

export function stabilizeWorkspaceRail(next: WorkspaceRail, previous: WorkspaceRail): WorkspaceRail {
  const stabilize = (item: WorkspaceRailItem) => ({...item, groups: stabilizeRailGroups(item.groups,
    [...previous.items, ...previous.settledItems].find((old) => old.id === item.id)?.groups ?? [])});
  return {...next, chiefGroups: stabilizeRailGroups(next.chiefGroups, previous.chiefGroups),
    items: next.items.map(stabilize), settledItems: next.settledItems.map(stabilize)};
}

export function workspaceWorkerStageSentence(ticket: WorkItemTicketFacts, lifecycle: Lifecycle | null): string {
  const stage = lifecycle?.stageLabel[ticket.stage];
  if (!stage) return "";
  if (ticket.blocked || ticket.ticket_status === "blocked") return `Blocked at ${stage}.`;
  if (ticket.awaiting_approval) return `Awaiting your approval at ${stage}.`;
  if (ticket.awaiting_answer) return `Awaiting your answer at ${stage}.`;
  if (ticket.assigned) return `Awaiting you at ${stage}.`;
  if (ticket.agent_state === "errored") return `The last turn failed at ${stage}.`;
  if (ticket.awaiting_agent_approval) return `Awaiting an agent's approval at ${stage}.`;
  if (ticket.conversation_signal?.working) return `Currently doing ${stage}.`;
  return `At ${stage}.`;
}

export function workspaceAttentionGroups(
  cards: readonly WorkItemTicketFacts[]
): WorkspaceTicketGroup[] {
  return railGroups(cards.filter(railTicketEligible), true);
}

export function workspaceHoverGroups(cards: readonly WorkItemTicketFacts[]): WorkspaceTicketGroup[] {
  return railGroups(cards.filter(railTicketEligible), true);
}

/** Sidebar visibility stays separate from the durable unread conversation index. */
export function workspaceRailChildMessageConversations(
  rows: readonly WorkspaceMessageConversation[],
  parent: Pick<BoardSprintItem, "id" | "title" | "settled"> & { tickets: readonly WorkItemTicketFacts[] },
  cards: readonly BoardCard[] = []
): WorkspaceMessageConversation[] {
  const byTicket = new Map(parent.tickets.map((ticket) => [ticket.id, ticket]));
  for (const card of cards) byTicket.set(card.id, card);
  const visible = (id: string) => !byTicket.get(id)?.is_backlogged;
  return workspaceChildMessageConversations(
    rows,
    {...parent, tickets: parent.tickets.filter((ticket) => visible(ticket.id))},
    cards
  ).filter((row) => visible(row.id));
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
    conversationSignal: item.conversation_signal,
    conversationId: item.conversation_id,
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
