import Fastify from 'fastify';
import { performance } from 'node:perf_hooks';
import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import { createSignalNavigationRuntime } from '../navigation/index.ts';
import { registerArea } from '../core/authority/register.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import type { CurrentViewSnapshot } from '../../web/src/lib/navigation/contracts.ts';

const view = encodeURIComponent(
  JSON.stringify({
    projection_version: 7,
    scope_generation: 1,
    locale: 'en-US',
    selected: { kind: 'chief', id: 'chief' },
    selected_conversation_id: null,
    review_conversation_ids: [],
    review_skipped_keys: [],
    review_priority_keys: [],
    windows: [],
  }),
);
const snapshotPath = `/api/navigation/view/snapshot?view=${view}`;
const streamPath = `/api/navigation/view/events?view=${view}`;

function setup() {
  let producer: SignalPublicationCoordinator | undefined;
  let navigation: ReturnType<typeof createSignalNavigationRuntime> | undefined;
  let attached = false;
  const database = fixture({
    commitSteps: [
      (tx) => producer?.finalize_signal(tx),
      (tx) => {
        if (attached) navigation!.navigationCommitStep(tx);
      },
    ],
  });
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'view-epoch',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run(`INSERT INTO conversations
      (conversation_id,backend_key,model,workspace_folder,access,created_at)
      VALUES('actual','codex','fixture','/synthetic','full',0)`);
    tx.run("INSERT INTO agents VALUES('chief_of_staff','actual')");
  });
  producer = new SignalPublicationCoordinator({ database, require_runtime_ownership: () => {} });
  producer.bind_runtime(() => null);
  producer.initialize();
  navigation = createSignalNavigationRuntime(producer);
  navigation.attach();
  attached = true;
  const app = Fastify({ forceCloseConnections: true });
  let revokeSnapshot = false;
  app.addHook('onSend', async (_request, _reply, payload) => {
    if (revokeSnapshot) {
      await new Promise<void>((resolve) => setImmediate(resolve));
      producer!.invalidate('shutdown');
    }
    return payload;
  });
  const config = loadConfig(undefined, { PLAN_TEST_MODE: '1' });
  registerArea(
    app,
    { prefix: '/api/navigation/view', routes: navigation.routes },
    {
      database,
      config,
      clock: buildClock(config),
      signalAuthority: producer,
    },
  );
  return {
    app,
    producer,
    navigation,
    revokeSnapshot: () => {
      revokeSnapshot = true;
    },
  };
}

// Distinct gap: ordinary requests cannot force revocation inside an asynchronous
// send hook. Reuse the original Fastify proof with the real producer, attachment,
// navigation installer, view rebuild, and routes. No separate server host runs.
test('navigation_snapshot_admission', async () => {
  const f = setup();
  try {
    const current = await f.app.inject(snapshotPath);
    expect(current.statusCode).toBe(200);
    expect(current.json()).toMatchObject({
      event: 'snapshot',
      mode: 'current',
      content: {
        records: expect.arrayContaining([
          expect.objectContaining({ kind: 'conversation', id: 'actual' }),
        ]),
      },
    });
    f.revokeSnapshot();
    const response = await f.app.inject(snapshotPath);
    expect(response.statusCode).toBe(503);
    expect(response.headers['cache-control']).toBe('no-store');
    expect(response.json()).toEqual({ detail: 'navigation publication is not ready' });
  } finally {
    await f.app.close();
    f.navigation.close();
  }
});

async function initialFrame(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): Promise<CurrentViewSnapshot> {
  const decoder = new TextDecoder();
  let text = '';
  while (!text.includes('\n\n')) {
    const result = await reader.read();
    if (result.done) throw new Error('stream ended before its initial snapshot');
    text += decoder.decode(result.value, { stream: true });
  }
  expect(text).toContain('event: snapshot\n');
  const data = text.split('\n').find((line) => line.startsWith('data: '));
  if (!data) throw new Error('stream snapshot has no data');
  return JSON.parse(data.slice(6)) as CurrentViewSnapshot;
}

// Distinct gap: a permit assertion cannot prove that actual HTTP delivery closes.
// Reuse the same application on one ephemeral listener. No browser or new host.
test('actual producer revocation closes an open HTTP stream and reattachment admits a fresh snapshot', async () => {
  const started = performance.now();
  const f = setup();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const listening = performance.now();
    const address = await f.app.listen({ host: '127.0.0.1', port: 0 });
    const startupMilliseconds = performance.now() - listening;
    const open = async () => {
      const response = await fetch(address + streamPath, { signal: AbortSignal.timeout(3000) });
      expect(response.status).toBe(200);
      expect(response.headers.get('content-type')).toContain('text/event-stream');
      if (!response.body) throw new Error('stream body is absent');
      return response.body.getReader();
    };
    reader = await open();
    const old = await initialFrame(reader);
    const ended = reader.read();
    f.producer.invalidate('detached');
    expect((await ended).done).toBe(true);
    expect(() => f.producer.validate(old.authority)).toThrow();
    f.navigation.close();
    f.navigation.attach();
    reader = await open();
    const fresh = await initialFrame(reader);
    expect(fresh.mode).toBe('current');
    expect(fresh.authority.permit.installation_id).not.toBe(old.authority.permit.installation_id);
    expect(fresh.authority.permit.runtime_epoch).toBe(old.authority.permit.runtime_epoch);
    expect(fresh.authority.delivery_session_id).toBe(old.authority.delivery_session_id);
    console.info(
      JSON.stringify({
        proof: 'actual-publication-http-stream',
        startupMilliseconds,
        caseMilliseconds: performance.now() - started,
      }),
    );
  } finally {
    await reader?.cancel();
    await f.app.close();
    f.navigation.close();
  }
});
