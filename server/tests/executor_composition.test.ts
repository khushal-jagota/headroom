import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import {
  drivers_of_factories,
  registry_of_factories,
  unread_driver_host,
} from '../drivers/testing/registry.ts';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
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
import { LocaleBackendChildFactory } from '../executor/routing.ts';
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import { VpsExecutorRegistry } from '../executor/registry.ts';
import { MacRemoteChild } from '../executor/vps_remote_child.ts';
import { ExecutorStore } from '../executor/data/runs.ts';
import { compose_production_backends } from '../lifecycle/production_bindings.ts';
import { create_backend_runtime } from '../conversation/snapshot/composition.ts';
import { write_ticket_conversation_start } from '../tickets/data/index.ts';
import {
  create_production_conversation_core,
  start_production_server,
} from '../lifecycle/production.ts';
import {
  create_test_environment_operations,
  create_test_environment_runtime,
} from '../environments/testing/composition.ts';
import type { StallSource } from '../stalls/contracts.ts';
import { MacUpdateService } from '../environments/updates/mac/service.ts';
import type { MacUpdateServiceContract } from '../environments/updates/mac/contracts.ts';
import { Deferred } from '../core/deferred.ts';
import { write_bindings } from '../conversation/start_admission/index.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { decode_value } from '../executor/protocol.ts';
import { accept_lease_in_transaction } from '../executor/vps_presence_store.ts';
import { createReadAccess } from '../core/database/index.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';

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
    backend_key: parse_backend_instance_id('codex'),
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
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,machine_id,first_leased_at_ms) VALUES(?,?,?)',
      [incarnation, ORIGINAL_MAC_MACHINE_ID, Date.now() - 120000],
    );
    write_ticket_conversation_start(tx, ticket.id, {
      conversation_id: 'kept',
      backend: 'codex',
      model: 'model',
      reasoning_effort: null,
      now: 100,
    });
    tx.run(
      'INSERT INTO mac_executor_presence(machine_id,executor_incarnation,identity_json,last_lease_at_ms) VALUES(?,?,?,?)',
      [
        ORIGINAL_MAC_MACHINE_ID,
        incarnation,
        JSON.stringify({
          protocol_version: PROTOCOL_VERSION,
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
  let registry!: VpsExecutorRegistry;
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
        registry = new VpsExecutorRegistry(current.database, current.clock, {
          db_path: path,
          changes: current.changes,
          media_signal: current.media_signal,
          original_enabled: true,
        });
        return registry;
      },
      readOpenRuns() {
        endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
        return registry.open_runs();
      },
      createConversationCore(current) {
        context = current;
        const http = create_backend_runtime({
          database: current.database,
          config: current.config,
          now: () => current.clock.now(),
        });
        bound = compose_production_backends(
          current,
          {
            registry: registry_of_factories({ claude: local, codex: local }),
            host: unread_driver_host,
          },
          {
            drivers: [],
            driver_host: () => unread_driver_host,
            http: () => http,
            stall_sources: () => [local_source],
          },
        );
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
    expect(bound.http.machine_catalog(ORIGINAL_MAC_MACHINE_ID)).toBe(endpoint.catalog);
    const sources = bound.stall_sources();
    expect(sources).toContain(local_source);
    expect(sources).toHaveLength(4);
    expect(endpoint.mailbox.start_admission).toBe(core.start_admission);
    expect(mac_updates).toBeInstanceOf(MacUpdateService);
    expect(endpoint.updates).toBe(mac_updates);
    const check = vi.spyOn(mac_updates, 'check');
    expect(endpoint.presence.update_check?.()).toEqual({
      schema_version: 1,
      target: null,
      prefetch: null,
    });
    expect(check).toHaveBeenCalledTimes(1);
    const named_machine = '66666666-6666-4666-8666-666666666666';
    context.database.write((tx) => {
      tx.run("INSERT INTO machines VALUES (?,'Named','server','cyan',0)", [named_machine]);
      tx.run('INSERT INTO machine_executor_credentials(machine_id) VALUES (?)', [named_machine]);
    });
    const [named, same_named] = await Promise.all([
      registry.ensure(named_machine),
      registry.ensure(named_machine),
    ]);
    expect(named).toBe(same_named);
    expect(named.mailbox.start_admission).toBe(core.start_admission);
    expect(named.updates).toBe(mac_updates.for_machine(named_machine));
    expect(named.updates).not.toBe(endpoint.updates);
    expect(bound.http.machine_catalog(named_machine)).toBe(named.catalog);
    // The Mac's last lease, from before this home started, lists the drivers it runs.
    context.database.write((tx) =>
      accept_lease_in_transaction(
        tx,
        {
          identity: {
            protocol_version: PROTOCOL_VERSION,
            application_revision: 'synthetic',
            spool_format_version: 5,
            executor_incarnation: incarnation,
          },
          process_id: 4242,
          mac_wall_clock_ms: 0,
          queue_depth: 0,
          oldest_item_age_ms: null,
          waiting_conversations: [],
          waiting_conversations_truncated: false,
          working_turns: [],
          admission_stopped: false,
          cleanup_uncertainties: [],
          driver_kinds: ['hermes', 'codex', 'claude'],
        },
        0,
        ORIGINAL_MAC_MACHINE_ID,
      ),
    );
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
      bound.registry.require_live('codex').child_factory({
        resolved_start: away,
        event_sink: { async report() {} },
        message_files: endpoint.message_files,
      }),
    ).toBeInstanceOf(MacRemoteChild);
    const unconfigured = compose_production_backends(
      { ...context, config: { ...context.config, executor_id: null }, executor: null },
      {
        registry: registry_of_factories({ claude: local, codex: local }),
        host: unread_driver_host,
      },
      {
        drivers: [],
        driver_host: () => unread_driver_host,
        http: () => bound.http,
        stall_sources: () => [],
      },
    );
    expect(() =>
      unconfigured.registry.require_live('codex').child_factory({
        resolved_start: start,
        event_sink: { async report() {} },
        message_files: endpoint.message_files,
      }),
    ).toThrow('Execution admission invariant violated');
    expect(local).not.toHaveBeenCalled();
    await prompt_acknowledgement_shutdown(server, core, endpoint, context, path);
  } finally {
    await server?.close({ deadline: performance.now() / 1000 + 5 });
    await server?.shutdown_settled;
    vi.unstubAllEnvs();
  }
});

