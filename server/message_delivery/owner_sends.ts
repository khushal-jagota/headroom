import { createHash } from 'node:crypto';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { PromptDeliveryRefusalReason } from '../conversation/contracts.ts';
import type { ConversationMessageContent } from '../conversation/contracts.ts';
import { delivery_fate_json, type OwnerSendBody } from '../conversation/http.ts';
import { conversation_event_payload_from_canonical_json } from '../conversation/record/codec.ts';
import { owner_send_epoch } from '../conversation/record/owner_send_receipts.ts';
import { outcome_fate } from '../conversation/runtime/messages.ts';
import { manager_agent_key } from '../managers/data.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { conversation_link_lock } from '../runtime/conversation_start.ts';

export type OwnerSendTarget = { kind: 'chief' | 'manager' | 'ticket'; id: string };
type Delivery = { conversation_id: string | null; fate?: unknown } & Record<string, unknown>;
type Keys = [OwnerSendTarget['kind'], string, string];
interface SendReceipt {
  body_digest: string;
  conversation_id: string | null;
  epoch: string | null;
  response: string | null;
  pending_kind: 'prompt_delivery_refused' | 'prompt_delivery_uncertain' | null;
}

const active = new WeakMap<Database, Set<string>>();

function receipt(sql: ReadSql, keys: Keys): SendReceipt | undefined {
  return sql.get<SendReceipt>(
    'SELECT body_digest,conversation_id,epoch,response,pending_kind FROM owner_send_receipts ' +
      'WHERE owner_kind=? AND owner_id=? AND message_id=?',
    keys,
  );
}

function current_conversation(sql: ReadSql, target: OwnerSendTarget): string | null {
  if (target.kind === 'ticket') {
    return (
      sql.get<{ conversation_id: string | null }>(
        'SELECT conversation_id FROM tickets WHERE id=?',
        [target.id],
      )?.conversation_id ?? null
    );
  }
  const agent_key = target.kind === 'chief' ? CHIEF_SETTINGS_KEY : manager_agent_key(target.id);
  return (
    sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=?',
      [agent_key],
    )?.conversation_id ?? null
  );
}

function recorded_delivery(
  sql: ReadSql,
  keys: Keys,
  conversation_id: string | null,
): Delivery | null {
  const [owner_kind, owner_id, message_id] = keys;
  // A pre-upgrade browser still owns its original message ID. Its notebook entry is a receipt.
  const event = sql.get<{
    conversation_id: string;
    kind: 'prompt' | 'prompt_delivery_refused' | 'prompt_delivery_uncertain' | 'prompt_discarded';
    payload: string;
  }>(
    "SELECT conversation_id,kind,payload FROM conversation_events WHERE json_extract(payload,'$.sender_message_id')=? " +
      "AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') " +
      "AND json_extract(payload,'$.sender.kind')='owner' AND json_extract(payload,'$.recipient.kind')=? " +
      "AND json_extract(payload,'$.recipient.id')=? " +
      (conversation_id === null ? '' : 'AND conversation_id=? ') +
      'ORDER BY ' +
      (conversation_id === null
        ? "CASE kind WHEN 'prompt' THEN 0 WHEN 'prompt_delivery_uncertain' THEN 1 ELSE 2 END,"
        : '') +
      'created_at DESC,sequence DESC LIMIT 1',
    conversation_id === null
      ? [message_id, owner_kind, owner_id]
      : [message_id, owner_kind, owner_id, conversation_id],
  );
  return event
    ? {
        conversation_id: event.conversation_id,
        ...delivery_fate_json(
          outcome_fate(conversation_event_payload_from_canonical_json(event.kind, event.payload)),
        ),
      }
    : null;
}

function interrupted(conversation_id: string | null, uncertain = false): Delivery {
  return uncertain
    ? { conversation_id, fate: 'uncertain' }
    : {
        conversation_id,
        fate: 'refused',
        refusal_reason: PromptDeliveryRefusalReason.write_to_backend_failed,
      };
}

export async function owner_send_status(
  database: Database,
  system: ConversationSystem,
  target: OwnerSendTarget,
  message_id: string,
): Promise<Delivery | null> {
  const keys: Keys = [target.kind, target.id, message_id];
  const read = () =>
    database.read((sql) => {
      const saved = receipt(sql, keys);
      return {
        saved,
        actual: saved?.conversation_id ?? null,
        current: current_conversation(sql, target),
        recorded: recorded_delivery(sql, keys, saved?.conversation_id ?? null),
        epoch: owner_send_epoch(sql),
      };
    });
  const first = read();
  if (first.recorded) return first.recorded;
  const conversation_id = first.actual ?? first.current;
  if (conversation_id !== null) {
    const progress = await system.owner_send_progress(conversation_id, message_id, target);
    if (progress?.state === 'queued')
      return { conversation_id, fate: 'queued', queue_position: progress.position };
    if (progress?.state === 'uncertain') return interrupted(conversation_id, true);
    if (progress?.state === 'pending') return null;
  }
  const latest = read();
  if (latest.recorded) return latest.recorded;
  const saved = latest.saved;
  if (!saved) return null;
  const response = saved.response === null ? null : (JSON.parse(saved.response) as Delivery);
  if (response && response.fate !== 'queued') return response;
  if (active.get(database)?.has(JSON.stringify(keys))) return null;
  // A send can become admitted after the live read. Leave that transition for the next lookup.
  if (
    saved.epoch === latest.epoch &&
    (conversation_id !== (latest.actual ?? latest.current) ||
      saved.pending_kind !== first.saved?.pending_kind ||
      saved.response !== first.saved?.response)
  )
    return null;
  if (
    saved.epoch !== latest.epoch ||
    (saved.conversation_id !== null && saved.conversation_id !== latest.current) ||
    saved.pending_kind !== null ||
    response?.fate === 'queued'
  ) {
    return interrupted(saved.conversation_id, saved.pending_kind === 'prompt_delivery_uncertain');
  }
  return null;
}

