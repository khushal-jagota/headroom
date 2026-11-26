/** General send and owner recap intake; the service owns dispatch. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import type { Principal } from '../core/contracts.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { conversation_machine_waiting_reason } from '../machines/conversation_machine.ts';
import { PlannerError } from '../core/contracts.ts';
import { dictionaryBody } from '../list_reads/http.ts';
import { delivery_fate_json } from '../conversation/http.ts';
import type { PromptDeliveryMode } from '../conversation/contracts.ts';
import { MessageAcceptedForDelivery, MessageRecordedToOwner } from './contracts.ts';
import type { MessageDeliveryResult } from './contracts.ts';
import { send_message, type SendMessageOptions } from './service.ts';
import { answer_at_acceptance } from './acceptance.ts';
import { request_recap } from './recap_request.ts';

export function principal(raw: unknown): Principal {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new PlannerError('validation', 'target must be a principal object');
  }
  const value = raw as Record<string, unknown>;
  const kind = value.kind;
  if (typeof kind !== 'string' || !['owner', 'chief', 'manager', 'goal', 'ticket'].includes(kind)) {
    throw new PlannerError(
      'validation',
      'recipient kind must be owner, chief, manager, or ticket',
      {
        kind: kind ?? null,
      },
    );
  }
  if (kind === 'goal') {
    throw new PlannerError('validation', 'unknown message recipient kind', { kind });
  }
  const id = value.id;
  if (typeof id !== 'string' || !id.trim()) {
    throw new PlannerError('validation', `${kind} recipient id is required`, { id: id ?? null });
  }
  if ((kind === 'owner' || kind === 'chief') && id.trim() !== kind) {
    throw new PlannerError('validation', `${kind} principal id must be ${kind}`, { id });
  }
  return { kind: kind as Principal['kind'], id: id.trim() };
}
export function result_json(result: MessageDeliveryResult): Record<string, unknown> {
  return {
    target: result.recipient,
    conversation_id: result.conversation_id,
    ...(result.fate instanceof MessageRecordedToOwner
      ? { fate: 'recorded' }
      : result.fate instanceof MessageAcceptedForDelivery
        ? { fate: 'accepted' }
        : delivery_fate_json(result.fate)),
  };
}
export function message_text(raw: unknown): string {
  if (typeof raw !== 'string' || !raw.trim())
    throw new PlannerError('validation', 'message must not be empty');
  return raw;
}
export function message_delivery_mode(...values: [] | [unknown]): PromptDeliveryMode {
  const mode = values.length === 0 ? 'steer' : values[0];
  if (typeof mode !== 'string' || !['queue', 'steer', 'send_now'].includes(mode))
    throw new PlannerError('validation', 'mode must be queue, steer, or send_now', { mode });
  return mode as PromptDeliveryMode;
}
/** An accepted message that waits for its machine says so, for example "Waiting for Mac 1". */
export function machine_waiting_fields(
  sql: ReadSql,
  conversation_id: string | null,
): { machine_waiting_reason?: string } {
  const reason =
    conversation_id === null
      ? null
      : conversation_machine_waiting_reason(sql, conversation_id, Date.now());
  return reason === null ? {} : { machine_waiting_reason: reason };
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.post('/messages/send', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const recipient = principal(request.body.target);
    const message = message_text(request.body.message);
    const mode =
      'mode' in request.body ? message_delivery_mode(request.body.mode) : message_delivery_mode();
    if (!services.conversationSystem) throw new Error('conversation system is unavailable');
    const send = (options?: SendMessageOptions) =>
      send_message(
        services.conversationSystem!,
        services.database,
        services.clock,
        services.identity(request),
        recipient,
        message,
        mode as PromptDeliveryMode,
        options,
      );
    if (!('message_id' in request.body)) return result_json(await send());
    const message_id = request.body.message_id;
    if (typeof message_id !== 'string' || !message_id.trim())
      throw new PlannerError('validation', 'message_id must be text');
    // A message to the owner is recorded at once, so its answer is already final.
    if (recipient.kind === 'owner')
      return result_json(await send({ sender_message_id: message_id }));
    // The sender minted the ID. The answer comes at acceptance, before native delivery.
    return answer_at_acceptance(
      (recording_receipt) => send({ sender_message_id: message_id, recording_receipt }),
      {
        submission_id: message_id,
        accepted: (conversation_id): Record<string, unknown> => ({
          target: recipient,
          conversation_id,
          fate: 'accepted',
          message_id,
          ...services.database.read((sql) => machine_waiting_fields(sql, conversation_id)),
        }),
        before_acceptance: result_json,
        failure_label: 'accepted message delivery failed',
      },
    );
  });
  app.post('/recap-requests', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const context = services.identity(request);
    const recipient = principal(request.body.target);
    if (!services.conversationSystem) throw new Error('conversation system is unavailable');
    return result_json(
      await request_recap(
        services.conversationSystem,
        services.database,
        services.clock,
        context,
        recipient,
      ),
    );
  });
};
