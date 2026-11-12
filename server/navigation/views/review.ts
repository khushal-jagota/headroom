import type { ReadSql } from '../../core/database/contracts.ts';
import type { Fact, Key, ReviewMessage } from './contracts.ts';
import { messageOwnerIsLive } from './logic.ts';
import { binaryCompare } from './ordering.ts';
export function record(sql: ReadSql, key: Key): Fact | null {
  const row = sql.get<{ value: string }>('SELECT value FROM navigation_records WHERE kind=? AND id=?', [...key]);
  return row ? JSON.parse(row.value) as Fact : null;
}
export function canonicalReviewAddress(sql: ReadSql, conversationId: string): string | null {
  return sql.get<{id: string}>(
    "SELECT a.id FROM navigation_records a JOIN navigation_records owner ON owner.kind=json_extract(a.value,'$.owner_kind') AND owner.id=json_extract(a.value,'$.owner_id') WHERE a.kind='address' AND json_extract(a.value,'$.conversation_id')=? ORDER BY json_extract(a.value,'$.current') DESC,a.id LIMIT 1", [conversationId],
  )?.id ?? null;
}
export function reviewMessageItems(sql: ReadSql, ticketId: string | null = null): ReviewMessage[] {
  const candidates = sql.all<{id: string; value: string}>(ticketId === null
    ? "SELECT id,value FROM navigation_records WHERE kind='conversation' ORDER BY id"
    : "SELECT c.id,c.value FROM navigation_records c WHERE c.kind='conversation' AND EXISTS(SELECT 1 FROM navigation_records a WHERE a.kind='address' AND json_extract(a.value,'$.conversation_id')=c.id AND json_extract(a.value,'$.owner_kind')='ticket' AND json_extract(a.value,'$.owner_id')=?) ORDER BY c.id", ticketId === null ? [] : [ticketId]);
  const items: (ReviewMessage & { current: boolean })[] = [];
  for (const candidate of candidates) {
    const conversation: Fact = JSON.parse(candidate.value);
    if (!conversation.attention.durable.message_for_owner) continue;
    const addressId = canonicalReviewAddress(sql, candidate.id);
    const address = addressId === null ? null : record(sql, ['address', addressId]);
    if (!address || !addressId || ticketId !== null && (address.owner_kind !== 'ticket' || address.owner_id !== ticketId)) continue;
    const owner = record(sql, [address.owner_kind, address.owner_id]);
    if (!owner || !messageOwnerIsLive(address.owner_kind, owner)) continue;
    items.push({ conversation_id: candidate.id, address_id: addressId, owner_kind: address.owner_kind, owner_id: address.owner_id, waiting_since: conversation.first_unread_owner_message_at, current: address.current });
  }
  return items.sort((a, b) => Number(b.current) - Number(a.current) || binaryCompare(a.address_id, b.address_id) || binaryCompare(a.conversation_id, b.conversation_id))
    .map(({ current: _current, ...item }) => item);
}
