/** Public record encoding and the replay/live HTTP join. */
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import { PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS } from './configuration.ts';
import {
  ToolCallFinishedEventPayload,
  AgentMessageDeltaFrame,
  ToolCallProgressFrame,
  ModelThinkingFrame,
  HeldPromptsChangedFrame,
} from '../events.ts';
import type { ConversationLiveTailFrame } from '../events.ts';
import type { StoredConversationEvent } from '../record/contracts.ts';
import { conversation_event_payload_json_object } from '../record/codec.ts';
import { readable_tool_call_detail } from '../tool_detail.ts';
import type { ConversationViewRuntime } from '../views/routes.ts';
import type { LiveFrame } from '../../live_updates/contracts.ts';
export function public_event(event: StoredConversationEvent, backend_key: BackendInstanceId) {
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
const live_frame = (event: string, data: unknown): LiveFrame => ({
  event,
  data: JSON.stringify(data),
});
/**
 * Opens the committed-event tail of one conversation. A null frame is a quiet keep-alive.
 * The event stream and the socket read the same frames.
 */
export function open_event_tail(
  runtime: ConversationViewRuntime,
  id: string,
  backend: BackendInstanceId,
  after: number,
) {
  // Subscribe before reading replay. Events queued during replay are suppressed by its high water.
  const subscription = runtime.live_tail.subscribe(id);
  async function* frames(): AsyncGenerator<LiveFrame | null> {
    try {
      const replayed = runtime.store.read_events_after(id, after);
      let highest = replayed.at(-1)?.sequence ?? after;
      for (const event of replayed)
        yield live_frame('conversation-event', public_event(event, backend));
      yield live_frame('conversation-frame', { frame: 'held_prompts_changed' });
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
          yield null;
          continue;
        }
        if (item === null) return;
        pending = subscription.next_item();
        if ('sequence' in item) {
          if (item.sequence <= highest) continue;
          highest = item.sequence;
          yield live_frame('conversation-event', public_event(item, backend));
        } else {
          const frame = frame_json(item);
          if (frame) yield live_frame('conversation-frame', frame);
        }
      }
    } finally {
      subscription.close();
    }
  }
  return { frames: frames(), close: () => subscription.close() };
}
