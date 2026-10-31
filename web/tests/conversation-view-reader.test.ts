import { afterEach, expect, it, vi } from 'vitest';
import { ConversationViewReader } from '../src/lib/conversation/view/reader.svelte';
import { viewSnapshot, viewChanges } from './support/conversation/view-fixture';
import type { FixtureEvent } from './support/conversation/view-fixture';
import type { ViewBodyReference } from '../src/lib/conversation/view/contracts';
import { admitsTranscriptItem, transcriptHistoryEdges } from '../src/lib/conversation/view/presentation';
import { threadItems } from '../src/lib/conversation/threadLayout';

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
it('preserves hidden report page cursors without admitting deferred report bodies', () => {
  const page = snapshot().page;
  const visible = page.items[0]!;
  if (visible.kind !== 'row') throw new Error('Expected fixture row');
  const report = {...visible, kind: 'row' as const, key: 'report', row_kind: 'provider_selection_reported' as const,
    order: {sequence: 0, slot: 0}, source_sequence: 0, turn_key: null, work_group_key: null,
    fold_rule: 'never' as const, sender_message_id: null,
    body: {kind: 'deferred' as const, reference: {body_id: 'report-body', version: '1', byte_count: 100, media_type: 'application/json' as const}}};
  const scope = {kind: 'turn' as const, key: 'turn'};
  const cursor = {projection_version: page.projection_version, lens: page.lens, scope, order: report.order};
  const hiddenPage = {...page, scope, items: [report], older: cursor, newer: cursor};
  const admitted = [report, visible].filter(admitsTranscriptItem);
  expect(admitted).toEqual([visible]);
  expect(transcriptHistoryEdges([hiddenPage], admitted).map(edge => [edge.direction, edge.anchorKey, edge.cursor])).toEqual([
    ['older', visible.key, cursor], ['newer', visible.key, cursor],
  ]);
  expect(transcriptHistoryEdges([hiddenPage], []).map(edge => [edge.direction, edge.anchorKey])).toEqual([
    ['older', null], ['newer', null],
  ]);
  const mixed = {...hiddenPage, items: [report, visible, {...report, key: 'last-report', order: {sequence: 3, slot: 0}}]};
  expect(transcriptHistoryEdges([mixed], admitted).map(edge => edge.anchorKey)).toEqual([visible.key, visible.key]);
});
it('keeps legacy tool work contiguous across retained provider observations', () => {
  const tool = {kind: 'tool_call' as const, key: 'tool-1', sequence: 1, createdAt: 1,
    toolCallId: 'tool-1', title: 'Read', toolKind: 'read', detail: null, startedDetail: null,
    cappedDetailSequence: null, status: 'completed' as const, progress: null};
  const report = {kind: 'provider_selection_reported' as const, key: 'report', sequence: 2, createdAt: 2,
    provider: 'claude' as const, turnNumber: 1, childLifetime: 'child', model: 'claude-haiku-4-5-20251001', reasoningEffort: null};
  const stored = [tool, report, {...tool, key: 'tool-2', sequence: 3, toolCallId: 'tool-2'}];
  const items = threadItems(stored);
  expect(items).toHaveLength(1);
  expect(items[0]?.kind === 'work_group' && items[0].entries.map(entry => entry.key)).toEqual(['tool-1', 'tool-2']);
});
const readers: ConversationViewReader[] = [];
afterEach(() => { for (const reader of readers) reader.close(); readers.length = 0; Source.all = []; vi.useRealTimers(); });
async function start(initial = snapshot()) {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const reader = new ConversationViewReader('c', 'focus', true); readers.push(reader);
  const ready = reader.connect(); Source.all.at(-1)!.send('snapshot', initial); await ready;
  return reader;
}