async function prompt_acknowledgement_shutdown(
  server: StartedServer,
  core: SqliteProcessConversationSystem,
  endpoint: VpsExecutor,
  context: ConversationCompositionContext,
  path: string,
): Promise<void> {
  const state = core.conversations.get('kept')!,
    reference = state.retained_child_reference!;
  const turn = state.running_turn!.token;
  context.database.write((tx) =>
    write_bindings(tx, turn, [
      {
        identity: 'shutdown-ack',
        work_class: 'worker',
        destination: ORIGINAL_MAC_MACHINE_ID,
      },
    ]),
  );
  context.database.read((sql) => core.start_admission.restore_bindings(sql, turn));
  const command = decode_value(
    {
      command_id: 'shutdown-ack-prompt',
      child: reference,
      operation: 'write_prompt',
      turn,
      arguments: {
        kind: 'write_prompt',
        content: text_message_content('proof'),
        sender_content: text_message_content('proof'),
        sender_label: 'fixture',
        mode: 'queue',
      },
    },
    'ChildCommand',
  );
  await endpoint.mailbox.put_child_command(command);
  const identity = endpoint.presence.current_identity()!;
  await endpoint.mailbox.poll(identity, 0, 0);
  const save_release = new Deferred<void>(),
    confirmation_release = new Deferred<void>();
  const original_save = endpoint.mailbox.record_results.bind(endpoint.mailbox);
  const held_save = vi
    .spyOn(endpoint.mailbox, 'record_results')
    .mockImplementation(async (...args) => {
      await save_release.promise;
      return original_save(...args);
    });
  const confirm = vi.spyOn(core, 'confirm_remote_prompt_accepted');
  const runs_stop = vi.spyOn(endpoint.runs, 'stop');
  const read_database = () => context.database.read((sql) => sql.get('SELECT 1 AS open'));
  let save: Promise<unknown> | undefined, closing: Promise<void> | undefined;
  let raw_closed = false,
    endpoint_closed = false,
    save_settled = false;
  try {
    save = endpoint.record_outcomes(identity, {
      outcomes: [
        {
          command_id: command.command_id,
          child_outcome: {
            command_id: command.command_id,
            child: reference,
            disposition: 'succeeded',
            result: {
              kind: 'prompt_accepted',
              composed_content_delivered: true,
              agent_acceptance_proven: true,
            },
          },
          model_list: null,
        },
      ],
    });
    void save.then(
      () => {
        save_settled = true;
      },
      () => {
        save_settled = true;
      },
    );
    await vi.waitFor(() => expect(held_save).toHaveBeenCalledOnce());
    closing = server.close({ deadline: performance.now() / 1000 + 1 });
    void closing.catch(() => {});
    expect(core.shutting_down).toBe(true);
    // Shutdown clears earlier barriers. This hold belongs to the acknowledgement admitted later.
    state.steer_settlements.add(confirmation_release);
    void server.shutdown_settled.then(
      () => {
        raw_closed = true;
      },
      () => {},
    );
    void endpoint.settled().then(
      () => {
        endpoint_closed = true;
      },
      () => {},
    );
    expect(runs_stop).not.toHaveBeenCalled();
    expect(read_database()).toEqual({ open: 1 });
    expect(endpoint.update_barrier).toBeDefined();
    expect(endpoint.update_barrier!.drained).toBe(false);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    save_release.resolve(undefined);
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(confirm).toHaveBeenCalledWith(reference, turn);
    expect(state.settlement_tasks.size).toBeGreaterThan(0);
    expect(save_settled).toBe(false);
    expect(endpoint.update_barrier!.drained).toBe(false);
    expect(runs_stop).not.toHaveBeenCalled();
    expect(raw_closed || endpoint_closed).toBe(false);
    expect(read_database()).toEqual({ open: 1 });
    const fresh = createReadAccess(path);
    try {
      expect(
        fresh.read((sql) =>
          sql.get<{ state: string }>('SELECT state FROM executor_commands WHERE command_id=?', [
            command.command_id,
          ]),
        ),
      ).toEqual({ state: 'finished' });
      expect(fresh.read((sql) => sql.all('SELECT identity FROM automatic_start_bindings'))).toEqual(
        [{ identity: 'shutdown-ack' }],
      );
    } finally {
      fresh.close();
    }
    state.steer_settlements.delete(confirmation_release);
    confirmation_release.resolve(undefined);
    await save;
    expect(save_settled).toBe(true);
    expect(endpoint.update_barrier!.drained).toBe(true);
    await server.shutdown_settled;
    await closing;
    expect(raw_closed && endpoint_closed).toBe(true);
    expect(read_database).toThrow('database is closed');
    const successor = acquireRuntimeOwnership(path),
      after = createReadAccess(path);
    try {
      expect(after.read((sql) => sql.all('SELECT * FROM automatic_start_bindings'))).toEqual([]);
    } finally {
      after.close();
      successor.close();
    }
  } finally {
    save_release.resolve(undefined);
    state.steer_settlements.delete(confirmation_release);
    confirmation_release.resolve(undefined);
    await save?.catch(() => {});
    await closing?.catch(() => {});
    held_save.mockRestore();
    confirm.mockRestore();
    runs_stop.mockRestore();
  }
}

