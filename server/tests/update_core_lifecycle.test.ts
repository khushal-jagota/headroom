/** Real core admission and native shutdown are absent from the in-memory update proofs. */
import { afterEach, expect, test, vi } from 'vitest';
import {
  closeSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import BetterSqlite3 from 'better-sqlite3';
import { PYTHON_BASELINE_SEED } from '../core/database/schema_steps/baseline.ts';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { fixture, fixture_path } from './ticket_fixture.ts';
import * as integrity_notices from '../tickets/integrity_notice.ts';
import { pin_existing } from '../environments/updates/installer/selection.ts';
import { open_control_server } from '../lifecycle/control.ts';
import { executor_link_socket_path } from '../core/http/executor_link.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { MacHttpClient } from '../executor/mac/transport/http.ts';
import { issue_home_machine_credential } from '../machines/executor_credentials.ts';
import { write_credential } from '../executor/credential_file.ts';
import { InstallationRecord } from '../environments/updates/installer/record.ts';
import { FileUpdateAttemptStore } from '../environments/updates/attempts.ts';
import { startServer } from '../core/start.ts';
import type { TestCompositionContext } from '../core/start.ts';
import type { Database } from '../core/database/contracts.ts';
import { createReadAccess } from '../core/database/index.ts';
import { UpdateBarrier } from '../environments/update_barrier.ts';
import { flock } from '../core/locks/flock.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import { BackgroundLoops } from '../core/loops.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { BackendStateStore } from '../conversation/backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../tickets/execution_admission.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { ScriptedBackendRegistry } from '../conversation/testing/scripted_registry.ts';
import type { ScriptedChild } from '../conversation/testing/scripted_child.ts';
import { createEnvironmentRuntime } from '../environments/runtime.ts';
import type { ComposedEnvironmentRuntime } from '../environments/runtime.ts';
import { MetricsRuntime } from '../environments/vps_metrics/runtime.ts';
import { UpdateService } from '../environments/updates/service.ts';
import { create_mac_update_composition } from '../environments/updates/mac/composition.ts';
import type {
  MacUpdateCompositionContext,
  MacUpdateComposition,
} from '../environments/updates/mac/contracts.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { InstallerOperations, PublishedUpdate } from '../environments/updates/contracts.ts';
import {
  GLIBC_LIBRARIES,
  SUPPORTED_CPU_FEATURES,
  SUPPORTED_PLATFORMS,
} from '../environments/updates/logic/runtime_contract.ts';
import { collect_vps_status } from '../environments/vps_status/collect.ts';
import { instant } from '../environments/updates/logic/experience.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  void promise.catch(() => {});
  return { promise, resolve, reject };
}
function shutdown_failure(error: unknown): Error {
  const detail = JSON.stringify(error, (_key, value: unknown) =>
    value instanceof Error
      ? {
          type: value.constructor.name,
          name: value.name,
          message: value.message,
          errors: value instanceof AggregateError ? value.errors : undefined,
        }
      : value,
  );
  return new Error(`Actual shutdown failure: ${detail}`, { cause: error });
}
async function reached<T>(promise: Promise<T>, label: string, state?: () => unknown): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          let detail: unknown;
          try {
            detail = state?.();
          } catch (error) {
            detail = String(error);
          }
          reject(new Error(`${label} was not reached: ${JSON.stringify(detail)}`));
        }, 5000);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