it('does not replay existing live output when its first body read races a newer version', async () => {
  const initial = snapshot();
  const reference: ViewBodyReference = { body_id: 'live:turn', version: '1', byte_count: 100, media_type: 'text/markdown' };
  initial.status.live_output = { runtime_epoch: 'epoch', turn_key: 'turn:1', version: 1, byte_count: 100, reference };
  const reader = await start(initial);
  expect(reader.liveOutputIsArrival(reference, false)).toBe(false);
  const next = { ...reference, version: '2', byte_count: 200 };
  // A version that overtakes the initial body read is still restoration until
  // some text actually appears. Replacing painted text is a genuine arrival.
  expect(reader.liveOutputIsArrival(next, false)).toBe(false);
  expect(reader.liveOutputIsArrival(next, true)).toBe(true);
  reader.status = { ...initial.status, live_output: { ...initial.status.live_output!, turn_key: 'turn:2', reference: next } };
  expect(reader.liveOutputIsArrival(next, false)).toBe(true);
});

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
  expect(reader.safeReadThroughSequence).toBe(1);
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
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(snapshot().page)));
  expect(await reader.load()).toBe(true);
  expect(reader.trouble).toBe(true); // A successful page does not repair the tail.
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
  expect(reader.safeReadThroughSequence).toBe(1);
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
// the browser destination proof, which checks actual newest admission and placement.
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

it('retains actual newest on fresh entry without reading a watermark-based history window', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const fetch = vi.fn(async () => new Response(JSON.stringify(snapshot().page)));
  vi.stubGlobal('fetch', fetch);
  const initial = viewSnapshot('c', 'focus', [{ ...rows[0], sequence: 2000 }]);
  const reader = new ConversationViewReader('c', 'focus', true);
  readers.push(reader);
  const ready = reader.connect(); Source.all[0].send('snapshot', initial); await ready;
  await Promise.resolve();
  expect(fetch).not.toHaveBeenCalled();
  expect(reader.pages[0].items).toEqual(initial.page.items);
  expect(reader.safeReadThroughSequence).toBe(2000);
  expect(reader.entryPhase).toBe('placing');
  reader.finishEntry();
  expect(reader.entryPhase).toBe('complete');
});

it('recovers the initial entry gate after a failed stream connection', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const reader = new ConversationViewReader('c', 'focus', true);
  readers.push(reader);
  const first = reader.connect();
  Source.all[0].onerror?.();
  await expect(first).rejects.toBeDefined();
  expect(reader.entryPhase).toBe('error');
  expect(reader.loading).toBe(false);
  const retry = reader.connect();
  Source.all[1].send('snapshot', snapshot()); await retry;
  expect(reader.entryPhase).toBe('placing');
  expect(reader.trouble).toBe(false);
  reader.finishEntry();
  expect(reader.entryPhase).toBe('complete');
});

it('does not publish an initial snapshot after its reader closes', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const reader = new ConversationViewReader('c', 'focus', true);
  readers.push(reader);
  void reader.connect();
  reader.close();
  Source.all[0].send('snapshot', snapshot());
  await Promise.resolve();
  expect(reader.pages).toEqual([]);
});

it('releases placement when the newer half fails and retries the original context', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const anchorPage = viewSnapshot('c', 'focus', [{ ...rows[0], sequence: 11 }]).page;
  anchorPage.newer = cursor(11);
  const newerPage = viewSnapshot('c', 'focus', [{ ...rows[0], sequence: 12 }]).page;
  const { fetch, pending } = holdPages();
  const anchor = { key: anchorPage.items[0].key, order: { sequence: 11, slot: 0 }, offset: -17, following: false };
  const reader = new ConversationViewReader('c', 'focus', true, undefined, anchor);
  readers.push(reader);
  const ready = reader.connect();
  Source.all.at(-1)!.send('snapshot', snapshot());
  await flushDemand();
  pending[0].complete(Response.json(anchorPage));
  await vi.waitFor(() => expect(pending).toHaveLength(2));
  // The anchor's older page exists, but the context below it is still incomplete.
  expect(reader.restoring).toBe(true);
  pending[1].complete(Response.json({ error: 'offline' }, { status: 503 }));
  await ready;
  await vi.waitFor(() => expect(reader.restorationFailed).toBe(true));
  expect(reader.safeReadThroughSequence).toBe(1);
  expect(reader.restoring).toBe(false);
  reader.retryRestoration();
  await vi.waitFor(() => expect(pending).toHaveLength(3));
  expect(JSON.parse(new URL(fetch.mock.calls[2][0], 'http://local').searchParams.get('cursor')!).order.sequence).toBe(11);
  pending[2].complete(Response.json(newerPage));
  await vi.waitFor(() => expect(reader.restoring).toBe(false));
  expect(reader.restorationFailed).toBe(false);
  expect(reader.pages.flatMap(page => page.items).map(item => item.key)).toEqual([anchorPage.items[0].key, newerPage.items[0].key]);
});

