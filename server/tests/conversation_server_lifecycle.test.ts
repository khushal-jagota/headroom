/** Startup and shutdown own one record runtime and retain its lease through cleanup. */
import { dirname, join, resolve } from 'node:path';
import type { FastifyListenOptions } from 'fastify';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import * as application from '../app.ts';
import * as database_module from '../core/database/index.ts';
import { start_production_server } from '../lifecycle/production.ts';
import type { ProductionServerDependencies } from '../lifecycle/production.ts';
import type { ProductionBindingContext } from '../lifecycle/production_bindings.ts';
import { run_application_process } from '../lifecycle/application.ts';
import * as backend_composition from '../conversation/snapshot/production.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { RecordingBackendProbeEnvironment } from '../conversation/snapshot/recording_environment.ts';
import { write_manifest } from '../conversation/deploy_interruptions/files.ts';
import { RECOVERY_PROMPT } from '../conversation/deploy_interruptions/coordinator.ts';
import { WAITS } from '../stalls/registry.ts';
import { MetricsRuntime } from '../environments/vps_metrics/runtime.ts';
import { create_mac_update_composition } from '../environments/updates/mac/composition.ts';
import type { ApplicationSchemaSeed, ReadSql } from '../core/database/contracts.ts';
import { afterEach, expect, test, vi } from 'vitest';
import { create, fixture } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import type { TestCompositionContext, StartedServer } from '../core/start.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { ManagerWakeLoop } from '../manager_wakes/runtime.ts';
import { ScriptedBackendRegistry } from '../conversation/testing/scripted_registry.ts';
import {
  BackendPermissionAsk,
  PermissionAskRaised,
  BackgroundTaskStarted,
  BackgroundTaskEnded,
} from '../conversation/backends/contracts.ts';
import { PermissionAskOption, UserInputAnswer } from '../conversation/events.ts';
import type { CurrentViewSnapshot } from '../../web/src/lib/navigation/contracts.ts';
import { ConversationViewRebuilder } from '../conversation/views/data/rebuild.ts';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { agent_resolve } from '../runtime/conversation_start.ts';
import { ConversationStartRequest } from '../conversation/contracts.ts';
import { resolve_conversation_start_request } from '../conversation/logic/conversation_start_resolution.ts';
import { ensure_started_conversation_record } from '../conversation/record/data/store.ts';
import { CLEANUP_INTERVAL_SECONDS } from '../conversation/video_uploads/configuration.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
function setup() {
  const database = fixture();
  const path = database.read((sql) =>
    sql.get<{ file: string }>("SELECT file FROM pragma_database_list WHERE name='main'"),
  )!.file;
  const root = dirname(path);
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
    PLAN_HERMES_HOME: join(root, 'hermes'),
    PLAN_APP_ROOT: resolve(import.meta.dirname, '../..'),
    PLAN_FAKE_NOW: '2026-11-11T10:00:00+00:00',
  }))
    vi.stubEnv(key, value);
  vi.stubEnv('PLAN_SERVER_LISTENER_FD', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_ROOT', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_DATABASE', undefined);
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'old-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'old-runtime')");
    tx.run(`INSERT INTO conversations
      (conversation_id,backend_key,model,workspace_folder,access,created_at)
      VALUES('retained','codex','fixture','/synthetic','full',0)`);
  });
  return { database, path };
}