function canonical_rows(database: Pick<Database, 'read'>): unknown {
  return database.read((sql) =>
    sql
      .all<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
      )
      .map(({ name }) => [
        name,
        sql
          .all(`SELECT * FROM "${name.replaceAll('"', '""')}"`)
          .map((row) => JSON.stringify(row))
          .sort(),
      ]),
  );
}
function release(): PublishedUpdate {
  return {
    publication_run_id: 1,
    artifact_id: 2,
    proof_artifact_id: 3,
    manifest: {
      app_sha: 'b'.repeat(40),
      source_digest: 'c'.repeat(64),
      artifact_digest: 'd'.repeat(64),
      runtime_contract: {
        version: 2,
        system: 'linux',
        architecture: 'x86_64',
        platforms: SUPPORTED_PLATFORMS.map((platform) => ({ ...platform })),
        node_major: 22,
        minimum_node_version: '22.18.0',
        cpu_features: [...SUPPORTED_CPU_FEATURES],
        external_libraries: [...GLIBC_LIBRARIES],
        external_executables: ['sh', 'node'],
        inventory_sha256: 'e'.repeat(64),
      },
    },
    archive_sha256: 'f'.repeat(64),
    schema_floor: 'video_upload_ledger',
    expires_at: new Date(Date.now() + 86400_000).toISOString(),
    mac: null,
  };
}
async function owned_server(
  installer?: Partial<InstallerOperations>,
  startup?: {
    before_start?(root: string): void;
    configure_environment?(environment: ComposedEnvironmentRuntime): void;
    worker_readiness?: boolean;
  },
) {
  const published = release();
  const root = mkdtempSync(join(tmpdir(), 'panels-update-core-'));
  const path = join(root, 'data/planner.db');
  const registry = new ScriptedBackendRegistry();
  let context!: TestCompositionContext;
  let environment!: ComposedEnvironmentRuntime;
  const mac_contexts: MacUpdateCompositionContext[] = [];
  let mac_composition!: MacUpdateComposition;
  let mac_inert = false;
  const source = fixture();
  source.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'update-core',11,'proof-epoch',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'old-runtime')");
  });
  source.close();
  mkdirSync(join(root, 'data'));
  copyFileSync(fixture_path(source), path);
  for (const [key, suffix] of Object.entries({
    HOME: 'homes/user',
    CODEX_HOME: 'homes/codex',
    CLAUDE_CONFIG_DIR: 'homes/claude',
    PLAN_APP_ROOT: 'app',
    PLAN_WORKSPACE_ROOT: 'workspace',
    PLAN_LOGS_DIR: 'logs',
    PLAN_BACKUP_DIR: 'backups',
  })) {
    const target = join(root, suffix);
    mkdirSync(target, { recursive: true });
    vi.stubEnv(key, target);
    expect(relative(root, target).startsWith('..')).toBe(false);
  }
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: path,
    PLAN_CONFIG_PATH: join(root, 'absent-config.yaml'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'credential.key'),
    PLAN_TEST_MODE: '0',
    PLAN_DISPATCH_ENABLED: 'true',
    PLAN_TICK_SECONDS: '1',
    PLAN_PORT: '0',
    PLAN_APP_SHA: 'a'.repeat(40),
  }))
    vi.stubEnv(key, value);
  vi.stubEnv('PLAN_SERVER_LISTENER_FD', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_ROOT', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_DATABASE', undefined);
  expect(process.env.VITEST).toBe('true');
  const provider = {
    available: async () => published,
    download_artifact: async () => {
      throw new Error('no external download');
    },
  };
  try {
    startup?.before_start?.(root);
    const server = await startServer({
      enableWorkerReadiness: startup?.worker_readiness ?? true,
      createConversationCore(value) {
        return new SqliteProcessConversationSystem({
          store: value.record.store,
          delivery_model_refusal: (backend, model, conversation_id) =>
            new BackendStateStore(value.database).delivery_model_refusal(
              backend,
              model,
              conversation_id,
            ),
          execution_refusal: (id) =>
            value.database.read((sql) =>
              conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
            ),
          backend_registry: registry.registry,
          message_files: new ConversationMessageFiles(value.config.db_path),
          unix_time_now: () => value.clock.nowUnix(),
          require_runtime_ownership: value.require_runtime_ownership,
          require_signal_current: () => value.publication.current_read(),
          save_attempt: value.publication.save_attempt,
          on_shutdown: value.revoke_signal,
          publish_event: (event) => value.record.live_tail.publish_event(event),
          publish_frame: (id, frame) => value.record.live_tail.publish_frame(id, frame),
          publish_status_changed: (id) => value.record.live_tail.views.publish_status_changed(id),
          emit_change: () => value.changes.emit(),
        });
      },
      async createEnvironments(value) {
        context = value;
        environment = await createEnvironmentRuntime(value, {
          app_root: join(root, 'app'),
          service_unit: 'scripted-unit.service',
          provider: () => provider,
          collector: null,
          staging: {
            stage: async () => {
              const staged = join(root, '.app-next-' + published.manifest.app_sha);
              mkdirSync(staged);
              return staged;
            },
          },
          installer: {
            node_schema_steps_pending: () => false,
            stage_snapshot: async (_database: string, backup_directory: string) => {
              const staged = join(backup_directory, '.backup-staged-scripted');
              mkdirSync(staged, { recursive: true });
              return staged;
            },
            prepare: () => ['scripted-installer'],
            launch: async () => {
              throw new Error('no actual installer');
            },
            ...installer,
          },
          create_mac(mac_context) {
            mac_contexts.push(mac_context);
            const ready_release = vi.fn(mac_context.ready_release);
            mac_composition = create_mac_update_composition({ ...mac_context, ready_release });
            mac_inert = ready_release.mock.calls.length === 0;
            return mac_composition;
          },
          // The status collector is real and is never requested by these proofs.
          collect_status: collect_vps_status,
        });
        startup?.configure_environment?.(environment);
        return environment;
      },
    });
    const post = (route: string, body: unknown) =>
      fetch(server.address + route, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    const send = (text: string) =>
      post('/api/chief/conversation/send', {
        content: [{ piece: 'text', text }],
        sender_label: 'Owner',
        sender_message_id: text,
      });
    const status = async () => {
      const response = await fetch(server.address + '/api/updates');
      expect(response.status).toBe(200);
      return response.json();
    };
    return {
      root,
      path,
      registry,
      context,
      environment,
      mac_contexts,
      mac_composition,
      mac_inert,
      provider,
      server,
      post,
      send,
      status,
      async close() {
        try {
          await server.close({ deadline: performance.now() / 1000 + 1 });
        } catch (error) {
          throw shutdown_failure(error);
        } finally {
          await registry.close();
          rmSync(root, { recursive: true, force: true });
        }
      },
    };
  } catch (error) {
    await registry.close();
    rmSync(root, { recursive: true, force: true });
    throw error;
  }
}

