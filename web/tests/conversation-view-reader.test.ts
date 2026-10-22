import { afterEach, expect, it, vi } from 'vitest';
import { ConversationViewReader } from '../src/lib/conversation/view/reader.svelte';
import { viewSnapshot, viewChanges } from './support/conversation/view-fixture';
import type { FixtureEvent } from './support/conversation/view-fixture';

class Source {
  static all: Source[] = [];
  handlers = new Map<string, (event: { data: string }) => void>();
  closed = false;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { Source.all.push(this); }
  addEventListener(name: string, handler: (event: { data: string }) => void) { this.handlers.set(name, handler); }
  close() { this.closed = true; }
  send(name: string, value: unknown) { this.handlers.get(name)?.({ data: JSON.stringify(value) }); }
}
const rows: FixtureEvent[] = [{ conversation_id: 'c', sequence: 1, kind: 'message_to_owner', created_at: 1, payload: { text: 'original' } }];
const snapshot = () => viewSnapshot('c', 'focus', rows);
const readers: ConversationViewReader[] = [];
afterEach(() => { for (const reader of readers) reader.close(); readers.length = 0; Source.all = []; vi.useRealTimers(); });
async function start() {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const reader = new ConversationViewReader('c', 'focus', true); readers.push(reader);
  const ready = reader.connect(); Source.all[0].send('snapshot', snapshot()); await ready;
  return reader;
}

it('replays the unchanged coverage after a deferred history read and ignores the closed stream', async () => {
  const reader = await start();
  let complete!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { complete = resolve; })));
  const loading = reader.load();
  const scope = { kind: 'turn' as const, key: 'turn:e1' };
  const second = reader.expand(scope, true);
  await Promise.resolve();
  expect(Source.all[0].closed).toBe(true);
  const update = viewSnapshot('c', 'focus', [{ ...rows[0], payload: { text: 'newer text' } }]); update.coverage.through_sequence = 2;
  Source.all[0].send('changes', viewChanges(update, 1));
  expect(reader.coverage).toBe(1);
  complete(new Response(JSON.stringify(snapshot().page))); await loading;
  await vi.advanceTimersByTimeAsync(0);
  expect(Source.all).toHaveLength(1); // The second queued read still owns demand.
  complete(new Response(JSON.stringify({ ...snapshot().page, scope, items: [] }))); await second;
  await vi.advanceTimersByTimeAsync(0);
  const tail = Source.all.at(-1)!;
  expect(new URL(tail.url, 'http://local').searchParams.get('after')).toBe('1');
  expect(new URL(tail.url, 'http://local').searchParams.get('ranges')).toContain('timeline');
  tail.send('changes', viewChanges(update, 1));
  expect(reader.coverage).toBe(2);
  expect(JSON.stringify(reader.pages)).toContain('newer text');
});

it('does not restore a collapsed scope from a delayed response and resumes after failed reads', async () => {
  const reader = await start();
  let complete!: (response: Response) => void;
  const scope = { kind: 'turn' as const, key: 'turn:e1' };
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { complete = resolve; })));
  const loading = reader.expand(scope, true); await Promise.resolve();
  await reader.expand(scope, false);
  complete(new Response(JSON.stringify({ ...snapshot().page, scope }))); await loading;
  expect(reader.pages.every(page => page.scope.kind === 'timeline')).toBe(true);
  await vi.advanceTimersByTimeAsync(0);
  Source.all.at(-1)!.send('changes', viewChanges(snapshot(), 1));
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network'); }));
  await reader.load(); await vi.advanceTimersByTimeAsync(0);
  expect(Source.all.at(-1)!.closed).toBe(false);
  Source.all.at(-1)!.send('reset', {});
  // A reset asks for everything again. It does not take the conversation away first.
  expect(reader.pages).toHaveLength(1);
  expect(reader.coverage).toBe(0);
});


it('moves an updated item to its canonical expanded scope and never restores an absent scope', async () => {
  const reader = await start();
  const scope = { kind: 'turn' as const, key: 'turn:e1' };
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...snapshot().page, scope, items: [] }))));
  await reader.expand(scope, true); await vi.advanceTimersByTimeAsync(0);
  const change = viewChanges(snapshot(), 1);
  change.scope_changes = [
    { scope: { kind: 'timeline', key: null }, item_keys: [], removed_keys: ['row:1'] },
    { scope, item_keys: ['row:1'], removed_keys: [] },
  ];
  Source.all.at(-1)!.send('changes', change);
  expect(reader.pages.find(page => page.scope.kind === 'timeline')?.items).toHaveLength(0);
  expect(reader.pages.find(page => page.scope.kind === 'turn')?.items).toHaveLength(1);
  await reader.expand(scope, false);
  Source.all.at(-1)!.send('changes', change);
  expect(reader.pages.every(page => page.scope.kind === 'timeline')).toBe(true);
});


