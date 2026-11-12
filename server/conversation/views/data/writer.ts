/** Transactional projection writes. Source bodies remain in canonical event rows. */
import type { ReadSql, TransactionSql } from '../../../core/database/contracts.ts';
import type { ConversationEventKind, ConversationEventPayload } from '../../events.ts';
import type { StoredConversationEvent } from '../../record/contracts.ts';
import { conversation_event_payload_from_canonical_json } from '../../record/codec.ts';
import { message_content_text } from '../../message_content.ts';
import { PROJECTION_BACKFILL_BATCH } from '../configuration.ts';
import { VIEW_PROJECTION_VERSION } from '../contracts.ts';
import { sent_at_milliseconds } from '../logic/bodies.ts';
import { event_effect } from '../logic/effects.ts';
import { event_has_rest_text, excerpt, owner_message_preview } from '../logic/rest.ts';
import { project_rest_event } from '../status.ts';
import { encode_wire } from './storage.ts';
function source(sql: ReadSql, conversation_id: string, sequence: number): ConversationEventPayload {
    const row = sql.get<{
        kind: ConversationEventKind;
        payload: string;
    }>('SELECT kind,payload FROM conversation_events WHERE conversation_id=? AND sequence=?', [conversation_id, sequence]);
    if (row === undefined) {
        throw new Error('Projection source event is absent');
    }
    return conversation_event_payload_from_canonical_json(row.kind, row.payload);
}
function state(sql: ReadSql, conversation_id: string): [
    string | null,
    string | null
] {
    const row = sql.get<{
        current_turn_key: string | null;
        current_group_key: string | null;
    }>('SELECT current_turn_key,current_group_key FROM conversation_view_state WHERE conversation_id=?', [conversation_id]);
    if (row === undefined) {
        throw new Error('Projection state is absent');
    }
    return [row.current_turn_key, row.current_group_key];
}
export function append_projection(tx: TransactionSql, events: Iterable<StoredConversationEvent>): void {
    for (const stored of events) {
        append(tx, stored.conversation_id, stored.sequence, stored.created_at, stored.payload);
    }
}
function rest_references(sql: ReadSql, conversation_id: string): Set<number> {
    const row = sql.get<{
        latest_full_happened_sequence: number | null;
        latest_focus_happened_sequence: number | null;
        current_turn_key: string | null;
    }>('SELECT latest_full_happened_sequence,latest_focus_happened_sequence,current_turn_key FROM conversation_view_state WHERE conversation_id=?', [conversation_id]);
    const result = new Set<number>();
    if (row === undefined) {
        return result;
    }
    if (row.latest_full_happened_sequence !== null) {
        result.add(row.latest_full_happened_sequence);
    }
    if (row.latest_focus_happened_sequence !== null) {
        result.add(row.latest_focus_happened_sequence);
    }
    if (row.current_turn_key !== null) {
        const tool = sql.get<{
            sequence: number;
        }>("SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND turn_key=? AND row_kind='tool_call' AND removed=0 ORDER BY sequence DESC LIMIT 1", [conversation_id, row.current_turn_key]);
        if (tool !== undefined) {
            result.add(tool.sequence);
        }
    }
    const owner = sql.get<{
        sequence: number;
    }>("SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND row_kind='agent_message' AND focus_visible=1 AND removed=0 ORDER BY sequence DESC LIMIT 1", [conversation_id]);
    if (owner !== undefined) {
        result.add(owner.sequence);
    }
    return result;
}
function rest_summary(sql: ReadSql, conversation_id: string, sequence: number, payload: ConversationEventPayload): string | null {
    const summary: Record<string, unknown> = { ...(project_rest_event(sql, conversation_id, sequence) ?? {}) };
    if (payload.kind === 'message_to_owner') {
        summary._owner_message_preview = owner_message_preview(message_content_text(payload.content));
    }
    return Object.keys(summary).length ? encode_wire(summary) : null;
}
/** Python json.dumps defaults are used for the stored plan summary only. */
function plan_summary_json(value: unknown): string {
    if (value === null) {
        return 'null';
    }
    if (typeof value === 'string') {
        return JSON.stringify(value).replace(/[\u007f-\uffff]/g, character => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'));
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
        return JSON.stringify(value);
    }
    if (Array.isArray(value)) {
        return '[' + value.map(plan_summary_json).join(', ') + ']';
    }
    return '{' + Object.entries(value as Record<string, unknown>).map(([key, item]) => plan_summary_json(key) + ': ' + plan_summary_json(item)).join(', ') + '}';
}
function append(tx: TransactionSql, conversation_id: string, sequence: number, created_at: number, payload: ConversationEventPayload): void {
    tx.run('INSERT OR IGNORE INTO conversation_view_state(conversation_id,projection_version) VALUES (?,?)', [conversation_id, VIEW_PROJECTION_VERSION]);
    const previous_summaries = rest_references(tx, conversation_id);
    const effect = event_effect(payload);
    let [turn_key, group_key] = state(tx, conversation_id);
    const removed: number[] = [];
    if (effect.sender_message_id !== null) {
        const previous = tx.get<{
            source_sequence: number;
        }>("SELECT source_sequence FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind='sender_message' AND lookup_id=?", [conversation_id, effect.sender_message_id]);
        if (previous !== undefined) {
            removed.push(previous.source_sequence);
        }
        lookup(tx, conversation_id, 'sender_message', effect.sender_message_id, sequence, turn_key);
    }
    if (effect.reconciles_sequence !== null && effect.reconciles_sequence < sequence) {
        const previous = tx.get<{
            sequence: number;
        }>("SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND sequence=? AND row_kind='prompt_uncertain'", [conversation_id, effect.reconciles_sequence]);
        if (previous !== undefined) {
            removed.push(previous.sequence);
        }
    }
    for (const previous_sequence of new Set(removed)) {
        tx.run('UPDATE conversation_view_rows SET removed=1,changed_sequence=? WHERE conversation_id=? AND sequence=?', [sequence, conversation_id, previous_sequence]);
    }
    let matched: {
        source_sequence: number;
    } | undefined;
    if (effect.settlement && effect.lookup_kind !== null) {
        matched = tx.get<{
            source_sequence: number;
        }>('SELECT source_sequence FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind=? AND lookup_id=?', [conversation_id, effect.lookup_kind, effect.lookup_id]);
        if (matched !== undefined) {
            tx.run('UPDATE conversation_view_rows SET settlement_sequence=?,changed_sequence=? WHERE conversation_id=? AND sequence=?', [sequence, sequence, conversation_id, matched.source_sequence]);
            if (effect.lookup_kind === 'user_input') {
                tx.run('DELETE FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind=? AND lookup_id=?', [conversation_id, effect.lookup_kind, effect.lookup_id]);
            }
        }
    }
    if ((payload.kind === 'permission_answered' || payload.kind === 'user_input_answered')
        && payload.fate === 'saved' && payload.answer_id && matched !== undefined) {
        lookup(tx, conversation_id, 'saved_answer', payload.answer_id, matched.source_sequence, turn_key);
    }
    if (payload.kind === 'answer_applied') {
        const original = tx.get<{
            source_sequence: number;
        }>("SELECT source_sequence FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind='saved_answer' AND lookup_id=?", [conversation_id, payload.answer_id]);
        if (original !== undefined) {
            tx.run('UPDATE conversation_view_rows SET changed_sequence=? WHERE conversation_id=? AND sequence=?', [sequence, conversation_id, original.source_sequence]);
            lookup(tx, conversation_id, 'applied_answer', payload.answer_id, sequence, turn_key);
        }
    }
    if (effect.row_kind !== null && matched === undefined) {
        tx.run('INSERT INTO conversation_view_rows(conversation_id,sequence,row_kind,created_at,changed_sequence,focus_visible,sender_message_id) VALUES (?,?,?,?,?,?,?)', [conversation_id, sequence, effect.row_kind, created_at, sequence, Number(effect.focus_visible), effect.sender_message_id]);
        [turn_key, group_key] = layout_row(tx, conversation_id, sequence, created_at, payload, effect.row_kind, effect.focus_visible, turn_key, group_key, sequence);
        if (effect.lookup_kind !== null && (!effect.settlement || effect.lookup_kind === 'credential_request')) {
            lookup(tx, conversation_id, effect.lookup_kind, effect.lookup_id, sequence, turn_key);
        }
    }
    if (effect.row_kind !== null || matched !== undefined) {
        const summary_sequence = matched?.source_sequence ?? sequence;
        previous_summaries.add(summary_sequence);
        const summary = rest_summary(tx, conversation_id, summary_sequence, payload);
        const permission_title = payload.kind === 'permission_asked' ? excerpt(payload.title) || excerpt(payload.detail ?? '') : null;
        tx.run('UPDATE conversation_view_rows SET rest_summary=?,permission_title_excerpt=COALESCE(?,permission_title_excerpt) WHERE conversation_id=? AND sequence=?', [summary, permission_title, conversation_id, summary_sequence]);
    }
    if (event_has_rest_text(payload)) {
        const happened_sequence = matched?.source_sequence ?? sequence;
        tx.run('UPDATE conversation_view_state SET latest_full_happened_sequence=MAX(COALESCE(latest_full_happened_sequence,0),?),'
            + 'latest_focus_happened_sequence=CASE WHEN ? THEN MAX(COALESCE(latest_focus_happened_sequence,0),?) ELSE latest_focus_happened_sequence END WHERE conversation_id=?', [happened_sequence, Number(effect.focus_visible), happened_sequence, conversation_id]);
    }
    if (payload.kind === 'plan_updated') {
        const current = payload.entries.findIndex(entry => entry.status === 'in_progress');
        const summary = { total_count: payload.entries.length, completed_count: payload.entries.filter(entry => entry.status === 'completed').length,
            current_position: current < 0 ? null : current + 1, current_text_excerpt: current < 0 ? null : excerpt(payload.entries[current].text),
            has_unfinished_entry: payload.entries.some(entry => entry.status !== 'completed') };
        const body = encode_wire({ entries: payload.entries.map(entry => ({ text: entry.text, status: entry.status })) });
        tx.run('UPDATE conversation_view_state SET plan_sequence=?,plan_summary=?,plan_body_bytes=? WHERE conversation_id=?', [sequence, plan_summary_json(summary), Buffer.byteLength(body, 'utf8'), conversation_id]);
    }
    if (effect.closes_turn) {
        tx.run("DELETE FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind IN ('permission','user_input')", [conversation_id]);
    }
    tx.run('UPDATE conversation_view_state SET through_sequence=?,current_turn_key=?,current_group_key=? WHERE conversation_id=?', [sequence, turn_key, group_key, conversation_id]);
    if (removed.length > 0) {
        repair_removed_boundaries(tx, conversation_id, Math.min(...removed), sequence);
        repair_rest_pointer(tx, conversation_id);
    }
    const keep_summaries = rest_references(tx, conversation_id);
    for (const obsolete of previous_summaries) {
        if (!keep_summaries.has(obsolete)) {
            tx.run('UPDATE conversation_view_rows SET rest_summary=NULL WHERE conversation_id=? AND sequence=?', [conversation_id, obsolete]);
        }
    }
    for (const referenced of keep_summaries) {
        const summary = tx.get<{
            rest_summary: string | null;
        }>('SELECT rest_summary FROM conversation_view_rows WHERE conversation_id=? AND sequence=?', [conversation_id, referenced]);
        if (summary !== undefined && (summary.rest_summary === null || effect.closes_turn || removed.length > 0)) {
            const projected = rest_summary(tx, conversation_id, referenced, source(tx, conversation_id, referenced));
            if (projected !== null) {
                tx.run('UPDATE conversation_view_rows SET rest_summary=? WHERE conversation_id=? AND sequence=?', [projected, conversation_id, referenced]);
            }
        }
    }
}
function lookup(tx: TransactionSql, conversation_id: string, kind: string, identity: string | null, sequence: number, turn_key: string | null): void {
    if (identity === null) {
        return;
    }
    tx.run('INSERT INTO conversation_view_lookups VALUES (?,?,?,?,?) ON CONFLICT(conversation_id,lookup_kind,lookup_id) DO UPDATE SET source_sequence=excluded.source_sequence,turn_key=excluded.turn_key', [conversation_id, kind, identity, sequence, turn_key]);
}
function layout_row(tx: TransactionSql, conversation_id: string, sequence: number, created_at: number, payload: ConversationEventPayload, row_kind: string, focus_visible: boolean, turn_key: string | null, group_key: string | null, changed_sequence: number): [
    string | null,
    string | null
] {
    let row_group: string | null = null;
    if (row_kind === 'tool_call') {
        if (group_key === null) {
            group_key = `work:e${sequence}`;
            tx.run('INSERT INTO conversation_view_groups(conversation_id,group_key,source_sequence,turn_key,entry_count,latest_entry_sequence,changed_sequence) VALUES (?,?,?,?,?,?,?)', [conversation_id, group_key, sequence, turn_key, 0, sequence, changed_sequence]);
        }
        row_group = group_key;
        tx.run('UPDATE conversation_view_groups SET entry_count=entry_count+1,latest_entry_sequence=?,changed_sequence=? WHERE conversation_id=? AND group_key=?', [sequence, changed_sequence, conversation_id, group_key]);
        if (turn_key !== null) {
            tx.run('UPDATE conversation_view_turns SET tool_call_count=tool_call_count+1,changed_sequence=? WHERE conversation_id=? AND turn_key=?', [changed_sequence, conversation_id, turn_key]);
        }
    }
    else if (row_kind !== 'token_usage') {
        group_key = null;
    }
    tx.run('UPDATE conversation_view_rows SET turn_key=?,work_group_key=?,changed_sequence=? WHERE conversation_id=? AND sequence=?', [turn_key, row_group, changed_sequence, conversation_id, sequence]);
    if (row_kind === 'agent_message' && turn_key !== null) {
        tx.run('UPDATE conversation_view_turns SET full_message_count=full_message_count+1,latest_full_message_sequence=?,focus_message_count=focus_message_count+?,'
            + 'latest_focus_message_sequence=CASE WHEN ? THEN ? ELSE latest_focus_message_sequence END,changed_sequence=? WHERE conversation_id=? AND turn_key=?', [sequence, Number(focus_visible), Number(focus_visible), sequence, changed_sequence, conversation_id, turn_key]);
    }
    if (payload.kind === 'prompt' && event_effect(payload).opens_turn && turn_key === null) {
        turn_key = `turn:e${sequence}`;
        tx.run('INSERT INTO conversation_view_turns(conversation_id,turn_key,source_sequence,created_at,started_at_milliseconds,focus_head,changed_sequence) VALUES (?,?,?,?,?,?,?)', [conversation_id, turn_key, sequence, created_at, sent_at_milliseconds(payload, created_at), 1, changed_sequence]);
        tx.run('UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?', [sequence, conversation_id]);
        group_key = null;
    }
    else if (payload.kind === 'turn_ended') {
        const segment = tx.get<{
            segment_start_sequence: number;
        }>('SELECT segment_start_sequence FROM conversation_view_state WHERE conversation_id=?', [conversation_id])!.segment_start_sequence;
        tx.run('UPDATE conversation_view_groups SET ending_sequence=?,changed_sequence=? WHERE conversation_id=? AND source_sequence>? AND ending_sequence IS NULL', [sequence, changed_sequence, conversation_id, segment]);
        tx.run('UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?', [sequence, conversation_id]);
        if (turn_key !== null) {
            tx.run('UPDATE conversation_view_turns SET ending_sequence=?,ending=?,ended_at=?,focus_head=(focus_message_count>0 OR EXISTS(SELECT 1 FROM conversation_view_rows r '
                + 'WHERE r.conversation_id=conversation_view_turns.conversation_id AND r.sequence=conversation_view_turns.source_sequence AND r.focus_visible=1 AND r.removed=0)),'
                + 'changed_sequence=? WHERE conversation_id=? AND turn_key=?', [sequence, payload.ending, created_at, changed_sequence, conversation_id, turn_key]);
        }
        tx.run("UPDATE conversation_view_rows SET changed_sequence=? WHERE conversation_id=? AND sequence IN (SELECT source_sequence FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind='saved_answer' AND turn_key=?)", [changed_sequence, conversation_id, conversation_id, turn_key]);
        turn_key = null;
        group_key = null;
    }
    return [turn_key, group_key];
}
function repair_removed_boundaries(tx: TransactionSql, conversation_id: string, first_removed: number, changed_sequence: number): void {
    const previous_end = tx.get<{
        value: number | null;
    }>("SELECT MAX(sequence) AS value FROM conversation_view_rows WHERE conversation_id=? AND row_kind='turn_ended' AND sequence<?", [conversation_id, first_removed])!.value;
    const first = (previous_end ?? 0) + 1;
    const next_end = tx.get<{
        value: number | null;
    }>("SELECT MIN(sequence) AS value FROM conversation_view_rows WHERE conversation_id=? AND row_kind='turn_ended' AND sequence>=?", [conversation_id, first_removed])!.value;
    const last = next_end ?? changed_sequence;
    tx.run('DELETE FROM conversation_view_groups WHERE conversation_id=? AND source_sequence BETWEEN ? AND ?', [conversation_id, first, last]);
    tx.run('DELETE FROM conversation_view_turns WHERE conversation_id=? AND source_sequence BETWEEN ? AND ?', [conversation_id, first, last]);
    const original_segment = tx.get<{
        segment_start_sequence: number;
    }>('SELECT segment_start_sequence FROM conversation_view_state WHERE conversation_id=?', [conversation_id])!.segment_start_sequence;
    tx.run('UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?', [first - 1, conversation_id]);
    let after = first - 1;
    let turn_key: string | null = null;
    let group_key: string | null = null;
    while (true) {
        const rows = tx.all<{
            sequence: number;
            row_kind: string;
            created_at: number;
            focus_visible: number;
        }>('SELECT sequence,row_kind,created_at,focus_visible FROM conversation_view_rows WHERE conversation_id=? AND sequence>? AND sequence<=? AND removed=0 ORDER BY sequence LIMIT ?', [conversation_id, after, last, PROJECTION_BACKFILL_BATCH]);
        if (rows.length === 0) {
            break;
        }
        for (const row of rows) {
            [turn_key, group_key] = layout_row(tx, conversation_id, row.sequence, row.created_at, source(tx, conversation_id, row.sequence), row.row_kind, Boolean(row.focus_visible), turn_key, group_key, changed_sequence);
            after = row.sequence;
        }
    }
    if (next_end === null) {
        tx.run('UPDATE conversation_view_state SET current_turn_key=?,current_group_key=? WHERE conversation_id=?', [turn_key, group_key, conversation_id]);
    }
    else {
        tx.run('UPDATE conversation_view_state SET segment_start_sequence=? WHERE conversation_id=?', [original_segment, conversation_id]);
    }
}
export function rebuild_conversation_view(tx: TransactionSql, conversation_id: string): void {
    for (const table of ['lookups', 'groups', 'turns', 'rows', 'state']) {
        tx.run(`DELETE FROM conversation_view_${table} WHERE conversation_id=?`, [conversation_id]);
    }
    append_events_after(tx, conversation_id, 0);
}
export function append_events_after(tx: TransactionSql, conversation_id: string, after: number): void {
    while (true) {
        const rows = tx.all<{
            sequence: number;
            created_at: number;
            kind: ConversationEventKind;
            payload: string;
        }>('SELECT sequence,created_at,kind,payload FROM conversation_events WHERE conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?', [conversation_id, after, PROJECTION_BACKFILL_BATCH]);
        if (rows.length === 0) {
            break;
        }
        for (const row of rows) {
            append(tx, conversation_id, row.sequence, row.created_at, conversation_event_payload_from_canonical_json(row.kind, row.payload));
            after = row.sequence;
        }
    }
}
export function repair_rest_line_after_deleting_rows(tx: TransactionSql, conversation_id: string): void {
    repair_rest_pointer(tx, conversation_id);
    for (const referenced of rest_references(tx, conversation_id)) {
        if (tx.get('SELECT 1 FROM conversation_view_rows WHERE conversation_id=? AND sequence=? AND rest_summary IS NULL', [conversation_id, referenced]) !== undefined) {
            tx.run('UPDATE conversation_view_rows SET rest_summary=? WHERE conversation_id=? AND sequence=?', [rest_summary(tx, conversation_id, referenced, source(tx, conversation_id, referenced)), conversation_id, referenced]);
        }
    }
}
function repair_rest_pointer(tx: TransactionSql, conversation_id: string): void {
    for (const lens of ['full', 'focus']) {
        const column = `latest_${lens}_happened_sequence`;
        const current = tx.get<{
            value: number | null;
        }>(`SELECT ${column} AS value FROM conversation_view_state WHERE conversation_id=?`, [conversation_id])!.value;
        if (current === null || tx.get('SELECT 1 FROM conversation_view_rows WHERE conversation_id=? AND sequence=? AND removed=0', [conversation_id, current]) !== undefined) {
            continue;
        }
        let found: number | null = null;
        // The scoped SQL interface has no cursor. Keep the Python scan bounded in batches.
        let before: number | null = null;
        while (found === null) {
            const rows: {
                sequence: number;
            }[] = tx.all<{
                sequence: number;
            }>('SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND removed=0 '
                + (lens === 'focus' ? 'AND focus_visible=1 ' : '')
                + (before === null ? '' : 'AND sequence<? ')
                + 'ORDER BY sequence DESC LIMIT ?', before === null ? [conversation_id, PROJECTION_BACKFILL_BATCH] : [conversation_id, before, PROJECTION_BACKFILL_BATCH]);
            if (rows.length === 0) {
                break;
            }
            for (const row of rows) {
                before = row.sequence;
                if (event_has_rest_text(source(tx, conversation_id, row.sequence))) {
                    found = row.sequence;
                    break;
                }
            }
        }
        tx.run(`UPDATE conversation_view_state SET ${column}=? WHERE conversation_id=?`, [found, conversation_id]);
    }
}
