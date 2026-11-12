/** Bounded closures over the shared navigation projection. */
import type { ReadSql } from '../../core/database/contracts.ts';
import type {
  Continuity,
  NavigationRecord,
  ReviewHistoryKey,
  ViewContent,
  ViewCounts,
  ViewRecord,
  ViewRequest,
  ViewWindow,
  ViewWindowRequest,
} from '../../../web/src/lib/navigation/contracts.ts';
import { coverage } from '../data/registry.ts';
import { compact, digest, ORDERING_GENERATION } from '../logic/json.ts';
import { NavigationUnavailable } from '../logic/errors.ts';
import { windowKey } from '../logic/view.ts';
import { VIEW_CONFIG } from './configuration.ts';
import type { Key, WindowPosition } from './contracts.ts';
import { project, sectionKey } from './logic.ts';
import { binaryCompare } from './ordering.ts';
import { orderedRows } from './ordered.ts';
import type { OrderedRows } from './ordered.ts';
import { canonicalReviewAddress, record, reviewMessageItems } from './review.ts';
const identity = (key: Key): string => compact(key);
function sortPairs(left: Key, right: Key): number {
  return binaryCompare(left[0], right[0]) || binaryCompare(left[1], right[1]);
}
function continuity(sql: ReadSql): Continuity {
  const source = coverage(sql);
  const meta = sql.get<{
    projection_version: number;
    epoch: string;
    revision: number;
    ordering_generation: string;
  }>('SELECT projection_version,epoch,revision,ordering_generation FROM navigation_view_meta');
  if (
    source.schema_version !== 10 ||
    !meta ||
    meta.projection_version !== 1 ||
    meta.epoch !== source.epoch ||
    String(meta.revision) !== source.revision ||
    meta.ordering_generation !== ORDERING_GENERATION ||
    sql.get('SELECT 1 FROM navigation_dirty LIMIT 1')
  )
    throw new NavigationUnavailable('scoped navigation projection is not activated');
  return source;
}
function anchor(binding: string, row: WindowPosition): string {
  const offset = row.length - 5;
  const values = [
    ...row.slice(0, offset),
    row[offset],
    (row[offset + 1] as Buffer).toString('hex'),
    (row[offset + 2] as Buffer).toString('hex'),
    ...row.slice(offset + 3),
  ];
  return Buffer.from(compact([binding, values]))
    .toString('base64')
    .replaceAll('+', '-')
    .replaceAll('/', '_');
}
class StaleViewAnchor extends Error {}
function decode(binding: string, value: string): WindowPosition {
  try {
    if (!/^[A-Za-z0-9_-]*={0,2}$/.test(value) || value.length % 4 !== 0) throw new Error();
    const parsed: unknown = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
    if (
      !Array.isArray(parsed) ||
      parsed.length !== 2 ||
      typeof parsed[0] !== 'string' ||
      !Array.isArray(parsed[1])
    )
      throw new Error();
    const row: unknown[] = parsed[1];
    if (
      ![5, 6].includes(row.length) ||
      row.slice(0, row.length - 4).some((value) => !Number.isInteger(value)) ||
      row.slice(row.length - 4).some((value) => typeof value !== 'string')
    )
      throw new Error();
    const offset = row.length - 5;
    const hex = row.slice(offset + 1, offset + 3) as string[];
    if (hex.some((text) => !/^(?:[a-f0-9]{2})*$/.test(text))) throw new Error();
    const result = [
      ...row.slice(0, offset),
      row[offset],
      Buffer.from(hex[0], 'hex'),
      Buffer.from(hex[1], 'hex'),
      ...row.slice(offset + 3),
    ] as WindowPosition;
    if (anchor(parsed[0], result) !== value) throw new Error();
    if (parsed[0] !== binding) throw new StaleViewAnchor();
    return result;
  } catch (error) {
    if (error instanceof StaleViewAnchor) throw error;
    throw new Error('invalid navigation anchor');
  }
}
interface PendingWindow {
  window: ViewWindowRequest;
  key: string;
  binding: string;
  total: number;
  backward: boolean;
  order: OrderedRows | null;
  rows: WindowPosition[];
  accepted: WindowPosition[];
}
export function readContent(sql: ReadSql, value: ViewRequest): [Continuity, ViewContent] {
  const sourceCoverage = continuity(sql);
  const records = new Map<string, ViewRecord>();
  const keys = new Map<string, Key>();
  let used = 0;
  const recordBytes =
    VIEW_CONFIG.maximum_bytes - Math.min(128 * 1024, Math.floor(VIEW_CONFIG.maximum_bytes / 4));
  function closure(key: Key, settledOnly: boolean, review: boolean): Map<string, ViewRecord> {
    const pending: { key: Key; full: boolean }[] = [{ key, full: !settledOnly }];
    if (review && key[0] === 'ticket')
      for (const message of reviewMessageItems(sql, key[1]))
        pending.push({ key: ['address', message.address_id], full: true });
    const found = new Map<string, ViewRecord>();
    while (pending.length) {
      const { key: item, full } = pending.pop()!;
      const encoded = identity(item);
      const existing = found.get(encoded) ?? records.get(encoded);
      if (
        existing !== undefined &&
        (item[0] !== 'manager' || !full || 'recap_reference' in existing.value)
      )
        continue;
      const raw = record(sql, item);
      if (raw === null) throw new NavigationUnavailable('navigation view dependency is absent');
      const row = project({ kind: item[0], id: item[1], value: raw } as NavigationRecord, !full);
      found.set(encoded, row);
      keys.set(encoded, item);
      if (['ticket', 'manager', 'chief', 'goal'].includes(item[0])) {
        if (item[0] !== 'goal' && raw.conversation_id)
          pending.push({ key: ['conversation', raw.conversation_id], full: true });
        if (raw.project_id && (item[0] !== 'manager' || full))
          pending.push({ key: ['project', raw.project_id], full: true });
        if (item[0] === 'goal')
          pending.push({
            key: raw.holder_kind === 'manager' ? ['manager', raw.holder_id] : ['chief', 'chief'],
            full: true,
          });
        if (item[0] === 'ticket')
          pending.push({
            key: raw.manager_kind === 'chief' ? ['chief', 'chief'] : ['manager', raw.manager_id],
            full: true,
          });
      } else if (item[0] === 'address') {
        pending.push(
          { key: [raw.owner_kind, raw.owner_id], full: true },
          { key: ['conversation', raw.conversation_id], full: true },
        );
      }
    }
    return found;
  }
  function admit(key: Key, required = false, settledOnly = false, review = false): boolean {
    const additional = closure(key, settledOnly, review);
    let size = 0,
      newKeys = 0;
    for (const [key, row] of additional) {
      const old = records.get(key);
      size += Buffer.byteLength(compact(row)) - (old ? Buffer.byteLength(compact(old)) : 0);
      if (!old) newKeys++;
    }
    if (records.size + newKeys > VIEW_CONFIG.maximum_records || used + size > recordBytes) {
      if (required)
        throw new NavigationUnavailable('selected navigation content exceeds its budget');
      return false;
    }
    for (const [key, row] of additional) records.set(key, row);
    used += size;
    return true;
  }
  admit(['chief', 'chief'], true);
  const selected = value.selected;
  const selectedExists =
    !!selected &&
    !!sql.get('SELECT 1 FROM navigation_view_index WHERE kind=? AND id=?', [
      selected.kind,
      selected.id,
    ]);
  if (selected && selectedExists) admit([selected.kind, selected.id], true);
  if (value.selected_conversation_id && (!selected || selectedExists)) {
    if (!selected) throw new Error('old conversation requires its selected owner');
    const address = sql.get<{ id: string }>(
      "SELECT id FROM navigation_view_index WHERE conversation_id=? AND owner_kind=? AND owner_id=? AND kind='address' ORDER BY id LIMIT 1",
      [value.selected_conversation_id, selected.kind, selected.id],
    );
    if (!address)
      throw new NavigationUnavailable('selected conversation is not addressed to this owner');
    admit(['address', address.id], true);
  }
  const reviewContext = value.review_conversation_ids.map((conversation_id) => {
    const canonical_address_id = canonicalReviewAddress(sql, conversation_id);
    if (canonical_address_id !== null) admit(['address', canonical_address_id], true);
    return { conversation_id, canonical_address_id };
  });
  const reviewBase = [sectionKey('review', null), 'current', value.locale];
  const eligibleCount =
    sql.get<{ value: number }>(
      'SELECT value FROM navigation_view_counts WHERE section_key=? AND profile=? AND locale=?',
      reviewBase,
    )?.value ?? 0;
  function historyMembers(items: ReviewHistoryKey[]): Set<string> {
    const found = new Set<string>();
    for (const item of items) {
      let key: Key;
      if (item.kind === 'ticket') key = ['ticket', item.id];
      else {
        const conversation = record(sql, ['conversation', item.id]);
        if (!conversation?.attention.durable.message_for_owner) continue;
        const addressId = canonicalReviewAddress(sql, item.id);
        const address = addressId === null ? null : record(sql, ['address', addressId]);
        if (address === null || addressId === null) continue;
        key =
          address.owner_kind === 'ticket' ? ['ticket', address.owner_id] : ['address', addressId];
      }
      if (
        sql.get(
          'SELECT 1 FROM navigation_view_memberships WHERE section_key=? AND profile=? AND locale=? AND kind=? AND id=?',
          [...reviewBase, ...key],
        )
      )
        found.add(identity(key));
    }
    return found;
  }
  const skipped = historyMembers(value.review_skipped_keys),
    priority = historyMembers(value.review_priority_keys);
  const cohortDigest = digest(
    value.review_priority_keys.map((item) => [item.kind, item.id] as Key).sort(sortPairs),
  );
  const reviewSummary = { skipped: skipped.size, new: eligibleCount - skipped.size };
  const pendingWindows: PendingWindow[] = [];
  for (let window of value.windows) {
    const key = windowKey(window);
    const binding = digest([
      key,
      'current',
      value.locale,
      ORDERING_GENERATION,
      sourceCoverage.epoch,
      window.section === 'review' ? cohortDigest : null,
    ]);
    let boundary: WindowPosition | null;
    try {
      boundary = window.anchor === null ? null : decode(binding, window.anchor);
    } catch (error) {
      if (!(error instanceof StaleViewAnchor)) throw error;
      window = { ...window, anchor: null, direction: 'after' };
      boundary = null;
    }
    if (boundary !== null && boundary.length !== (window.section === 'review' ? 6 : 5))
      throw new Error('invalid navigation anchor arity');
    const total =
      sql.get<{ value: number }>(
        'SELECT value FROM navigation_view_counts WHERE section_key=? AND profile=? AND locale=?',
        [key, 'current', value.locale],
      )?.value ?? 0;
    let backward = window.direction === 'before';
    const order =
      window.limit && total
        ? orderedRows(
            sql,
            [key, 'current', value.locale],
            total,
            window.section === 'review',
            priority,
          )
        : null;
    function pageRows(start: number): WindowPosition[] {
      const result: WindowPosition[] = [];
      if (!order) return result;
      for (let step = 0; step < window.limit; step++) {
        const index = backward ? start - step : start + step;
        if (index < 0 || index >= total) break;
        result.push(order.row(index));
      }
      return result;
    }
    const start =
      boundary === null
        ? backward
          ? total - 1
          : 0
        : order === null
          ? 0
          : backward
            ? order.seek(boundary, false) - 1
            : order.seek(boundary, true);
    let rows = pageRows(start);
    if (!rows.length && total && window.limit && boundary !== null) {
      backward = !backward;
      rows = pageRows(backward ? total - 1 : 0);
      window = { ...window, anchor: null, direction: backward ? 'before' : 'after' };
    }
    pendingWindows.push({ window, key, binding, total, backward, order, rows, accepted: [] });
  }
  function admitMember(item: PendingWindow, row: WindowPosition): boolean {
    return admit(
      [row[row.length - 2] as string, row[row.length - 1] as string],
      false,
      item.window.section === 'settled',
      item.window.section === 'review',
    );
  }
  // Give each window one closure before any window consumes its remaining page budget.
  for (const item of pendingWindows)
    for (const row of item.rows.slice(0, 1)) if (admitMember(item, row)) item.accepted.push(row);
  for (let position = 1; position < VIEW_CONFIG.maximum_window_rows; position++)
    for (const item of pendingWindows) {
      if (
        position < item.rows.length &&
        item.accepted.length === position &&
        admitMember(item, item.rows[position])
      )
        item.accepted.push(item.rows[position]);
    }
  const windows: ViewWindow[] = pendingWindows.map((item) => {
    const { window, key, binding, backward, total, accepted, order } = item;
    if (backward) accepted.reverse();
    function continuation(row: WindowPosition, direction: '<' | '>'): string | null {
      const index = order!.index(row);
      const exists = direction === '<' ? index > 0 : index + 1 < total;
      return exists ? anchor(binding, row) : null;
    }
    return {
      forward_overlap:
        accepted.length > 1 && continuation(accepted.at(-1)!, '>')
          ? continuation(accepted[Math.max(0, Math.floor(accepted.length / 2) - 1)], '>')
          : null,
      backward_overlap:
        accepted.length > 1 && continuation(accepted[0], '<')
          ? continuation(accepted[Math.floor(accepted.length / 2)], '<')
          : null,
      window_id: digest(key),
      request: window,
      members: accepted.map((row) => ({
        kind: row[row.length - 2],
        id: row[row.length - 1],
      })) as ViewWindow['members'],
      total,
      previous: accepted.length ? continuation(accepted[0], '<') : null,
      next: accepted.length ? continuation(accepted.at(-1)!, '>') : null,
    };
  });
  const globals = Object.fromEntries(
    sql
      .all<{ name: string; value: number }>(
        'SELECT name,value FROM navigation_view_globals ORDER BY name',
      )
      .map((row) => [row.name, row.value]),
  );
  if (
    compact(Object.keys(globals).sort()) !==
    compact(['running_conversations', 'out_claims', 'review', 'unread', 'settled'].sort())
  )
    throw new NavigationUnavailable('navigation aggregates are incomplete');
  const content: ViewContent = {
    records: [...records]
      .sort(([left], [right]) => sortPairs(keys.get(left)!, keys.get(right)!))
      .map(([, row]) => row),
    windows,
    counts: globals as unknown as ViewCounts,
    review_context: reviewContext,
    review_summary: reviewSummary,
  };
  if (Buffer.byteLength(compact(content)) > VIEW_CONFIG.maximum_bytes)
    throw new NavigationUnavailable('navigation content and metadata exceed the view budget');
  return [sourceCoverage, content];
}
