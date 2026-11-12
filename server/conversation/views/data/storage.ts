/** Indexed, bounded selected-view reads and source-versioned body access. */
import type { ReadSql, SqlValue } from '../../../core/database/contracts.ts';
import { PlannerError } from '../../../core/contracts.ts';
import { AnswerFate, ConversationBackendKey } from '../../contracts.ts';
import * as event from '../../events.ts';
import {
  canonical_json,
  conversation_event_payload_from_canonical_json,
} from '../../record/codec.ts';
import { BODY_CHUNK_BYTES, HISTORY_PAGE_BODY_BYTES, HISTORY_PAGE_ITEMS } from '../configuration.ts';
import { VIEW_PROJECTION_VERSION, VIEW_PROTOCOL_VERSION } from '../contracts.ts';
import type {
  AskDeadReason,
  ConversationViewItem,
  ConversationViewLens,
  ConversationViewPage,
  ConversationViewRow,
  ConversationViewTurn,
  ConversationViewWorkGroup,
  ViewBodyChunk,
  ViewBodyReference,
  ViewHistoryScope,
  ViewPageCursor,
  ViewRowBody,
  ViewRowKind,
} from '../contracts.ts';
import { row_body } from '../logic/bodies.ts';
import { sequence_expired } from '../omissions.ts';

export class ViewBodyChanged extends Error {}

function* wire_fragments(value: unknown, field?: string): Generator<string> {
  if (value === null || typeof value !== 'object') {
    yield canonical_json(value, field);
    return;
  }
  if (Array.isArray(value)) {
    yield '[';
    for (let index = 0; index < value.length; index++) {
      if (index) {
        yield ',';
      }
      yield* wire_fragments(value[index]);
    }
    yield ']';
    return;
  }
  yield '{';
  const entries = Object.entries(value);
  for (let index = 0; index < entries.length; index++) {
    const [key, child] = entries[index];
    if (index) {
      yield ',';
    }
    yield JSON.stringify(key) + ':';
    yield* wire_fragments(child, key);
  }
  yield '}';
}

export function encode_wire(value: unknown): string {
  return [...wire_fragments(value)].join('');
}

function raise_if_expired(sql: ReadSql, conversation_id: string, sequence: number): void {
  if (sequence_expired(sql, conversation_id, sequence)) {
    throw new PlannerError('diagnostic_expired', 'This diagnostic detail expired.', {
      conversation_id,
      sequence,
    });
  }
}

export function _payload(
  sql: ReadSql,
  conversation_id: string,
  sequence: number,
): event.ConversationEventPayload {
  const source = sql.get<{ kind: event.ConversationEventKind; payload: string }>(
    'SELECT kind,payload FROM conversation_events WHERE conversation_id=? AND sequence=?',
    [conversation_id, sequence],
  );
  if (source === undefined) {
    raise_if_expired(sql, conversation_id, sequence);
    throw new Error('View source is absent');
  }
  return conversation_event_payload_from_canonical_json(source.kind, source.payload);
}

interface RowRecord {
  sequence: number;
  row_kind: ViewRowKind;
  created_at: number;
  settlement_sequence: number | null;
  changed_sequence: number;
  sender_message_id: string | null;
  turn_key: string | null;
  work_group_key: string | null;
  focus_visible: number;
  turn_ending_sequence: number | null;
  turn_ending: event.ConversationTurnEnding | null;
  latest_full_message_sequence: number | null;
  latest_focus_message_sequence: number | null;
  focus_head: number | null;
}

export function _row_record(sql: ReadSql, conversation_id: string, sequence: number): RowRecord {
  const row = sql.get<RowRecord>(
    'SELECT r.*, t.ending_sequence AS turn_ending_sequence,t.ending AS turn_ending,' +
      't.latest_full_message_sequence,t.latest_focus_message_sequence,t.focus_head ' +
      'FROM conversation_view_rows r LEFT JOIN conversation_view_turns t ' +
      'ON r.conversation_id=t.conversation_id AND r.turn_key=t.turn_key ' +
      'WHERE r.conversation_id=? AND r.sequence=? AND r.removed=0',
    [conversation_id, sequence],
  );
  if (row === undefined) {
    raise_if_expired(sql, conversation_id, sequence);
    throw new Error('View row is absent');
  }
  return row;
}

