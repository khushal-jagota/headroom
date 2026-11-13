/** Actual startup preserves retained turns and keeps away/unconfigured Mac work remote. */
import { dirname, join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path, create } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import type { ConversationCompositionContext, StartedServer } from '../core/start.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../conversation/contracts.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { BackendChildFactory } from '../conversation/backends/contracts.ts';
import { VpsExecutor } from '../executor/vps_transport.ts';
import { MacRemoteChild } from '../executor/vps_remote_child.ts';
import { ExecutorStore } from '../executor/data/runs.ts';
import { compose_production_backends } from '../lifecycle/production_bindings.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { write_ticket_conversation_start } from '../tickets/data/index.ts';
import {
  create_production_conversation_core,
  start_production_server,
} from '../lifecycle/production.ts';
import {
  create_test_environment_operations,
  create_test_environment_runtime,
} from '../environments/testing/composition.ts';
import { MailboxStallSource } from '../executor/data/mailbox.ts';
import type { StallSource } from '../stalls/contracts.ts';
import { MacUpdateService } from '../environments/updates/mac/service.ts';
import type { MacUpdateServiceContract } from '../environments/updates/mac/contracts.ts';

const incarnation = '11111111-1111-4111-8111-111111111111';
function retained_fixture() {
  const database = fixture(),
    path = fixture_path(database),
    root = dirname(path);
  database.write((tx) => {
    // Reuse the staged lifecycle host's prepared navigation frontier.
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'old-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'old-runtime')");
  });
  const ticket = create(database, { locale: 'macos' });
  const initial = new ConversationStore(database, { integer_now: () => 100 });
  const materials = (id: string) =>
    new ConversationRoleMaterials({
      role_text: 'Synthetic Worker',
      identity_environment_variables: [
        ['PLAN_ACTOR', 'worker'],
        ['PLAN_TICKET_ID', id],
      ],
    });
  const start = new ResolvedConversationStart({
    conversation_id: 'kept',
    backend_key: 'codex',
    model: 'model',
    reasoning_effort: null,
    role_materials: materials(ticket.id),
    workspace_folder: '/tmp',
    access: 'full',
  });
  initial.create_conversation(start);
  initial.transition_lifecycle('kept', [
    {
      kind: 'turn',
      identity: '1',
      turn_number: 1,
      operation: 'turn',
      status: 'running',
      provider_identity: null,
    },
  ]);
  database.write((tx) => {
    write_ticket_conversation_start(tx, ticket.id, {
      conversation_id: 'kept',
      backend: 'codex',
      model: 'model',
      reasoning_effort: null,
      now: 100,
    });
    tx.run(
      'INSERT INTO mac_executor_presence(singleton,executor_incarnation,identity_json,last_lease_at_ms) VALUES(1,?,?,?)',
      [
        incarnation,
        JSON.stringify({
          protocol_version: 22,
          application_revision: 'synthetic',
          spool_format_version: 5,
          executor_incarnation: incarnation,
        }),
        Date.now() - 120000,
      ],
    );
  });
  new ExecutorStore(database).register_child(
    { executor_incarnation: incarnation, child_id: 'retained', conversation_id: 'kept' },
    'ticket',
    ticket.id,
    Date.now(),
  );
  database.close();
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: path,
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '1',
    PLAN_EXECUTOR_ID: 'synthetic-executor',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
  }))
    vi.stubEnv(key, value);
  return { path, start, materials, root };
}
test('configured restart adopts before reconciliation and Mac locality never selects a local child', async () => {
  const { path, start, materials } = retained_fixture();
  const local = vi.fn<BackendChildFactory>(() => {
    throw new Error('local constructor selected');
  });
  let core!: SqliteProcessConversationSystem, endpoint!: VpsExecutor;
  let context!: ConversationCompositionContext,
    bound!: ReturnType<typeof compose_production_backends>;
  let mac_updates!: MacUpdateServiceContract;
  const local_source: StallSource = {
    tracked_waits: () => [
      {
        kind: 'vps_save_retrying',
        wait_key: 'local',
        conversation_id: 'kept',
        detail: 'Synthetic local save',
        started_at_ms: 100,
      },
    ],
  };
  let server: StartedServer | undefined;
  try {
    server = await startServer({
      createExecutor(current) {
        endpoint = new VpsExecutor(current.database, current.clock, {
          db_path: path,
          changes: current.changes,
          media_signal: current.media_signal,
        });
        return endpoint;
      },
      readOpenRuns: () => endpoint.open_runs(),
      createConversationCore(current) {
        context = current;
        const http = create_backend_runtime({
          database: current.database,
          config: current.config,
          now: () => current.clock.now(),
          providers: unavailable_backend_providers(),
        });
        bound = compose_production_backends(current, {
          claude: () => local,
          codex: () => local,
          hermes: () => local,
          http: () => http,
          stall_sources: () => [local_source],
        });
        core = create_production_conversation_core(current, bound);
        return core;
      },
      createBackendServices: () => bound.http,
      async createEnvironments(current) {
        const runtime = await create_test_environment_runtime(current);
        mac_updates = runtime.mac_updates;
        return runtime;
      },
    });
    expect(core.runtime_turn('kept')?.token.turn_number).toBe(1);
    expect(core.conversation_state('kept')?.child).toBeInstanceOf(MacRemoteChild);
    expect(endpoint.presence.is_present()).toBe(false);
    expect(bound.http.machine_catalog()).toBe(endpoint.catalog);
    const sources = bound.stall_sources();
    expect(sources).toContain(local_source);
    expect(sources.some((source) => source instanceof MailboxStallSource)).toBe(true);
    expect(mac_updates).toBeInstanceOf(MacUpdateService);
    expect(endpoint.updates).toBe(mac_updates);
    const check = vi.spyOn(mac_updates, 'check');
    expect(endpoint.presence.update_check?.()).toEqual({
      schema_version: 1,
      target: null,
      prefetch: null,
    });
    expect(check).toHaveBeenCalledTimes(1);
    const away_ticket = create(context.database, { locale: 'macos' });
    const away = new ResolvedConversationStart({
      ...start,
      conversation_id: 'away',
      role_materials: materials(away_ticket.id),
    });
    context.record.store.create_conversation(away);
    context.database.write((tx) =>
      write_ticket_conversation_start(tx, away_ticket.id, {
        conversation_id: 'away',
        backend: 'codex',
        model: 'model',
        reasoning_effort: null,
        now: 100,
      }),
    );
    expect(
      bound.factories.codex({
        resolved_start: away,
        event_sink: { async report() {} },
        message_files: endpoint.message_files,
      }),
    ).toBeInstanceOf(MacRemoteChild);
    const unconfigured = compose_production_backends(
      { ...context, config: { ...context.config, executor_id: null }, executor: null },
      {
        claude: () => local,
        codex: () => local,
        hermes: () => local,
        http: () => bound.http,
        stall_sources: () => [],
      },
    );
    expect(() =>
      unconfigured.factories.codex({
        resolved_start: start,
        event_sink: { async report() {} },
        message_files: endpoint.message_files,
      }),
    ).toThrow('Execution admission invariant violated');
    expect(local).not.toHaveBeenCalled();
  } finally {
    await server?.close({ deadline: performance.now() / 1000 + 5 });
    await server?.shutdown_settled;
    vi.unstubAllEnvs();
  }
});