it('retires timed-out restoration history without canceling unrelated demand or accepting late pages', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const oldPage = viewSnapshot('c', 'focus', [{...rows[0],sequence:11}]).page;
  const {pending} = holdPages();
  const anchor = {key:oldPage.items[0].key,order:oldPage.items[0].order,offset:-17,following:false};
  const reader = new ConversationViewReader('c','focus',true,undefined,anchor);
  readers.push(reader);
  const ready = reader.connect();
  Source.all.at(-1)!.send('snapshot',snapshot());
  await flushDemand();
  const scope = {kind:'turn' as const,key:'unrelated'};
  const ordinary = reader.load(scope);
  const original = reader.restorationAnchor;
  reader.failTextRestoration(anchor.key);
  expect(reader.restoring).toBe(false);
  expect(pending[0].signal.aborted).toBe(true);
  expect(reader.restorationAnchor).toBe(original);
  await ready;
  await vi.waitFor(() => expect(pending).toHaveLength(2));
  expect(reader.pages.flatMap(page=>page.items).some(item=>item.key===anchor.key)).toBe(false);
  expect(pending).toHaveLength(2);
  expect(pending[1].signal.aborted).toBe(false);
  pending[1].complete(Response.json({...snapshot().page,scope}));
  expect(await ordinary).toBe(true);
  reader.retryRestoration();
  await flushDemand();
  expect(reader.restorationAttempt).toBe(1);
  expect(reader.restorationOutcome).toBe('pending');
  expect(pending).toHaveLength(3);
  expect(pending[2].signal.aborted).toBe(false);
  pending[2].complete(Response.json(oldPage));
  await vi.waitFor(() => expect(reader.restoring).toBe(false));
  expect(reader.restorationAnchor).toBe(original);
  expect(reader.pages.flatMap(page=>page.items).some(item=>item.key===anchor.key)).toBe(true);
  const pages = reader.pages;
  pending[0].complete(Response.json({...oldPage,items:[]}));
  await flushDemand();
  expect(reader.pages).toBe(pages);
  expect(reader.safeReadThroughSequence).toBe(1);
});

it('preserves an ordinary caller that shares the exact restoration history request', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const oldPage = viewSnapshot('c','focus',[{...rows[0],sequence:11}]).page;
  const {pending} = holdPages();
  const anchor = {key:oldPage.items[0].key,order:oldPage.items[0].order,offset:-17,following:false};
  const reader = new ConversationViewReader('c','focus',true,undefined,anchor);
  readers.push(reader);
  const ready = reader.connect();
  Source.all.at(-1)!.send('snapshot',snapshot());
  await flushDemand();
  const ordinary = reader.load(timeline,{...cursor(11),order:{...anchor.order,slot:anchor.order.slot+1}},'older',true);
  reader.failTextRestoration(anchor.key);
  expect(pending[0].signal.aborted).toBe(false);
  pending[0].complete(Response.json(oldPage));
  expect(await ordinary).toBe(true);
  await ready;
  expect(reader.restorationOutcome).toBe('failed');
  expect(reader.restoring).toBe(false);
});

it('ignores a restoration read after cancellation and leaves ordinary history reads active', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const oldPage = viewSnapshot('c', 'focus', [{ ...rows[0], sequence: 11 }]).page;
  const { pending } = holdPages();
  const anchor = { key: oldPage.items[0].key, order: oldPage.items[0].order, offset: -17, following: false };
  const reader = new ConversationViewReader('c', 'focus', true, undefined, anchor);
  readers.push(reader);
  const ready = reader.connect();
  Source.all.at(-1)!.send('snapshot', snapshot());
  await flushDemand();
  reader.cancelRestoration();
  pending[0].complete(Response.json(oldPage));
  await ready;
  expect(reader.restorationOutcome).toBe('idle');
  expect(reader.restorationAnchor).toBeNull();
  expect(reader.pages.flatMap(page => page.items).some(item => item.key === anchor.key)).toBe(false);
  const ordinary = reader.load({kind:'timeline',key:null}, cursor(11), 'older');
  await vi.waitFor(() => expect(pending).toHaveLength(2));
  reader.cancelRestoration();
  pending[1].complete(Response.json(oldPage));
  expect(await ordinary).toBe(true);
});