export function conversation_backend(
  sql: ReadSql,
  conversation_id: string,
): ConversationBackendKey {
  return (
    sql.get<{ backend_key: ConversationBackendKey }>(
      'SELECT backend_key FROM conversations WHERE conversation_id=?',
      [conversation_id],
    )?.backend_key ?? ConversationBackendKey.hermes
  );
}

export function _body(
  sql: ReadSql,
  conversation_id: string,
  row: RowRecord,
  options: {
    stopped?: boolean;
    full_tool_detail?: boolean;
    backend_key?: ConversationBackendKey;
  } = {},
): ViewRowBody {
  let settlement =
    row.settlement_sequence === null
      ? null
      : _payload(sql, conversation_id, row.settlement_sequence);
  const saved =
    (settlement?.kind === 'permission_answered' || settlement?.kind === 'user_input_answered') &&
    settlement.fate === AnswerFate.saved;
  if (
    saved &&
    (settlement?.kind === 'permission_answered' || settlement?.kind === 'user_input_answered')
  ) {
    const applied = sql.get(
      "SELECT 1 FROM conversation_view_lookups WHERE conversation_id=? AND lookup_kind='applied_answer' AND lookup_id=?",
      [conversation_id, settlement.answer_id],
    );
    if (applied !== undefined) {
      settlement =
        settlement.kind === 'permission_answered'
          ? new event.PermissionAnsweredEventPayload({ ...settlement, fate: AnswerFate.applied })
          : new event.UserInputAnsweredEventPayload({ ...settlement, fate: AnswerFate.applied });
    }
  }
  let dead: AskDeadReason | null = null;
  if (
    ['permission_ask', 'user_input'].includes(row.row_kind) &&
    (row.settlement_sequence === null || saved)
  ) {
    const ending = sql.get(
      "SELECT sequence FROM conversation_view_rows WHERE conversation_id=? AND row_kind='turn_ended' AND sequence>? ORDER BY sequence LIMIT 1",
      [conversation_id, row.sequence],
    );
    dead = ending !== undefined ? 'turn_ended' : options.stopped ? 'no_ending_recorded' : null;
  }
  const source = _payload(sql, conversation_id, row.sequence);
  const value = row_body(source, {
    created_at: row.created_at,
    settlement,
    settlement_sequence:
      row.settlement_sequence ?? (source.kind === 'tool_call_finished' ? row.sequence : null),
    dead_reason: dead,
    full_tool_detail: options.full_tool_detail ?? false,
    backend_key: options.backend_key ?? conversation_backend(sql, conversation_id),
  });
  if (value === null) {
    throw new Error('Source does not produce a view row');
  }
  return value;
}

export function _body_reference(
  row: RowRecord,
  encoded: Uint8Array,
  detail = false,
): ViewBodyReference {
  return {
    body_id: `${detail ? 'detail' : 'row'}:${row.sequence}`,
    version: `${row.changed_sequence}:${row.turn_ending_sequence ?? 0}`,
    byte_count: encoded.byteLength,
    media_type: 'application/json',
  };
}