// The earlier composed-host case supplied its own executor constructor and could
// not catch missing production revision or inventory handoff.
test('production constructs its real executor and reconciles that executor inventory', async () => {
  const { root } = retained_fixture();
  const revision = 'a'.repeat(40);
  vi.stubEnv('PLAN_APP_SHA', revision);
  const inventory = vi.spyOn(VpsExecutorRegistry.prototype, 'open_runs');
  const local = vi.fn<BackendChildFactory>(() => {
    throw new Error('local constructor selected');
  });
  let endpoint!: VpsExecutor, core!: SqliteProcessConversationSystem;
  let registry!: VpsExecutorRegistry;
  let server: StartedServer | undefined;
  try {
    server = await start_production_server({
      backends: {
        drivers: drivers_of_factories({
          claude: () => local,
          codex: () => local,
        }),
        driver_host: () => unread_driver_host,
        http: (current) =>
          create_backend_runtime({
            database: current.database,
            config: current.config,
            now: () => current.clock.now(),
          }),
        stall_sources: () => [],
      },
      environments: {
        dependencies: {
          application_root: root,
          node_executable: process.execPath,
        },
        async factory(current, dependencies) {
          if (!(current.executor instanceof VpsExecutorRegistry) || !current.core)
            throw new Error('Production must supply its real invocation executor and core');
          registry = current.executor;
          endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
          core = current.core;
          expect(dependencies.executor).toBe(registry);
          return create_test_environment_operations(current);
        },
      },
    });
    expect(endpoint.application_revision).toBe(revision);
    expect(inventory).toHaveBeenCalledTimes(1);
    expect(inventory.mock.contexts[0]).toBe(registry);
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

// Gap: a remote Ticket naming a backend kind its machine cannot run must be refused before any mailbox write.
test("a remote Ticket start follows its machine's advertised driver kinds, or the legacy set without them", () => {
  const database = fixture();
  try {
    const ticket = create(database, { locale: 'macos' });
    const materials = new ConversationRoleMaterials({
      role_text: 'Synthetic Worker',
      identity_environment_variables: [
        ['PLAN_ACTOR', 'worker'],
        ['PLAN_TICKET_ID', ticket.id],
      ],
    });
    new ConversationStore(database, { integer_now: () => 100 }).create_conversation(
      new ResolvedConversationStart({
        conversation_id: 'scripted-conversation',
        backend_key: parse_backend_instance_id('codex'),
        model: 'model',
        reasoning_effort: null,
        role_materials: materials,
        workspace_folder: '/tmp',
        access: 'full',
      }),
    );
    database.write((tx) =>
      write_ticket_conversation_start(tx, ticket.id, {
        conversation_id: 'scripted-conversation',
        backend: 'codex',
        model: 'model',
        reasoning_effort: null,
        now: 100,
      }),
    );
    const local = vi.fn(),
      remote = Object.assign(vi.fn(), { recover: () => null, retained_runs: () => [] });
    const factory = new LocaleBackendChildFactory(database, local, remote);
    const start_remote = (backend: string) =>
      factory.create({
        resolved_start: new ResolvedConversationStart({
          conversation_id: 'scripted-conversation',
          backend_key: parse_backend_instance_id(backend),
          model: 'model',
          reasoning_effort: null,
          role_materials: materials,
          workspace_folder: '/tmp',
          access: 'full',
        }),
        event_sink: { async report() {} },
        message_files: {} as never,
      });
    const store_lease = (driver_kinds?: string[]) =>
      database.write((tx) =>
        accept_lease_in_transaction(
          tx,
          {
            identity: {
              protocol_version: PROTOCOL_VERSION,
              application_revision: 'synthetic',
              spool_format_version: 5,
              executor_incarnation: incarnation,
            },
            process_id: 4242,
            mac_wall_clock_ms: 0,
            queue_depth: 0,
            oldest_item_age_ms: null,
            waiting_conversations: [],
            waiting_conversations_truncated: false,
            working_turns: [],
            admission_stopped: false,
            cleanup_uncertainties: [],
            ...(driver_kinds === undefined ? {} : { driver_kinds }),
          },
          0,
          ticket.machine_id,
        ),
      );
    expect(() => start_remote('scripted')).toThrow(BackendSpawnFailed);
    store_lease(['hermes']);
    expect(() => start_remote('codex')).toThrow(BackendSpawnFailed);
    expect(remote).not.toHaveBeenCalled();
    expect(local).not.toHaveBeenCalled();
    store_lease(['hermes', 'codex']);
    // A machine that still advertises a driver this build lacks cannot run it.
    expect(() => start_remote('hermes')).toThrow(
      "The Ticket's machine cannot run the hermes backend",
    );
    start_remote('codex');
    expect(remote).toHaveBeenCalledTimes(1);
    // A lease that lists no driver kinds runs no backend.
    store_lease();
    expect(() => start_remote('claude')).toThrow(BackendSpawnFailed);
    expect(remote).toHaveBeenCalledTimes(1);
    expect(local).not.toHaveBeenCalled();
  } finally {
    database.close();
  }
});
