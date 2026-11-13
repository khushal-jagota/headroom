/** Real core admission and native shutdown are absent from the in-memory update proofs. */
import { afterEach, expect, test, vi } from 'vitest';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import type { TestCompositionContext } from '../core/start.ts';
import type { Database } from '../core/database/contracts.ts';
import { createReadAccess } from '../core/database/index.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import { BackgroundLoops } from '../core/loops.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { model_is_enabled } from '../conversation/backend_state.ts';
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
        version: 1,
        system: 'linux',
        architecture: 'x86_64',
        platforms: SUPPORTED_PLATFORMS.map((platform) => ({ ...platform })),
        python_version: '3.12.12',
        node_major: 22,
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
async function owned_server(installer?: Partial<InstallerOperations>) {
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
    tx.run("INSERT INTO navigation_meta VALUES(1,'update-core',10,'proof-epoch',0)");
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
    HERMES_HOME: 'homes/hermes',
    PLAN_HERMES_HOME: 'homes/plan-hermes',
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
  const published = release();
  const provider = {
    available: async () => published,
    download_artifact: async () => {
      throw new Error('no external download');
    },
  };
  try {
    const server = await startServer({
      enableWorkerReadiness: true,
      createConversationCore(value) {
        return new SqliteProcessConversationSystem({
          store: value.record.store,
          model_is_enabled: (backend, model) =>
            value.database.read((sql) => model_is_enabled(sql, backend, model)),
          execution_refusal: (id) =>
            value.database.read((sql) =>
              conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
            ),
          backend_child_factories: registry.factories,
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
            require_exact_schema_frontier: () => {
              throw new Error('Node frontier admission is unavailable in this v1 fixture');
            },
            schema_change_pending: () => false,
            create_backup: async () => {
              throw new Error('no actual backup');
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
            mac_composition = create_mac_update_composition(
              { ...mac_context, ready_release },
              null,
            );
            mac_inert = ready_release.mock.calls.length === 0;
            return mac_composition;
          },
          // The status collector is real and is never requested by these proofs.
          collect_status: collect_vps_status,
        });
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

test('real core completion admits an idle update, refuses HTTP writes without mutation, and reopens after launch failure', async () => {
  let update_instance: UpdateService | undefined;
  const start_updates = UpdateService.prototype.start;
  vi.spyOn(UpdateService.prototype, 'start').mockImplementation(function (this: UpdateService) {
    update_instance = this;
    return start_updates.call(this);
  });
  const backup = deferred<string>(),
    backup_entered = deferred<void>();
  const launch = deferred<void>(),
    launch_entered = deferred<void>();
  const owned = await owned_server({
    schema_change_pending: () => true,
    create_backup: () => {
      backup_entered.resolve();
      return backup.promise;
    },
    launch: () => {
      launch_entered.resolve();
      return launch.promise;
    },
  });
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
      provider: mac_context.provider === owned.provider,
      inert: owned.mac_inert,
      route_handle: owned.environment.mac_updates === owned.mac_composition.service,
      live: mac_context.ready_release() === update_instance.ready_release(),
      configured: owned.mac_composition.operations.configured(),
      health: owned.mac_composition.reads.health_payload(),
      status: await owned.mac_composition.reads.status(),
    }).toEqual({
      calls: 1,
      store: true,
      owner: true,
      provider: true,
      inert: true,
      route_handle: true,
      live: true,
      configured: false,
      health: null,
      status: null,
    });
    const install = await owned.post('/api/updates/install', {
      app_sha: 'b'.repeat(40),
      mode: 'idle',
    });
    expect(install.status).toBe(200);
    expect((await owned.status()).attempt.state).toBe('waiting');
    expect(owned.context.core!.workspace_admission.closed).toBe(false);
    await child.finish();
    await reached(backup_entered.promise, 'idle readiness admission', () => ({
      activity: owned.context.core!.workspace_activity(),
      active: owned.context.core!.workspace_admission.active,
      closed: owned.context.core!.workspace_admission.closed,
      attempt: JSON.parse(readFileSync(join(owned.root, 'data/update-attempt.json'), 'utf8')),
    }));
    expect(owned.context.core!.runtime_turn(delivery.conversation_id)).toBeNull();
    expect((await owned.status()).attempt.state).toBe('preparing');
    expect(owned.context.core!.workspace_admission.closed).toBe(true);
    const refused = async (text: string) => {
      const before = canonical_rows(owned.context.database);
      expect((await owned.post('/api/projects', { name: text, summary: 'proof' })).status).toBe(
        503,
      );
      expect((await owned.send(text)).status).toBe(503);
      expect(canonical_rows(owned.context.database)).toEqual(before);
      const read = await fetch(owned.server.address + '/api/projects?detail=full');
      expect(read.status).toBe(200);
    };
    await refused('refused while preparing');
    backup.resolve(join(owned.root, 'backups/scripted-snapshot'));
    await reached(launch_entered.promise, 'installer launch after backup');
    expect((await owned.status()).attempt.state).toBe('installing');
    await refused('refused while installing');
    launch.reject(new Error('scripted installer did not start'));
    await vi.waitFor(async () => expect((await owned.status()).attempt.state).toBe('failed'));
    expect(owned.context.core!.workspace_admission.closed).toBe(false);
    expect(owned.context.core!.workspace_activity()).toBe('idle');
    const created = await owned.post('/api/projects', {
      name: 'work after failed update',
      summary: 'proof',
    });
    expect(created.status).toBe(200);
    const resumed = await owned.send('native work after failed update');
    expect(resumed.status).toBe(200);
    expect((await resumed.json()).fate).toBe('started');
    await child.finish();
  } finally {
    backup.resolve(join(owned.root, 'backups/scripted-snapshot'));
    launch.reject(new Error('test cleanup'));
    await owned.close();
  }
}, 20_000);

test('forced update finalizes automatically after a raw provider outlives shutdown grace', async () => {
  const loops_stop = vi.spyOn(BackgroundLoops.prototype, 'stop');
  const metrics_stop = vi.spyOn(MetricsRuntime.prototype, 'stop');
  const updates_stop = vi.spyOn(UpdateService.prototype, 'stop');
  const launch_entered = deferred<void>(),
    trigger_stop = deferred<void>(),
    stop_started = deferred<void>();
  let stop_server!: () => Promise<void>;
  let closing: Promise<void> | undefined;
  const owned = await owned_server({
    launch: async () => {
      launch_entered.resolve();
      await trigger_stop.promise;
      // Model the service manager's stop after successful external installer launch.
      // Never await shutdown here: UpdateService.stop joins this advancing flight.
      closing = stop_server();
      void closing.catch(() => {});
      stop_started.resolve();
    },
  });
  stop_server = () => owned.server.close({ deadline: performance.now() / 1000 + 0.5 });
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
    await vi.waitFor(async () => expect((await owned.status()).state).toBe('available'));
    const forced = await owned.post('/api/updates/install', {
      app_sha: 'b'.repeat(40),
      mode: 'now',
      confirm_activity: true,
    });
    expect(forced.status).toBe(200);
    expect((await forced.json()).attempt.mode).toBe('now');
    await reached(launch_entered.promise, 'forced installer launch');
    expect((await owned.status()).attempt.state).toBe('installing');
    expect(owned.context.core!.workspace_admission.closed).toBe(true);
    expect(
      JSON.parse(readFileSync(join(owned.root, 'data/update-attempt.json'), 'utf8'))
        .confirm_activity,
    ).toBe(true);
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
    trigger_stop.resolve();
    await reached(stop_started.promise, 'external launch requests server shutdown');
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
    trigger_stop.resolve();
    receipt.resolve();
    if (child) await Promise.allSettled([child.release('owned-detach')]);
    await owned.close();
    await pending;
    await provider_return;
  }
}, 20_000);