test('a press installs at once while work runs, refuses HTTP writes behind the trial marker, and reopens after launch failure', async () => {
  let update_instance: UpdateService | undefined;
  const start_updates = UpdateService.prototype.start;
  vi.spyOn(UpdateService.prototype, 'start').mockImplementation(function (this: UpdateService) {
    update_instance = this;
    return start_updates.call(this);
  });
  const launch = deferred<void>(),
    launch_entered = deferred<void>();
  const staged_copies: string[] = [];
  const launches = vi.fn();
  const owned = await owned_server({
    node_schema_steps_pending: () => true,
    // The first press stages its copy. The second meets a failed integrity check.
    stage_snapshot: async (_database, backup_directory) => {
      if (staged_copies.length) throw new Error('SQLite integrity check failed for the copy');
      const staged = join(backup_directory, '.backup-staged-scripted');
      mkdirSync(staged, { recursive: true });
      staged_copies.push(staged);
      return staged;
    },
    launch: () => {
      launches();
      launch_entered.resolve();
      return launch.promise;
    },
  });
  const attempt = () =>
    JSON.parse(readFileSync(join(owned.root, 'data/update-attempt.json'), 'utf8'));
  const seconds_left = (deadline_at: string) =>
    Number(instant(deadline_at).epochMicroseconds) / 1_000_000 - Date.now() / 1000;
  try {
    const sent = await owned.send('native work before update');
    expect(sent.status).toBe(200);
    const delivery = await sent.json();
    expect(delivery.fate).toBe('started');
    const child = owned.registry.child(delivery.conversation_id);
    expect(owned.context.core!.workspace_activity()).toBe('busy');
    await vi.waitFor(async () => expect((await owned.status()).state).toBe('available'));
    if (!update_instance) throw new Error('Actual update service did not start');
    const mac_context = owned.mac_contexts[0]!;
    expect({
      calls: owned.mac_contexts.length,
      store: mac_context.attempts === update_instance.attempts,
      owner: mac_context.child_processes === owned.context.child_processes,
      signal: mac_context.signal === owned.context.one_shot_children.signal,
      provider: mac_context.provider === owned.provider,
      inert: owned.mac_inert,
      route_handle: owned.environment.mac_updates === owned.mac_composition.service,
      live: mac_context.ready_release() === update_instance.ready_release(),
    }).toEqual({
      calls: 1,
      store: true,
      owner: true,
      signal: true,
      provider: true,
      inert: true,
      route_handle: true,
      live: true,
    });
    // The card names the running work, and the press carries no confirmation.
    expect((await owned.status()).detail).toContain('Panels resumes it afterwards');
    const install = await owned.post('/api/updates/install', { app_sha: 'b'.repeat(40) });
    expect(install.status).toBe(200);
    await reached(launch_entered.promise, 'installer launch while work runs', attempt);
    // The turn still runs. The update did not stop it or wait for it.
    expect(owned.context.core!.runtime_turn(delivery.conversation_id)).not.toBeNull();
    const launched = attempt();
    expect(launched).toMatchObject({
      state: 'installing',
      backup: null,
      snapshot_required: true,
      staged_snapshot: staged_copies[0],
    });
    // The installer snapshots after the stop, so its deadline covers the copy, the
    // steps and a rollback restore. The fixture database gets the 120 s floor per copy.
    expect(seconds_left(launched.deadline_at)).toBeGreaterThan(900 + 120 + 900 + 120 - 30);
    expect(seconds_left(launched.deadline_at)).toBeLessThanOrEqual(900 + 120 + 900 + 120);
    expect((await owned.status()).state).toBe('updating');
    const before = canonical_rows(owned.context.database);
    expect(
      (await owned.post('/api/projects', { name: 'refused while installing', summary: 'proof' }))
        .status,
    ).toBe(503);
    expect(canonical_rows(owned.context.database)).toEqual(before);
    expect((await fetch(owned.server.address + '/api/projects?detail=full')).status).toBe(200);
    launch.reject(new Error('scripted installer did not start'));
    await vi.waitFor(async () => expect((await owned.status()).attempt.state).toBe('failed'));
    expect(attempt().steps.failure_reported).toBeTypeOf('string');
    // The installer never claimed the attempt, so its staged copy is gone.
    expect(existsSync(staged_copies[0]!)).toBe(false);
    const created = await owned.post('/api/projects', {
      name: 'work after failed update',
      summary: 'proof',
    });
    expect(created.status).toBe(200);
    await child.finish();
    const resumed = await owned.send('native work after failed update');
    expect(resumed.status).toBe(200);
    const resumed_delivery = await resumed.json();
    expect(resumed_delivery.fate).toBe('started');
    await owned.registry.child(resumed_delivery.conversation_id).finish();
    // A staged copy that fails its check refuses the update while the home still serves.
    const refused_attempt = attempt().attempt_id;
    expect((await owned.post('/api/updates/install', { app_sha: 'b'.repeat(40) })).status).toBe(
      200,
    );
    await vi.waitFor(() =>
      expect(attempt()).toMatchObject({
        state: 'failed',
        attempt_id: expect.not.stringMatching(refused_attempt),
      }),
    );
    expect(attempt().detail).toContain('failed its check before Panels stopped');
    expect(launches).toHaveBeenCalledTimes(1);
    expect(
      (await owned.post('/api/projects', { name: 'after refused snapshot', summary: 'proof' }))
        .status,
    ).toBe(200);
  } finally {
    launch.reject(new Error('test cleanup'));
    await owned.close();
  }
}, 20_000);