it('retains the original saved target within the shared bound and releases it for a new destination', async () => {
  const page = snapshot().page;
  const anchor = { key: page.items[0].key, order: page.items[0].order, offset: -17, following: false };
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const reader = new ConversationViewReader('c', 'focus', true, undefined, anchor);
  readers.push(reader);
  const ready = reader.connect(); Source.all.at(-1)!.send('snapshot', snapshot()); await ready;
  const original = reader.restorationAnchor;
  reader.failTextRestoration(anchor.key);
  reader.retryRestoration();
  await vi.waitFor(() => expect(reader.restoring).toBe(false));
  expect(reader.restorationAnchor).toBe(original);
  reader.settleTextRestoration();
  for (let index = 0; index < 3; index++) {
    const scope = {kind:'turn' as const,key:`saved-turn:${index}`};
    const next = viewSnapshot('c', 'focus', Array.from({length:100}, (_, n) => ({...rows[0],sequence:100+index*100+n}))).page;
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({...next,scope})));
    await reader.expand(scope,true);
  }
  expect(reader.pages.flatMap(page => page.items).length).toBeLessThanOrEqual(300);
  expect(reader.pages.flatMap(page => page.items).some(item => item.key === anchor.key)).toBe(true);
  reader.cancelRestoration();
  expect(reader.restorationAnchor).toBeNull();
});

it('reports a missing direct text piece after body admission, including after a settled address changes', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const page = snapshot().page;
  const anchor = {key:page.items[0].key,order:page.items[0].order,offset:-17,following:false,content:{pieceIndex:1,lineOffset:0,text:{contentIdentity:{algorithm:'sha256' as const,digest:'exact',utf16Length:4},blockIndex:0,textLeafPath:[0],utf16Offset:0}}};
  const reader = new ConversationViewReader('c','focus',true,undefined,anchor); readers.push(reader);
  const ready = reader.connect(); Source.all.at(-1)!.send('snapshot',snapshot()); await ready;
  reader.textBodyReady(anchor.key,[{piece:'text'},{piece:'text'}]);
  expect(reader.restorationOutcome).toBe('pending');
  reader.settleTextRestoration();
  reader.textBodyReady(anchor.key,[{piece:'text'},{piece:'image'}]);
  expect(reader.restorationOutcome).toBe('unavailable');
  expect(reader.restorationAnchor?.content).toEqual(anchor.content);
});

function recoveryOwner() {
  const controller = new AbortController();
  let current = true;
  return { controller, retire: () => { current = false; }, owner: {
    kind: 'restoration-fallback' as const, signal: controller.signal, isCurrent: () => current
  } };
}

it('rejects obsolete recovery admission before replacing retained history, even without transport abort', async () => {
  const reader = await start();
  const pages = reader.pages;
  const { pending } = holdPages();
  const attempt = recoveryOwner();
  const recovery = reader.loadLatest(attempt.owner);
  await flushDemand();
  attempt.retire();
  pending[0].complete(Response.json(snapshot().page));
  expect(await recovery).toBe(false);
  expect(reader.pages).toBe(pages);
  expect(reader.trouble).toBe(false);
});

it('retires a canceled recovery queue slot while an ignored-abort transport remains unresolved', async () => {
  const reader = await start();
  const { pending } = holdPages();
  const attempt = recoveryOwner();
  const recovery = reader.loadLatest(attempt.owner);
  await flushDemand();
  attempt.controller.abort();
  expect(await recovery).toBe(false);
  const ordinary = reader.load({kind:'turn', key:'after-cancel'});
  await flushDemand();
  expect(pending).toHaveLength(2);
  pending[1].complete(Response.json({...snapshot().page, scope:{kind:'turn',key:'after-cancel'}}));
  expect(await ordinary).toBe(true);
  const pages = reader.pages;
  pending[0].complete(Response.json(snapshot().page));
  await flushDemand();
  expect(reader.pages).toBe(pages);
});

it('preserves ordinary demand that shares recovery admission when recovery cancels', async () => {
  const reader = await start(readSnapshot(100));
  await readHistory(reader);
  sendReadChanges(101, 100);
  const { pending } = holdPages();
  const attempt = recoveryOwner();
  const recovery = reader.loadLatest(attempt.owner);
  const ordinary = reader.load(timeline, null, 'older', true);
  await flushDemand();
  attempt.controller.abort();
  expect(await recovery).toBe(false);
  expect(pending[0].signal.aborted).toBe(false);
  pending[0].complete(Response.json(readSnapshot(101).page));
  expect(await ordinary).toBe(true);
  expect(reader.safeReadThroughSequence).toBe(101);
});