export function read_row(
  sql: ReadSql,
  conversation_id: string,
  sequence: number,
  options: {
    stopped?: boolean;
    backend_key?: ConversationBackendKey;
  } = {},
): [ConversationViewRow, number] {
  const row = _row_record(sql, conversation_id, sequence);
  const value = _body(sql, conversation_id, row, options);
  const encoded = Buffer.from(encode_wire(value));
  let fold_rule: 'never' | 'earlier_message' | 'work' = 'never';
  if (row.row_kind === 'tool_call') {
    fold_rule = 'work';
  } else if (value.kind === 'agent_message' && !value.to_owner && row.turn_key !== null) {
    fold_rule = 'earlier_message';
  }
  const result: ConversationViewRow = {
    kind: 'row',
    key: `e${sequence}`,
    row_kind: row.row_kind,
    order: { sequence, slot: row.row_kind === 'tool_call' ? 1 : 0 },
    source_sequence: sequence,
    sender_message_id: row.sender_message_id,
    changed_through_sequence: row.changed_sequence,
    created_at: row.created_at,
    turn_key: row.turn_key,
    work_group_key: row.work_group_key,
    fold_rule,
    body: { kind: 'inline', value },
  };
  if (encoded.byteLength > HISTORY_PAGE_BODY_BYTES) {
    result.body = { kind: 'deferred', reference: _body_reference(row, encoded) };
    return [result, 0];
  }
  return [result, encoded.byteLength];
}

interface TurnRecord {
  source_sequence: number;
  created_at: number;
  started_at_milliseconds: number;
  ending_sequence: number | null;
  ending: event.ConversationTurnEnding | null;
  ended_at: number | null;
  full_message_count: number;
  focus_message_count: number;
  latest_full_message_sequence: number | null;
  latest_focus_message_sequence: number | null;
  tool_call_count: number;
  changed_sequence: number;
}

export function read_turn(
  sql: ReadSql,
  conversation_id: string,
  key: string,
  lens: ConversationViewLens,
  options: { stopped?: boolean } = {},
): ConversationViewTurn {
  const row = sql.get<TurnRecord>(
    'SELECT * FROM conversation_view_turns WHERE conversation_id=? AND turn_key=?',
    [conversation_id, key],
  );
  if (row === undefined) {
    throw new Error('View turn is absent');
  }
  const focus = lens === 'focus';
  const latest = sql.get<{ turn_key: string }>(
    'SELECT turn_key FROM conversation_view_turns WHERE conversation_id=? ' +
      (focus ? 'AND focus_head=1 ' : '') +
      'ORDER BY source_sequence DESC LIMIT 1',
    [conversation_id],
  );
  const is_stopped = !!options.stopped && row.ending_sequence === null;
  const ended = row.ending_sequence !== null;
  const ordinary_count = row.full_message_count - row.focus_message_count;
  const retained_ordinary =
    row.latest_full_message_sequence !== null &&
    row.latest_full_message_sequence !== row.latest_focus_message_sequence;
  const last = focus ? row.latest_focus_message_sequence : row.latest_full_message_sequence;
  return {
    kind: 'turn',
    key,
    order: { sequence: row.source_sequence, slot: 1 },
    changed_through_sequence: row.changed_sequence,
    settled: ended || is_stopped,
    stopped: is_stopped,
    started_at_unix_milliseconds: row.started_at_milliseconds,
    ending: row.ending,
    duration_seconds: ended ? Math.max(0, row.ended_at! - row.created_at) : null,
    tool_call_count: focus ? 0 : row.tool_call_count,
    folded_message_count:
      !focus && (ended || is_stopped) ? Math.max(0, ordinary_count - Number(retained_ordinary)) : 0,
    latest_message_key: last === null ? null : `e${last}`,
    is_latest: latest?.turn_key === key,
  };
}

interface GroupRecord {
  source_sequence: number;
  changed_sequence: number;
  turn_key: string | null;
  entry_count: number;
  latest_entry_sequence: number;
  ending_sequence: number | null;
}