test('ordinary shutdown retains ownership beyond grace while a provider return is held', async () => {
  const loops_stop = vi.spyOn(BackgroundLoops.prototype, 'stop');
  const metrics_stop = vi.spyOn(MetricsRuntime.prototype, 'stop');
  const updates_stop = vi.spyOn(UpdateService.prototype, 'stop');
  let closing: Promise<void> | undefined;
  const owned = await owned_server();
  const receipt = deferred<void>(),
    receipt_entered = deferred<void>();
  let child: ScriptedChild | undefined;
  let pending: Promise<unknown> | undefined;
  let provider_return: ReturnType<ScriptedChild['write_prompt']> | undefined;
  try {
    const first = await owned.send('create native child');
    expect(first.status).toBe(200);
    const { conversation_id } = await first.json();
    child = owned.registry.child(conversation_id);
    await child.finish();
    await owned.context.core!.wait_until_quiescent();
    const original_prompt = child.write_prompt.bind(child);
    // Hold the provider method's accepted return, not a wire request after child exit.
    child.write_prompt = (...args) => {
      provider_return = original_prompt(...args).then(async (accepted) => {
        receipt_entered.resolve();
        await receipt.promise;
        return accepted;
      });
      return provider_return;
    };
    await child.configure('detach', { barrier: 'owned-detach' });
    pending = owned.send('pending native receipt').then(
      () => undefined,
      () => undefined,
    );
    await reached(receipt_entered.promise, 'held provider method return');
    expect(owned.context.core!.workspace_activity()).toBe('busy');
    expect(owned.context.core!.workspace_admission.active).toBe(true);
    // Database capabilities are frozen. Observe the actual store/publication doors
    // without replacing their SQL, settlement, or native cleanup behavior.
    const store = owned.context.record.store;
    const methods = new Set<string>();
    for (
      let prototype = Object.getPrototypeOf(store);
      prototype !== Object.prototype;
      prototype = Object.getPrototypeOf(prototype)
    ) {
      for (const name of Object.getOwnPropertyNames(prototype))
        if (name !== 'constructor' && typeof Reflect.get(store, name) === 'function')
          methods.add(name);
    }
    const observed_store = store as unknown as Record<string, (...args: unknown[]) => unknown>;
    const accesses = [
      ...[...methods].map((name) => vi.spyOn(observed_store, name)),
      vi.spyOn(owned.context.publication, 'current_read'),
      vi.spyOn(owned.context.publication, 'save_attempt'),
    ];
    let returned = false;
    void provider_return!.then(
      () => {
        returned = true;
      },
      () => {},
    );
    closing = owned.server.close({ deadline: performance.now() / 1000 + 0.5 });
    void closing.catch(() => {});
    await reached(child.wait_for_barrier('owned-detach'), 'original native detach');
    expect(loops_stop).toHaveBeenCalledOnce();
    expect(metrics_stop).toHaveBeenCalledOnce();
    expect(updates_stop).toHaveBeenCalledOnce();
    const deadline = loops_stop.mock.calls[0][0]!.deadline!;
    expect(deadline).toBe(metrics_stop.mock.calls[0][0]!.deadline);
    expect(deadline).toBe(updates_stop.mock.calls[0][0]!.deadline);
    expect(deadline).toBeGreaterThan(performance.now() / 1000);
    expect(owned.environment.before_worker_readiness_poll()).toBe(false);
    expect(() => acquireRuntimeOwnership(owned.path)).toThrow('ConversationRuntimeAlreadyOwned');
    expect(returned).toBe(false);
    await child.release('owned-detach');
    let closed = false;
    void closing!.then(() => {
      closed = true;
    });
    // Observe the actual core's bounded observer, rather than sleep until grace.
    await expect(owned.context.core!.shutdown({ deadline })).rejects.toBeInstanceOf(
      ShutdownDeadlineExceeded,
    );
    await pending;
    expect(closed).toBe(false);
    expect(returned).toBe(false);
    expect(owned.server.shutdown_state).toMatchObject({ kind: 'stopping', failures: [] });
    expect(owned.server.shutdown_state.pending).toContain('conversation core');
    expect(() => acquireRuntimeOwnership(owned.path)).toThrow('ConversationRuntimeAlreadyOwned');
    const retained_rows = canonical_rows(owned.context.database);
    receipt.resolve();
    await reached(provider_return!, 'raw provider return while original ownership remains held');
    await closing!.catch((error: unknown) => {
      throw shutdown_failure(error);
    });
    expect(returned).toBe(true);
    expect(closed).toBe(true);
    expect(owned.server.shutdown_state.kind).toBe('closed');
    const successor = acquireRuntimeOwnership(owned.path);
    const successor_reads = createReadAccess(owned.path);
    try {
      const before = canonical_rows(successor_reads);
      expect(before).toEqual(retained_rows);
      const access_counts = accesses.map((spy) => spy.mock.calls.length);
      await new Promise<void>((resolve) => setImmediate(resolve));
      expect(accesses.map((spy) => spy.mock.calls.length)).toEqual(access_counts);
      expect(canonical_rows(successor_reads)).toEqual(before);
    } finally {
      successor_reads.close();
      successor.close();
    }
  } finally {
    receipt.resolve();
    if (child) await Promise.allSettled([child.release('owned-detach')]);
    await owned.close();
    await pending;
    await provider_return;
  }
}, 20_000);

