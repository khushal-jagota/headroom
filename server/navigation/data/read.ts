import type { ReadSql } from '../../core/database/contracts.ts';
import type { ViewRequest, ViewContent, NavigationRecord, CurrentAuthority, CurrentViewSnapshot, ViewRecord } from '../../../web/src/lib/navigation/contracts.ts';
import type { NavigationSource } from '../contracts.ts';
import { NavigationUnavailable } from '../logic/errors.ts';
import { coverage } from './registry.ts';
import { compact, digest, ORDERING_GENERATION } from '../logic/json.ts';
import { viewIdentity, windowKey } from '../logic/view.ts';
function record(sql: ReadSql, kind: string, id: string): NavigationRecord | null {
  const row = sql.get<{ value: string }>('SELECT value FROM navigation_records WHERE kind=? AND id=?', [kind, id]);
  return row ? { kind, id, value: JSON.parse(row.value) } as NavigationRecord : null;
}
export function snapshot(sql: ReadSql, value: ViewRequest, authority: CurrentAuthority, sources: readonly NavigationSource[] = []): CurrentViewSnapshot {
  if (sql.get('SELECT 1 FROM navigation_dirty LIMIT 1')) throw new NavigationUnavailable('navigation projection is not current');
  const current = coverage(sql);
  const records = new Map<string, ViewRecord>();
  const admit = (kind: string, id: string): void => {
    const identity = compact([kind, id]);
    if (records.has(identity)) return;
    const item = record(sql, kind, id);
    if (!item) throw new NavigationUnavailable('navigation view dependency is absent');
    records.set(identity, item as ViewRecord);
    const body = item.value as unknown as Record<string, unknown>;
    if (['ticket', 'manager', 'chief', 'goal'].includes(kind) && body.conversation_id) admit('conversation', String(body.conversation_id));
    if (body.project_id) admit('project', String(body.project_id));
  };
  admit('chief', 'chief');
  const selectedExists = value.selected && record(sql, value.selected.kind, value.selected.id);
  if (value.selected && selectedExists) admit(value.selected.kind, value.selected.id);
  if (value.selected_conversation_id && (!value.selected || selectedExists)) {
    if (!value.selected) throw new NavigationUnavailable('old conversation requires its selected owner');
    throw new NavigationUnavailable('selected conversation is not addressed to this owner');
  }
  const contributions = sources.flatMap(source => source.view ? [source.view(sql, value)] : []);
  for (const contribution of contributions) for (const item of contribution.records ?? []) records.set(compact([item.kind, item.id]), item);
  const contributedWindows = new Map(contributions.flatMap(item => item.windows ?? []).map(window => [window.window_id, window]));
  const windows = value.windows.map(window => {
    if (window.anchor !== null) {
      let envelope: [string, unknown[]];
      try {
        if (!/^[A-Za-z0-9_-]*={0,2}$/.test(window.anchor) || window.anchor.length % 4 !== 0) throw new NavigationUnavailable();
        const parsed: unknown = JSON.parse(Buffer.from(window.anchor, 'base64url').toString());
        if (!Array.isArray(parsed) || parsed.length !== 2 || typeof parsed[0] !== 'string' || !Array.isArray(parsed[1])) throw new NavigationUnavailable();
        const row = parsed[1];
        if (![5, 6].includes(row.length) || row.slice(0, row.length - 4).some(item => !Number.isInteger(item)) || row.slice(row.length - 4).some(item => typeof item !== 'string')) throw new NavigationUnavailable();
        const hex = row.slice(row.length - 4, row.length - 2) as string[];
        if (hex.some(item => !/^(?:[a-f0-9]{2})*$/.test(item))) throw new NavigationUnavailable();
        if (Buffer.from(compact(parsed)).toString('base64').replaceAll('+', '-').replaceAll('/', '_') !== window.anchor) throw new NavigationUnavailable();
        envelope = parsed as [string, unknown[]];
      } catch { throw new NavigationUnavailable('invalid navigation anchor'); }
      const binding = digest([windowKey(window), 'current', value.locale, ORDERING_GENERATION, current.epoch, window.section === 'review' ? digest(value.review_priority_keys.map(item => [item.kind, item.id]).sort()) : null]);
      if (envelope[0] !== binding) window = { ...window, anchor: null, direction: 'after' };
      else if (envelope[1].length !== (window.section === 'review' ? 6 : 5)) throw new NavigationUnavailable('invalid navigation anchor arity');
    }
    return contributedWindows.get(digest(windowKey(window))) ?? { window_id: digest(windowKey(window)), request: window, members: [], total: 0, previous: null, next: null, forward_overlap: null, backward_overlap: null };
  });
  const content: ViewContent = {
    records: [...records.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([, row]) => row),
    windows,
    counts: { running_conversations: 0, out_claims: 0, review: 0, unread: 0, settled: 0 },
    review_context: value.review_conversation_ids.map(conversation_id => ({ conversation_id, canonical_address_id: null })),
    review_summary: { skipped: 0, new: 0 },
  };
  for (const contribution of contributions) {
    if (contribution.counts) for (const name of Object.keys(content.counts) as (keyof typeof content.counts)[]) content.counts[name] += contribution.counts[name] ?? 0;
    if (contribution.review_context) content.review_context = contribution.review_context;
    if (contribution.review_summary) { content.review_summary.skipped += contribution.review_summary.skipped; content.review_summary.new += contribution.review_summary.new; }
  }
  if (records.size > 512) throw new NavigationUnavailable('selected navigation content exceeds its budget');
  if (Buffer.byteLength(compact(content)) > 768 * 1024) throw new NavigationUnavailable('navigation content and metadata exceed the view budget');
  return { event: 'snapshot', wire_version: 3, projection_version: 7, view_id: viewIdentity(value), scope_generation: value.scope_generation, ordering_generation: ORDERING_GENERATION, coverage: current, content_version: digest(content), mode: 'current', authority, content };
}