it('waits for incompatible ordinary history before admitting automatic newest demand', async () => {
  const reader = await start();
  const { pending } = holdPages();
  const ordinary = reader.load(timeline, cursor(11));
  await flushDemand();
  const attempt = recoveryOwner();
  const recovery = reader.loadLatest(attempt.owner);
  expect(pending[0].signal.aborted).toBe(false);
  pending[0].complete(Response.json(snapshot().page));
  expect(await ordinary).toBe(true);
  await flushDemand();
  expect(pending).toHaveLength(2);
  pending[1].complete(Response.json(snapshot().page));
  expect(await recovery).toBe(true);
});

it('wakes pending recovery at capacity and retires canceled pending admission before scope collapse', async () => {
  const reader = await start();
  const { pending } = holdPages();
  const scopes = Array.from({length:20}, (_, i) => ({kind:'turn' as const,key:`capacity:${i}`}));
  const reads = scopes.map(scope => reader.load(scope));
  await flushDemand();
  const canceled = recoveryOwner();
  const canceledRead = reader.loadLatest(canceled.owner);
  canceled.controller.abort();
  expect(await canceledRead).toBe(false);
  const active = recoveryOwner();
  const recovery = reader.loadLatest(active.owner);
  await reader.expand(scopes[19], false);
  for (let i = 0; i < 19; i++) {
    await flushDemand();
    pending[i].complete(Response.json({...snapshot().page,scope:scopes[i]}));
    expect(await reads[i]).toBe(true);
  }
  expect(await reads[19]).toBe(false);
  await flushDemand();
  expect(pending).toHaveLength(20);
  pending[19].complete(Response.json(snapshot().page));
  expect(await recovery).toBe(true);
});

it('keeps genuine newest-page failure through target retirement, reconnect, and unrelated admission', async () => {
  const reader = await start();
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('newest unavailable'); }));
  expect(await reader.loadLatest(recoveryOwner().owner)).toBe(false);
  expect(reader.trouble).toBe(true);
  reader.cancelRestoration();
  const reconnect = reader.connect();
  Source.all.at(-1)!.send('snapshot', snapshot());
  await reconnect;
  expect(reader.trouble).toBe(true);
  const scope = {kind:'turn' as const,key:'unrelated-success'};
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({...snapshot().page,scope})));
  expect(await reader.load(scope)).toBe(true);
  expect(reader.trouble).toBe(true);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(snapshot().page)));
  expect(await reader.load(timeline, cursor(11), 'older')).toBe(true);
  expect(reader.trouble).toBe(true); // Historical success does not retry newest admission.
  expect(await reader.load(timeline,null,'older',true)).toBe(true);
  expect(reader.trouble).toBe(false);
});

function readSnapshot(...sequences: number[]) {
  return viewSnapshot('c', 'focus', sequences.map(sequence => ({ ...rows[0], sequence })));
}
async function readHistory(reader: ConversationViewReader) {
  const page = { ...readSnapshot(1).page, newer: cursor(1) };
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(page)));
  await reader.load(timeline, cursor(2), 'older', true);
  await vi.advanceTimersByTimeAsync(0);
}
function sendReadChanges(sequence: number, from: number, visible = true) {
  const change = viewChanges(readSnapshot(sequence), from);
  if (!visible) { change.upserts = []; change.scope_changes = []; }
  Source.all.at(-1)!.send('changes', change);
}

it('freezes future read credit in history across refused rows, hidden frames, expanded scopes, and reconnects', async () => {
  const reader = await start(readSnapshot(100));
  expect(reader.safeReadThroughSequence).toBe(100);
  await readHistory(reader);
  sendReadChanges(101, 100);
  expect(reader.coverage).toBe(101);
  expect(reader.safeReadThroughSequence).toBe(100);
  expect(reader.pages.flatMap(page => page.items).some(item => item.key === 'row:101')).toBe(false);
  sendReadChanges(102, 101, false);
  let sequence = 102;
  for (const kind of ['turn', 'work_group'] as const) {
    const scope = { kind, key: kind };
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...readSnapshot(102).page, scope })));
    await reader.expand(scope, true);
    await vi.advanceTimersByTimeAsync(0);
    sendReadChanges(sequence + 1, sequence, false);
    sequence++;
    expect(reader.safeReadThroughSequence).toBe(100);
  }
  expect(reader.coverage).toBe(104);
  expect(reader.safeReadThroughSequence).toBe(100);
});