it('refreshes held ask bodies on a runtime stop without advancing durable coverage', async () => {
  const reader = await start();
  const fetch = vi.fn(async () => new Response(JSON.stringify(snapshot().page)));
  vi.stubGlobal('fetch', fetch);
  const change = viewChanges(snapshot(), 1);
  change.upserts = []; change.scope_changes = []; change.status.stopped_without_ending = true;
  Source.all.at(-1)!.send('changes', change);
  await Promise.resolve();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(reader.coverage).toBe(1);
  expect(Source.all[0].closed).toBe(true);
});


it('publishes unknown runtime status when an established stream fails', async () => {
  const reader = await start();
  reader.status = { ...reader.status!, turn_running: true };
  Source.all.at(-1)!.onerror?.();
  expect(reader.trouble).toBe(true);
  expect(reader.status?.authority).toBe('unknown');
  expect(reader.status?.turn_running).toBeNull();
  expect(reader.status?.live_output).toBeNull();
  expect(reader.pages).toHaveLength(1);
});

it('keeps every held scope when a reconnect brings a snapshot of the same conversation', async () => {
  // The stream is torn down once per page of scroll-back, so its snapshot is an
  // ordinary reconnect, not a new conversation. Discarding history here took the
  // reader's place away every time they scrolled back.
  const reader = await start();
  const scope = { kind: 'turn' as const, key: 'turn:e1' };
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...snapshot().page, scope }))));
  await reader.expand(scope, true); await vi.advanceTimersByTimeAsync(0);
  expect(reader.pages).toHaveLength(2);
  const replacement = snapshot(); replacement.status.diagnostics_expired = true;
  Source.all.at(-1)!.send('snapshot', replacement);
  expect(reader.pages).toHaveLength(2);
  expect(reader.coverage).toBe(1);
  expect(reader.isExpanded(scope)).toBe(true);
  expect(reader.status?.diagnostics_expired).toBe(true);
});

it('cancels queued page demand on reset and admits fresh demand after the replacement snapshot', async () => {
  const reader = await start();
  // Keep a page queued while a stream reset remains authoritative.
  let complete!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { complete = resolve; })));
  const loading = reader.load();
  Source.all[0].send('reset', {});
  expect(await loading).toBe(false);
  // What is on screen stays on screen until its replacement exists.
  expect(reader.pages).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1500);
  Source.all.at(-1)!.send('snapshot', snapshot());
  const scope = { kind: 'turn' as const, key: 'turn:e1' };
  const next = reader.load(scope); await Promise.resolve();
  complete(new Response(JSON.stringify({ ...snapshot().page, scope }))); await next;
  expect(reader.pages).toHaveLength(2);
});

it('re-reads everything at a runtime epoch boundary without emptying the conversation', async () => {
  const reader = await start();
  const change = viewChanges(snapshot(), 1); change.status.runtime_epoch = 'replacement';
  Source.all[0].send('changes', change);
  // Suspect, so read it all again - and keep showing it while that happens.
  expect(reader.pages).toHaveLength(1);
  expect(reader.coverage).toBe(0);
  await vi.advanceTimersByTimeAsync(0);
  expect(new URL(Source.all.at(-1)!.url, 'http://local').searchParams.has('after')).toBe(false);
  Source.all.at(-1)!.send('snapshot', snapshot());
  expect(reader.pages).toHaveLength(1);
});

it('distinguishes a canonical expired body from an unrelated HTTP 410 failure', async () => {
  const reader = await start();
  const reference = { body_id: 'row:1', version: '1', byte_count: 0, media_type: 'application/json' as const };
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({error:{code:'diagnostic_expired'}}, {status:410})));
  await expect(reader.body(reference, new AbortController().signal)).rejects.toThrow('This diagnostic detail expired.');
  vi.stubGlobal('fetch', vi.fn(async () => new Response('Gone', {status:410})));
  await expect(reader.body(reference, new AbortController().signal)).rejects.toThrow('Conversation body: 410');
});

// These reader proofs cover admission and ownership. Visible placement belongs to
// the browser destination proof, which must keep automatic recovery unavailable.
const timeline = { kind: 'timeline' as const, key: null };
const cursor = (sequence: number) => ({ projection_version: snapshot().page.projection_version, lens: 'focus' as const, scope: { ...timeline }, order: { sequence, slot: 0 } });
function holdPages() {
  const pending: { signal: AbortSignal; complete: (response: Response) => void }[] = [];
  const fetch = vi.fn((_url: string, options: RequestInit) => new Promise<Response>(complete => {
    // Deliberately ignore abort: a late response must still fail the identity guard.
    pending.push({ signal: options.signal as AbortSignal, complete });
  }));
  vi.stubGlobal('fetch', fetch);
  return { fetch, pending };
}
async function flushDemand() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

