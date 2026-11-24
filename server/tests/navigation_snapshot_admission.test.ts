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
import { review_body } from '../navigation/views/review_cards.ts';

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

async function setup(asking = false, events: Array<[number, string, object]> = []) {
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
    tx.run("INSERT INTO agents(agent_key,conversation_id) VALUES('chief_of_staff','actual')");
    for (const [sequence, kind, payload] of events)
      tx.run(
        "INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) VALUES('actual',?,?,?,0)",
        [sequence, kind, JSON.stringify(payload)],
      );
  });
  producer = new SignalPublicationCoordinator({ database, require_runtime_ownership: () => {} });
  producer.bind_runtime(() =>
    asking
      ? { pending_permission_ask_ids: new Set(['ask']), pending_user_input_questions: new Map() }
      : null,
  );
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
  await app.ready();
  await navigation.start();
  return {
    app,
    database,
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
  const f = await setup();
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
  const f = await setup();
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

// Distinct gap: nothing admitted an ask to the Review queue before. Only the real projection
// can show that it holds the Chief's card, keeps the unread list as it was, and drops the card
// when the ask is gone. Reuses this fixture: no Ticket, browser or second host.
test('a Chief with an open ask joins Review and not the unread list', async () => {
  const snapshot = async (f: Awaited<ReturnType<typeof setup>>, section: 'review' | 'unread') => {
    const request = encodeURIComponent(
      JSON.stringify({
        projection_version: 7,
        scope_generation: 1,
        locale: 'en-US',
        selected: { kind: 'chief', id: 'chief' },
        selected_conversation_id: null,
        review_conversation_ids: [],
        review_skipped_keys: [],
        review_priority_keys: [],
        windows: [
          {
            section,
            owner: null,
            group: 'all',
            child_filter: 'all',
            anchor: null,
            direction: 'after',
            limit: 30,
          },
        ],
      }),
    );
    const response = await f.app.inject(`/api/navigation/view/snapshot?view=${request}`);
    expect(response.statusCode).toBe(200);
    return response.json() as CurrentViewSnapshot;
  };
  const members = async (f: Awaited<ReturnType<typeof setup>>, section: 'review' | 'unread') =>
    ((await snapshot(f, section)).content.windows[0]?.members ?? []).map(
      (key) => `${key.kind}:${key.id}`,
    );
  const conversation = async (f: Awaited<ReturnType<typeof setup>>) =>
    (await snapshot(f, 'review')).content.records.find(
      (record) => record.kind === 'conversation' && record.id === 'actual',
    )?.value;
  const permission = (detail: string) => ({
    ask_id: 'ask',
    title: 'Run a command',
    detail,
    options: [{ option_id: 'allow', label: 'Allow', option_kind: 'allow_once' }],
  });
  const events = (detail: string): Array<[number, string, object]> => [
    [1, 'message_to_owner', { text: 'Done' }],
    [2, 'permission_asked', permission(detail)],
    [3, 'tool_call_started', {}],
  ];
  const asking = await setup(true);
  const quiet = await setup(false);
  const answerable = await setup(true, events('ls'));
  const answered = await setup(false, events('ls'));
  const oversized = await setup(true, events('x'.repeat(4096)));
  // A key request keeps the conversation waiting after its permission ask was answered.
  const superseded = await setup(true, [
    ...events('ls'),
    [4, 'permission_answered', { ask_id: 'ask', option_id: 'allow' }],
  ]);
  try {
    // Distinct risk: an unloaded Review card offline has only the window. Its conversation record
    // must carry the read bound (the newest owner message, not the newest event) and the waiting
    // ask in the conversation route's shape, only while it waits, and never an ask over the cap.
    expect(await conversation(answerable)).toMatchObject({
      latest_owner_message_sequence: 1,
      pending_ask: { kind: 'permission', ...permission('ls') },
    });
    expect(await conversation(answered)).toMatchObject({
      latest_owner_message_sequence: 1,
      pending_ask: null,
    });
    expect(await conversation(oversized)).toMatchObject({ pending_ask: null });
    expect(await conversation(superseded)).toMatchObject({ pending_ask: null });
    // Outside the review window the record leaves the ask out of the view's budget. The Chief's
    // conversation is admitted with the Chief first, so the Review case above also proves that
    // the review window brings the ask back to a record admitted without it.
    const outside = (await answerable.app.inject(snapshotPath)).json() as CurrentViewSnapshot;
    const plain = outside.content.records.find(
      (record) => record.kind === 'conversation' && record.id === 'actual',
    )?.value;
    expect(plain).toMatchObject({ latest_owner_message_sequence: 1 });
    expect(plain).not.toHaveProperty('pending_ask');
    expect(await members(asking, 'review')).toHaveLength(1);
    expect(await members(asking, 'review')).toEqual([expect.stringMatching(/^address:/)]);
    expect(await members(asking, 'unread')).toEqual([]);
    expect(await members(quiet, 'review')).toEqual([]);
    // Distinct risk: GET /api/review rebuilt on this window must show the same ask-only card,
    // say why it waits, and refuse to read an unprojected index as an empty queue.
    expect(asking.database.read(review_body)).toMatchObject({
      available: true,
      total: 1,
      cards: [
        { kind: 'chief', id: 'chief', title: 'Chief of Staff', waits: ['question'], stage: null },
      ],
    });
    expect(quiet.database.read(review_body)).toEqual({ available: true, total: 0, cards: [] });
    expect(fixture().read(review_body)).toEqual({ available: false, total: null, cards: [] });
  } finally {
    await asking.app.close();
    asking.navigation.close();
    await quiet.app.close();
    quiet.navigation.close();
    for (const f of [answerable, answered, oversized, superseded]) {
      await f.app.close();
      f.navigation.close();
    }
  }
});