it('credits newest acquisition only through the checkpoint captured when queued demand executes, then verifies replay', async () => {
  const reader = await start(readSnapshot(100));
  await readHistory(reader);
  const { pending } = holdPages();
  const newest = reader.load(timeline, null, 'older', true);
  // Enqueued demand has not paused the tail yet. A refused row arrives first.
  sendReadChanges(101, 100);
  await flushDemand();
  pending[0].complete(Response.json(readSnapshot(101, 102).page));
  expect(await newest).toBe(true);
  expect(reader.coverage).toBe(101);
  expect(reader.safeReadThroughSequence).toBe(101);
  await vi.advanceTimersByTimeAsync(0);
  expect(new URL(Source.all.at(-1)!.url, 'http://local').searchParams.get('after')).toBe('101');
  sendReadChanges(102, 101);
  expect(reader.safeReadThroughSequence).toBe(102);
  sendReadChanges(103, 102, false);
  expect(reader.safeReadThroughSequence).toBe(103);
});

it('requires admitted versions at the newest edge, permits cursor-end recovery, and rejects empty cursor exhaustion', async () => {
  const reader = await start(readSnapshot(100));
  await readHistory(reader);
  sendReadChanges(101, 100);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(readSnapshot().page)));
  await reader.load(timeline, cursor(1), 'newer', true);
  await vi.advanceTimersByTimeAsync(0);
  sendReadChanges(102, 101, false);
  expect(reader.safeReadThroughSequence).toBe(100);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(readSnapshot(102).page)));
  await reader.load(timeline, cursor(1), 'newer', true);
  expect(reader.safeReadThroughSequence).toBe(102);
  await vi.advanceTimersByTimeAsync(0);
  const updated = viewChanges(readSnapshot(103), 102);
  const edge = { ...readSnapshot(102).page.items[0], order: { sequence: 102, slot: 1 }, changed_through_sequence: 103 };
  updated.upserts = [edge];
  updated.scope_changes = [{ scope: timeline, item_keys: [edge.key], removed_keys: [] }];
  Source.all.at(-1)!.send('changes', updated);
  expect(reader.safeReadThroughSequence).toBe(103);
  const removal = viewChanges(readSnapshot(104), 103);
  removal.upserts = []; removal.scope_changes = []; removal.removed_keys = [edge.key];
  Source.all.at(-1)!.send('changes', removal);
  sendReadChanges(105, 104, false);
  expect(reader.safeReadThroughSequence).toBe(103);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(readSnapshot().page)));
  await reader.load(timeline, null, 'older', true);
  expect(reader.safeReadThroughSequence).toBe(105);
});

it('keeps newest evidence when older rows leave retention and freezes it when expansion evicts the timeline', async () => {
  const initial = readSnapshot(...Array.from({ length: 100 }, (_, i) => i + 201));
  const reader = await start(initial);
  for (const [first, last] of [[101, 200], [1, 100]]) {
    const page = readSnapshot(...Array.from({ length: 100 }, (_, i) => first + i)).page;
    page.newer = cursor(last);
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(page)));
    await reader.load(timeline, cursor(first), 'older');
  }
  await vi.advanceTimersByTimeAsync(0);
  sendReadChanges(301, 300);
  expect(reader.pages.flatMap(page => page.items)).toHaveLength(300);
  expect(reader.safeReadThroughSequence).toBe(301);
  // A changed old row and a new edge arrive together. Retention evicts the
  // changed row, so checking membership before trim would grant false credit.
  const changedOld = { ...readSnapshot(2).page.items[0], changed_through_sequence: 302 };
  const update = viewChanges(readSnapshot(302), 301);
  update.upserts.unshift(changedOld);
  update.scope_changes[0].item_keys.unshift(changedOld.key);
  Source.all.at(-1)!.send('changes', update);
  expect(reader.safeReadThroughSequence).toBe(301);
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(readSnapshot(302).page)));
  await reader.load(timeline, null, 'older', true);
  expect(reader.safeReadThroughSequence).toBe(302);
  for (let index = 0; index < 3; index++) {
    const scope = { kind: 'turn' as const, key: `credit:${index}` };
    const page = readSnapshot(...Array.from({ length: 100 }, (_, i) => i + 400 + index * 100)).page;
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...page, scope })));
    await reader.expand(scope, true);
  }
  await vi.advanceTimersByTimeAsync(0);
  sendReadChanges(303, 302, false);
  expect(reader.safeReadThroughSequence).toBe(302);
});