export function read_group(
  sql: ReadSql,
  conversation_id: string,
  key: string,
  options: { stopped?: boolean } = {},
): ConversationViewWorkGroup {
  const row = sql.get<GroupRecord>(
    'SELECT * FROM conversation_view_groups WHERE conversation_id=? AND group_key=?',
    [conversation_id, key],
  );
  if (row === undefined) {
    throw new Error('View work group is absent');
  }
  return {
    kind: 'work_group',
    key,
    order: { sequence: row.source_sequence, slot: 0 },
    changed_through_sequence: row.changed_sequence,
    turn_key: row.turn_key,
    entry_count: row.entry_count,
    latest_entry_key: `e${row.latest_entry_sequence}`,
    settled: row.ending_sequence !== null || (!!options.stopped && row.turn_key !== null),
  };
}

type Member = [string, SqlValue[]];
function candidate_members(
  lens: ConversationViewLens,
  scope: ViewHistoryScope,
  stopped: boolean,
  reverse_probe = false,
): Member[] {
  const focus = lens === 'focus';
  const visible = focus ? 'r.focus_visible=1 AND ' : '';
  const latest = focus ? 't.latest_focus_message_sequence' : 't.latest_full_message_sequence';
  const has_head = focus ? 'COALESCE(t.focus_head,0)=1' : 't.turn_key IS NOT NULL';
  const settled = stopped ? '1' : 't.ending_sequence IS NOT NULL';
  const join =
    'LEFT JOIN conversation_view_turns t ON r.conversation_id=t.conversation_id AND r.turn_key=t.turn_key ';
  if (scope.kind === 'work_group') {
    return [
      [
        "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,1 AS slot " +
          'FROM conversation_view_rows r WHERE r.conversation_id=? AND r.removed=0 ' +
          `AND ${visible}r.work_group_key=?`,
        [scope.key],
      ],
    ];
  }
  if (scope.kind === 'turn') {
    const ordinary_index =
      reverse_probe && !focus ? 'INDEXED BY conversation_view_rows_ordinary_turn ' : '';
    const members: Member[] = [
      [
        "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,0 AS slot " +
          'FROM conversation_view_rows r ' +
          ordinary_index +
          join +
          'WHERE r.conversation_id=? ' +
          `AND r.removed=0 AND ${visible}r.turn_key=? AND r.row_kind='agent_message' ` +
          `AND r.focus_visible=0 AND r.sequence<>${latest}`,
        [scope.key],
      ],
    ];
    if (!focus) {
      members.push([
        "SELECT 'work_group' AS kind,group_key AS key,source_sequence AS sequence,0 AS slot " +
          'FROM conversation_view_groups WHERE conversation_id=? AND turn_key=?',
        [scope.key],
      ]);
    }
    return members;
  }
  const members: Member[] = [
    [
      "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,0 AS slot " +
        'FROM conversation_view_rows r ' +
        join +
        'WHERE r.conversation_id=? AND r.removed=0 ' +
        `AND ${visible}r.row_kind<>'tool_call' AND NOT(r.row_kind='agent_message' AND r.focus_visible=0 ` +
        `AND ${has_head} AND (${settled}) AND r.sequence<>${latest})`,
      [],
    ],
    [
      "SELECT 'turn' AS kind,turn_key AS key,source_sequence AS sequence,1 AS slot " +
        'FROM conversation_view_turns WHERE conversation_id=?' +
        (focus ? ' AND focus_head=1' : ''),
      [],
    ],
  ];
  if (focus) {
    return members;
  }
  const unturned = stopped ? ' AND turn_key IS NULL' : '';
  members.push([
    "SELECT 'work_group' AS kind,group_key AS key,source_sequence AS sequence,0 AS slot " +
      'FROM conversation_view_groups WHERE conversation_id=? AND ending_sequence IS NULL' +
      unturned,
    [],
  ]);
  members.push([
    "SELECT 'row' AS kind,'e'||r.sequence AS key,r.sequence AS sequence,1 AS slot " +
      'FROM conversation_view_groups g CROSS JOIN conversation_view_rows r ' +
      'ON r.conversation_id=g.conversation_id AND r.sequence=g.latest_entry_sequence ' +
      'WHERE g.conversation_id=? AND g.ending_sequence IS NULL' +
      unturned.replace(' turn_key', ' g.turn_key') +
      ' AND r.removed=0 AND r.work_group_key=g.group_key',
    [],
  ]);
  return members;
}