it('coalesces equivalent request values and refuses incompatible ordinary demand', async () => {
  const reader = await start();
  const { fetch, pending } = holdPages();
  const first = reader.load(timeline, cursor(10), 'older');
  const equivalent = reader.load({ ...timeline }, cursor(10), 'older');
  expect(await reader.load(timeline, cursor(11), 'older')).toBe(false);
  expect(await reader.load(timeline, cursor(10), 'newer')).toBe(false);
  await flushDemand();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(pending[0].signal.aborted).toBe(false);
  pending[0].complete(Response.json(snapshot().page));
  expect(await first).toBe(true);
  expect(await equivalent).toBe(true);
});

it('distinguishes replacement intent and keeps the successor owned after obsolete completion', async () => {
  const reader = await start();
  const { fetch, pending } = holdPages();
  const history = reader.load(timeline, cursor(10), 'older');
  await flushDemand();
  // Even an otherwise identical request has different semantics when it replaces.
  const replacement = reader.load(timeline, cursor(10), 'older', true);
  expect(pending[0].signal.aborted).toBe(true);
  const oldPage = snapshot().page;
  oldPage.items = oldPage.items.map(item => ({ ...item, key: 'obsolete' }));
  pending[0].complete(Response.json(oldPage));
  expect(await history).toBe(false);
  await flushDemand();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(reader.pages.flatMap(page => page.items).some(item => item.key === 'obsolete')).toBe(false);
  // The obsolete finally must not delete the successor's scope reservation.
  expect(await reader.load(timeline, cursor(11), 'older')).toBe(false);
  const equivalent = reader.load({ ...timeline }, cursor(10), 'older', true);
  pending[1].complete(Response.json(snapshot().page));
  expect(await replacement).toBe(true);
  expect(await equivalent).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('replaces incompatible cursored replacement demand with an actual newest-page read', async () => {
  const reader = await start();
  const { fetch, pending } = holdPages();
  const restore = reader.load(timeline, cursor(10), 'older', true);
  await flushDemand();
  const latest = reader.load(timeline, null, 'older', true);
  expect(pending[0].signal.aborted).toBe(true);
  pending[0].complete(Response.json(snapshot().page));
  expect(await restore).toBe(false);
  await flushDemand();
  const url = new URL(fetch.mock.calls[1][0], 'http://local');
  expect(url.searchParams.has('cursor')).toBe(false);
  expect(url.searchParams.get('direction')).toBe('older');
  pending[1].complete(Response.json(snapshot().page));
  expect(await latest).toBe(true);
});

it('reuses a scope slot at twenty demands and preserves unrelated queued scopes', async () => {
  const reader = await start();
  const { fetch, pending } = holdPages();
  const history = reader.load(timeline, cursor(10));
  await flushDemand();
  const scopes = Array.from({ length: 19 }, (_, i) => ({ kind: 'turn' as const, key: `turn:${i}` }));
  const others = scopes.map(scope => reader.load(scope));
  expect(await reader.load({ kind: 'turn', key: 'overflow' })).toBe(false);
  const latest = reader.load(timeline, null, 'older', true);
  expect(pending[0].signal.aborted).toBe(true);
  pending[0].complete(Response.json(snapshot().page));
  expect(await history).toBe(false);
  for (let i = 0; i < scopes.length; i++) {
    await flushDemand();
    expect(pending[i + 1].signal.aborted).toBe(false);
    pending[i + 1].complete(Response.json({ ...snapshot().page, scope: scopes[i] }));
    expect(await others[i]).toBe(true);
  }
  await flushDemand();
  pending[20].complete(Response.json(snapshot().page));
  expect(await latest).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(21);
  expect(reader.pages.length).toBeLessThanOrEqual(20);
});

it('returns false for failed, invalid, canceled, and disposed admission', async () => {
  const reader = await start();
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network'); }));
  expect(await reader.load()).toBe(false);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...snapshot().page, conversation_id: 'other' })));
  expect(await reader.load()).toBe(false);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...snapshot().page, scope: { kind: 'turn', key: 'wrong' } })));
  expect(await reader.load()).toBe(false);
  const { pending } = holdPages();
  const scope = { kind: 'turn' as const, key: 'turn:canceled' };
  const canceled = reader.load(scope);
  await flushDemand();
  await reader.expand(scope, false);
  pending[0].complete(Response.json({ ...snapshot().page, scope }));
  expect(await canceled).toBe(false);
  const disposed = reader.load();
  reader.close();
  expect(await disposed).toBe(false);
  expect(await reader.load()).toBe(false);
});
