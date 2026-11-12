/** General send and owner recap intake; the service owns dispatch. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import type { Principal } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { dictionaryBody } from '../list_reads/http.ts';
import { delivery_fate_json } from '../conversation/http.ts';
import type { PromptDeliveryMode } from '../conversation/contracts.ts';
import { MessageRecordedToOwner } from './contracts.ts';
import type { MessageDeliveryResult } from './contracts.ts';
import { send_message } from './service.ts';
import { request_recap } from './recap_request.ts';

function principal(raw: unknown): Principal {
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
function result_json(result: MessageDeliveryResult): Record<string, unknown> {
  return {
    target: result.recipient,
    conversation_id: result.conversation_id,
    ...(result.fate instanceof MessageRecordedToOwner
      ? { fate: 'recorded' }
      : delivery_fate_json(result.fate)),
  };
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.post('/messages/send', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const recipient = principal(request.body.target);
    const message = request.body.message;
    if (typeof message !== 'string' || !message.trim()) {
      throw new PlannerError('validation', 'message must not be empty');
    }
    const mode = 'mode' in request.body ? request.body.mode : 'steer';
    if (typeof mode !== 'string' || !['queue', 'steer', 'send_now'].includes(mode)) {
      throw new PlannerError('validation', 'mode must be queue, steer, or send_now', { mode });
    }
    if (!services.conversationSystem) throw new Error('conversation system is unavailable');
    return result_json(
      await send_message(
        services.conversationSystem,
        services.database,
        services.clock,
        services.identity(request),
        recipient,
        message,
        mode as PromptDeliveryMode,
      ),
    );
  });
  app.post('/recap-requests', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const context = services.identity(request);
    requireOwner(context);
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