interface Candidate {
  kind: 'row' | 'turn' | 'work_group';
  key: string;
  sequence: number;
  slot: number;
}

export function read_page(
  sql: ReadSql,
  conversation_id: string,
  lens: ConversationViewLens,
  options: {
    scope?: ViewHistoryScope;
    cursor?: ViewPageCursor | null;
    direction?: 'older' | 'newer';
    stopped?: boolean;
  } = {},
): ConversationViewPage {
  const scope = options.scope ?? { kind: 'timeline', key: null };
  const cursor = options.cursor ?? null;
  const stopped = options.stopped ?? false;
  if ((scope.kind === 'timeline') !== (scope.key === null)) {
    throw new Error('History scope key does not match its kind');
  }
  if (
    cursor !== null &&
    (cursor.projection_version !== VIEW_PROJECTION_VERSION ||
      cursor.lens !== lens ||
      cursor.scope.kind !== scope.kind ||
      cursor.scope.key !== scope.key)
  ) {
    throw new Error('History cursor does not match the requested view');
  }
  const descending = (options.direction ?? 'older') === 'older';
  const way = descending ? 'DESC' : 'ASC';
  const parameters: SqlValue[] = [];
  const bounded: string[] = [];
  for (const [member, scope_parameters] of candidate_members(lens, scope, stopped)) {
    parameters.push(conversation_id, ...scope_parameters);
    let clause = '';
    if (cursor !== null) {
      clause = ` WHERE (sequence,slot) ${descending ? '<' : '>'} (?,?)`;
      parameters.push(cursor.order.sequence, cursor.order.slot);
    }
    parameters.push(HISTORY_PAGE_ITEMS + 1);
    bounded.push(
      `SELECT * FROM (SELECT * FROM (${member})${clause} ORDER BY sequence ${way} LIMIT ?)`,
    );
  }
  parameters.push(HISTORY_PAGE_ITEMS + 1);
  const candidates = sql.all<Candidate>(
    'SELECT * FROM (' +
      bounded.join(' UNION ALL ') +
      `) ORDER BY sequence ${way},slot ${way} LIMIT ?`,
    parameters,
  );
  const items: ConversationViewItem[] = [];
  let body_bytes = 0;
  const backend_key = conversation_backend(sql, conversation_id);
  for (const candidate of candidates.slice(0, HISTORY_PAGE_ITEMS)) {
    let item: ConversationViewItem;
    let count = 0;
    if (candidate.kind === 'row') {
      [item, count] = read_row(sql, conversation_id, candidate.sequence, { stopped, backend_key });
    } else if (candidate.kind === 'turn') {
      item = read_turn(sql, conversation_id, candidate.key, lens, { stopped });
    } else {
      item = read_group(sql, conversation_id, candidate.key, { stopped });
    }
    if (items.length && body_bytes + count > HISTORY_PAGE_BODY_BYTES) {
      break;
    }
    items.push(item);
    body_bytes += count;
  }
  if (descending) {
    items.reverse();
  }
  const position = (item: ConversationViewItem): ViewPageCursor => ({
    projection_version: VIEW_PROJECTION_VERSION,
    lens,
    scope,
    order: item.order,
  });
  const more = candidates.length > items.length;
  let reverse_exists = false;
  if (cursor !== null) {
    const edge = items.length ? items[descending ? items.length - 1 : 0].order : cursor.order;
    const probes: string[] = [];
    const probe_parameters: SqlValue[] = [];
    for (const [member, scope_parameters] of candidate_members(
      lens,
      scope,
      stopped,
      true,
    ).reverse()) {
      probes.push(
        `SELECT 1 FROM (SELECT * FROM (${member}) WHERE (sequence,slot) ` +
          `${descending ? '>' : '<'} (?,?) ORDER BY sequence ${descending ? 'DESC' : 'ASC'} LIMIT 1)`,
      );
      probe_parameters.push(conversation_id, ...scope_parameters, edge.sequence, edge.slot);
    }
    reverse_exists =
      sql.get('SELECT 1 FROM (' + probes.join(' UNION ALL ') + ') LIMIT 1', probe_parameters) !==
      undefined;
  }
  let older = items.length && (descending ? more : reverse_exists) ? position(items[0]) : null;
  let newer =
    items.length && (descending ? reverse_exists : more) ? position(items[items.length - 1]) : null;
  if (!items.length && cursor !== null && reverse_exists) {
    if (descending) {
      newer = cursor;
    } else {
      older = cursor;
    }
  }
  return {
    conversation_id,
    protocol_version: VIEW_PROTOCOL_VERSION,
    projection_version: VIEW_PROJECTION_VERSION,
    lens,
    scope,
    items,
    body_bytes,
    older,
    newer,
  };
}

