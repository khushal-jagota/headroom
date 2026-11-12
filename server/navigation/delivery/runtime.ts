/** Scoped wire-3 delivery over the committed navigation projection. */
import { once } from 'node:events';
import type { ServerResponse } from 'node:http';
import type { Database, ReadSql } from '../../core/database/contracts.ts';
import type { CurrentAuthority, CurrentViewSnapshot, ViewRequest } from '../../../web/src/lib/navigation/contracts.ts';
import type { NavigationAuthority } from '../contracts.ts';
import { compact } from '../logic/json.ts';
import { windowKey } from '../logic/view.ts';
import { coverage } from '../data/registry.ts';
import { DELIVERY } from './configuration.ts';
import { prune } from './retention.ts';
type Coverage = CurrentViewSnapshot['coverage'];
interface Notice { coverage: Coverage; keys: Set<string>; sections: Set<string>; aggregate: boolean; reset: boolean }
const key = (kind: string, id: string): string => compact([kind, id]);
function notice(sql: ReadSql, previous: Coverage | null): Notice {
  const current = coverage(sql);
  const result: Notice = { coverage: current, keys: new Set(), sections: new Set(), aggregate: false, reset: false };
  if (!previous || (['scope', 'schema_version', 'semantics', 'epoch'] as const).some(name => current[name] !== previous[name])) return { ...result, aggregate: true, reset: true };
  const after = Number(previous.revision), revision = Number(current.revision);
  if (revision < after) return { ...result, aggregate: true, reset: true };
  const changes = sql.all<{ revision: number; footprint: string }>('SELECT revision,footprint FROM navigation_view_changes WHERE revision>? ORDER BY revision', [after]);
  if (revision !== after && (!changes.length || changes[0].revision !== after + 1 || changes.at(-1)!.revision !== revision || changes.length !== revision - after)) return { ...result, aggregate: true, reset: true };
  for (const change of changes) {
    const value = JSON.parse(change.footprint) as { keys: [string, string][]; sections: string[]; globals: boolean };
    for (const [kind, id] of value.keys) result.keys.add(key(kind, id));
    for (const section of value.sections) result.sections.add(section);
    result.aggregate ||= value.globals;
  }
  return result;
}
export function difference(previous: CurrentViewSnapshot, current: CurrentViewSnapshot): Record<string, unknown> & { event: string } {
  const { event: _event, mode: _mode, content, ...common } = current;
  if (current.content_version === previous.content_version) return { ...common, event: 'checkpoint', previous_coverage: previous.coverage };
  const old = new Map(previous.content.records.map(row => [key(row.kind, row.id), compact(row)]));
  const fresh = new Set(content.records.map(row => key(row.kind, row.id)));
  return { ...common, event: 'patch', previous_coverage: previous.coverage, previous_content_version: previous.content_version,
    upserts: content.records.filter(row => old.get(key(row.kind, row.id)) !== compact(row)),
    evictions: previous.content.records.filter(row => !fresh.has(key(row.kind, row.id))).map(({ kind, id }) => ({ kind, id })),
    windows: content.windows, counts: content.counts, review_context: content.review_context, review_summary: content.review_summary };
}
function frame(value: object & { event: string }): string { return `event: ${value.event}\ndata: ${compact(value)}\n\n`; }
class ScopedStream {
  private previous: CurrentViewSnapshot;
  private keys: Set<string>;
  private readonly sections: Set<string>;
  private readonly reviewContext: boolean;
  private closed = false;
  private sending = true;
  private dirty = false;
  private pending: Coverage | undefined;
  private lastSend = Date.now();
  private readonly abort = new AbortController();
  private unwatch: () => void = () => {};
  private readonly response: ServerResponse;
  private readonly authority: NavigationAuthority;
  private readonly token: CurrentAuthority;
  private readonly snapshot: () => CurrentViewSnapshot;
  private readonly forget: () => void;
  constructor(response: ServerResponse, authority: NavigationAuthority,
    token: CurrentAuthority, request: ViewRequest, initial: CurrentViewSnapshot,
    snapshot: () => CurrentViewSnapshot, forget: () => void) {
    this.response = response; this.authority = authority; this.token = token;
    this.snapshot = snapshot; this.forget = forget;
    this.previous = initial;
    this.keys = new Set(initial.content.records.map(row => key(row.kind, row.id)));
    this.sections = new Set(request.windows.map(windowKey));
    this.reviewContext = !!(request.review_conversation_ids.length || request.review_skipped_keys.length || request.review_priority_keys.length);
    if (request.review_skipped_keys.length || request.review_priority_keys.length) this.sections.add(compact(['review', null, 'all', 'all']));
    this.unwatch = authority.watch(token, () => this.close());
    if (this.closed) { this.unwatch(); return; }
    response.once('close', () => this.close());
    void this.send(frame(initial)).catch(() => this.close()).finally(() => { this.sending = false; void this.flush(); });
  }
  private async send(body: string): Promise<void> {
    if (this.closed) return;
    this.authority.validate(this.token);
    if (!this.response.write(body)) {
      const deadline = setTimeout(() => this.abort.abort(), DELIVERY.sendTimeoutMilliseconds);
      try { await once(this.response, 'drain', { signal: this.abort.signal }); }
      finally { clearTimeout(deadline); }
    }
    this.authority.validate(this.token);
    this.lastSend = Date.now();
  }
  notify(value: Notice): void {
    if (this.closed) return;
    if (value.reset) { this.close(); return; }
    this.pending = value.coverage;
    this.dirty ||= value.aggregate || [...value.keys].some(item => this.keys.has(item))
      || [...value.sections].some(item => this.sections.has(item))
      || this.reviewContext && [...value.keys].some(item => (JSON.parse(item) as string[])[0] === 'address');
    void this.flush();
  }
  private async flush(): Promise<void> {
    if (this.closed || this.sending || !this.pending) return;
    this.sending = true;
    try {
      const target = this.pending, dirty = this.dirty;
      this.pending = undefined; this.dirty = false;
      this.authority.validate(this.token);
      if (Number(target.revision) > Number(this.previous.coverage.revision)) {
        const current = dirty ? this.snapshot() : { ...this.previous, coverage: target };
        if (current.coverage.epoch !== this.previous.coverage.epoch) { this.close(); return; }
        await this.send(frame(difference(this.previous, current)));
        this.previous = current;
        this.keys = new Set(current.content.records.map(row => key(row.kind, row.id)));
      }
    } catch { this.close(); }
    finally { this.sending = false; if (this.pending && !this.closed) void this.flush(); }
  }
  heartbeat(): void {
    if (this.closed || this.sending || Date.now() - this.lastSend < DELIVERY.heartbeatMilliseconds) return;
    this.sending = true;
    void this.send(': heartbeat\n\n').catch(() => this.close()).finally(() => { this.sending = false; void this.flush(); });
  }
  close(): void {
    if (this.closed) return;
    this.closed = true; this.abort.abort(); this.unwatch(); this.forget(); this.response.end();
  }
}
/** One notice cursor serves all subscriptions. Content reads happen only for affected views. */
export class ScopedDelivery {
  private previous: Coverage | null = null;
  private readonly streams = new Set<ScopedStream>();
  private timer: ReturnType<typeof setInterval> | undefined;
  private retention: ReturnType<typeof setInterval> | undefined;
  private readonly database: Database;
  constructor(database: Database) { this.database = database; }
  start(): void {
    if (this.timer) throw new Error('scoped navigation delivery already started');
    this.database.write(prune, { changeSignal: 'silent' });
    this.previous = this.database.read(sql => coverage(sql));
    this.timer = setInterval(() => this.poll(), DELIVERY.pollMilliseconds);
    this.retention = setInterval(() => {
      try { this.database.write(prune, { changeSignal: 'silent' }); }
      catch (error) { console.error('navigation retention failed; retry next interval', error); }
    }, DELIVERY.retentionMilliseconds);
  }
  private poll(): void {
    try {
      const value = this.database.read(sql => notice(sql, this.previous));
      const changed = compact(this.previous) !== compact(value.coverage);
      if (changed) {
        this.previous = value.coverage;
        for (const stream of this.streams) stream.notify(value);
      }
      for (const stream of this.streams) stream.heartbeat();
    } catch { for (const stream of [...this.streams]) stream.close(); }
  }
  open(response: ServerResponse, authority: NavigationAuthority, token: CurrentAuthority,
    request: ViewRequest, snapshot: () => CurrentViewSnapshot): void {
    let stream: ScopedStream | undefined;
    try {
      const initial = snapshot();
      stream = new ScopedStream(response, authority, token, request, initial, snapshot,
        () => { if (stream) this.streams.delete(stream); });
      if (!response.writableEnded) this.streams.add(stream);
    } catch { response.end(); }
  }
  close(): void {
    clearInterval(this.timer); clearInterval(this.retention);
    this.timer = undefined; this.retention = undefined;
    for (const stream of [...this.streams]) stream.close();
  }
}