// Distinct gap: protocol tests do not prove the process composition shares the
// record's sole reconciliation/epoch or leaves normal sends explicitly unavailable.
test('normal staged server serves the reconciled record through real publication without a fabricated provider', async () => {
  const f = setup();
  vi.stubEnv('PLAN_TEST_MODE', '0');
  const production = vi
    .spyOn(backend_composition, 'production_backend_providers')
    .mockImplementation(() => {
      throw new Error('Bare startup must not construct a production provider');
    });
  const schema = JSON.parse(
    readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as ApplicationSchemaSeed;
  const writer = vi.spyOn(database_module, 'createDatabase');
  const forbidden = vi.fn((): never => {
    throw new Error('schema refusal entered startup');
  });
  // Refused starts never open a writer, rotate the epoch, construct a core, or start a loop.
  for (const revisions of [
    [],
    ['older'],
    ['newer'],
    ['unknown'],
    [schema.alembicRevision, 'extra'],
  ]) {
    f.database.write((tx) => {
      tx.run('DELETE FROM alembic_version');
      for (const revision of revisions) tx.run('INSERT INTO alembic_version VALUES(?)', [revision]);
    });
    const before = readFileSync(f.path);
    await expect(
      startServer({ createConversationCore: forbidden, notificationDelivery: forbidden }),
    ).rejects.toThrow(
      `actual=${JSON.stringify([...revisions].sort())}; expected=${JSON.stringify([schema.alembicRevision])}`,
    );
    expect(readFileSync(f.path).equals(before)).toBe(true);
    expect(
      f.database.read((sql) => sql.get('SELECT epoch FROM conversation_runtime_epoch')),
    ).toEqual({ epoch: 'old-runtime' });
  }
  f.database.write((tx) => tx.run('DROP TABLE alembic_version'));
  await expect(
    startServer({ createConversationCore: forbidden, notificationDelivery: forbidden }),
  ).rejects.toThrow(
    `actual=<missing alembic_version>; expected=${JSON.stringify([schema.alembicRevision])}`,
  );
  expect(writer).not.toHaveBeenCalled();
  expect(forbidden).not.toHaveBeenCalled();
  writer.mockRestore();
  f.database.write((tx) => {
    tx.run(schema.statements.find((sql) => sql.startsWith('CREATE TABLE alembic_version ('))!);
    tx.run('INSERT INTO alembic_version VALUES(?)', [schema.alembicRevision]);
  });
  const previous_handlers = process.listeners('SIGTERM');
  let publish_address!: (address: string) => void;
  let release_listen!: () => void;
  const listening = new Promise<string>((resolve) => {
    publish_address = resolve;
  });
  const resume = new Promise<void>((resolve) => {
    release_listen = resolve;
  });
  const create_app = application.createApp;
  vi.spyOn(application, 'createApp').mockImplementation(async (...args) => {
    const app = await create_app(...args);
    const listen = app.listen.bind(app);
    app.listen = (async (options: FastifyListenOptions) => {
      const address = await listen(options);
      publish_address(address);
      await resume;
      return address;
    }) as typeof app.listen;
    return app;
  });
  let context: TestCompositionContext | undefined;
  let epochBeforePublication: string | undefined;
  const starting = startServer({
    beforePublication(database) {
      epochBeforePublication = database.read((sql) =>
        sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch'),
      )!.epoch;
    },
    testAreas(value) {
      context = value;
      return [];
    },
  });
  try {
    const address = await Promise.race([
      listening,
      starting.then(() => {
        throw new Error('listen boundary was not observed');
      }),
    ]);
    expect(context?.conversationSystem).toBeUndefined();
    expect(production).not.toHaveBeenCalled();
    expect(epochBeforePublication).not.toBe('old-runtime');
    expect(
      f.database.read((sql) =>
        sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch'),
      )?.epoch,
    ).toBe(epochBeforePublication);
    const response = await fetch(address + '/api/conversation/conversations/retained/view');
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ page: { conversation_id: 'retained' } });
    expect(() => acquireRuntimeOwnership(f.path)).toThrow('ConversationRuntimeAlreadyOwned');
    expect(
      process.listeners('SIGTERM').find((handler) => !previous_handlers.includes(handler)),
    ).toBeDefined();
  } finally {
    try {
      const stop = process
        .listeners('SIGTERM')
        .find((handler) => !previous_handlers.includes(handler));
      stop?.('SIGTERM');
    } finally {
      release_listen();
      // Release and join even if startup rejects before the listen gate is observed.
      const server = await starting;
      await server.close({ deadline: performance.now() / 1000 + 1 });
    }
  }
  const replacement = acquireRuntimeOwnership(f.path);
  replacement.close();
});

