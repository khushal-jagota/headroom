import { createHash } from 'node:crypto';
import { answer_at_acceptance } from './acceptance.ts';
import { own_recipient_delivery } from './lifetime.ts';
import { OwnerSendRefusalReason, type OwnerSendOutcome } from './contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { PromptDeliveryRefusedEventPayload } from '../conversation/events.ts';
import { MACHINE_UNAVAILABLE } from '../conversation/contracts.ts';
import { is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import type { ConversationSystem, NativeMessageOperation } from '../conversation/contracts.ts';
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

type TerminalKind =
  'prompt' | 'prompt_delivery_refused' | 'prompt_delivery_uncertain' | 'prompt_discarded';

function recorded_event(
  sql: ReadSql,
  keys: Keys,
  conversation_id: string | null,
): { conversation_id: string; kind: TerminalKind; payload: string } | undefined {
  const [owner_kind, owner_id, message_id] = keys;
  // A pre-upgrade browser still owns its original message ID. Its notebook entry is a receipt.
  return sql.get<{ conversation_id: string; kind: TerminalKind; payload: string }>(
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
}

function event_delivery(event: {
  conversation_id: string;
  kind: TerminalKind;
  payload: string;
}): Delivery {
  const payload = conversation_event_payload_from_canonical_json(event.kind, event.payload);
  return {
    conversation_id: event.conversation_id,
    ...delivery_fate_json(outcome_fate(payload)),
    ...(payload instanceof PromptDeliveryRefusedEventPayload && payload.refusal_detail !== null
      ? { reason: payload.refusal_detail }
      : {}),
  };
}

function recorded_delivery(sql: ReadSql, keys: Keys, conversation_id: string | null) {
  const event = recorded_event(sql, keys, conversation_id);
  return event ? event_delivery(event) : null;
}

/** Home refused the message before it took custody, and says why. */
function refused_before_acceptance(
  conversation_id: string | null,
  refusal_reason: OwnerSendRefusalReason,
  reason: string,
): Delivery {
  return { conversation_id, fate: 'refused', refusal_reason, reason };
}
const HOME_RESTARTED = 'Panels restarted before it accepted this message.';
/** The cause of a send that failed before custody, from the error that stopped it. */
function refusal_for_error(conversation_id: string | null, error: unknown): Delivery {
  if (is_shutdown_business_cancellation(error))
    return refused_before_acceptance(
      conversation_id,
      OwnerSendRefusalReason.home_restarted_before_acceptance,
      HOME_RESTARTED,
    );
  const reason = error instanceof Error ? error.message : String(error);
  const code = error instanceof PlannerError ? error.code : null;
  return refused_before_acceptance(
    conversation_id,
    code === 'conversation_save_failed'
      ? OwnerSendRefusalReason.conversation_save_failed
      : code === 'not_found'
        ? OwnerSendRefusalReason.recipient_unavailable
        : code === MACHINE_UNAVAILABLE
          ? OwnerSendRefusalReason.machine_unavailable
          : OwnerSendRefusalReason.refused_before_acceptance,
    reason,
  );
}

type Outcome = OwnerSendOutcome['outcome'];
/** Journal states and conversation event kinds name the same settled outcomes. */
const SETTLED: Readonly<Record<string, Outcome>> = {
  delivered: 'delivered',
  prompt: 'delivered',
  refused: 'refused',
  prompt_delivery_refused: 'refused',
  discarded: 'discarded',
  prompt_discarded: 'discarded',
  uncertain: 'uncertain',
  prompt_delivery_uncertain: 'uncertain',
};
/** A send answer in today's fate vocabulary, as an outcome. */
function fate_outcome(fate: unknown): Outcome {
  if (fate === 'queued' || fate === 'accepted') return 'pending';
  if (fate === 'started' || fate === 'injected') return 'delivered';
  if (fate === 'refused') return 'refused';
  if (fate === 'suppressed') return 'discarded';
  return 'uncertain';
}

interface StoredOwnerSend {
  readonly outcome: OwnerSendOutcome;
  /** A repeat's answer: accepted while pending, otherwise the final answer. */
  readonly answer: Delivery;
}

/**
 * Read one owner send from stored rows: the recipient's journal, then conversation events,
 * then the stored final answer. Nothing here waits on a conversation or send lock.
 * Pending means durable custody: a send not yet admitted has no outcome.
 */
function stored_owner_send(
  sql: ReadSql,
  keys: Keys,
  target: OwnerSendTarget,
  sending: boolean,
): StoredOwnerSend | null {
  const message_id = keys[2];
  const saved = receipt(sql, keys);
  const conversation_id = saved?.conversation_id ?? null;
  const settled = (answer: Delivery, outcome = fate_outcome(answer.fate)): StoredOwnerSend => {
    const text = (value: unknown) => (typeof value === 'string' ? value : null);
    return {
      outcome: {
        message_id,
        conversation_id: answer.conversation_id,
        outcome,
        refusal_reason: outcome === 'refused' ? text(answer.refusal_reason) : null,
        // A message withdrawn for editing names no reason; its fate reads as refused.
        reason: outcome === 'discarded' || outcome === 'refused' ? text(answer.reason) : null,
      },
      answer:
        outcome === 'pending'
          ? { conversation_id: answer.conversation_id, fate: 'accepted', message_id }
          : answer,
    };
  };
  const pending = () => settled({ conversation_id, fate: 'accepted' });
  // The journal settles with each terminal event, and alone holds a refusal made before a
  // fresh conversation started.
  const journal =
    conversation_id === null
      ? undefined
      : sql.get<{ state: string; outcome: string | null }>(
          'SELECT state,outcome FROM recipient_deliveries WHERE submission_id=?',
          [`${conversation_id}:${message_id}`],
        );
  if (journal && !(journal.state in SETTLED)) return pending();
  if (journal?.outcome != null) {
    // A refusal's event carries its sentence; the journal keeps only the outcome.
    const detail =
      journal.state === 'refused' ? recorded_delivery(sql, keys, conversation_id)?.reason : null;
    return settled(
      {
        conversation_id,
        ...(JSON.parse(journal.outcome) as Record<string, unknown>),
        ...(typeof detail === 'string' ? { reason: detail } : {}),
      },
      SETTLED[journal.state],
    );
  }
  // A terminal row without an outcome was discarded here because another conversation that
  // linked first admitted the message, so its event is looked up in any conversation.
  const discarded_elsewhere = journal?.state === 'discarded';
  const event = recorded_event(sql, keys, discarded_elsewhere ? null : conversation_id);
  if (event) return settled(event_delivery(event), SETTLED[event.kind]);
  if (!saved) return null;
  const response = saved.response === null ? null : (JSON.parse(saved.response) as Delivery);
  if (response && response.fate !== 'queued') return settled(response);
  // Older code stored a queued answer for a message held before the journal existed.
  if (
    response?.fate === 'queued' &&
    sql.get(
      "SELECT 1 FROM conversation_held_prompts WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=?",
      [conversation_id, message_id],
    ) !== undefined
  )
    return pending();
  // Not admitted yet, and still in flight: there is no outcome to report so far.
  if (sending) return null;
  if (saved.pending_kind === 'prompt_delivery_uncertain')
    return settled({ conversation_id, fate: 'uncertain' });
  if (saved.epoch !== owner_send_epoch(sql) || saved.pending_kind !== null)
    return settled(
      refused_before_acceptance(
        conversation_id,
        OwnerSendRefusalReason.home_restarted_before_acceptance,
        HOME_RESTARTED,
      ),
    );
  if (conversation_id !== null && conversation_id !== current_conversation(sql, target))
    return settled(
      refused_before_acceptance(
        conversation_id,
        OwnerSendRefusalReason.recipient_conversation_replaced,
        "The recipient's conversation was replaced before it accepted this message.",
      ),
    );
  return null;
}

function is_sending(database: Database, keys: Keys): boolean {
  return active.get(database)?.has(JSON.stringify(keys)) ?? false;
}

/** The status read: stored rows only, never a conversation lock or a per-target send lock. */
export function owner_send_outcome(
  database: Database,
  target: OwnerSendTarget,
  message_id: string,
): OwnerSendOutcome | null {
  const keys: Keys = [target.kind, target.id, message_id];
  return (
    database.read((sql) => stored_owner_send(sql, keys, target, is_sending(database, keys)))
      ?.outcome ?? null
  );
}

type SendOperation = (
  body: OwnerSendBody,
  remember_destination: (content: ConversationMessageContent) => ConversationMessageContent,
  recording_receipt: NativeMessageOperation | null,
) => Promise<Delivery>;

/**
 * Record one owner request without letting an exact repeat move into another conversation.
 * The answer comes at acceptance. Delivery continues under the recipient's journal, and only
 * its final answer is stored, so code before acceptance answers reads every stored answer.
 */
export function run_owner_send(
  database: Database,
  system: ConversationSystem,
  principal: Principal,
  target: OwnerSendTarget,
  body: OwnerSendBody,
  send: SendOperation,
): Promise<Delivery> {
  const message_id = body.sender_message_id;
  if (principal.kind !== 'owner' || message_id === null)
    return send(body, (content) => content, null);
  const keys: Keys = [target.kind, target.id, message_id];
  const lock_key = JSON.stringify(keys);
  const { provided_fields, sender_label: _label, ...parsed } = body;
  const body_digest = createHash('sha256')
    .update(
      JSON.stringify({ ...parsed, sets_reasoning_effort: provided_fields.has('reasoning_effort') }),
    )
    .digest('hex');
  // The lock ends at acceptance: the admission lease releases it, and so does the answer.
  return conversation_link_lock(`owner-send:${JSON.stringify([target.kind, target.id])}`).run(
    async () => {
      const previous = database.read((sql) => receipt(sql, keys));
      if (previous && previous.body_digest !== body_digest)
        throw new HttpError(422, 'sender_message_id already names a different message');
      if (previous) {
        const known = database.read((sql) =>
          stored_owner_send(sql, keys, target, is_sending(database, keys)),
        );
        if (known) return known.answer;
        // Pin the original target even if a reset races with the status read.
        if (previous.conversation_id) body = { ...body, conversation_id: previous.conversation_id };
      } else {
        const legacy = database.read((sql) => recorded_delivery(sql, keys, null));
        if (legacy) {
          if (legacy.conversation_id !== database.read((sql) => current_conversation(sql, target)))
            throw new HttpError(409, 'This message belongs to the earlier conversation');
          // An older client's existing core deduplication checks payload equality.
          return send(
            { ...body, conversation_id: legacy.conversation_id },
            (content) => content,
            null,
          );
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
      // Shutdown joins the whole delivery, including its final answer.
      const deliver = (recording_receipt: NativeMessageOperation) =>
        own_recipient_delivery(system, async () => {
          try {
            const delivered = await send(body, remember_destination, recording_receipt);
            // A queued message is still pending. The journal and events carry it from here.
            if (delivered.fate !== 'queued')
              database.write(
                (tx) => {
                  tx.run(
                    'UPDATE owner_send_receipts SET response=?,pending_kind=NULL,pending_payload=NULL ' +
                      'WHERE owner_kind=? AND owner_id=? AND message_id=?',
                    [JSON.stringify(delivered), ...keys],
                  );
                },
                { changeSignal: 'silent' },
              );
            return delivered;
          } catch (error) {
            // Without a custody row nothing was admitted, so nothing crossed the agent wire.
            database.write(
              (tx) => {
                const bound = receipt(tx, keys)?.conversation_id ?? null;
                if (
                  bound !== null &&
                  tx.get('SELECT 1 FROM recipient_deliveries WHERE submission_id=?', [
                    `${bound}:${message_id}`,
                  ]) !== undefined
                )
                  return;
                tx.run(
                  'UPDATE owner_send_receipts SET response=? WHERE owner_kind=? AND owner_id=? AND message_id=?',
                  [JSON.stringify(refusal_for_error(bound, error)), ...keys],
                );
              },
              { changeSignal: 'silent' },
            );
            throw error;
          } finally {
            sending.delete(lock_key);
          }
        });
      return answer_at_acceptance(deliver, {
        submission_id: message_id,
        accepted: (conversation_id): Delivery => ({
          conversation_id:
            conversation_id ?? database.read((sql) => receipt(sql, keys)?.conversation_id ?? null),
          fate: 'accepted',
          message_id,
        }),
        before_acceptance: (delivered) => delivered,
        failure_label: 'accepted owner send failed',
      });
    },
  );
}
