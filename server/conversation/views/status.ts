/** Compact facts for the resting composer, independent from transcript residency. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { message_content_text } from '../message_content.ts';
import { STATUS_INLINE_BODY_BYTES } from './configuration.ts';
import type { ConversationViewLens, ConversationViewStatus, ViewBackgroundTask, ViewBodyChunk, ViewBodyReference, ViewLiveOutput, ViewPlanProgress, ViewRequestIdentity, ViewRestEvent, ViewRestTool, } from './contracts.ts';
import { excerpt } from './logic/rest.ts';
import { _body, _body_reference, _payload, _row_record, encode_wire, read_turn } from './data/storage.ts';
/** The status reader consumes runtime output through this narrow synchronous seam. */
export interface ViewStatusLiveOutput {
    readonly runtime_epoch: string;
    output(conversation_id: string): ViewLiveOutput | null;
    read_chunk(conversation_id: string, reference: ViewBodyReference, offset: number): ViewBodyChunk;
    status(conversation_id: string): readonly [
        number,
        number | null
    ];
    latest_tool_id(conversation_id: string): string | null;
    progress(conversation_id: string): ViewBodyReference | null;
}
export function project_rest_event(sql: ReadSql, conversation_id: string, sequence: number): ViewRestEvent | null {
    const row = _row_record(sql, conversation_id, sequence);
    const body = _body(sql, conversation_id, row);
    if (body.kind === 'prompt' || body.kind === 'agent_message') {
        const source = _payload(sql, conversation_id, sequence);
        if (source.kind !== 'prompt' && source.kind !== 'agent_message' && source.kind !== 'message_to_owner') {
            throw new Error('Message row source does not match its kind');
        }
        const text = excerpt(message_content_text(source.content));
        if (!text) {
            return null;
        }
        return { kind: body.kind, sender_label: source.kind === 'prompt' ? source.sender_label : null, excerpt: text };
    }
    if (body.kind === 'prompt_refused') {
        return { kind: 'prompt_refused', sender_label: body.sender_label, refusal_reason: body.reason,
            refusal_detail_excerpt: excerpt(body.refusal_detail ?? '') || null };
    }
    if (body.kind === 'prompt_uncertain' || body.kind === 'prompt_discarded') {
        return { kind: body.kind, sender_label: body.sender_label, refusal_reason: null, refusal_detail_excerpt: null };
    }
    if (body.kind === 'tool_call') {
        const encoded = Buffer.from(encode_wire(body), 'utf8');
        return { kind: 'tool_call', tool_call_id: body.tool_call_id, title: excerpt(body.title), tool_kind: body.tool_kind,
            body: encoded.byteLength <= STATUS_INLINE_BODY_BYTES
                ? { kind: 'inline', value: body }
                : { kind: 'deferred', reference: _body_reference(row, encoded) } };
    }
    if (body.kind === 'turn_ended' && body.ending !== 'completed') {
        return { ...body, error_summary: excerpt(body.error_summary ?? '') || null };
    }
    return null;
}
function rest_event(sql: ReadSql, conversation_id: string, sequence: number): ViewRestEvent | null {
    const row = sql.get<{
        rest_summary: string | null;
    }>('SELECT rest_summary FROM conversation_view_rows WHERE conversation_id=? AND sequence=?', [conversation_id, sequence]);
    if (!row?.rest_summary) {
        return null;
    }
    const summary = JSON.parse(row.rest_summary) as Record<string, unknown>;
    delete summary._owner_message_preview;
    return Object.keys(summary).length ? summary as unknown as ViewRestEvent : null;
}
function request_identity(sql: ReadSql, conversation_id: string, kind: 'permission' | 'user_input', allowed_ids: readonly string[]): ViewRequestIdentity | null {
    let best: {
        sequence: number;
        identity: string;
    } | null = null;
    for (const identity of allowed_ids) {
        const row = sql.get<{
            source_sequence: number;
        }>('SELECT source_sequence FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind=? AND lookup_id=?', [conversation_id, kind, identity]);
        if (row !== undefined && (best === null || row.source_sequence > best.sequence)) {
            best = { sequence: row.source_sequence, identity };
        }
    }
    return best === null ? null : { kind, request_id: best.identity, row_key: `e${best.sequence}`, source_sequence: best.sequence };
}
interface StateRow {
    current_turn_key: string | null;
    latest_focus_happened_sequence: number | null;
    latest_full_happened_sequence: number | null;
    plan_sequence: number | null;
    plan_body_bytes: number;
    plan_summary: string;
    diagnostics_expired: number;
}
export function read_status(sql: ReadSql, conversation_id: string, lens: ConversationViewLens, live: ViewStatusLiveOutput, options: {
    running: boolean | null;
    permission_ids?: readonly string[];
    input_ids?: readonly string[];
}): ConversationViewStatus {
    const { running, permission_ids = [], input_ids = [] } = options;
    const state = sql.get<StateRow>('SELECT s.*,EXISTS(SELECT 1 FROM conversation_expired_ranges e WHERE e.conversation_id=s.conversation_id) AS diagnostics_expired '
        + 'FROM conversation_view_state s WHERE s.conversation_id=?', [conversation_id]);
    const turn_key = state?.current_turn_key ?? null;
    const stopped = running === false && turn_key !== null;
    const latest_head = sql.get<{
        turn_key: string;
    }>('SELECT turn_key FROM conversation_view_turns WHERE conversation_id=? '
        + (lens === 'focus' ? 'AND focus_head=1 ' : '') + 'ORDER BY source_sequence DESC LIMIT 1', [conversation_id]);
    const latest_turn = latest_head === undefined ? null : read_turn(sql, conversation_id, latest_head.turn_key, lens, { stopped });
    const sequence = lens === 'focus' ? state?.latest_focus_happened_sequence : state?.latest_full_happened_sequence;
    let latest_happened: ViewRestEvent | null = sequence == null ? null : rest_event(sql, conversation_id, sequence);
    if (stopped) {
        latest_happened = { kind: 'turn_stopped' };
    }
    const output = lens === 'full' ? live.output(conversation_id) : null;
    if (output !== null && output.byte_count > 0 && !stopped) {
        const chunk = live.read_chunk(conversation_id, output.reference, 0);
        latest_happened = { kind: 'streaming_agent_message', sender_label: null, excerpt: excerpt(chunk.text) };
    }
    let latest_tool: ViewRestTool | null = null;
    if (lens === 'full' && latest_turn !== null) {
        const tool = sql.get<{
            sequence: number;
        }>("SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND turn_key=? AND row_kind='tool_call' AND removed=0 ORDER BY sequence DESC LIMIT 1", [conversation_id, latest_turn.key]);
        if (tool !== undefined) {
            const found = rest_event(sql, conversation_id, tool.sequence);
            latest_tool = found?.kind === 'tool_call' ? found : null;
        }
    }
    const permission = request_identity(sql, conversation_id, 'permission', permission_ids);
    const user_input = request_identity(sql, conversation_id, 'user_input', input_ids);
    const permission_title = permission === null ? null : sql.get<{
        permission_title_excerpt: string | null;
    }>('SELECT permission_title_excerpt FROM conversation_view_rows WHERE conversation_id=? AND sequence=?', [conversation_id, permission.source_sequence])?.permission_title_excerpt ?? null;
    let plan_reference: ViewBodyReference | null = null;
    let progress: ViewPlanProgress = { total_count: 0, completed_count: 0, current_position: null, current_text_excerpt: null, has_unfinished_entry: false };
    if (state?.plan_sequence != null) {
        plan_reference = { body_id: `plan:${state.plan_sequence}`, version: String(state.plan_sequence),
            byte_count: state.plan_body_bytes, media_type: 'application/json' };
        progress = JSON.parse(state.plan_summary) as ViewPlanProgress;
    }
    const owner_message = sql.get<{
        sequence: number;
        rest_summary: string | null;
    }>("SELECT sequence,rest_summary FROM conversation_view_rows WHERE conversation_id=? AND row_kind='agent_message' AND focus_visible=1 AND removed=0 ORDER BY sequence DESC LIMIT 1", [conversation_id]);
    const boundary = sql.get<{
        row_kind: string;
        sequence: number;
    }>("SELECT row_kind,sequence FROM conversation_view_rows WHERE conversation_id=? AND row_kind IN ('prompt','turn_ended') AND removed=0 ORDER BY sequence DESC LIMIT 1", [conversation_id]);
    let failed = false;
    if (boundary?.row_kind === 'turn_ended') {
        const ending = _payload(sql, conversation_id, boundary.sequence);
        failed = ending.kind === 'turn_ended' && ending.ending === 'failed';
    }
    const background_tasks = sql.all<ViewBackgroundTask>('SELECT task_id,task_description FROM conversation_background_tasks WHERE conversation_id=? ORDER BY rowid', [conversation_id]);
    const [revision, activity_at] = live.status(conversation_id);
    return {
        runtime_epoch: live.runtime_epoch,
        revision,
        activity_at_unix_milliseconds: activity_at,
        rest: { latest_happened, latest_turn, latest_tool, permission_title_excerpt: permission_title,
            latest_owner_message_sequence: owner_message?.sequence ?? 0,
            latest_owner_message_preview: owner_message?.rest_summary ? (JSON.parse(owner_message.rest_summary) as {
                _owner_message_preview?: string | null;
            })._owner_message_preview ?? null : null,
            last_boundary_failed: failed, plan_progress: progress },
        authority: running === null ? 'unknown' : 'current',
        diagnostics_expired: Boolean(state?.diagnostics_expired),
        turn_key,
        turn_running: running,
        stopped_without_ending: stopped,
        background_tasks,
        pending_permission: permission,
        pending_user_input: user_input,
        plan: plan_reference,
        live_output: output,
        live_tool_key: lens === 'full' ? live.latest_tool_id(conversation_id) : null,
        live_tool_progress: lens === 'full' ? live.progress(conversation_id) : null,
    };
}