it('grants no transcript credit to a status-only reader', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const reader = new ConversationViewReader('c', 'focus', false); readers.push(reader);
  const ready = reader.connect();
  Source.all.at(-1)!.send('snapshot', viewSnapshot('c', 'focus', rows, false));
  await ready;
  sendReadChanges(2, 1, false);
  expect(reader.coverage).toBe(2);
  expect(reader.safeReadThroughSequence).toBe(0);
});

it('retires old read evidence when a reconnect snapshot loses its new rows during retention', async () => {
  const reader = await start(readSnapshot(...Array.from({ length: 100 }, (_, i) => i + 201)));
  for (const first of [101, 1]) {
    const page = readSnapshot(...Array.from({ length: 100 }, (_, i) => first + i)).page;
    page.newer = cursor(first + 99);
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(page)));
    await reader.load(timeline, cursor(first), 'older');
  }
  await vi.advanceTimersByTimeAsync(0);
  const replacement = readSnapshot(300, 301);
  replacement.page.older = cursor(300);
  Source.all.at(-1)!.send('snapshot', replacement);
  const held = reader.pages.flatMap(page => page.items).map(item => item.key);
  expect(held).toContain('row:300');
  expect(held).not.toContain('row:301');
  expect(reader.coverage).toBe(301);
  expect(reader.safeReadThroughSequence).toBe(300);
  sendReadChanges(302, 301, false);
  expect(reader.safeReadThroughSequence).toBe(300);
});


it.each(['abort', 'stale', 'fetch-failure'] as const)('grants no read credit to %s recovery admission or its late response', async cause => {
  const reader = await start(readSnapshot(100));
  await readHistory(reader);
  sendReadChanges(101, 100);
  const pages = reader.pages;
  const { pending } = holdPages();
  const attempt = recoveryOwner();
  const recovery = reader.loadLatest(attempt.owner);
  await flushDemand();
  if (cause === 'abort') attempt.controller.abort();
  if (cause === 'stale') attempt.retire();
  pending[0].complete(cause === 'fetch-failure' ? new Response('unavailable', {status:503})
    : Response.json(readSnapshot(101).page));
  expect(await recovery).toBe(false);
  expect(reader.pages).toBe(pages);
  expect(reader.safeReadThroughSequence).toBe(100);
  // A status frame after reconnect cannot turn the rejected page into delivery.
  await vi.advanceTimersByTimeAsync(0);
  sendReadChanges(102, 101, false);
  expect(reader.coverage).toBe(102);
  expect(reader.safeReadThroughSequence).toBe(100);
});

it('retires an expired saved target without credit and credits only valid newest recovery after admission', async () => {
  vi.useFakeTimers(); vi.stubGlobal('EventSource', Source);
  const initial = readSnapshot(100);
  const item = initial.page.items[0];
  const anchor = {key:item.key,order:item.order,offset:-17,following:false};
  const reader = new ConversationViewReader('c','focus',true,undefined,anchor);
  readers.push(reader);
  const ready = reader.connect(); Source.all.at(-1)!.send('snapshot',initial); await ready;
  await readHistory(reader);
  sendReadChanges(101,100);
  reader.failTextRestoration(anchor.key, true);
  reader.cancelRestoration();
  expect(reader.restorationAnchor).toBeNull();
  expect(reader.safeReadThroughSequence).toBe(100);
  const { pending } = holdPages();
  const recovery = reader.loadLatest(recoveryOwner().owner);
  await flushDemand();
  expect(reader.safeReadThroughSequence).toBe(100);
  pending[0].complete(Response.json(readSnapshot(101).page));
  expect(await recovery).toBe(true);
  expect(reader.safeReadThroughSequence).toBe(101);
  expect(reader.pages.flatMap(page=>page.items).map(item=>item.key)).toEqual(['row:101']);
  expect(reader.pages.flatMap(page=>page.items).length).toBeLessThanOrEqual(300);
});
