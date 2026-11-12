/** The six selected-conversation view routes. */
import { Readable } from 'node:stream';
import { readSync } from 'node:fs';
import type { FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { integerQuery, QueryIntegerError } from '../../list_reads/http.ts';
import type { Database, ReadSql, SqlValue } from '../../core/database/contracts.ts';
import type { ConversationRecordStore } from '../record/contracts.ts';
import type { ConversationLiveTail } from '../live_tail.ts';
import { SUBSCRIBER_BYTE_LIMIT, SUBSCRIBER_CHANGE_LIMIT } from './configuration.ts';
import { VIEW_PROJECTION_VERSION, VIEW_PROTOCOL_VERSION } from './contracts.ts';
import type {
  ConversationViewItem,
  ConversationViewLens,
  ConversationViewPage,
  ConversationViewSnapshot,
  ConversationViewStatus,
  ViewBodyReference,
  ViewHistoryScope,
  ViewMessageReceipt,
  ViewMessageReceipts,
  ViewPageCursor,
  ViewRetainedRange,
  ViewScopeChanges,
  ViewTailChanges,
} from './contracts.ts';
import { read_status } from './status.ts';
import {
  ViewBodyChanged,
  body_content,
  conversation_backend,
  encode_wire,
  iter_body_content,
  read_body_chunk,
  read_group,
  read_page,
  read_row,
  read_turn,
  scope_contains_item,
} from './data/storage.ts';
export interface ConversationViewRuntime {
  readonly database: Database;
  readonly store: ConversationRecordStore;
  readonly live_tail: ConversationLiveTail;
  readonly sse_heartbeat_ms: number;
  runtime_facts(conversation_id: string): Promise<{
    running: boolean | null;
    permissions: readonly string[];
    inputs: readonly string[];
  }>;
}
class HttpFailure extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}
export class ConversationViewUpgrading extends Error {}
export const VIEW_UPGRADING_DETAIL = 'Conversation view is upgrading';
function fail(status: number, detail: string): never {
  throw new HttpFailure(status, detail);
}
function text(value: unknown): value is string {
  return typeof value === 'string';
}
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
// Pydantic permits underscores in the integer part, but not fractional zeros.
function normalized_integer_text(raw: string): string {
  const point = raw.indexOf('.');
  const whole = point < 0 ? raw : raw.slice(0, point);
  const fraction = point < 0 ? '' : raw.slice(point);
  return whole.replace(/(?<=[0-9])_(?=[0-9])/g, '') + fraction;
}
function integer_field(fields: Record<string, unknown>, key: string): boolean {
  const value = fields[key];
  let decoded: unknown = value;
  if (typeof value === 'boolean') {
    decoded = Number(value);
  } else if (typeof value === 'string') {
    const normalized = normalized_integer_text(value).trim();
    if (/^[+-]?[0-9]+(?:\.0+)?$/.test(normalized)) {
      decoded = Number(normalized);
    }
  }
  if (typeof decoded !== 'number' || !Number.isInteger(decoded)) {
    return false;
  }
  fields[key] = decoded;
  return true;
}
function normalized_scope(value: ViewHistoryScope): ViewHistoryScope {
  return { kind: value.kind, key: value.key };
}
function normalized_order(value: { sequence: number; slot: number }) {
  return { sequence: value.sequence, slot: value.slot };
}
function scope(value: unknown): value is ViewHistoryScope {
  return (
    object(value) &&
    typeof value.kind === 'string' &&
    ['timeline', 'turn', 'work_group'].includes(value.kind) &&
    (value.key === null || text(value.key))
  );
}
function order(value: unknown): value is {
  sequence: number;
  slot: number;
} {
  return object(value) && integer_field(value, 'sequence') && integer_field(value, 'slot');
}
function cursor(value: unknown): value is ViewPageCursor {
  return (
    object(value) &&
    integer_field(value, 'projection_version') &&
    (value.lens === 'focus' || value.lens === 'full') &&
    scope(value.scope) &&
    order(value.order)
  );
}
function reference(value: unknown): value is ViewBodyReference {
  return (
    object(value) &&
    text(value.body_id) &&
    text(value.version) &&
    integer_field(value, 'byte_count') &&
    text(value.media_type)
  );
}
function retained(value: unknown): value is ViewRetainedRange {
  return object(value) && scope(value.scope) && order(value.first) && order(value.last);
}
function decode<T>(raw: string | undefined, valid: (value: unknown) => value is T): T | null {
  if (raw === undefined) {
    return null;
  }
  try {
    const value: unknown = JSON.parse(raw);
    if (!valid(value)) {
      throw new Error('invalid');
    }
    // TypeAdapter(TypedDict) removes unrecognized keys from accepted cursors.
    if (valid === reference) {
      const decoded = value as ViewBodyReference;
      return {
        body_id: decoded.body_id,
        version: decoded.version,
        byte_count: decoded.byte_count,
        media_type: decoded.media_type,
      } as T;
    }
    if (valid === scope) {
      return normalized_scope(value as ViewHistoryScope) as T;
    }
    if (valid === cursor) {
      const decoded = value as ViewPageCursor;
      return {
        projection_version: decoded.projection_version,
        lens: decoded.lens,
        scope: normalized_scope(decoded.scope),
        order: normalized_order(decoded.order),
      } as T;
    }
    return value;
  } catch {
    return fail(422, 'invalid conversation view cursor');
  }
}
function queries(request: FastifyRequest): Record<string, string> {
  const source = request.query as Record<string, unknown>;
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(source ?? {})) {
    const selected = Array.isArray(value) ? value.at(-1) : value;
    if (typeof selected === 'string') {
      result[key] = selected;
    }
  }
  return result;
}
function bounded(raw: string | undefined, limit: number): string | undefined {
  if (raw !== undefined && Array.from(raw).length > limit) {
    fail(422, 'query value is too long');
  }
  return raw;
}
function lens(query: Record<string, string>): ConversationViewLens {
  const result = query.lens ?? 'focus';
  if (result !== 'focus' && result !== 'full') {
    fail(422, 'invalid lens');
  }
  return result;
}
function transcript(query: Record<string, string>): boolean {
  if (query.transcript === undefined) {
    return true;
  }
  if (['true', '1', 'yes', 'on', 't', 'y'].includes(query.transcript.toLowerCase())) {
    return true;
  }
  if (['false', '0', 'no', 'off', 'f', 'n'].includes(query.transcript.toLowerCase())) {
    return false;
  }
  return fail(422, 'invalid transcript');
}
function number(raw: string | undefined, fallback?: number, nonnegative = false): number {
  if (raw === undefined && fallback !== undefined) {
    return fallback;
  }
  let value: number | undefined;
  try {
    value = integerQuery(raw === undefined ? raw : normalized_integer_text(raw), 'value');
  } catch (error) {
    if (error instanceof QueryIntegerError) {
      return fail(422, 'invalid integer');
    }
    throw error;
  }
  if (value === undefined || (nonnegative && value < 0)) {
    return fail(422, 'invalid integer');
  }
  return value;
}