function reference_source(
  reference: ViewBodyReference,
): ['row' | 'detail' | 'plan', string, number] {
  const match = /^(row|detail|plan):([0-9]+)$/.exec(reference.body_id);
  if (match === null) {
    throw new Error('Unsupported body reference');
  }
  return [match[1] as 'row' | 'detail' | 'plan', match[2], Number(match[2])];
}

function references_equal(left: ViewBodyReference, right: ViewBodyReference): boolean {
  return (
    left.body_id === right.body_id &&
    left.version === right.version &&
    left.byte_count === right.byte_count &&
    left.media_type === right.media_type
  );
}

export function read_body_chunk(
  sql: ReadSql,
  conversation_id: string,
  reference: ViewBodyReference,
  offset: number,
  options: { stopped?: boolean } = {},
): ViewBodyChunk {
  const [kind, raw_sequence, sequence] = reference_source(reference);
  let current: ViewBodyReference;
  let encoded: Buffer;
  if (kind === 'plan') {
    const source = _payload(sql, conversation_id, sequence);
    if (source.kind !== 'plan_updated') {
      throw new Error('Source is not a plan');
    }
    encoded = Buffer.from(encode_wire({ entries: source.entries }));
    current = {
      body_id: reference.body_id,
      version: raw_sequence,
      byte_count: encoded.byteLength,
      media_type: 'application/json',
    };
  } else {
    const row = _row_record(sql, conversation_id, sequence);
    const value = _body(sql, conversation_id, row, {
      stopped: options.stopped,
      full_tool_detail: kind === 'detail',
    });
    encoded = Buffer.from(encode_wire(value));
    current = _body_reference(row, encoded, kind === 'detail');
  }
  if (!references_equal(current, reference)) {
    throw new ViewBodyChanged('Body source version changed');
  }
  if (offset < 0 || offset > encoded.byteLength) {
    throw new Error('Body offset is outside the source');
  }
  let end = Math.min(offset + BODY_CHUNK_BYTES, encoded.byteLength);
  while (end < encoded.byteLength && (encoded[end] & 0xc0) === 0x80) {
    end--;
  }
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
      encoded.subarray(offset, end),
    );
  } catch (error) {
    throw new Error('Body offset is not a UTF-8 boundary', { cause: error });
  }
  return { reference: current, offset, text, next_offset: end < encoded.byteLength ? end : null };
}

