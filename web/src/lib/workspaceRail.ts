import type { Lifecycle } from "./lifecycle";
import { workspaceChildMessageConversations } from "./workspaceMessageDiscovery";
import type {
  BoardCard,
  BoardSprintItem,
  WorkspaceMessageConversation,
} from "./types";
import {
  workItemTicketGroupKey,
  workItemTicketGroups,
  type WorkItemAttentionFacts,
  type WorkItemTicketFacts
} from "./workItemPresentation";


/** Only fields the workspace presentation reads, independent of detail/config responses. */
export type WorkspaceTicket = WorkItemTicketFacts & Pick<BoardCard, 'orchestrator' | 'sprint_item_id' | 'sprint_item_title'>;
export type WorkspaceOrchestrator = Pick<BoardSprintItem,
  'id' | 'title' | 'created_at' | 'settled' | 'project_id' | 'project' | 'recap_preview' | 'conversation_id'>
  & WorkItemAttentionFacts & {conversation_signal?: Partial<import('./conversationSignal').ConversationSignal>; tickets: readonly WorkItemTicketFacts[]};
export type WorkspaceChief = Pick<import('./types').ChiefManagementSettings,'label' | 'conversation_id'>
  & {awaiting_reply:boolean | null;conversation_signal?: Partial<import('./conversationSignal').ConversationSignal>};

export type WorkspaceTicketGroup = {
  key: string;
  label: string;
  defaultCollapsed: boolean;
  cards: WorkItemTicketFacts[];
};

export type WorkspaceRailItem = {
  id: string;
  title: string;
  createdAt: number;
  conversationSignal: WorkspaceOrchestrator["conversation_signal"];
  conversationId: string | null;
  groups: WorkspaceTicketGroup[];
  rested: boolean;
  settled: boolean;
  project: string | null;
  projectId: string | null;
  tickets: WorkItemTicketFacts[];
  recapPreview: string;
};

export type WorkspaceRail = {
  groups: WorkspaceTicketGroup[];
  items: WorkspaceRailItem[];
  settledItems: WorkspaceRailItem[];
  chiefGroups: WorkspaceTicketGroup[];
};

export const workspaceCardGroupKey = workItemTicketGroupKey;
function railGroups(
  cards: readonly WorkItemTicketFacts[],
  sidebarPresentation = false
): WorkspaceTicketGroup[] {
  // Unread stays an independent mark in the rail, never a category or tone.
  const originals = new Map(cards.map((card) => [card.id, card]));
  const groups = workItemTicketGroups(
    sidebarPresentation ? cards.map((card) => ({...card, awaiting_reply: false})) : cards
  );
  return groups.map((group) => ({
    key: group.key,
    label: sidebarPresentation && group.key === "empty" ? "Other" : group.label,
    defaultCollapsed: group.quiet,
    cards: group.tickets.map((card) => originals.get(card.id)!)
  }));
}

export function workspaceGroups(cards: readonly WorkspaceTicket[]): WorkspaceTicketGroup[] {
  return railGroups(cards);
}

export function railTicketEligible(card: WorkItemTicketFacts): boolean {
  return !card.is_backlogged && !card.blocked && card.ticket_status !== "blocked"
    && card.stage !== "done";
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

/** Refresh exact conversation facts without moving the currently presented rows. */
export function refreshWorkspaceRailSignals(current: WorkspaceRail, next: WorkspaceRail): WorkspaceRail {
  const items = [...next.items, ...next.settledItems];
  const tickets = new Map([...next.groups.flatMap((group) => group.cards),
    ...items.flatMap((item) => item.tickets)].map((ticket) => [ticket.id, ticket]));
  const heads = new Map(items.map((item) => [item.id, item]));
  const refreshTicket = (ticket: WorkItemTicketFacts): WorkItemTicketFacts => {
    const fresh = tickets.get(ticket.id);
    return fresh ? {...ticket, conversation_signal: fresh.conversation_signal,
      conversation_id: fresh.conversation_id} : {...ticket,conversation_signal:undefined};
  };
  const refreshGroups = (groups: WorkspaceTicketGroup[]) => groups.map((group) =>
    ({...group, cards: group.cards.map(refreshTicket)}));
  const refreshItems = (rows: WorkspaceRailItem[]) => rows.map((item) => {
    const fresh = heads.get(item.id);
    return {...item, ...(fresh ? {conversationSignal: fresh.conversationSignal,
      conversationId: fresh.conversationId} : {conversationSignal:undefined}), groups: refreshGroups(item.groups),
      tickets: item.tickets.map(refreshTicket)};
  });
  return {...current, groups: refreshGroups(current.groups), chiefGroups: refreshGroups(current.chiefGroups),
    items: refreshItems(current.items), settledItems: refreshItems(current.settledItems)};
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

/** Use the existing concise recap; missing data gets only a factual list count. */
export function workspaceListRecap(groups: readonly WorkspaceTicketGroup[], recap = ""): string {
  if (recap.trim()) return recap;
  const count = groups.reduce((total, group) => total + group.cards.length, 0);
  return count ? `${count} Ticket${count === 1 ? "" : "s"} in this list.` : "Nothing in this list right now.";
}

/** Sidebar visibility stays separate from the durable unread conversation index. */
export function workspaceRailChildMessageConversations(
  rows: readonly WorkspaceMessageConversation[],
  parent: Pick<WorkspaceOrchestrator, "id" | "title" | "settled"> & { tickets: readonly WorkItemTicketFacts[] },
  cards: readonly WorkspaceTicket[] = []
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
  cards: readonly WorkspaceTicket[],
  sprintItems: readonly WorkspaceOrchestrator[] = []
): WorkspaceRail {
  // Complete children come from each orchestrator, never the currently open pane.
  const items = sprintItems.map((item) => ({
    id: item.id,
    title: item.title,
    createdAt: item.created_at,
    conversationSignal: item.conversation_signal,
    conversationId: item.conversation_id,
    groups: workspaceAttentionGroups(item.tickets),
    recapPreview: item.recap_preview,
    tickets: [...item.tickets],
    settled: item.settled === true,
    rested: item.settled === true,
    project: item.project ?? null,
    projectId: item.project_id ?? null
  } satisfies WorkspaceRailItem));

  items.sort(
    (left, right) =>
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
    chiefGroups: workspaceAttentionGroups(chiefTickets)
  };
}