async function runtime_facts(runtime: ConversationViewRuntime, conversation_id: string) {
  const exists = runtime.database.read(
    (sql) =>
      sql.get('SELECT 1 FROM conversations WHERE conversation_id=?', [conversation_id]) !==
      undefined,
  );
  if (!exists) {
    fail(404, 'conversation is absent');
  }
  return runtime.runtime_facts(conversation_id);
}
export function view_coverage(sql: ReadSql, conversation_id: string): number {
  const row = sql.get<{
    latest_sequence: number;
  }>('SELECT latest_sequence FROM conversations WHERE conversation_id=?', [conversation_id]);
  if (!row) {
    throw new Error('Conversation is absent');
  }
  const projected = sql.get<{
    through_sequence: number;
    projection_version: number;
  }>(
    'SELECT through_sequence,projection_version FROM conversation_view_state WHERE conversation_id=?',
    [conversation_id],
  );
  if (
    row.latest_sequence &&
    (!projected || projected.projection_version !== VIEW_PROJECTION_VERSION)
  ) {
    throw new ConversationViewUpgrading();
  }
  if (row.latest_sequence && projected!.through_sequence !== row.latest_sequence) {
    throw new Error('Conversation projection is behind its events');
  }
  return row.latest_sequence;
}
function empty_page(conversation_id: string, lens: ConversationViewLens): ConversationViewPage {
  return {
    conversation_id,
    protocol_version: VIEW_PROTOCOL_VERSION,
    projection_version: VIEW_PROJECTION_VERSION,
    lens,
    scope: { kind: 'timeline', key: null },
    items: [],
    older: null,
    newer: null,
    body_bytes: 0,
  };
}
export async function conversation_view_snapshot(
  runtime: ConversationViewRuntime,
  conversation_id: string,
  lens: ConversationViewLens,
  transcript: boolean,
): Promise<ConversationViewSnapshot> {
  const facts = await runtime_facts(runtime, conversation_id);
  return runtime.database.read((sql) => {
    const coverage = view_coverage(sql, conversation_id);
    const status = read_status(sql, conversation_id, lens, runtime.live_tail.views, {
      running: facts.running,
      permission_ids: facts.permissions,
      input_ids: facts.inputs,
    });
    const page = transcript
      ? read_page(sql, conversation_id, lens, { stopped: status.stopped_without_ending })
      : empty_page(conversation_id, lens);
    return { page, coverage: { through_sequence: coverage }, status };
  });
}
export function conversation_view_changes(
  sql: ReadSql,
  conversation_id: string,
  lens: ConversationViewLens,
  after: number,
  ranges: ViewRetainedRange[],
  status: ConversationViewStatus,
): ViewTailChanges | null {
  const coverage = view_coverage(sql, conversation_id);
  if (after < 0 || after > coverage) {
    return null;
  }
  let query =
    "SELECT 'row' kind,sequence,'e'||sequence key,removed,CASE WHEN row_kind='tool_call' THEN 1 ELSE 0 END slot FROM conversation_view_rows WHERE conversation_id=? AND changed_sequence>? " +
    (lens === 'focus' ? 'AND focus_visible=1 ' : '') +
    "UNION ALL SELECT 'turn',source_sequence,turn_key,0,1 FROM conversation_view_turns WHERE conversation_id=? AND changed_sequence>? ";
  const parameters: SqlValue[] = [conversation_id, after, conversation_id, after];
  if (lens === 'full') {
    query +=
      " UNION ALL SELECT 'work_group',source_sequence,group_key,0,0 FROM conversation_view_groups WHERE conversation_id=? AND changed_sequence>?";
    parameters.push(conversation_id, after);
  }
  parameters.push(SUBSCRIBER_CHANGE_LIMIT + 1);
  const changes = sql.all<{
    kind: 'row' | 'turn' | 'work_group';
    sequence: number;
    key: string;
    removed: number;
    slot: number;
  }>(`SELECT * FROM (${query}) LIMIT ?`, parameters);
  if (changes.length > SUBSCRIBER_CHANGE_LIMIT) {
    return null;
  }
  const backend_key = conversation_backend(sql, conversation_id);
  const upserts: ConversationViewItem[] = [];
  const removed: string[] = [];
  let invalidated: ViewHistoryScope[] = [];
  const scopes: ViewHistoryScope[] = ranges.length
    ? ranges.map((held) => held.scope)
    : [{ kind: 'timeline', key: null }];
  const scope_changes: ViewScopeChanges[] = scopes.map((scope) => ({
    scope,
    item_keys: [],
    removed_keys: [],
  }));
  const equal = (a: ViewHistoryScope, b: ViewHistoryScope) => a.kind === b.kind && a.key === b.key;
  const less = (
    a: {
      sequence: number;
      slot: number;
    },
    b: {
      sequence: number;
      slot: number;
    },
  ) => a.sequence < b.sequence || (a.sequence === b.sequence && a.slot <= b.slot);
  for (const changed of changes) {
    if (changed.removed) {
      removed.push(changed.key);
      invalidated = ranges.length
        ? ranges.map((held) => held.scope)
        : [{ kind: 'timeline', key: null }];
      continue;
    }
    const matching = ranges.filter(
      (held) =>
        (less(held.first, changed) && less(changed, held.last)) ||
        (held.scope.kind === 'timeline' && changed.sequence >= held.last.sequence),
    );
    const matching_scopes = ranges.length ? matching.map((held) => held.scope) : scopes;
    let admitted = false;
    for (const change of scope_changes) {
      const belongs =
        matching_scopes.some((value) => equal(value, change.scope)) &&
        scope_contains_item(
          sql,
          conversation_id,
          lens,
          change.scope,
          changed.kind,
          changed.key,
          changed.sequence,
          { stopped: status.stopped_without_ending },
        );
      (belongs ? change.item_keys : change.removed_keys).push(changed.key);
      admitted ||= belongs;
    }
    if (!admitted) {
      continue;
    }
    let item: ConversationViewItem;
    if (changed.kind === 'row') {
      [item] = read_row(sql, conversation_id, changed.sequence, {
        stopped: status.stopped_without_ending,
        backend_key,
      });
    } else if (changed.kind === 'turn') {
      item = read_turn(sql, conversation_id, changed.key, lens, {
        stopped: status.stopped_without_ending,
      });
    } else
      item = read_group(sql, conversation_id, changed.key, {
        stopped: status.stopped_without_ending,
      });
    upserts.push(item);
  }
  const result: ViewTailChanges = {
    kind: 'changes',
    conversation_id,
    protocol_version: VIEW_PROTOCOL_VERSION,
    projection_version: VIEW_PROJECTION_VERSION,
    lens,
    from_sequence: after,
    coverage: { through_sequence: coverage },
    upserts,
    scope_changes,
    removed_keys: removed,
    invalidated_scopes: invalidated,
    status,
  };
  return Buffer.byteLength(encode_wire(result)) <= SUBSCRIBER_BYTE_LIMIT ? result : null;
}
function frame(kind: string, value: unknown): string {
  return `event: ${kind}\ndata: ${encode_wire(value)}\n\n`;
}
export async function* conversation_view_tail(
  runtime: ConversationViewRuntime,
  conversation_id: string,
  lens: ConversationViewLens,
  transcript: boolean,
  after: number | null,
  ranges: ViewRetainedRange[],
  control: {
    close?: () => void;
  } = {},
): AsyncGenerator<string> {
  const turn_key = runtime.database.read(
    (sql) =>
      sql.get<{
        current_turn_key: string | null;
      }>('SELECT current_turn_key FROM conversation_view_state WHERE conversation_id=?', [
        conversation_id,
      ])?.current_turn_key ?? null,
  );
  const subscription = runtime.live_tail.views.subscribe(conversation_id, lens, {
    transcript,
    turn_key,
  });
  control.close = () => subscription.close();
  try {
    if (after === null) {
      const snapshot = await conversation_view_snapshot(runtime, conversation_id, lens, transcript);
      after = snapshot.coverage.through_sequence;
      yield frame('snapshot', snapshot);
      if (snapshot.page.items.length) {
        ranges = [
          {
            scope: snapshot.page.scope,
            first: snapshot.page.items[0].order,
            last: snapshot.page.items.at(-1)!.order,
          },
        ];
      }
    } else subscription.notify({ durable: false });
    while (true) {
      const keep = await subscription.next_wake(runtime.sse_heartbeat_ms);
      if (keep === null) {
        yield ': keep-alive\n\n';
        continue;
      }
      if (!keep) {
        if (subscription.overflow) {
          yield frame('reset', { kind: 'reset', reason: 'overflow' });
        }
        return;
      }
      const facts = await runtime_facts(runtime, conversation_id);
      const current_after: number = after ?? 0;
      const result: ViewTailChanges | null = runtime.database.read(
        (sql): ViewTailChanges | null => {
          const status = read_status(sql, conversation_id, lens, runtime.live_tail.views, {
            running: facts.running,
            permission_ids: facts.permissions,
            input_ids: facts.inputs,
          });
          if (!transcript) {
            return {
              kind: 'changes' as const,
              conversation_id,
              protocol_version: VIEW_PROTOCOL_VERSION,
              projection_version: VIEW_PROJECTION_VERSION,
              lens,
              from_sequence: current_after,
              coverage: { through_sequence: view_coverage(sql, conversation_id) },
              upserts: [],
              scope_changes: [],
              removed_keys: [],
              invalidated_scopes: [],
              status,
            };
          }
          return conversation_view_changes(
            sql,
            conversation_id,
            lens,
            current_after,
            ranges,
            status,
          );
        },
      );
      if (result === null) {
        yield frame('reset', { kind: 'reset', reason: 'gap' });
        return;
      }
      after = result.coverage.through_sequence;
      yield frame('changes', result);
    }
  } finally {
    subscription.close();
    control.close = undefined;
  }
}
export function createConversationViewRoutes(
  getRuntime: () => ConversationViewRuntime,
): FastifyPluginAsync<AreaServices> {
  return async (app) => {
    const path = '/conversations/:conversation_id/view';
    const id = (request: FastifyRequest) =>
      (
        request.params as {
          conversation_id: string;
        }
      ).conversation_id;
    const wrap =
      (handler: (request: FastifyRequest, reply: import('fastify').FastifyReply) => unknown) =>
      async (request: FastifyRequest, reply: import('fastify').FastifyReply) => {
        try {
          return await handler(request, reply);
        } catch (error) {
          if (error instanceof HttpFailure) {
            return reply.code(error.status).send({ detail: error.message });
          }
          if (error instanceof ConversationViewUpgrading) {
            return reply.code(409).send({ detail: VIEW_UPGRADING_DETAIL });
          }
          throw error;
        }
      };
    app.get(
      path,
      wrap(async (request) => {
        const q = queries(request);
        return conversation_view_snapshot(getRuntime(), id(request), lens(q), transcript(q));
      }),
    );
    app.get(
      path + '/pages',
      wrap(async (request) => {
        const runtime = getRuntime(),
          conversation_id = id(request),
          q = queries(request),
          selected_lens = lens(q);
        const facts = await runtime_facts(runtime, conversation_id);
        const selected_scope = decode(bounded(q.scope, 2048), scope),
          selected_cursor = decode(bounded(q.cursor, 4096), cursor);
        const direction = q.direction ?? 'older';
        if (direction !== 'older' && direction !== 'newer') {
          fail(422, 'invalid direction');
        }
        try {
          return runtime.database.read((sql) =>
            read_page(sql, conversation_id, selected_lens, {
              scope: selected_scope ?? undefined,
              cursor: selected_cursor ?? undefined,
              direction,
              stopped: facts.running === false,
            }),
          );
        } catch (error) {
          if (error instanceof Error && error.constructor === Error) {
            fail(409, error.message);
          }
          throw error;
        }
      }),
    );
    app.get(
      path + '/body',
      wrap(async (request) => {
        const runtime = getRuntime(),
          conversation_id = id(request),
          q = queries(request);
        const facts = await runtime_facts(runtime, conversation_id);
        const ref = decode(bounded(q.reference, 4096), reference);
        if (ref === null) {
          fail(422, 'body reference is required');
        }
        const offset = number(q.offset, 0, true);
        try {
          return ref.body_id.startsWith('live') || ref.body_id.startsWith('progress:')
            ? runtime.live_tail.views.read_chunk(conversation_id, ref, offset)
            : runtime.database.read((sql) =>
                read_body_chunk(sql, conversation_id, ref, offset, {
                  stopped: facts.running === false,
                }),
              );
        } catch (error) {
          if (error instanceof ViewBodyChanged) {
            fail(409, error.message);
          }
          if (error instanceof Error && error.constructor === Error) {
            fail(422, error.message);
          }
          throw error;
        }
      }),
    );
    app.get(
      path + '/tail',
      { config: { uncompressed: true } },
      wrap(async (request, reply) => {
        const runtime = getRuntime(),
          conversation_id = id(request),
          q = queries(request),
          selected_lens = lens(q),
          selected_transcript = transcript(q);
        await runtime_facts(runtime, conversation_id);
        if (number(q.projection_version, VIEW_PROJECTION_VERSION) !== VIEW_PROJECTION_VERSION) {
          fail(409, 'Refresh required: conversation view version changed');
        }
        const after = q.after === undefined ? null : number(q.after, undefined, true);
        let ranges: ViewRetainedRange[];
        try {
          const value: unknown = JSON.parse(bounded(q.ranges, 8192) ?? '[]');
          if (!Array.isArray(value) || !value.every(retained)) {
            throw new Error();
          }
          ranges = value.map((held) => ({
            scope: normalized_scope(held.scope),
            first: normalized_order(held.first),
            last: normalized_order(held.last),
          }));
        } catch {
          return fail(422, 'invalid retained ranges');
        }
        if (ranges.length > 20) {
          fail(422, 'too many retained ranges');
        }
        runtime.database.read((sql) => view_coverage(sql, conversation_id));
        const control: {
          close?: () => void;
        } = {};
        const stream = Readable.from(
          conversation_view_tail(
            runtime,
            conversation_id,
            selected_lens,
            selected_transcript,
            after,
            ranges,
            control,
          ),
        );
        reply.raw.once('close', () => {
          control.close?.();
          stream.destroy();
        });
        reply.type('text/event-stream; charset=utf-8');
        return reply.send(stream);
      }),
    );
    app.get(
      path + '/receipts',
      wrap(async (request) => {
        const runtime = getRuntime(),
          conversation_id = id(request),
          q = queries(request);
        await runtime_facts(runtime, conversation_id);
        let identities: string[];
        try {
          const value: unknown = JSON.parse(bounded(q.message_ids, 16384) ?? '');
          if (!Array.isArray(value) || !value.every(text)) {
            throw new Error();
          }
          identities = value;
        } catch {
          return fail(422, 'invalid message identities');
        }
        if (
          identities.length > 64 ||
          identities.some((value) => !value || Array.from(value).length > 200)
        ) {
          fail(422, 'too many or invalid message identities');
        }
        return runtime.database.read((sql) => {
          const receipts: ViewMessageReceipt[] = [];
          for (const identity of new Set(identities)) {
            const row = sql.get<{
              sequence: number;
              row_kind: ViewMessageReceipt['fate'];
            }>(
              "SELECT r.sequence,r.row_kind FROM conversation_view_lookups l JOIN conversation_view_rows r ON r.conversation_id=l.conversation_id AND r.sequence=l.source_sequence WHERE l.conversation_id=? AND l.lookup_kind='sender_message' AND l.lookup_id=? AND r.focus_visible=1 AND r.removed=0",
              [conversation_id, identity],
            );
            if (
              row &&
              ['prompt', 'prompt_refused', 'prompt_uncertain', 'prompt_discarded'].includes(
                row.row_kind,
              )
            ) {
              receipts.push({
                sender_message_id: identity,
                source_sequence: row.sequence,
                fate: row.row_kind,
              });
            }
          }
          return {
            conversation_id,
            coverage: { through_sequence: view_coverage(sql, conversation_id) },
            receipts,
          } satisfies ViewMessageReceipts;
        });
      }),
    );
    app.get(
      path + '/body/content',
      wrap(async (request, reply) => {
        const runtime = getRuntime(),
          conversation_id = id(request),
          q = queries(request);
        const facts = await runtime_facts(runtime, conversation_id);
        const ref = decode(bounded(q.reference, 4096), reference);
        if (ref === null) {
          fail(422, 'body reference is required');
        }
        try {
          let chunks: Iterable<Uint8Array>;
          if (ref.body_id.startsWith('live') || ref.body_id.startsWith('progress:')) {
            const source = runtime.live_tail.views.open_content(conversation_id, ref);
            chunks = (function* () {
              try {
                let remaining = ref.byte_count;
                while (remaining) {
                  const chunk = Buffer.alloc(Math.min(65536, remaining));
                  const count = readSync(source.fd, chunk, 0, chunk.length, null);
                  if (!count) {
                    throw new ViewBodyChanged('Live body prefix is incomplete');
                  }
                  remaining -= count;
                  yield chunk.subarray(0, count);
                }
              } finally {
                source.close();
              }
            })();
          } else {
            const body = runtime.database.read((sql) =>
              body_content(sql, conversation_id, ref, { stopped: facts.running === false }),
            );
            chunks = iter_body_content(body);
          }
          reply.type(
            ref.media_type.startsWith('text/')
              ? ref.media_type + '; charset=utf-8'
              : ref.media_type,
          );
          return reply.send(Readable.from(chunks));
        } catch (error) {
          if (error instanceof ViewBodyChanged) {
            fail(409, error.message);
          }
          if (error instanceof Error && error.constructor === Error) {
            fail(422, error.message);
          }
          throw error;
        }
      }),
    );
  };
}
