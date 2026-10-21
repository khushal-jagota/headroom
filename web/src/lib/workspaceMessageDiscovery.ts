import type { WorkspaceMessageConversation } from "./types";
import type { WorkspaceTicket, WorkspaceManager, WorkspaceChief } from "./workspaceRail";
import type { WorkItemTicketFacts } from "./workItemPresentation";

/** Count conversations, not rows or messages. An ask without an unread reply adds none. */
export function workspaceUnreadConversationCount(
  tickets: readonly (Pick<WorkItemTicketFacts, "awaiting_reply"> & { conversation_id?: string | null })[]
): number {
  return new Set(tickets.filter((ticket) => ticket.awaiting_reply && ticket.conversation_id)
    .map((ticket) => ticket.conversation_id)).size;
}

export function workspaceSettledUnreadCount(
  rows: readonly WorkspaceMessageConversation[],
  items: readonly WorkspaceManager[] = []
): number {
  // One conversation can have more than one served reference. Its primary row need
  // not belong to the settled parent, so count membership before discarding aliases.
  const settledIds = new Set<string>();
  for (const item of items) {
    if (!item.settled) continue;
    if (item.conversation_id) settledIds.add(item.conversation_id);
    for (const ticket of item.tickets) {
      if (ticket.conversation_id) settledIds.add(ticket.conversation_id);
    }
  }
  return new Set(rows.filter((row) => row.settled || settledIds.has(row.conversationId))
    .map((row) => row.conversationId)).size;
}

export type WorkspaceMessageIndex = {
  readonly unreadConversationIds:ReadonlySet<string>;
  readonly ticketsById:ReadonlyMap<string,WorkspaceTicket>;
  readonly firstMessageByTicket:ReadonlyMap<string,WorkspaceMessageConversation>;
  readonly messagesByParent:ReadonlyMap<string,readonly WorkspaceMessageConversation[]>;
};

/** Build once for an effective publication, then reuse for every visible parent. */
export function workspaceMessageIndex(rows:readonly WorkspaceMessageConversation[],cards:readonly WorkspaceTicket[],
  ticketsById:ReadonlyMap<string,WorkspaceTicket>=new Map(cards.map(card=>[card.id,card]))):WorkspaceMessageIndex {
  const unreadConversationIds=new Set<string>();
  const firstMessageByTicket=new Map<string,WorkspaceMessageConversation>();
  const messagesByParent=new Map<string,WorkspaceMessageConversation[]>();
  for(const row of rows) {
    unreadConversationIds.add(row.conversationId);
    if(row.kind!=='ticket')continue;
    if(!firstMessageByTicket.has(row.id))firstMessageByTicket.set(row.id,row);
    if(row.parentId!==null && row.parentId!==undefined) {
      const siblings=messagesByParent.get(row.parentId) ?? [];
      siblings.push(row);messagesByParent.set(row.parentId,siblings);
    }
  }
  return {unreadConversationIds,ticketsById,firstMessageByTicket,messagesByParent};
}

/** Retain each parent's real child access before deduplicating within that parent. */
export function workspaceChildMessageConversations(
  rows: readonly WorkspaceMessageConversation[],
  parent: Pick<WorkspaceManager, "id" | "title" | "settled"> & {tickets: readonly WorkItemTicketFacts[]},
  cards: readonly WorkspaceTicket[] = [],
  index:WorkspaceMessageIndex=workspaceMessageIndex(rows,cards)
): WorkspaceMessageConversation[] {
  const children = new Map<string,WorkspaceMessageConversation>();
  for (const ticket of parent.tickets) {
    const canonical = index.ticketsById.get(ticket.id);
    // The canonical same-Ticket address and read fact win over a stale child copy.
    if (canonical && (!canonical.awaiting_reply || ticketParentId(canonical) !== parent.id)) continue;
    const conversationId = canonical ? canonical.conversation_id : ticket.conversation_id;
    if (!conversationId || !index.unreadConversationIds.has(conversationId)) continue;
    const sameTicket = index.firstMessageByTicket.get(ticket.id);
    if (sameTicket && sameTicket.parentId !== parent.id) continue;
    if (!children.has(conversationId)) children.set(conversationId, {
      conversationSignal: canonical ? canonical.conversation_signal : ticket.conversation_signal,
      kind:"ticket", id:ticket.id, title:canonical?.title ?? ticket.title, conversationId,
      parentId:parent.id, parentTitle:parent.title, settled:parent.settled === true
    });
  }
  for (const row of index.messagesByParent.get(parent.id) ?? []) {
    if (!children.has(row.conversationId)) children.set(row.conversationId,row);
  }
  return [...children.values()];
}

function ticketParentId(card: WorkspaceTicket): string {
  return card.manager?.kind === "chief" ? "chief"
    : card.manager?.kind === "manager" ? card.manager.id
    : card.manager_id ?? "chief";
}

/** Existing durable unread facts form this index; settlement never limits discovery. */
export function workspaceMessageConversations(
  cards: readonly WorkspaceTicket[],
  items: readonly WorkspaceManager[],
  chief?: WorkspaceChief | null
): WorkspaceMessageConversation[] {
  const byItem = new Map(items.map((item) => [item.id, item]));
  const byTicket = new Map(cards.map((card) => [card.id, card]));
  const conversations = new Map<string, WorkspaceMessageConversation>();
  const add = (row: WorkspaceMessageConversation) => {
    if (!conversations.has(row.conversationId)) conversations.set(row.conversationId, row);
  };
  for (const card of cards) {
    if (!card.awaiting_reply || !card.conversation_id) continue;
    // An explicit Chief assignment wins over any stale legacy parent column.
    const parentId = ticketParentId(card);
    const parent = byItem.get(parentId);
    add({
      conversationSignal: card.conversation_signal,
      kind: "ticket", id: card.id, title: card.title, conversationId: card.conversation_id,
      parentId, parentTitle: parentId === "chief" ? chief?.label ?? "Chief of Staff"
        : parent?.title ?? card.manager_title,
      settled: parentId !== "chief" && parent?.settled === true
    });
  }
  // Complete item children also cover a historical Worker omitted by the board.
  for (const item of items) {
    for (const ticket of item.tickets) {
      if (byTicket.has(ticket.id) || !ticket.awaiting_reply || !ticket.conversation_id) continue;
      add({
        conversationSignal: ticket.conversation_signal,
        kind: "ticket", id: ticket.id, title: ticket.title, conversationId: ticket.conversation_id,
        parentId: item.id, parentTitle: item.title, settled: item.settled === true
      });
    }
    if (item.awaiting_reply && item.conversation_id) add({
      conversationSignal: item.conversation_signal,
      kind: "manager", id: item.id, title: item.title, conversationId: item.conversation_id,
      parentId: null, parentTitle: null, settled: item.settled === true
    });
  }
  if (chief?.awaiting_reply && chief.conversation_id) add({
    conversationSignal: chief.conversation_signal,
    kind: "chief", id: "chief", title: chief.label, conversationId: chief.conversation_id,
    parentId: null, parentTitle: null, settled: false
  });
  return [...conversations.values()];
}
