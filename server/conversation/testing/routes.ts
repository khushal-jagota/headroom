/** Test-only access to the existing fake. No production conversation routes live here. */
import type { Area } from '../../core/authority/register.ts';
import type { PromptDeliveryFate } from '../contracts.ts';
import {
  ConversationStartRequest, ConversationRoleMaterials,
  PromptDeliveryStarted, PromptDeliveryQueued, PromptDeliveryInjected,
  PromptDeliveryRefused, PromptDeliveryUncertain, PromptDeliverySuppressed,
} from '../contracts.ts';
import { InMemoryConversationSystem } from '../in_memory_conversation_system.ts';
import { message_content_from_stored, MessageText, MessageImage, MessageFile } from '../message_content.ts';

type SendOptions = Parameters<InMemoryConversationSystem['send']>[2];
type SendBody = SendOptions & { text?: string; content?: unknown };

function deliveryAnswer(fate: PromptDeliveryFate): Record<string, unknown> {
  if (fate instanceof PromptDeliveryStarted) return { fate: 'started' };
  if (fate instanceof PromptDeliveryQueued) return { fate: 'queued', queue_position: fate.queue_position };
  if (fate instanceof PromptDeliveryInjected) return { fate: 'injected' };
  if (fate instanceof PromptDeliveryRefused) return { fate: 'refused', refusal_reason: fate.refusal_reason };
  if (fate instanceof PromptDeliveryUncertain) return { fate: 'uncertain' };
  if (fate instanceof PromptDeliverySuppressed) return { fate: 'suppressed', reason: fate.reason };
  throw new TypeError('unknown delivery fate');
}

/** Match dataclass fields, with the message JSON discriminant at this HTTP boundary. */
function snapshot(value: unknown): unknown {
  if (value instanceof MessageText) return { piece: 'text', text: value.text };
  if (value instanceof MessageImage) return {
    piece: 'image', stored_file_id: value.stored_file_id,
    media_type: value.media_type, file_name: value.file_name,
  };
  if (value instanceof MessageFile) return {
    piece: 'file', stored_file_id: value.stored_file_id, media_type: value.media_type,
    file_name: value.file_name, byte_count: value.byte_count,
  };
  if (Array.isArray(value)) return value.map(snapshot);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, snapshot(item)]));
  }
  return value;
}

export function inMemoryConversationRoutes(system: InMemoryConversationSystem): readonly Area[] {
  return [{ prefix: '/api/test/in-memory-conversations', routes: async (app) => {
    app.post<{ Body: ConversationStartRequest }>('/start', async request => {
      const role = request.body.role_materials;
      await system.start_conversation(new ConversationStartRequest({
        ...request.body,
        role_materials: role == null ? null : new ConversationRoleMaterials(role),
      }));
      return { started: true };
    });
    app.post<{ Params: { conversation_id: string }; Body: SendBody }>('/:conversation_id/send', async request => {
      const { text, content, ...options } = request.body;
      const message = message_content_from_stored({ text, content });
      return deliveryAnswer(await system.send(request.params.conversation_id, message, options));
    });
    app.get<{ Params: { conversation_id: string } }>('/:conversation_id/state', async request => {
      const id = request.params.conversation_id;
      return {
        running: await system.is_running(id),
        workspace_activity: system.workspace_activity(),
        held_prompts: snapshot(await system.held_prompts(id)),
        observations: snapshot(system.observations(id)),
        backend_prompt_writes: snapshot(system.backend_prompt_writes(id)),
      };
    });
    app.post<{ Params: { conversation_id: string } }>('/:conversation_id/complete', async request => {
      system.complete_running_turn(request.params.conversation_id);
      return { complete: true };
    });
  } }];
}