// Distinct gap: an initialization exception must stop the rebuilder/drain the
// existing record and release the actual borrowed ownership, without an HTTP app.
test('partial startup failure drains the record and releases the exact lease', async () => {
  const f = setup();
  await expect(
    startServer({
      beforePublication() {
        throw new Error('bootstrap proof failure');
      },
    }),
  ).rejects.toThrow('bootstrap proof failure');
  const replacement = acquireRuntimeOwnership(f.path);
  replacement.close();
  expect(
    f.database.read((sql) => sql.get('SELECT epoch FROM conversation_runtime_epoch')),
  ).toBeDefined();
});

// Distinct gap: isolated publication tests use a synthetic runtime reader. This
// uses the ordinary Chief send route, shared core/record/tail, scripted process,
// real navigation projection, and actual server shutdown with pending HTTP work.
test('ordinary sends publish real core status and shutdown fences a blocked native receipt before lease release', async () => {
  const began = performance.now();
  const f = setup();
  vi.stubEnv('PLAN_TEST_MODE', '0');
  vi.stubEnv('PLAN_DISPATCH_ENABLED', 'true');
  const registry = new ScriptedBackendRegistry();
  const broken = create(f.database, { title: 'Broken saved Ticket' });
  f.database.write((tx) =>
    tx.run("UPDATE tickets SET employee_backend='invalid' WHERE id=?", [broken.id]),
  );
  const wakePoll = vi.spyOn(ManagerWakeLoop.prototype, 'poll_once');
  const wakeStop = vi.spyOn(ManagerWakeLoop.prototype, 'stop');
  const intervals = vi.spyOn(globalThis, 'setInterval');
  const clear_interval = vi.spyOn(globalThis, 'clearInterval');
  const video_cleanup = vi.spyOn(VideoUploadService.prototype, 'recover_and_cleanup');
  const janitor = vi.spyOn(SqliteProcessConversationSystem.prototype, 'start_idle_child_janitor');
  let metrics_ready:
    { milliseconds: number; configured_read_workers: number; owns_collection: boolean } | undefined;
  const metrics_start = MetricsRuntime.prototype.start;
  vi.spyOn(MetricsRuntime.prototype, 'start').mockImplementation(async function (
    this: MetricsRuntime,
  ) {
    const began = performance.now();
    await metrics_start.call(this);
    metrics_ready = {
      milliseconds: performance.now() - began,
      configured_read_workers: this.config.read_workers,
      owns_collection: this.owns_collection,
    };
  });
  let features_verified = false;
  let context!: TestCompositionContext;
  // Ticket's baseline-only fixture predates these migration-owned checker tables.
  const schema = JSON.parse(
    readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as ApplicationSchemaSeed;
  const chief_start = resolve_conversation_start_request(
    new ConversationStartRequest({
      conversation_id: 'startup-chief',
      ...agent_resolve(f.database, undefined, {
        db_path: f.path,
        workspace_root: dirname(f.path),
      }),
    }),
  );
  // Add immutable pre-start capture and upload inputs to this existing host.
  f.database.write((tx) => {
    ensure_started_conversation_record(tx, chief_start, { created_at: 0 });
    tx.run(
      'INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) ' +
        'ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id ' +
        'WHERE agents.conversation_id IS NULL',
      ['chief_of_staff', chief_start.conversation_id],
    );
    tx.run(
      "INSERT INTO conversation_lifecycle_facts VALUES('retained','turn','1',1,'turn','running',NULL)",
    );
    for (const [id, expiry, state] of [
      ['startup-expired', 0, 'reserved'],
      ['startup-adoption', Math.floor(Date.now() / 1000) + 3600, 'ready'],
    ] as const)
      tx.run(
        'INSERT INTO conversation_video_uploads ' +
          '(upload_id,destination_json,filename,byte_count,received_bytes,state,media_type,expires_at,stored_file_id) ' +
          'VALUES(?,?,?,?,?,?,?,?,?)',
        [
          id,
          JSON.stringify({
            kind: 'chief',
            id: 'chief',
            expected_conversation_id: chief_start.conversation_id,
          }),
          'synthetic.mp4',
          21,
          21,
          state,
          'video/mp4',
          expiry,
          'f_' + id,
        ],
      );
  });
  const upload_root = join(dirname(f.path), 'files/video-uploads');
  mkdirSync(upload_root, { recursive: true });
  const video_bytes = Buffer.from('synthetic ready video');
  writeFileSync(join(upload_root, 'startup-expired'), video_bytes);
  writeFileSync(join(upload_root, 'startup-adoption'), video_bytes);
  write_manifest(f.path, {
    version: 1,
    deployment_id: 'startup-composition',
    capture_id: 'retained-turn',
    captured_at: 1,
    state: 'captured',
    detail: null,
    turns: [
      {
        conversation_id: 'retained',
        turn_number: 1,
        occurrence_id: null,
        runtime_epoch: 'old-runtime',
        original_status: 'running',
        display_name: 'Retained synthetic work',
        owner_kind: 'unknown',
        owner_id: null,
        claim_revision: null,
        claim: null,
      },
    ],
  });
  // Existing actual-core host also proves replay admission without counting it as busy.
  f.database.write((tx) => {
    for (const id of ['replay-first', 'replay-second']) {
      tx.run(
        `INSERT INTO conversations
        (conversation_id,backend_key,model,workspace_folder,access,created_at,latest_sequence)
        VALUES(?,'codex','fixture','/synthetic','full',0,1)`,
        [id],
      );
      tx.run(
        `INSERT INTO conversation_events VALUES(?,1,'command_reply','{"reply_text":"Replay fixture"}',0)`,
        [id],
      );
    }
  });
  let ready = false;
  let replay_checks = 0;
  const rebuild_start = ConversationViewRebuilder.prototype.start;
  vi.spyOn(ConversationViewRebuilder.prototype, 'start').mockImplementation(function (
    this: ConversationViewRebuilder,
    database,
    admission_closed,
  ) {
    expect(ready).toBe(true);
    expect(context.core).toBeDefined();
    context.publication.current_read();
    expect(context.core!.workspace_admission.active).toBe(false);
    rebuild_start.call(this, database, () => {
      replay_checks++;
      if (replay_checks === 2) {
        context.core!.workspace_admission.seal();
        try {
          return admission_closed!();
        } finally {
          context.core!.workspace_admission.reopen();
        }
      }
      return admission_closed!();
    });
  });
  const create_app = application.createApp;
  vi.spyOn(application, 'createApp').mockImplementation(async (...args) => {
    const app = await create_app(...args);
    app.addHook('onReady', async () => {
      ready = true;
    });
    return app;
  });
  const bindings_seen: ProductionBindingContext[] = [];
  const tracked_waits = vi.fn((sql: ReadSql, _now_ms: number) => {
    context.require_runtime_ownership();
    expect(sql.get('SELECT 1 AS alive')).toEqual({ alive: 1 });
    return [];
  });
  const unavailable = (): never => {
    throw new Error('External effect is outside this scripted composition proof');
  };
  const dependencies: ProductionServerDependencies = {
    backends: {
      claude: (binding) => {
        bindings_seen.push(binding);
        return registry.factories.claude;
      },
      codex: (binding) => {
        bindings_seen.push(binding);
        return registry.factories.codex;
      },
      hermes: (binding) => {
        bindings_seen.push(binding);
        return registry.factories.hermes;
      },
      http(binding) {
        bindings_seen.push(binding);
        return create_backend_runtime({
          database: binding.database,
          config: binding.config,
          now: () => binding.clock.now(),
          providers: unavailable_backend_providers(),
          environment: new RecordingBackendProbeEnvironment(),
          one_shot_children: binding.one_shot_children,
        });
      },
      stall_sources: () => [{ tracked_waits }],
    },
    environments: {
      dependencies: {
        executor: null,
        application_root: dirname(f.path),
        node_executable: process.execPath,
        admitted_legacy_null_sha: null,
      },
      factory: async (value) => {
        context = value;
        return {
          app_root: dirname(f.path),
          service_unit: null,
          provider: () => null,
          collector: null,
          staging: { stage: async () => unavailable() },
          installer: {
            require_exact_schema_frontier: unavailable,
            schema_change_pending: unavailable,
            create_backup: async () => unavailable(),
            prepare: unavailable,
            launch: async () => unavailable(),
          },
          create_mac: (mac_context) => ({
            service: create_mac_update_composition(mac_context, null).service,
            operations: {
              prepare: async () => unavailable(),
              phase: () => null,
              reported_at: () => null,
              configured: () => false,
            },
            reads: { health_payload: () => null, status: async () => null },
          }),
          collect_status: async () => unavailable(),
        };
      },
    },
  };
  // Missing and partial tables use the SAME validation/entry as the complete table.
  const exit = vi.fn<(code: 0 | 1) => void>();
  await run_application_process(
    {
      start: () => start_production_server({ ...dependencies, backends: {} }),
    },
    exit,
  );
  expect(exit).toHaveBeenCalledExactlyOnceWith(1);
  exit.mockClear();
  await expect(
    start_production_server({
      ...dependencies,
      backends: { claude: dependencies.backends.claude },
    }),
  ).rejects.toThrow('Missing production backend factories: codex, hermes');
  expect(bindings_seen).toEqual([]);
  let server!: StartedServer;
  await run_application_process(
    {
      start: async (onShutdownComplete) => {
        server = await start_production_server(dependencies, onShutdownComplete);
        return server;
      },
    },
    exit,
  );
  let stop: StopOptions | undefined;
  const post = (text: string, content: unknown = [{ piece: 'text', text }]) =>
    fetch(server.address + '/api/chief/conversation/send', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        content,
        sender_label: 'Owner',
        sender_message_id: text,
      }),
    });
  try {
    await vi.waitFor(() => expect(replay_checks).toBe(2));
    expect(
      f.database.read((sql) =>
        sql.get('SELECT through_sequence FROM conversation_view_state WHERE conversation_id=?', [
          'replay-first',
        ]),
      ),
    ).toEqual({ through_sequence: 1 });
    expect(
      f.database.read((sql) =>
        sql.get('SELECT 1 FROM conversation_view_state WHERE conversation_id=?', ['replay-second']),
      ),
    ).toBeUndefined();
    expect(context.core!.workspace_admission.closed).toBe(false);
    expect(context.core!.options.wait_registry).toBe(WAITS);
    expect(janitor).toHaveBeenCalledOnce();
    expect(tracked_waits).toHaveBeenCalled();
    expect(
      context.database.read((sql) => sql.get('SELECT checked_at_ms FROM stall_checker_heartbeat')),
    ).toEqual({ checked_at_ms: tracked_waits.mock.calls.at(-1)![1] });
    expect(new Set(bindings_seen).size).toBe(1);
    expect(bindings_seen).toHaveLength(4);
    expect(existsSync(join(upload_root, 'startup-expired'))).toBe(false);
    expect(
      f.database.read((sql) =>
        sql.get("SELECT state FROM conversation_video_uploads WHERE upload_id='startup-expired'"),
      ),
    ).toEqual({ state: 'cancelled' });
    await vi.waitFor(async () =>
      expect(
        (await registry.inspect('retained'))
          .flatMap((snapshot) => snapshot.delivered_text)
          .some((text) => text.includes(RECOVERY_PROMPT)),
      ).toBe(true),
    );
    const recovered = registry.child('retained');
    await recovered.finish();
    await recovered.flush_events();
    await context.core!.wait_until_quiescent();
    expect(
      f.database.read((sql) =>
        sql.get(
          "SELECT exact_match,recovery_state FROM deploy_interruptions WHERE source_conversation_id='retained'",
        ),
      ),
    ).toEqual({ exact_match: 1, recovery_state: 'resumed' });
    const cleanup_timer = intervals.mock.calls
      .map((args, index) => ({ args, timer: intervals.mock.results[index].value }))
      .filter(({ args }) => args[1] === CLEANUP_INTERVAL_SECONDS * 1000);
    expect(cleanup_timer).toHaveLength(1);
    const tick_cleanup = cleanup_timer[0].args[0] as () => void;
    const cleanup_calls = video_cleanup.mock.calls.length;
    const cleanup_error = new Error('Synthetic transient cleanup failure');
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    video_cleanup.mockImplementationOnce(() => {
      throw cleanup_error;
    });
    tick_cleanup();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(errors).toHaveBeenCalledWith('Video upload cleanup failed', cleanup_error);
    expect(clear_interval).not.toHaveBeenCalledWith(cleanup_timer[0].timer);
    tick_cleanup();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(video_cleanup).toHaveBeenCalledTimes(cleanup_calls + 2);
    errors.mockRestore();
    await vi.waitFor(() => expect(wakePoll).toHaveBeenCalled());
    await expect(wakePoll.mock.results[0].value).resolves.toEqual([]);
    const chief_id = context.database.read((sql) =>
      sql.get<{ conversation_id: string }>(
        "SELECT conversation_id FROM agents WHERE agent_key='chief_of_staff'",
      ),
    )!.conversation_id;
    const notice =
      "The Ticket 'Broken saved Ticket' is unusable because its saved record is invalid. " +
      'Reason: PlannerError: unknown agent backend (employee_backend). Panels remains available. ' +
      'Inspect this Ticket before any repair.';
    await vi.waitFor(async () => {
      const delivered = (await registry.inspect(chief_id)).flatMap((value) => value.delivered_text);
      expect(delivered.filter((text) => text.includes(notice))).toHaveLength(1);
    });
    await registry.child(chief_id).finish();
    await vi.waitFor(async () => expect(await context.core!.is_running(chief_id)).toBe(false));
    const sent = await post('work', [{ piece: 'video_upload', upload_id: 'startup-adoption' }]);
    expect(sent.status).toBe(200);
    const delivery = (await sent.json()) as { conversation_id: string; fate: string };
    expect(delivery.fate).toBe('started');
    const id = delivery.conversation_id;
    const child = registry.child(id);
    const adopted = join(dirname(f.path), 'files/conversations', id, 'f_startup-adoption');
    expect(readFileSync(adopted)).toEqual(video_bytes);
    expect(readFileSync(adopted + '.media-type', 'utf8')).toBe('video/mp4');
    expect(
      f.database.read((sql) =>
        sql.get(
          "SELECT state,outcome,conversation_id FROM conversation_video_uploads WHERE upload_id='startup-adoption'",
        ),
      ),
    ).toEqual({ state: 'linked', outcome: 'accepted', conversation_id: id });
    const request_id = '19000000-0000-4000-8000-000000000021';
    const requested = await fetch(server.address + '/api/credentials/requests', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-plan-actor': 'chief',
        'x-plan-conversation-id': id,
      },
      body: JSON.stringify({
        request_id,
        name: 'Synthetic composition key',
        provider: 'Synthetic service',
        use: 'Continue authorized synthetic work',
        auth_kind: 'bearer',
        header_name: null,
        help: null,
        help_url: null,
        replacement: null,
      }),
    });
    expect(requested.status).toBe(200);
    const saved = await fetch(server.address + `/api/credentials/requests/${request_id}/save`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        name: 'Synthetic composition key',
        value: 'synthetic-private-value',
        expected_revision: null,
      }),
    });
    expect(saved.status).toBe(200);
    expect(await saved.json()).toMatchObject({ state: 'saved', delivery: { status: 'injected' } });
    expect(
      (await child.inspect()).calls.some(
        (call) =>
          call.command === 'steer' && JSON.stringify(call.arguments.content).includes(request_id),
      ),
    ).toBe(true);
    expect(
      f.database.read((sql) =>
        sql.get('SELECT status FROM credential_request_delivery_attempts WHERE request_id=?', [
          request_id,
        ]),
      ),
    ).toEqual({ status: 'injected' });
    features_verified = true;
    const settle_backend_events = async () => {
      await child.flush_events();
      await context.core!.wait_until_quiescent();
    };
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
    const attention = async () => {
      const response = await fetch(server.address + `/api/navigation/view/snapshot?view=${view}`);
      expect(response.status).toBe(200);
      const snapshot = (await response.json()) as CurrentViewSnapshot;
      const record = snapshot.content.records.find(
        (item) => item.kind === 'conversation' && item.id === id,
      );
      if (record?.kind !== 'conversation')
        throw new Error('active conversation is absent from navigation');
      return record.value.attention.current;
    };
    expect((await attention()).signal.working).toBe(true);
    await child.emit(
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id: 'permission',
          title: 'Permission',
          detail: null,
          options: [
            new PermissionAskOption({
              option_id: 'allow',
              label: 'Allow',
              option_kind: 'allow_once',
            }),
          ],
        }),
      }),
    );
    await settle_backend_events();
    await child.request_questions();
    await settle_backend_events();
    expect((await attention()).awaiting_answer).toBe(true);
    const turn = context.core!.runtime_turn(id)!;
    const [permission] = turn.pending_permission_ask_ids;
    const [question] = turn.pending_user_input_questions.keys();
    expect(await context.core!.answer_permission_ask(id, permission, 'allow')).toBe('applied');
    expect((await attention()).awaiting_answer).toBe(true);
    expect(
      await context.core!.answer_user_input(id, question, [
        new UserInputAnswer({ question_id: 'scope', answers: ['Composer'] }),
        new UserInputAnswer({ question_id: 'proof', answers: ['Browser test'] }),
      ]),
    ).toBe('applied');
    expect((await attention()).awaiting_answer).toBe(false);
    await child.emit(new BackgroundTaskStarted({ task_id: 'task', task_description: 'Task' }));
    await settle_backend_events();
    await child.finish();
    await settle_backend_events();
    expect(context.core!.runtime_turn(id)).toBeNull();
    expect((await attention()).signal.working).toBe(true);
    await child.emit(new BackgroundTaskEnded({ task_id: 'task', task_status: 'completed' }));
    await settle_backend_events();
    expect((await attention()).signal.working).toBe(false);
    expect((await post('fail')).status).toBe(200);
    await child.finish({ ending: 'failed', error_summary: 'Synthetic failure' });
    await settle_backend_events();
    expect((await attention()).last_turn_failed).toBe(true);
    const through = context.record.store.read_conversation(id)!.latest_sequence;
    // The acknowledgment's domain policy belongs elsewhere. Exercise its actual
    // source transaction and producer trigger without a fabricated route.
    context.record.database.write((tx) =>
      tx.run('INSERT INTO conversation_error_acknowledgements VALUES(?,?)', [id, through]),
    );
    expect((await attention()).last_turn_failed).toBe(false);
    await child.configure('write_prompt', { barrier: 'shutdown' });
    const pending = post('shutdown').then(
      () => null,
      () => null,
    );
    await child.wait_for_barrier('shutdown');
    expect(exit).not.toHaveBeenCalled();
    await server.close((stop = { deadline: performance.now() / 1000 + 1 }));
    expect(exit).toHaveBeenCalledExactlyOnceWith(0);
    await pending;
    expect(wakeStop).toHaveBeenCalledOnce();
    for (const result of wakePoll.mock.results) await expect(result.value).resolves.toEqual([]);
    const replacement = acquireRuntimeOwnership(f.path);
    replacement.close();
  } finally {
    await server.close((stop ??= { deadline: performance.now() / 1000 + 1 }));
    await registry.close();
    console.info(
      'PRODUCTION_COMPOSITION_BOUNDARY',
      JSON.stringify({
        case_milliseconds: performance.now() - began,
        metrics_ready,
        features_verified: features_verified
          ? ['startup_recovery', 'credential_delivery', 'upload_expiry', 'video_adoption']
          : [],
      }),
    );
  }
});