type SendOperation = (
  body: OwnerSendBody,
  remember_destination: (content: ConversationMessageContent) => ConversationMessageContent,
) => Promise<Delivery>;

/** Record one owner request without letting an exact repeat move into another conversation. */
export function run_owner_send(
  database: Database,
  system: ConversationSystem,
  principal: Principal,
  target: OwnerSendTarget,
  body: OwnerSendBody,
  send: SendOperation,
): Promise<Delivery> {
  const message_id = body.sender_message_id;
  if (principal.kind !== 'owner' || message_id === null) return send(body, (content) => content);
  const keys: Keys = [target.kind, target.id, message_id];
  const lock_key = JSON.stringify(keys);
  const { provided_fields, sender_label: _label, ...parsed } = body;
  const body_digest = createHash('sha256')
    .update(
      JSON.stringify({ ...parsed, sets_reasoning_effort: provided_fields.has('reasoning_effort') }),
    )
    .digest('hex');
  return conversation_link_lock(`owner-send:${JSON.stringify([target.kind, target.id])}`).run(
    async () => {
      const previous = database.read((sql) => receipt(sql, keys));
      if (previous && previous.body_digest !== body_digest)
        throw new HttpError(422, 'sender_message_id already names a different message');
      if (previous) {
        const delivery = await owner_send_status(database, system, target, message_id);
        if (delivery) return delivery;
        // Pin the original target even if a reset races with the status read.
        if (previous.conversation_id) body = { ...body, conversation_id: previous.conversation_id };
      } else {
        const legacy = database.read((sql) => recorded_delivery(sql, keys, null));
        if (legacy) {
          if (legacy.conversation_id !== database.read((sql) => current_conversation(sql, target)))
            throw new HttpError(409, 'This message belongs to the earlier conversation');
          // An older client's existing core deduplication checks payload equality.
          return send({ ...body, conversation_id: legacy.conversation_id }, (content) => content);
        }
        database.write(
          (tx) => {
            const inserted = tx.run(
              'INSERT INTO owner_send_receipts(owner_kind,owner_id,message_id,body_digest,epoch) ' +
                'VALUES(?,?,?,?,?) ON CONFLICT(message_id) DO NOTHING',
              [...keys, body_digest, owner_send_epoch(tx)],
            );
            if (inserted.changes !== 1)
              throw new HttpError(422, 'sender_message_id already names a different destination');
          },
          { changeSignal: 'silent' },
        );
      }
      if (!active.has(database)) active.set(database, new Set());
      const sending = active.get(database)!;
      sending.add(lock_key);
      const remember_destination =
        (content: ConversationMessageContent): ConversationMessageContent =>
        async (conversation_id) => {
          database.write(
            (tx) => {
              const bound = receipt(tx, keys)?.conversation_id;
              if (bound && bound !== conversation_id)
                throw new HttpError(409, 'This message belongs to the earlier conversation');
              tx.run(
                'UPDATE owner_send_receipts SET conversation_id=? WHERE owner_kind=? AND owner_id=? AND message_id=?',
                [conversation_id, ...keys],
              );
            },
            { changeSignal: 'silent' },
          );
          return typeof content === 'function' ? content(conversation_id) : content;
        };
      try {
        const delivery = await send(body, remember_destination);
        database.write(
          (tx) => {
            const response = JSON.stringify(delivery);
            if (delivery.fate === 'queued')
              tx.run(
                'UPDATE owner_send_receipts SET response=? WHERE owner_kind=? AND owner_id=? AND message_id=?',
                [response, ...keys],
              );
            else
              tx.run(
                'UPDATE owner_send_receipts SET response=?,pending_kind=NULL,pending_payload=NULL ' +
                  'WHERE owner_kind=? AND owner_id=? AND message_id=?',
                [response, ...keys],
              );
          },
          { changeSignal: 'silent' },
        );
        return delivery;
      } catch (error) {
        // Input and startup errors before content selection cannot have crossed the agent wire.
        database.write(
          (tx) => {
            tx.run(
              'UPDATE owner_send_receipts SET response=? WHERE owner_kind=? AND owner_id=? AND message_id=? AND conversation_id IS NULL',
              [JSON.stringify(interrupted(null)), ...keys],
            );
          },
          { changeSignal: 'silent' },
        );
        throw error;
      } finally {
        sending.delete(lock_key);
      }
    },
  );
}
