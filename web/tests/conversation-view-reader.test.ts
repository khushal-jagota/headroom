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
  expect(reader.pages).toHaveLength(0);
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