test('an installed home reopens when a registered coordinator never claims the update', async () => {
  const owned = await owned_server({
    prepare() {
      mkdirSync(join(owned.root, 'app'), { recursive: true });
      writeFileSync(
        join(owned.root, 'app/manifest.json'),
        JSON.stringify({ app_sha: 'a'.repeat(40) }),
      );
      pin_existing(owned.root, 'a'.repeat(40));
      return ['scripted-installer'];
    },
    async launch(attempt_id) {
      new InstallationRecord({ data_directory: join(owned.root, 'data'), attempt_id }).record(
        'coordinator_registered',
      );
    },
  });
  try {
    await vi.waitFor(async () => expect((await owned.status()).state).toBe('available'));
    const install = await owned.post('/api/updates/install', { app_sha: 'b'.repeat(40) });
    expect(install.status).toBe(200);
    const { attempt } = await install.json();
    const marker = join(owned.root, 'data/update-barrier.json');
    await vi.waitFor(() => expect(existsSync(marker)).toBe(true));
    await vi.waitFor(() => expect(existsSync(marker)).toBe(false), { timeout: 10_000 });
    expect((await owned.status()).attempt).toMatchObject({
      attempt_id: attempt.attempt_id,
      state: 'failed',
    });
    expect((await owned.post('/api/projects', { name: 'retained', summary: 'proof' })).status).toBe(
      200,
    );
  } finally {
    await owned.close();
  }
}, 20000);

