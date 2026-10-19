import type { WorkspaceMessageConversation } from "./types";
import type { WorkspaceTicket, WorkspaceOrchestrator, WorkspaceChief } from "./workspaceRail";
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
  items: readonly WorkspaceOrchestrator[] = []
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

/** Retain each parent's real child access before deduplicating within that parent. */
export function workspaceChildMessageConversations(
  rows: readonly WorkspaceMessageConversation[],
  parent: Pick<WorkspaceOrchestrator, "id" | "title" | "settled"> & {tickets: readonly WorkItemTicketFacts[]},
  cards: readonly WorkspaceTicket[] = []
): WorkspaceMessageConversation[] {
  const unread = new Set(rows.map((row) => row.conversationId));
  const byTicket = new Map(cards.map((card) => [card.id,card]));
  const children = new Map<string,WorkspaceMessageConversation>();
  for (const ticket of parent.tickets) {
    const canonical = byTicket.get(ticket.id);
    // The canonical same-Ticket address and read fact win over a stale child copy.
    if (canonical && (!canonical.awaiting_reply || ticketParentId(canonical) !== parent.id)) continue;
    const conversationId = canonical ? canonical.conversation_id : ticket.conversation_id;
    if (!conversationId || !unread.has(conversationId)) continue;
    const sameTicket = rows.find((row) => row.kind === "ticket" && row.id === ticket.id);
    if (sameTicket && sameTicket.parentId !== parent.id) continue;
    if (!children.has(conversationId)) children.set(conversationId, {
      conversationSignal: canonical ? canonical.conversation_signal : ticket.conversation_signal,
      kind:"ticket", id:ticket.id, title:canonical?.title ?? ticket.title, conversationId,
      parentId:parent.id, parentTitle:parent.title, settled:parent.settled === true
    });
  }
  for (const row of rows) {
    if (row.kind === "ticket" && row.parentId === parent.id && !children.has(row.conversationId)) {
      children.set(row.conversationId,row);
    }
  }
  return [...children.values()];
}

function ticketParentId(card: WorkspaceTicket): string {
  return card.orchestrator?.kind === "chief" ? "chief"
    : card.orchestrator?.kind === "sprint_item" ? card.orchestrator.id
    : card.sprint_item_id ?? "chief";
}

/** Existing durable unread facts form this index; settlement never limits discovery. */
export function workspaceMessageConversations(
  cards: readonly WorkspaceTicket[],
  items: readonly WorkspaceOrchestrator[],
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
        : parent?.title ?? card.sprint_item_title,
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
      kind: "sprint_item", id: item.id, title: item.title, conversationId: item.conversation_id,
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
