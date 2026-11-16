/** Pure compact rest-line facts, without presentation labels. */
import type { ConversationEventPayload } from '../../events.ts';
import { message_content_text } from '../../message_content.ts';
import { OWNER_MESSAGE_PREVIEW_CHARACTERS, REST_EXCERPT_CHARACTERS } from '../configuration.ts';
export function excerpt(text: string): string {
  const first = text.split('\n', 1)[0]!.trim();
  const characters = Array.from(first);
  if (characters.length <= REST_EXCERPT_CHARACTERS) {
    return first;
  }
  return (
    characters
      .slice(0, REST_EXCERPT_CHARACTERS - 1)
      .join('')
      .trimEnd() + '…'
  );
}
export function owner_message_preview(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  const characters = Array.from(trimmed);
  if (characters.length <= OWNER_MESSAGE_PREVIEW_CHARACTERS) {
    return trimmed;
  }
  return (
    characters
      .slice(0, OWNER_MESSAGE_PREVIEW_CHARACTERS - 1)
      .join('')
      .trimEnd() + '…'
  );
}
export function event_has_rest_text(payload: ConversationEventPayload): boolean {
  switch (payload.kind) {
    case 'prompt':
    case 'agent_message':
    case 'message_to_owner':
      return Boolean(excerpt(message_content_text(payload.content)));
    case 'turn_ended':
      return payload.ending !== 'completed';
    case 'prompt_delivery_refused':
    case 'prompt_delivery_uncertain':
    case 'prompt_discarded':
    case 'tool_call_started':
    case 'tool_call_finished':
      return true;
    default:
      return false;
  }
}