test.each(['candidate', 'retained'] as const)(
  'a live %s home loses its registered coordinator and stays frozen until safe recovery',
  async (side) => {
    const current = 'a'.repeat(40),
      other = 'b'.repeat(40),
      id = 'lost-installer';
    const control_root = mkdtempSync(join(tmpdir(), 'panels-orphan-control-'));
    const socket = join(control_root, 'owner.sock'),
      restart = vi.fn();
    const supervisor = await open_control_server(
      socket,
      () => ({ version: 1, status: 'accepted' }),
      restart,
    );
    let lock: number | undefined, client: MacHttpClient | undefined;
    const owned = await owned_server(undefined, {
      before_start(root) {
        vi.stubEnv('PLAN_SERVER_CONTROL_SOCKET', socket);
        mkdirSync(join(root, 'app'), { recursive: true });
        writeFileSync(join(root, 'app/manifest.json'), JSON.stringify({ app_sha: current }));
        pin_existing(root, current);
        const now = new Date().toISOString();
        writeFileSync(
          join(root, 'data/update-attempt.json'),
          JSON.stringify({
            attempt_id: id,
            state: 'installing',
            mode: 'now',
            from_sha: side === 'retained' ? current : other,
            to_sha: side === 'candidate' ? current : other,
            created_at: now,
            updated_at: now,
            release: release(),
            steps: {
              coordinator_registered: now,
              installer_started: now,
              app_swapped: now,
              ...(side === 'retained' ? { rollback_app_restored: now } : {}),
            },
          }),
        );
        new UpdateBarrier(join(root, 'data/update-barrier.json')).freeze(id);
        lock = openSync(join(root, 'data/update-installer.lock'), 'a+');
        expect(flock(lock, 'try-exclusive')).toBe(0);
      },
    });
    try {
      const marker = join(owned.root, 'data/update-barrier.json');
      expect(restart).not.toHaveBeenCalled();
      closeSync(lock!);
      lock = undefined;
      await owned.environment.recover_update_barrier!();
      expect(existsSync(marker)).toBe(true);
      expect((await owned.post('/api/projects', { name: 'frozen', summary: 'proof' })).status).toBe(
        503,
      );
      if (side === 'candidate') {
        await vi.waitFor(() => expect(restart).toHaveBeenCalled());
        expect((await owned.status()).attempt.state).toBe('installing');
        return;
      }
      const grant = owned.context.database.write((tx) =>
        issue_home_machine_credential(tx, 'linux'),
      );
      const identity = {
        protocol_version: PROTOCOL_VERSION,
        spool_format_version: SPOOL_FORMAT_VERSION,
        application_revision: current,
        executor_incarnation: '11111111-1111-4111-8111-111111111111',
        connection_generation: 0,
      };
      client = new MacHttpClient(executor_link_socket_path(owned.path), grant.machine_id, identity);
      const secret = join(owned.root, 'credential');
      await write_credential(secret, grant.credential);
      await client.ensure_credential(secret);
      const lease = {
        identity,
        process_id: 1,
        mac_wall_clock_ms: Date.now(),
        queue_depth: 0,
        oldest_item_age_ms: null,
        waiting_conversations: [],
        waiting_conversations_truncated: false,
        working_turns: [],
        admission_stopped: false,
      };
      expect(await client.lease(lease)).toMatchObject({ error: { code: 'retry_later' } });
      expect(existsSync(marker)).toBe(true);
      expect(await client.lease(lease)).toMatchObject({ error: { code: 'retry_later' } });
      await vi.waitFor(() => expect(existsSync(marker)).toBe(false), { timeout: 6000 });
      expect((await owned.status()).attempt).toMatchObject({ state: 'failed', attempt_id: id });
      expect(restart).not.toHaveBeenCalled();
      expect(
        (await owned.post('/api/projects', { name: 'recovered', summary: 'proof' })).status,
      ).toBe(200);
    } finally {
      if (lock !== undefined) closeSync(lock);
      await client?.stop({ deadline: performance.now() / 1000 + 5 });
      await supervisor.close();
      await owned.close();
      rmSync(control_root, { recursive: true, force: true });
    }
  },
  30000,
);

test('a retained installer keeps startup health-only and orphan recovery retries without file changes', async () => {
  // Existing update hosts prove active updates, but not frozen startup or a failed
  // recovery dispatch with unchanged files. Reuse that host and its scripted adapters.
  let installer_lock: number | undefined;
  let fail_recovery = false;
  let recover_calls = 0;
  let failed_calls = 0;
  const metrics_start = vi.spyOn(MetricsRuntime.prototype, 'start');
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  const owned = await owned_server(undefined, {
    before_start(root) {
      new UpdateBarrier(join(root, 'data/update-barrier.json')).freeze('orphan');
      installer_lock = openSync(join(root, 'data/update-installer.lock'), 'a+');
      expect(flock(installer_lock, 'try-exclusive')).toBe(0);
    },
    configure_environment(environment) {
      const recover = environment.recover_update_barrier!.bind(environment);
      environment.recover_update_barrier = () => {
        recover_calls++;
        if (fail_recovery) {
          fail_recovery = false;
          failed_calls++;
          throw new Error('scripted recovery dispatch failure');
        }
        recover();
      };
    },
  });
  try {
    expect(
      (await fetch(owned.server.address + '/api/health?expected_sha=' + 'a'.repeat(40))).status,
    ).toBe(200);
    expect(metrics_start).not.toHaveBeenCalled();
    expect(existsSync(join(owned.root, 'workspace/.agents/skills'))).toBe(false);
    expect(owned.context.core!.workspace_admission.closed).toBe(true);
    expect((await owned.post('/api/projects', { name: 'held', summary: 'proof' })).status).toBe(
      503,
    );
    expect(owned.environment.update_barrier!.frozen).toBe(true);
    expect(recover_calls).toBeGreaterThan(0);
    // The abandoned installer releases only its kernel lock. Its marker never changes.
    fail_recovery = true;
    closeSync(installer_lock!);
    installer_lock = undefined;
    await vi.waitFor(() => expect(failed_calls).toBe(1), { timeout: 3500 });
    expect(owned.environment.update_barrier!.frozen).toBe(true);
    await vi.waitFor(() => expect(metrics_start).toHaveBeenCalledOnce(), { timeout: 3500 });
    expect(owned.environment.update_barrier!.frozen).toBe(false);
    expect(existsSync(join(owned.root, 'workspace/.agents/skills'))).toBe(false);
    expect(owned.context.core!.workspace_admission.closed).toBe(false);
    expect(
      errors.mock.calls.some(([message]) =>
        String(message).includes('Normal work could not resume'),
      ),
    ).toBe(true);
    expect((await owned.post('/api/projects', { name: 'released', summary: 'proof' })).status).toBe(
      200,
    );
  } finally {
    if (installer_lock !== undefined) closeSync(installer_lock);
    await owned.close();
  }
}, 15000);