export function body_content(
  sql: ReadSql,
  conversation_id: string,
  reference: ViewBodyReference,
  options: { stopped?: boolean } = {},
): unknown {
  const [kind, , sequence] = reference_source(reference);
  let value: unknown;
  let version: string;
  if (kind === 'plan') {
    const source = _payload(sql, conversation_id, sequence);
    if (source.kind !== 'plan_updated') {
      throw new Error('Source is not a plan');
    }
    value = { entries: source.entries };
    version = String(sequence);
  } else {
    const row = _row_record(sql, conversation_id, sequence);
    version = `${row.changed_sequence}:${row.turn_ending_sequence ?? 0}`;
    if (version !== reference.version) {
      if (
        sql.get('SELECT 1 FROM conversation_events WHERE conversation_id=? AND sequence=?', [
          conversation_id,
          sequence,
        ]) === undefined
      ) {
        raise_if_expired(sql, conversation_id, sequence);
      }
      throw new ViewBodyChanged('Body source version changed');
    }
    value = _body(sql, conversation_id, row, {
      stopped: options.stopped,
      full_tool_detail: kind === 'detail',
    });
  }
  let byte_count = 0;
  for (const chunk of iter_body_content(value)) {
    byte_count += chunk.byteLength;
  }
  if (
    version !== reference.version ||
    reference.media_type !== 'application/json' ||
    byte_count !== reference.byte_count
  ) {
    throw new ViewBodyChanged('Body source version changed');
  }
  return value;
}

export function* iter_body_content(value: unknown): Generator<Buffer> {
  for (const fragment of wire_fragments(value)) {
    let start = 0;
    while (start < fragment.length) {
      let end = Math.min(start + Math.floor(BODY_CHUNK_BYTES / 4), fragment.length);
      if (end < fragment.length && /[\uD800-\uDBFF]/.test(fragment[end - 1])) {
        end--;
      }
      yield Buffer.from(fragment.slice(start, end));
      start = end;
    }
  }
}

export function scope_contains_item(
  sql: ReadSql,
  conversation_id: string,
  lens: ConversationViewLens,
  scope: ViewHistoryScope,
  kind: string,
  key: string,
  sequence: number,
  options: { stopped: boolean },
): boolean {
  if (kind === 'turn') {
    if (scope.kind !== 'timeline') {
      return false;
    }
    if (lens === 'full') {
      return true;
    }
    return !!sql.get<{ focus_head: number }>(
      'SELECT focus_head FROM conversation_view_turns WHERE conversation_id=? AND turn_key=?',
      [conversation_id, key],
    )?.focus_head;
  }
  if (kind === 'work_group') {
    const row = sql.get<{ turn_key: string | null; ending_sequence: number | null }>(
      'SELECT turn_key,ending_sequence FROM conversation_view_groups WHERE conversation_id=? AND group_key=?',
      [conversation_id, key],
    );
    if (row === undefined || lens === 'focus') {
      return false;
    }
    if (scope.kind === 'turn') {
      return row.turn_key === scope.key;
    }
    return (
      scope.kind === 'timeline' &&
      row.ending_sequence === null &&
      (!options.stopped || row.turn_key === null)
    );
  }
  const row = _row_record(sql, conversation_id, sequence);
  if (scope.kind === 'work_group') {
    return row.work_group_key === scope.key;
  }
  const latest =
    lens === 'focus' ? row.latest_focus_message_sequence : row.latest_full_message_sequence;
  if (scope.kind === 'turn') {
    return (
      row.turn_key === scope.key &&
      row.row_kind === 'agent_message' &&
      !row.focus_visible &&
      sequence !== latest
    );
  }
  if (row.row_kind === 'tool_call') {
    const group = sql.get<{
      latest_entry_sequence: number;
      ending_sequence: number | null;
      turn_key: string | null;
    }>(
      'SELECT latest_entry_sequence,ending_sequence,turn_key FROM conversation_view_groups WHERE conversation_id=? AND group_key=?',
      [conversation_id, row.work_group_key],
    );
    return (
      group !== undefined &&
      sequence === group.latest_entry_sequence &&
      group.ending_sequence === null &&
      (!options.stopped || group.turn_key === null)
    );
  }
  const headed = lens === 'focus' ? row.focus_head : row.turn_key !== null;
  return !(
    row.row_kind === 'agent_message' &&
    !row.focus_visible &&
    headed &&
    (options.stopped || row.turn_ending_sequence !== null) &&
    sequence !== latest
  );
}
