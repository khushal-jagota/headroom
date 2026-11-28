/** A phone resend of a first message, whose response was lost, to each conversation owner. */
import { dirname, join, resolve } from 'node:path';
import { MINIMUM_SUPPORTED_BUILD } from '../ios_compatibility/policy.ts';
import { afterEach, expect, test, vi } from 'vitest';
import { startServer } from '../core/start.ts';
import type { StartedServer } from '../core/start.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { BackendStateStore } from '../conversation/backend_state.ts';
import { ScriptedBackendRegistry } from '../conversation/testing/scripted_registry.ts';
import { create_backend_runtime } from '../conversation/snapshot/composition.ts';
import { RecordingBackendProbeEnvironment } from '../conversation/snapshot/recording_environment.ts';
import { create_test_environment_runtime } from '../environments/testing/composition.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../tickets/execution_admission.ts';
import { create_manager } from '../managers/data.ts';
import { live_frames } from '../live_updates/testing/client.ts';
import { create, fixture, fixture_path } from './ticket_fixture.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

// Risk: the phone resends the exact body of a first message (null conversation_id, the same
// sender_message_id, and the creation-only backend_key, model and reasoning_effort) after its
// response was lost. The server could start a second conversation, record the message twice,
// or refuse the repeat with a 4xx because the creation fields now meet an existing conversation.
// Gap: no test sent the same owner body twice. One real server, because each owner has its own
// route and dispatcher branch. Each owner gets two concurrent copies (the phone timed out while
// the first was still in flight) and then a third copy after both settled.
test('a repeated first message reaches each owner once and the repeat is answered like the first', async () => {
  const database = fixture();
  const path = fixture_path(database);
  const root = dirname(path);
  const ticket = create(database, { title: 'Repeat Ticket' });
  const manager = database.write((tx) => {
    // The rows application bootstrap writes and the rules seed lacks.
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'repeat-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'repeat')");
    tx.run(
      `INSERT INTO manager_launch_defaults SELECT 1,employee_backend,employee_launch_model,
        employee_launch_reasoning_effort FROM chief_settings`,
    );
    return create_manager(tx, {
      title: 'Repeat Manager',
      project_id: 'project_other',
      clock: { nowUnix: () => 0, now: () => new Date(0) } as never,
    });
  });
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: path,
    PLAN_WORKSPACE_ROOT: root,
    PLAN_PORT: '0',
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_CREDENTIAL_KEY_PATH: path + '.credential-key',
    PLAN_EXECUTOR_ID: '',
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
    PLAN_APP_ROOT: resolve(import.meta.dirname, '../..'),
  }))
    vi.stubEnv(key, value);
  const failures = vi.spyOn(console, 'error');
  const registry = new ScriptedBackendRegistry();
  let server: StartedServer | undefined;
  try {
    const start = () =>
      startServer({
        createEnvironments: create_test_environment_runtime,
        createBackendServices: (context) =>
          create_backend_runtime({
            database: context.database,
            config: context.config,
            now: () => context.clock.now(),
            environment: new RecordingBackendProbeEnvironment(),
          }),
        createConversationCore(context) {
          return new SqliteProcessConversationSystem({
            store: context.record.store,
            execution_refusal: (id) =>
              context.database.read((sql) =>
                conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
              ),
            delivery_model_refusal: (backend, model, ticket_id) =>
              new BackendStateStore(context.database).delivery_model_refusal(
                backend,
                model,
                ticket_id,
              ),
            backend_registry: registry.registry,
            message_files: new ConversationMessageFiles(context.config.db_path),
            require_runtime_ownership: context.require_runtime_ownership,
            require_signal_current: () => context.publication.current_read(),
            save_attempt: context.publication.save_attempt,
            publish_event: (event) => context.record.live_tail.publish_event(event),
            publish_frame: (id, frame) => context.record.live_tail.publish_frame(id, frame),
            publish_status_changed: (id) =>
              context.record.live_tail.views.publish_status_changed(id),
          });
        },
      });
    server = await start();
    const call = async (
      method: 'GET' | 'POST',
      route: string,
      body?: string,
      iosBuild?: number,
    ) => {
      const response = await fetch(server!.address + '/api' + route, {
        method,
        headers: {
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
          ...(iosBuild === undefined ? {} : { 'x-panels-ios-build': String(iosBuild) }),
        },
        ...(body === undefined ? {} : { body }),
      });
      return { status: response.status, body: (await response.json()) as Record<string, any> };
    };
    // A send answers at acceptance. Its stored outcome reports the delivery.
    const settled = (kind: string, id: string, sender_message_id: string) =>
      vi.waitFor(async () => {
        const read = await call(
          'GET',
          `/conversation/owner-sends/${sender_message_id}?owner_kind=${kind}&owner_id=${id}`,
        );
        expect(read.body.outcome?.outcome).not.toBe('pending');
        return read.body.outcome;
      });
    const conversations = () =>
      database.read((sql) =>
        sql.all<{ conversation_id: string }>('SELECT conversation_id FROM conversations'),
      );
    const prompts = (sender_message_id: string) =>
      database.read((sql) =>
        sql.all<{ conversation_id: string; kind: string }>(
          `SELECT conversation_id, kind FROM conversation_events
           WHERE json_extract(payload,'$.sender_message_id')=? AND kind LIKE 'prompt%'`,
          [sender_message_id],
        ),
      );
    const owners = [
      {
        kind: 'chief',
        id: 'chief',
        base: '/chief/conversation',
        link: () =>
          database.read(
            (sql) =>
              sql.get<{ conversation_id: string | null }>(
                "SELECT conversation_id FROM agents WHERE agent_key='chief_of_staff'",
              )?.conversation_id,
          ),
      },
      {
        kind: 'ticket',
        id: ticket.id,
        base: `/tickets/${ticket.id}/conversation`,
        link: () =>
          database.read(
            (sql) =>
              sql.get<{ conversation_id: string | null }>(
                'SELECT conversation_id FROM tickets WHERE id=?',
                [ticket.id],
              )?.conversation_id,
          ),
      },
      {
        kind: 'manager',
        id: manager.id,
        base: `/managers/${manager.id}/conversation`,
        link: () =>
          database.read(
            (sql) =>
              sql.get<{ conversation_id: string | null }>(
                'SELECT conversation_id FROM agents WHERE agent_key=?',
                [manager.manager_agent_key],
              )?.conversation_id,
          ),
      },
    ];
    const first_bodies = new Map<string, string>();
    for (const owner of owners) {
      const before = conversations().length;
      const start = await call('GET', owner.base + '/start-values');
      expect(start).toMatchObject({
        status: 200,
        body: { backend_key: expect.any(String), model: expect.any(String) },
      });
      expect(start.body).toHaveProperty('reasoning_effort');
      // The phone's first message: no conversation_id key, the picker's creation choices, and
      // its default steer mode. The resend is the same bytes.
      const sender_message_id = `repeat-${owner.kind}`;
      const body = JSON.stringify({
        content: [{ piece: 'text', text: `hello ${owner.kind}` }],
        sender_label: 'You',
        mode: 'steer',
        backend_key: start.body.backend_key,
        model: start.body.model,
        reasoning_effort: start.body.reasoning_effort,
        sender_message_id,
        sent_at_unix_milliseconds: 1_783_166_400_000,
      });
      first_bodies.set(owner.kind, body);
      const send = () => call('POST', owner.base + '/send', body);
      const concurrent = await Promise.all([send(), send()]);
      for (const reply of concurrent) expect(reply, owner.kind).toMatchObject({ status: 200 });
      const id = concurrent[0].body.conversation_id;
      expect(typeof id, owner.kind).toBe('string');
      // The first answers at acceptance. The repeat answers accepted while the message is
      // pending, or its final answer once it settled, and never sends it again.
      expect(concurrent[0].body, owner.kind).toEqual({
        conversation_id: id,
        fate: 'accepted',
        message_id: sender_message_id,
      });
      expect(
        [concurrent[0].body, { conversation_id: id, fate: 'started' }],
        owner.kind,
      ).toContainEqual(concurrent[1].body);
      expect(await settled(owner.kind, owner.id, sender_message_id), owner.kind).toEqual({
        message_id: sender_message_id,
        conversation_id: id,
        outcome: 'delivered',
        refusal_reason: null,
        reason: null,
      });
      const later = await send();
      expect(later, owner.kind).toEqual({
        status: 200,
        body: { conversation_id: id, fate: 'started' },
      });
      expect(conversations().length - before, owner.kind).toBe(1);
      expect(owner.link(), owner.kind).toBe(id);
      expect(prompts(sender_message_id), owner.kind).toEqual([
        { conversation_id: id, kind: 'prompt' },
      ]);
      const children = await registry.inspect(id);
      expect(children, owner.kind).toHaveLength(1);
    }
    // Native builds use these same routes without a sender ID. Reuse the three linked owners
    // and the same private host so the compatibility check exercises actual HTTP and queue output.
    for (const owner of owners) {
      const conversation_id = owner.link()!;
      const body = JSON.stringify({
        conversation_id,
        content: [{ piece: 'text', text: 'reply without a sender ID' }],
        sender_label: 'You',
        mode: 'queue',
      });
      const send = owner.base + '/send';
      if (owner.kind === 'manager') {
        expect(await call('POST', send, body, MINIMUM_SUPPORTED_BUILD - 1)).toMatchObject({
          status: 426,
          body: { error: { code: 'ios_update_required' } },
        });
      }
      expect(await call('POST', send, body, MINIMUM_SUPPORTED_BUILD)).toEqual({
        status: 200,
        body: { conversation_id, fate: 'queued', queue_position: 1 },
      });
      const lookup = '/conversation/conversations/' + conversation_id;
      expect(await call('GET', lookup, undefined, MINIMUM_SUPPORTED_BUILD)).toMatchObject({
        status: 200,
        body: { conversation_id, held_prompts: [{ sender_label: 'owner' }] },
      });
    }
    for (const reconnect of [false, true]) {
      const frames = live_frames(
        server!.address,
        {
          kind: 'conversation_view',
          conversation_id: owners[2].link()!,
          params: { lens: 'full', transcript: 'true' },
        },
        AbortSignal.timeout(8000),
      );
      const first = (await frames.next()).value;
      await frames.return(undefined);
      expect(first?.kind, reconnect ? 'reconnect' : 'initial connection').toBe('snapshot');
      expect(JSON.stringify(first?.data)).toContain('hello manager');
    }
    // A missing HTTP response before reset must neither create nor send to a second Chief.
    const chief = owners[0];
    const first = chief.link()!;
    expect((await call('POST', '/chief/conversation/reset')).status).toBe(200);
    const repeated = await call('POST', chief.base + '/send', first_bodies.get('chief'));
    expect(repeated).toEqual({ status: 200, body: { conversation_id: first, fate: 'started' } });
    expect(chief.link()).toBeNull();
    expect(prompts('repeat-chief')).toEqual([{ conversation_id: first, kind: 'prompt' }]);
    const chief_lookup = '/conversation/owner-sends/repeat-chief?owner_kind=chief&owner_id=chief';
    expect((await call('GET', chief_lookup)).body).toEqual({
      outcome: {
        message_id: 'repeat-chief',
        conversation_id: first,
        outcome: 'delivered',
        refusal_reason: null,
        reason: null,
      },
    });
    expect(
      (
        await call(
          'POST',
          chief.base + '/send',
          JSON.stringify({ ...JSON.parse(first_bodies.get('chief')!), mode: 'queue' }),
        )
      ).status,
    ).toBe(422);
    expect((await call('POST', owners[2].base + '/send', first_bodies.get('chief'))).status).toBe(
      422,
    );

    // No browser copy is needed to find a queued message after a Home restart.
    const manager_owner = owners[2];
    const target = manager_owner.link()!;
    const queued_id = 'queued-before-restart';
    const queued_body = JSON.stringify({
      conversation_id: target,
      content: [{ piece: 'text', text: 'keep these words' }],
      sender_label: 'You',
      mode: 'queue',
      sender_message_id: queued_id,
    });
    const lookup = `/conversation/owner-sends/${queued_id}?owner_kind=manager&owner_id=${manager.id}`;
    const queued = await call('POST', manager_owner.base + '/send', queued_body);
    expect(queued).toEqual({
      status: 200,
      body: { conversation_id: target, fate: 'accepted', message_id: queued_id },
    });
    expect((await call('GET', lookup)).body).toEqual({
      outcome: {
        message_id: queued_id,
        conversation_id: target,
        outcome: 'pending',
        refusal_reason: null,
        reason: null,
      },
    });
    // A held message has no final answer yet, so none is stored.
    expect(
      database.read((sql) =>
        sql.get('SELECT response FROM owner_send_receipts WHERE message_id=?', [queued_id]),
      ),
    ).toEqual({ response: null });
    await server.close({ deadline: performance.now() / 1000 + 30 });
    server = undefined;
    server = await start();
    expect(await settled('manager', manager.id, queued_id)).toMatchObject({
      conversation_id: target,
      outcome: 'delivered',
    });
    expect(await call('POST', manager_owner.base + '/send', queued_body)).toEqual({
      status: 200,
      body: { conversation_id: target, fate: 'started' },
    });
    expect(prompts(queued_id)).toEqual([{ conversation_id: target, kind: 'prompt' }]);
    const recovered_payload = database.read((sql) =>
      sql.get<{ text: string; detail: string }>(
        `SELECT json_extract(payload, '$.text') AS text,
              json_extract(payload, '$.refusal_detail') AS detail
       FROM conversation_events WHERE json_extract(payload, '$.sender_message_id')=?`,
        [queued_id],
      ),
    );
    expect(recovered_payload).toEqual({
      text: 'keep these words',
      detail: null,
    });
    // A plain-HTTP sender that names its message is answered at acceptance with that name.
    const named = { target: { kind: 'manager', id: manager.id }, message: 'named', mode: 'queue' };
    expect(
      await call('POST', '/messages/send', JSON.stringify({ ...named, message_id: 'named-send' })),
    ).toEqual({
      status: 200,
      body: {
        target: named.target,
        conversation_id: target,
        fate: 'accepted',
        message_id: 'named-send',
      },
    });
    expect(
      database.read((sql) =>
        sql.get('SELECT 1 AS held FROM recipient_deliveries WHERE submission_id=?', [
          `${target}:named-send`,
        ]),
      ),
    ).toEqual({ held: 1 });
    // The first request failed before selecting content. A lost response and later
    // reset cannot turn it into a first send with a configuration that now validates.
    const preflight_id = 'refused-before-destination';
    const preflight_body = JSON.stringify({
      ...JSON.parse(first_bodies.get('manager')!),
      sender_message_id: preflight_id,
      backend_key: 'claude',
    });
    const preflight_url = `/conversation/owner-sends/${preflight_id}?owner_kind=manager&owner_id=${manager.id}`;
    const preflight = await call('POST', manager_owner.base + '/send', preflight_body);
    expect(preflight.status).toBe(400);
    expect(JSON.stringify(preflight.body)).toContain(
      'an existing Manager conversation cannot change backend',
    );
    expect((await call('POST', manager_owner.base + '/reset')).status).toBe(200);
    const failed = await call('GET', preflight_url);
    expect(failed.body.outcome).toEqual({
      message_id: preflight_id,
      conversation_id: null,
      outcome: 'refused',
      // The stored outcome names the cause, not a backend write.
      refusal_reason: 'refused_before_acceptance',
      reason: 'an existing Manager conversation cannot change backend',
    });
    expect(await call('POST', manager_owner.base + '/send', preflight_body)).toEqual({
      status: 200,
      body: {
        conversation_id: null,
        fate: 'refused',
        refusal_reason: 'refused_before_acceptance',
        reason: 'an existing Manager conversation cannot change backend',
      },
    });
    expect(manager_owner.link()).toBeNull();
    expect(prompts(preflight_id)).toEqual([]);
    expect(failures.mock.calls.flat().join('\n')).not.toMatch(
      /unhandled|uncertain failure|HTTP 500/,
    );
  } finally {
    await server?.close({ deadline: performance.now() / 1000 + 30 });
    await registry.close();
  }
}, 20_000);
