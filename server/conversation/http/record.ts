/** Public record encoding and the replay/live HTTP join. */
import { Readable } from 'node:stream';
import { PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS } from './configuration.ts';
import type { FastifyReply } from 'fastify';
import {
  ToolCallFinishedEventPayload,
  AgentMessageDeltaFrame,
  ToolCallProgressFrame,
  ModelThinkingFrame,
  HeldPromptsChangedFrame,
} from '../events.ts';
import type { ConversationLiveTailFrame } from '../events.ts';
import type { ConversationBackendKey } from '../contracts.ts';
import type { StoredConversationEvent } from '../record/contracts.ts';
import { conversation_event_payload_json_object } from '../record/codec.ts';
import { readable_tool_call_detail } from '../tool_detail.ts';
import type { ConversationViewRuntime } from '../views/routes.ts';
export function public_event(event: StoredConversationEvent, backend_key: ConversationBackendKey) {
  const payload = conversation_event_payload_json_object(event.payload);
  if (event.payload instanceof ToolCallFinishedEventPayload) {
    const readable = readable_tool_call_detail(event.payload.detail, { backend_key });
    const carried =
      readable === null
        ? null
        : Array.from(readable).slice(0, PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS).join('');
    payload.detail = carried;
    if (carried !== null && readable !== null && carried.length < readable.length)
      payload.detail_capped = true;
  }
  return {
    conversation_id: event.conversation_id,
    sequence: event.sequence,
    kind: event.kind,
    payload,
    created_at: event.created_at,
  };
}
function frame_json(frame: ConversationLiveTailFrame) {
  if (frame instanceof AgentMessageDeltaFrame)
    return { frame: 'agent_message_delta', text_delta: frame.text_delta };
  if (frame instanceof ToolCallProgressFrame)
    return { frame: 'tool_call_progress', tool_call_id: frame.tool_call_id, detail: frame.detail };
  if (frame instanceof ModelThinkingFrame) return { frame: 'model_thinking' };
  if (frame instanceof HeldPromptsChangedFrame) return { frame: 'held_prompts_changed' };
}
const stream_frame = (name: string, data: unknown) =>
  `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
export function send_event_tail(
  runtime: ConversationViewRuntime,
  id: string,
  backend: ConversationBackendKey,
  after: number,
  reply: FastifyReply,
) {
  // Subscribe before reading replay. Events queued during replay are suppressed by its high water.
  const subscription = runtime.live_tail.subscribe(id);
  async function* output() {
    try {
      const replayed = runtime.store.read_events_after(id, after);
      let highest = replayed.at(-1)?.sequence ?? after;
      for (const event of replayed)
        yield stream_frame('conversation-event', public_event(event, backend));
      yield stream_frame('conversation-frame', { frame: 'held_prompts_changed' });
      let pending = subscription.next_item();
      while (true) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        const heartbeat = Symbol();
        const item = await Promise.race([
          pending,
          new Promise<typeof heartbeat>((resolve) => {
            timer = setTimeout(() => resolve(heartbeat), runtime.sse_heartbeat_ms);
            timer.unref();
          }),
        ]).finally(() => clearTimeout(timer));
        if (item === heartbeat) {
          yield ': keep-alive\n\n';
          continue;
        }
        if (item === null) return;
        pending = subscription.next_item();
        if ('sequence' in item) {
          if (item.sequence <= highest) continue;
          highest = item.sequence;
          yield stream_frame('conversation-event', public_event(item, backend));
        } else {
          const frame = frame_json(item);
          if (frame) yield stream_frame('conversation-frame', frame);
        }
      }
    } finally {
      subscription.close();
    }
  }
  const source = output();
  const stream = Readable.from(reply.request.request_lifetime?.iterator(source) ?? source);
  reply.raw.once('close', () => {
    subscription.close();
    stream.destroy();
  });
  return reply.type('text/event-stream; charset=utf-8').send(stream);
}