test('admitted startup notices finish and a stale observer cannot reopen a replacement attempt', async () => {
  // The existing host proves frozen startup. This case closes start admission
  // between admitted notices and swaps the observed attempt within recovery.
  const notices = vi
    .spyOn(integrity_notices, 'notify_chief')
    .mockImplementation(async (_db, core) => {
      core.workspace_admission.close_starts();
    });
  const lost_work = vi.spyOn(
    SqliteProcessConversationSystem.prototype,
    'tell_agents_of_background_tasks_lost_at_restart',
  );
  const owned = await owned_server();
  try {
    await vi.waitFor(() => expect(lost_work).toHaveBeenCalledOnce());
    expect(notices).toHaveBeenCalledOnce();
    const attempts = new FileUpdateAttemptStore(
      join(owned.root, 'data/update-attempt.json'),
      () => {},
    );
    const record = {
      attempt_id: 'observed-A',
      from_sha: 'a'.repeat(40),
      to_sha: 'b'.repeat(40),
      mode: 'idle' as const,
      confirm_activity: false,
      state: 'done' as const,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      release: release(),
      detail: null,
      deadline_at: null,
      backup: null,
      snapshot_required: false,
      steps: {},
      mac_attempt_id: null,
      extra: {},
    };
    attempts.create(record);
    const barrier = owned.environment.update_barrier!;
    const original_reopen = owned.environment.reopen_update_work!.bind(owned.environment);
    const reopens = vi.fn(original_reopen);
    owned.environment.reopen_update_work = reopens;
    let swapped = false;
    owned.environment.recover_update_barrier = () => {
      barrier.release();
      attempts.remove(record.attempt_id, new Set(['done']));
      attempts.create({ ...record, attempt_id: 'replacement-B' });
      owned.context.core!.workspace_admission.close_starts();
      swapped = true;
    };
    barrier.freeze(record.attempt_id);
    await vi.waitFor(() => expect(swapped).toBe(true), { timeout: 3500 });
    expect(reopens).toHaveBeenCalledExactlyOnceWith(record.attempt_id);
    expect(owned.context.core!.workspace_admission.starts_closed).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 550));
    expect(reopens).toHaveBeenCalledOnce();
    expect(owned.context.core!.workspace_admission.starts_closed).toBe(true);
  } finally {
    await owned.close();
  }
}, 10000);