// The earlier composed-host case supplied its own executor constructor and could
// not catch missing production revision or inventory handoff.
test('production constructs its real executor and reconciles that executor inventory', async () => {
  const { root } = retained_fixture();
  const revision = 'a'.repeat(40);
  vi.stubEnv('PLAN_APP_SHA', revision);
  const inventory = vi.spyOn(VpsExecutor.prototype, 'open_runs');
  const local = vi.fn<BackendChildFactory>(() => {
    throw new Error('local constructor selected');
  });
  let endpoint!: VpsExecutor, core!: SqliteProcessConversationSystem;
  let server: StartedServer | undefined;
  try {
    server = await start_production_server({
      backends: {
        claude: () => local,
        codex: () => local,
        hermes: () => local,
        http: (current) =>
          create_backend_runtime({
            database: current.database,
            config: current.config,
            now: () => current.clock.now(),
            providers: unavailable_backend_providers(),
          }),
        stall_sources: () => [],
      },
      environments: {
        dependencies: {
          application_root: root,
          node_executable: process.execPath,
          admitted_legacy_null_sha: null,
        },
        async factory(current, dependencies) {
          if (!(current.executor instanceof VpsExecutor) || !current.core)
            throw new Error('Production must supply its real invocation executor and core');
          endpoint = current.executor;
          core = current.core;
          expect(dependencies.executor).toBe(endpoint);
          return create_test_environment_operations(current);
        },
      },
    });
    expect(endpoint.application_revision).toBe(revision);
    expect(inventory).toHaveBeenCalledTimes(1);
    expect(inventory.mock.contexts[0]).toBe(endpoint);
    expect(inventory.mock.results[0]!.value.get('kept')).toEqual({
      turn_number: 1,
      executor_process_current: true,
    });
    expect(core.runtime_turn('kept')?.token.turn_number).toBe(1);
    expect(core.conversation_state('kept')?.child).toBeInstanceOf(MacRemoteChild);
    expect(local).not.toHaveBeenCalled();
  } finally {
    await server?.close({ deadline: performance.now() / 1000 + 5 });
    await server?.shutdown_settled;
    inventory.mockRestore();
    vi.unstubAllEnvs();
  }
});
