/** Pure compact rest-line facts, without presentation labels. */
import type { ConversationEventPayload } from '../../events.ts';
import { message_content_text } from '../../message_content.ts';
import { OWNER_MESSAGE_PREVIEW_CHARACTERS, REST_EXCERPT_CHARACTERS } from '../configuration.ts';
function strip(text: string): string {
    return text.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, '');
}
function rstrip(text: string): string {
    return text.replace(/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, '');
}
export function excerpt(text: string): string {
    const first = strip(text.split('\n', 1)[0]);
    const characters = Array.from(first);
    if (characters.length <= REST_EXCERPT_CHARACTERS) {
        return first;
    }
    return rstrip(characters.slice(0, REST_EXCERPT_CHARACTERS - 1).join('')) + '…';
}
export function owner_message_preview(text: string): string | null {
    const trimmed = strip(text);
    if (!trimmed) {
        return null;
    }
    const characters = Array.from(trimmed);
    if (characters.length <= OWNER_MESSAGE_PREVIEW_CHARACTERS) {
        return trimmed;
    }
    return rstrip(characters.slice(0, OWNER_MESSAGE_PREVIEW_CHARACTERS - 1).join('')) + '…';
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
