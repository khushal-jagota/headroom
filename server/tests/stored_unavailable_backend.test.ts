/** Rows saved while Hermes was a built-in driver, read and refused by a build without it. */
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
import { readinessTestRoutes } from '../runtime/testing.ts';
import { managerWakeTestArea } from '../manager_wakes/testing.ts';
import { create_wake } from '../manager_wakes/data.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../tickets/execution_admission.ts';
import { audit_ticket_registry_integrity } from '../tickets/data/index.ts';
import { create_manager } from '../managers/data.ts';
import { create, fixture, fixture_path } from './ticket_fixture.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const MODEL = 'anthropic:sonnet';
const REFUSAL = {
  error: {
    code: 'validation',
    message: 'unknown agent backend',
    detail: { backend_key: 'hermes', backend_keys: ['codex', 'claude'] },
  },
};

/** One database in which every row that can name a backend names hermes. */
function stored_hermes() {
  const database = fixture();
  const path = fixture_path(database);
  const ticket = create(database, { title: 'Saved Hermes Ticket' });
  const manager = database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'stored-hermes-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'stored-hermes')");
    tx.run("INSERT INTO projects(id,name,created_at,updated_at) VALUES('project','Project',0,0)");
    tx.run(
      `INSERT INTO conversations
        (conversation_id,backend_key,model,workspace_folder,access,created_at,latest_sequence)
        VALUES('stored-hermes','hermes',?,'/synthetic','full',0,1)`,
      [MODEL],
    );
    tx.run(
      `INSERT INTO conversation_events
        VALUES('stored-hermes',1,'command_reply','{"reply_text":"Saved Hermes reply"}',0)`,
    );
    tx.run(
      "UPDATE tickets SET employee_backend='hermes',employee_launch_model=?,conversation_id='stored-hermes' WHERE id=?",
      [MODEL, ticket.id],
    );
    tx.run(
      "UPDATE worker_types SET definition_json=json_set(definition_json,'$.profile.default_backend','hermes') WHERE worker_type='coding'",
    );
    tx.run(
      "INSERT OR REPLACE INTO chief_settings VALUES('chief_of_staff','Chief','hermes',?,NULL,'')",
      [MODEL],
    );
    tx.run("INSERT OR REPLACE INTO manager_launch_defaults VALUES(1,'hermes',?,NULL)", [MODEL]);
    tx.run(
      `INSERT INTO goals (id,project_id,title,why,kind,active,completed,page,revision,created_at,
        updated_at,employee_backend,employee_launch_model)
        VALUES ('goal_hermes','project','Goal','Why','binary',1,0,'{}',1,0,0,'hermes',?)`,
      [MODEL],
    );
    const manager = create_manager(tx, {
      title: 'Saved Hermes Manager',
      project_id: 'project',
      clock: { nowUnix: () => 0, now: () => new Date(0) } as never,
    });
    create_wake(tx, {
      manager_id: manager.id,
      ticket_id: ticket.id,
      source_kind: 'worker_error',
      source_revision: 1,
      summary: 'A notice for a Manager saved on Hermes',
      now: 0,
    });
    return manager;
  });
  return { database, path, ticket, manager };
}

