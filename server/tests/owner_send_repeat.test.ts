/** A phone resend of a first message, whose response was lost, to each conversation owner. */
import { dirname, join, resolve } from 'node:path';
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
    server = await startServer({
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
          publish_status_changed: (id) => context.record.live_tail.views.publish_status_changed(id),
        });
      },
    });
    const call = async (method: 'GET' | 'POST', route: string, body?: string) => {
      const response = await fetch(server!.address + '/api' + route, {
        method,
        ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body }),
      });
      return { status: response.status, body: (await response.json()) as Record<string, any> };
    };
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
      const send = () => call('POST', owner.base + '/send', body);
      const concurrent = await Promise.all([send(), send()]);
      const later = await send();
      const replies = [...concurrent, later];
      for (const reply of replies) expect(reply, owner.kind).toMatchObject({ status: 200 });
      const id = replies[0].body.conversation_id;
      expect(typeof id, owner.kind).toBe('string');
      expect(
        replies.map((reply) => reply.body),
        owner.kind,
      ).toEqual(replies.map(() => ({ conversation_id: id, fate: 'started' })));
      expect(conversations().length - before, owner.kind).toBe(1);
      expect(owner.link(), owner.kind).toBe(id);
      expect(prompts(sender_message_id), owner.kind).toEqual([
        { conversation_id: id, kind: 'prompt' },
      ]);
      const children = await registry.inspect(id);
      expect(children, owner.kind).toHaveLength(1);
    }
    // A reset between the attempts leaves the owner with no current conversation. A null
    // conversation_id then means "start one", so the same resend is delivered again, there.
    const chief = owners[0];
    const first = chief.link()!;
    expect((await call('POST', '/chief/conversation/reset')).status).toBe(200);
    const after_reset = await call(
      'POST',
      '/chief/conversation/send',
      JSON.stringify({
        content: [{ piece: 'text', text: 'hello chief' }],
        sender_label: 'You',
        mode: 'steer',
        sender_message_id: 'repeat-chief',
        sent_at_unix_milliseconds: 1_783_166_400_000,
      }),
    );
    expect(after_reset).toMatchObject({ status: 200, body: { fate: 'started' } });
    expect(after_reset.body.conversation_id).not.toBe(first);
    expect(prompts('repeat-chief').map((row) => row.conversation_id)).toEqual([
      first,
      after_reset.body.conversation_id,
    ]);
    expect(failures.mock.calls.flat().join('\n')).not.toMatch(
      /unhandled|uncertain failure|HTTP 500/,
    );
  } finally {
    await server?.close({ deadline: performance.now() / 1000 + 30 });
    await registry.close();
  }
});
