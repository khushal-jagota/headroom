/** Shape conversion only. The server projection owns visibility and turn membership. */
import type { ConversationViewRow, ViewRowBody, ViewRestEvent } from './contracts';
import type { MessagePiece } from '../wire';
import type { ViewMessagePiece } from './contracts';
import type { TranscriptRow } from '../transcript';
import { messageNotDelivered, refusalSentence } from '../transcript';
import { whatThisRowSays } from '../whatHappened';
import type { ConversationViewItem, ConversationViewPage, ViewHistoryScope, ViewPageCursor } from './contracts';

export function transcriptHistoryEdges(pages: readonly Pick<ConversationViewPage, 'scope' | 'items' | 'older' | 'newer'>[], items: readonly ConversationViewItem[]): {
    scope: ViewHistoryScope; cursor: ViewPageCursor; direction: 'older' | 'newer'; anchorKey: string | null;
}[] {
    const admitted = new Set(items.map(item => item.key));
    return pages.flatMap(page => {
        if (page.scope.kind === 'timeline') return [];
        const visible = page.items.filter(item => admitted.has(item.key));
        return (['older', 'newer'] as const).flatMap(direction => {
            const cursor = page[direction];
            if (!cursor) return [];
            const boundary = direction === 'older' ? page.items[0] : page.items.at(-1);
            const compare = (item: ConversationViewItem) => boundary
                ? item.order.sequence - boundary.order.sequence || item.order.slot - boundary.order.slot : 0;
            // An entirely hidden page still owns its cursors. Attach to its nearest
            // admitted neighbor, or retain a boundary without a visual row if none exists.
            const anchor = direction === 'older'
                ? visible[0] ?? items.find(item => compare(item) >= 0) ?? items.at(-1)
                : visible.at(-1) ?? [...items].reverse().find(item => compare(item) <= 0) ?? items[0];
            return [{ scope: page.scope, cursor, direction, anchorKey: anchor?.key ?? null }];
        });
    });
}
function pieces(content: ViewMessagePiece[]): MessagePiece[] { return content.map(piece => piece.piece === 'image' ? { ...piece, file_name: piece.file_name ?? undefined } : piece); }
function uncachedRowForBody(item: Pick<ConversationViewRow, 'key' | 'source_sequence' | 'created_at'> & Partial<Pick<ConversationViewRow, 'sender_message_id'>>, body: ViewRowBody): TranscriptRow {
    const base = { key: item.key, sequence: item.source_sequence, createdAt: item.created_at };
    switch (body.kind) {
        case 'credential_request': return { ...base, kind: body.kind, request: body };
        case 'agent_turn_started': return { ...base, kind: body.kind, reason: body.reason };
        case 'background_task_started': return { ...base, kind: body.kind, taskDescription: body.task_description };
        case 'background_task_ended': return { ...base, kind: body.kind, taskDescription: body.task_description, taskStatus: body.task_status };
        case 'prompt': return { ...base, kind: body.kind, content: pieces(body.content), senderLabel: body.sender_label, senderMessageId: item.sender_message_id ?? null, mode: body.mode, sentAtUnixMilliseconds: body.sent_at_unix_milliseconds };
        case 'prompt_refused': return { ...base, kind: body.kind, content: pieces(body.content), senderLabel: body.sender_label, senderMessageId: item.sender_message_id ?? null, reason: body.reason, sentence: refusalSentence(body.reason, body.refusal_detail) };
        case 'prompt_uncertain':
        case 'prompt_discarded': return { ...base, kind: body.kind, content: pieces(body.content), senderLabel: body.sender_label, senderMessageId: item.sender_message_id ?? null };
        case 'agent_message': return { ...base, kind: body.kind, content: pieces(body.content), toOwner: body.to_owner };
        case 'tool_call': return { ...base, kind: body.kind, toolCallId: body.tool_call_id, title: body.title, toolKind: body.tool_kind, detail: body.detail, startedDetail: body.started_detail, cappedDetailSequence: body.capped_detail_sequence, status: body.status, progress: null };
        case 'permission_ask': return { ...base, kind: body.kind, askId: body.ask_id, title: body.title, detail: body.detail, options: body.options, state: body.state, deadReason: body.dead_reason, answeredOptionLabel: body.answered_option_label, answerFate:body.answer_fate };
        case 'user_input': return { ...base, kind: body.kind, requestId: body.request_id, questions: body.questions, state: body.state, deadReason: body.dead_reason, answers: body.answers === null ? null : Object.fromEntries(body.answers.map(a => [a.question_id, { answers: a.answers, ...(a.attachments?.length ? { attachments: pieces(a.attachments) } : {}) }])), failureDetail: body.failure_detail, answerFate:body.answer_fate };
        case 'model_changed': return { ...base, kind: body.kind, model: body.model, reasoningEffort: body.reasoning_effort };
        case 'token_usage': return { ...base, kind: body.kind, inputTokens: body.input_tokens, outputTokens: body.output_tokens, cachedInputTokens: body.cached_input_tokens, costUsd: body.cost_usd };
        case 'turn_ended': return { ...base, kind: body.kind, ending: body.ending, errorSummary: body.error_summary, automaticCompactionResult: body.automatic_compaction_result };
        case 'command_reply': return { ...base, kind: body.kind, replyText: body.reply_text };
        case 'context_compacted':
        case 'turn_stopped': return { ...base, kind: body.kind };
    }
}
const rowsByBody = new WeakMap<ViewRowBody, {
    key: string;
    row: TranscriptRow;
}>();
export function rowForBody(item: Pick<ConversationViewRow, 'key' | 'source_sequence' | 'created_at'> & Partial<Pick<ConversationViewRow, 'sender_message_id'>>, body: ViewRowBody): TranscriptRow {
    const cached = rowsByBody.get(body);
    if (cached?.key === item.key)
        return cached.row;
    const row = uncachedRowForBody(item, body);
    rowsByBody.set(body, { key: item.key, row });
    return row;
}
export function happened(event: ViewRestEvent | null, sender: string): {
    who: string | null;
    text: string;
} | null {
    if (event === null)
        return null;
    if ('excerpt' in event)
        return { who: event.kind === 'prompt' ? (event.sender_label === sender ? 'you' : event.sender_label) : null, text: event.excerpt };
    if (event.kind === 'tool_call')
        return event.body.kind === 'inline' ? whatThisRowSays(rowForBody({ key: 'rest', source_sequence: 0, created_at: 0 }, event.body.value), sender) : { who: null, text: event.title };
    if (event.kind === 'prompt_refused' || event.kind === 'prompt_uncertain' || event.kind === 'prompt_discarded') {
        const text = event.kind === 'prompt_refused' ? messageNotDelivered(refusalSentence(event.refusal_reason!)) : event.kind === 'prompt_uncertain' ? 'delivery uncertain · do not resend' : 'discarded';
        return { who: event.sender_label === sender ? 'you' : event.sender_label, text };
    }
    if (event.kind === 'turn_ended' || event.kind === 'context_compacted' || event.kind === 'turn_stopped')
        return whatThisRowSays(rowForBody({ key: 'rest', source_sequence: 0, created_at: 0 }, event), sender);
    return null;
}