// Risk: a build without Hermes refuses to start, quarantines or 500s on what Hermes left behind,
// or launches it on another backend. Gap: every other test stores only built-in keys, and the
// one that stored an unknown backend expected the Ticket to be quarantined as broken.
// One real server over one fixture, because the reads, the sends and the readiness pass each
// take a different path to the same key. Measured at about 1.1 s.
test('a build without Hermes reads every saved Hermes row and refuses only its launches', async () => {
  const f = stored_hermes();
  const root = dirname(f.path);
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: f.path,
    PLAN_WORKSPACE_ROOT: root,
    PLAN_PORT: '0',
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_CREDENTIAL_KEY_PATH: f.path + '.credential-key',
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
        registry.bind_runtime(context.worker_supervision);
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
      testAreas: (context) => [
        readinessTestRoutes(context.shutdown_options),
        managerWakeTestArea(
          { ...context, assert_database_owned: context.require_runtime_ownership },
          context.shutdown_options,
        ),
      ],
    });
    type Body = Record<string, any>;
    const call = async (method: 'GET' | 'POST', path: string, body?: unknown) => {
      const response = await fetch(server!.address + '/api' + path, {
        method,
        ...(body === undefined
          ? {}
          : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
      });
      return { status: response.status, body: (await response.json()) as Body };
    };
    const send = (text: string) => ({
      content: [{ piece: 'text', text }],
      sender_label: 'Owner',
      sender_message_id: text,
    });

    // The startup audit leaves the Ticket usable, and the update audit does not refuse.
    expect(
      f.database.read((sql) =>
        sql.get('SELECT integrity_error_reason FROM tickets WHERE id=?', [f.ticket.id]),
      ),
    ).toEqual({ integrity_error_reason: null });
    expect(f.database.write((tx) => audit_ticket_registry_integrity(tx))).toEqual([]);

    // Cards and the driver list name only this build's drivers.
    const drivers = await call('GET', '/drivers');
    expect(drivers.body.drivers.map((item: Body) => item.driver_kind)).toEqual(['codex', 'claude']);
    const cards = await call('GET', '/conversation/backends');
    expect(cards.body.backends.map((card: Body) => card.backend_key)).toEqual(['codex', 'claude']);

    // The saved conversation opens, with its view and event tail, and cannot steer.
    expect(await call('GET', '/conversation/conversations/stored-hermes')).toMatchObject({
      status: 200,
      body: { backend_key: 'hermes', supports_steer: false, is_running: false },
    });
    const view = await call('GET', '/conversation/conversations/stored-hermes/view?lens=focus');
    expect(view.status).toBe(200);
    expect(view.body.page.items.map((item: Body) => item.key)).toEqual(['e1']);
    const events = await call('GET', '/conversation/conversations/stored-hermes/events');
    expect(events.body.events.map((event: Body) => event.kind)).toEqual(['command_reply']);
    // A prompt is refused by name, twice: the first refusal leaves nothing reserved.
    for (const text of ['first', 'second'])
      expect(
        await call('POST', '/conversation/conversations/stored-hermes/send', send(text)),
      ).toEqual({ status: 400, body: REFUSAL });

    // Saved launch settings read back as saved.
    const ticket = await call('GET', `/tickets?detail=full&id=${f.ticket.id}`);
    expect(ticket.body).toMatchObject({ employee_backend: 'hermes', ticket_status: 'empty' });
    expect(ticket.body).not.toHaveProperty('integrity_error');
    const chief = await call('GET', '/workers/chief-of-staff/settings');
    expect(chief.body.launch_defaults.employee_backend).toBe('hermes');
    const managers = await call('GET', '/managers?detail=full');
    expect(managers.body.managers[0].manager.launch_configuration.employee_backend).toBe('hermes');
    expect((await call('GET', '/managers/launch-defaults')).body.employee_backend).toBe('hermes');
    expect((await call('GET', '/goals/goal_hermes')).body.employee_backend).toBe('hermes');
    expect(
      (await call('GET', '/workers/coding')).body.settings.launch_defaults.employee_backend,
    ).toBe('hermes');

    // Every launch from them is refused by name.
    expect(await call('POST', '/chief/conversation/send', send('chief'))).toEqual({
      status: 400,
      body: REFUSAL,
    });
    expect(
      await call('POST', `/managers/${f.manager.id}/conversation/send`, send('manager')),
    ).toEqual({ status: 400, body: REFUSAL });
    expect(
      await call('POST', '/tickets', { title: 'New', worker_type: 'coding', brief: 'Brief.' }),
    ).toEqual({ status: 400, body: REFUSAL });

    // The readiness pass leaves the Ticket alone, and a restart says why it did not start.
    expect((await call('POST', '/test/poll/readiness')).status).toBe(200);
    expect(await call('POST', `/tickets/${f.ticket.id}/restart-worker`, {})).toMatchObject({
      status: 200,
      body: {
        started: false,
        delivery_fate: null,
        not_started_because: 'the backend hermes is not available in this build',
      },
    });

    // A notice for a Manager saved on Hermes waits, quietly, for a new choice.
    expect((await call('POST', '/test/poll/manager-wakes')).status).toBe(200);
    expect(f.database.read((sql) => sql.all('SELECT status FROM manager_wake_batches'))).toEqual([
      { status: 'pending' },
    ]);

    // Nothing launched, and nothing failed unhandled.
    expect(await registry.inspect('stored-hermes')).toEqual([]);
    expect(f.database.read((sql) => sql.all('SELECT conversation_id FROM conversations'))).toEqual([
      { conversation_id: 'stored-hermes' },
    ]);
    expect(failures.mock.calls.flat().join('\n')).not.toMatch(
      /unhandled|uncertain failure|HTTP 500/,
    );
  } finally {
    await server?.close({ deadline: performance.now() / 1000 + 30 });
    await registry.close();
  }
});