test('a partial normal startup failure is one-shot while the update observer stays active', async () => {
  let installer_lock: number | undefined;
  const failed_start = vi.fn(async () => {
    throw new Error('scripted partial startup failure');
  });
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  const owned = await owned_server(undefined, {
    before_start(root) {
      new UpdateBarrier(join(root, 'data/update-barrier.json')).freeze('orphan');
      installer_lock = openSync(join(root, 'data/update-installer.lock'), 'a+');
      expect(flock(installer_lock, 'try-exclusive')).toBe(0);
    },
    configure_environment(environment) {
      environment.start = failed_start;
    },
  });
  try {
    closeSync(installer_lock!);
    installer_lock = undefined;
    await vi.waitFor(() => expect(failed_start).toHaveBeenCalledOnce(), { timeout: 3500 });
    const failures = () =>
      errors.mock.calls.filter(([message]) =>
        String(message).includes('Normal work could not resume after the update trial'),
      );
    await vi.waitFor(() => expect(failures()).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(failed_start).toHaveBeenCalledOnce();
    expect(failures()).toHaveLength(1);
  } finally {
    if (installer_lock !== undefined) closeSync(installer_lock);
    await owned.close();
  }
}, 10000);

test('startup puts back the previous app before admission refuses a database an installer left behind', async () => {
  // The rule's own proof runs a killed installer. This one proves the real start path asks
  // before admission: otherwise admission refuses and nothing ever starts.
  let root = '';
  await expect(
    owned_server(undefined, {
      before_start(owned_root) {
        root = owned_root;
        const database = new BetterSqlite3(join(root, 'data/planner.db'));
        try {
          database
            .prepare('UPDATE alembic_version SET version_num = ?')
            .run(PYTHON_BASELINE_SEED.alembicRevision);
        } finally {
          database.close();
        }
        mkdirSync(join(root, '.app-previous-' + 'c'.repeat(40)));
        writeFileSync(
          join(root, 'data/update-attempt.json'),
          JSON.stringify({
            attempt_id: 'killed',
            state: 'installing',
            from_sha: 'c'.repeat(40),
            to_sha: 'a'.repeat(40),
            backup: null,
            steps: { app_swapped: '2026-11-17T00:00:00+00:00' },
          }),
        );
      },
      configure_environment() {
        throw new Error('the server composed environments on a database it cannot admit');
      },
    }),
  ).rejects.toThrow(/interrupted update was undone/);
  expect(root).not.toBe('');
});

test('an accepted queued send drains even when worker readiness polling is disabled', async () => {
  const launched = deferred<void>();
  const owned = await owned_server(
    {
      launch: async () => {
        launched.resolve();
        throw new Error('stop after observing the launch');
      },
    },
    { worker_readiness: false },
  );
  try {
    await vi.waitFor(async () => expect((await owned.status()).state).toBe('available'));
    const first = await (await owned.send('first queued-update turn')).json();
    expect(first.fate).toBe('started');
    const second = await (await owned.send('second queued-update turn')).json();
    expect(second.fate).toBe('queued');
    const pending = deferred<void>();
    const core = owned.context.core!;
    const hasPending = core.has_pending_update_sends.bind(core);
    vi.spyOn(core, 'has_pending_update_sends').mockImplementation(() => {
      const held = hasPending();
      if (held) pending.resolve();
      return held;
    });
    expect((await owned.post('/api/updates/install', { app_sha: 'b'.repeat(40) })).status).toBe(
      200,
    );
    expect((await owned.status()).attempt.state).toBe('preparing');
    await reached(pending.promise, 'installer waiting for the queued send');
    const refused = await owned.send('new send during cutover');
    expect(refused.status).toBe(503);
    expect(JSON.stringify(await refused.json())).toContain('update');
    await owned.registry.child(first.conversation_id).finish();
    await reached(launched.promise, 'installer launch after queued send delivery');
    expect(await owned.context.core!.held_prompts(first.conversation_id)).toHaveLength(0);
    expect(owned.context.core!.runtime_turn(first.conversation_id)?.token.turn_number).toBe(2);
  } finally {
    await owned.close();
  }
});

test('a stuck queued send times out, the installer starts, and the next home delivers it', async () => {
  const launch = vi.fn(async () => {});
  const owned = await owned_server({ launch }, { worker_readiness: false });
  const message = 'stuck-accepted-message';
  try {
    await vi.waitFor(async () => expect((await owned.status()).state).toBe('available'));
    const first = await (await owned.send('running before update')).json();
    expect(first.fate).toBe('started');
    const queued = await (await owned.send(message)).json();
    expect(queued.fate).toBe('queued');
    const pressed = performance.now();
    expect((await owned.post('/api/updates/install', { app_sha: 'b'.repeat(40) })).status).toBe(
      200,
    );
    await vi.waitFor(() => expect(launch).toHaveBeenCalledOnce(), {
      timeout: 35000,
      interval: 100,
    });
    expect(performance.now() - pressed).toBeGreaterThanOrEqual(30000);
    expect((await owned.status()).attempt.state).toBe('installing');
    expect((await owned.send('new send during cutover')).status).toBe(503);
    await owned.server.close({ deadline: performance.now() / 1000 + 5 });

    // Boot another home from the database left by the stop. Pending owner receipts
    // resume from durable recipient custody during ordinary conversation startup.
    const restarted = await owned_server(undefined, {
      worker_readiness: false,
      before_start(root) {
        copyFileSync(owned.path, join(root, 'data/planner.db'));
      },
    });
    try {
      const lookup = await fetch(
        restarted.server.address +
          '/api/conversation/owner-sends/' +
          message +
          '?owner_kind=chief&owner_id=chief',
      );
      expect(lookup.status).toBe(200);
      expect(await lookup.json()).toMatchObject({
        delivery: { conversation_id: first.conversation_id, fate: 'started' },
      });
      expect(await restarted.context.core!.held_prompts(first.conversation_id)).toHaveLength(0);
    } finally {
      await restarted.close();
    }
  } finally {
    await owned.close();
  }
}, 45000);
