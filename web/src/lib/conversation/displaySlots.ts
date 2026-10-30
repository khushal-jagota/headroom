import type { OutgoingMessage } from './outgoing';
import type { ConversationViewItem, ConversationViewRow, ViewOrder } from './view/contracts';

export type MessageDisplaySlot = {
  key: string;
  order: ViewOrder;
  item: ConversationViewItem | null;
  local: OutgoingMessage | null;
};
export function isMessageRow(item: ConversationViewItem): boolean {
  return item.kind === 'row' && ['prompt', 'prompt_refused', 'prompt_uncertain', 'prompt_discarded'].includes(item.row_kind);
}
/** Only locally drawn messages retain an order. Historical rows keep server chronology.
 * The caller discards this small mounted ledger with its conversation.
 *
 * ``conversationEndSequence`` is the record's own latest sequence, which the reader holds
 * as its coverage. A locally drawn send takes its place from there, because the retained
 * window can end far behind the record and a send placed at that end lands inside
 * history, where the owner never sees it. */
export function reconcileDisplaySlots(previous: readonly MessageDisplaySlot[], items: readonly ConversationViewItem[], outgoing: readonly OutgoingMessage[], acknowledged: ReadonlySet<string>, withdrawnDisplayIds: ReadonlySet<string> = new Set(), conversationEndSequence: number | null = null): MessageDisplaySlot[] {
  const bySender = new Map(items.filter((i): i is ConversationViewRow => isMessageRow(i)).filter(i => i.sender_message_id !== null).map(i => [i.sender_message_id!, i]));
  const locals = new Map(outgoing.map(m => [m.displayMessageId ?? m.messageId, m]));
  const claimed = new Set<string>();
  const slots: MessageDisplaySlot[] = [];
  for (const old of previous) {
    if (old.local === null || withdrawnDisplayIds.has(old.key)) continue;
    const local = locals.get(old.key) ?? old.local;
    const item = bySender.get(local.messageId) ?? null;
    // A received row that leaves retention takes its presentation state with it.
    if (!locals.has(old.key) && (old.item !== null && item === null || !acknowledged.has(local.messageId) && item === null)) continue;
    if (item) claimed.add(item.key);
    slots.push({ ...old, local, item, order: item?.order ?? old.order });
    locals.delete(old.key);
  }
  // Coverage is 0 until the first page arrives, so the window's end stays the fallback.
  const end = Math.max(items.at(-1)?.order.sequence ?? 0, conversationEndSequence ?? 0);
  let offset = 0;
  for (const [key, local] of locals) {
    const item = bySender.get(local.messageId) ?? null;
    if (item) claimed.add(item.key);
    slots.push({ key, local, item, order: item?.order ?? { sequence: end + 0.5, slot: offset++ } });
  }
  // The owner's own messages keep the order they were sent in. A slot that takes its
  // record row's place must not leave a message sent after it drawn above it.
  let floor: ViewOrder | null = null;
  for (const slot of slots) {
    if (floor !== null && (slot.order.sequence < floor.sequence
        || slot.order.sequence === floor.sequence && slot.order.slot <= floor.slot))
      slot.order = { sequence: floor.sequence, slot: floor.slot + 1 };
    floor = slot.order;
  }
  for (const item of items) if (!claimed.has(item.key)) slots.push({key: `record:${item.key}`, order: item.order, item, local: null});
  return slots.sort((a,b) => a.order.sequence-b.order.sequence || a.order.slot-b.order.slot);
}
