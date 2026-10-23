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
 * The caller discards this small mounted ledger with its conversation. */
export function reconcileDisplaySlots(previous: readonly MessageDisplaySlot[], items: readonly ConversationViewItem[], outgoing: readonly OutgoingMessage[], acknowledged: ReadonlySet<string>): MessageDisplaySlot[] {
  const bySender = new Map(items.filter((i): i is ConversationViewRow => isMessageRow(i)).filter(i => i.sender_message_id !== null).map(i => [i.sender_message_id!, i]));
  const locals = new Map(outgoing.map(m => [m.displayMessageId ?? m.messageId, m]));
  const claimed = new Set<string>();
  const slots: MessageDisplaySlot[] = [];
  for (const old of previous) {
    if (old.local === null) continue;
    const local = locals.get(old.key) ?? old.local;
    const item = bySender.get(local.messageId) ?? null;
    // A received row that leaves retention takes its presentation state with it.
    if (!locals.has(old.key) && (old.item !== null && item === null || !acknowledged.has(local.messageId) && item === null)) continue;
    if (item) claimed.add(item.key);
    slots.push({ ...old, local, item });
    locals.delete(old.key);
  }
  const last = items.at(-1)?.order ?? { sequence: 0, slot: 0 };
  let offset = 0;
  for (const [key, local] of locals) {
    const item = bySender.get(local.messageId) ?? null;
    if (item) claimed.add(item.key);
    slots.push({ key, local, item, order: { sequence: last.sequence + 0.5, slot: offset++ } });
  }
  for (const item of items) if (!claimed.has(item.key)) slots.push({key: `record:${item.key}`, order: item.order, item, local: null});
  return slots.sort((a,b) => a.order.sequence-b.order.sequence || a.order.slot-b.order.slot);
}
