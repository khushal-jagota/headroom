/** Adapter protocols do not prove real selection or the catalog's composed DB barrier. */
import { afterEach, expect, test, vi } from 'vitest';
import type { MockInstance } from 'vitest';
import type { DriverInstance } from '../drivers/core/driver.ts';
import { RecordingBackendProbeEnvironment } from '../conversation/snapshot/recording_environment.ts';
import { VpsExecutorRegistry } from '../executor/registry.ts';
import { dirname, join, resolve } from 'node:path';
import { mkdirSync, realpathSync, writeFileSync } from 'node:fs';
import subprocess from 'node:child_process';
import http from 'node:http';
import https from 'node:https';
import { syncBuiltinESMExports } from 'node:module';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import type { ConversationCompositionContext, StartedServer } from '../core/start.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import { create_durable_codex_login_rejection } from '../drivers/codex/runtime/login_rejection.ts';
import * as claude from '../drivers/claude/runtime/adapter.ts';
import * as codex from '../drivers/codex/runtime/adapter.ts';
import { create_driver_registry, default_instance_configs } from '../drivers/core/registry.ts';
import { ModelCatalogCleanupFailed } from '../conversation/backends/catalog_cleanup.ts';
import * as snapshot from '../conversation/snapshot/composition.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import {
  compose_production_backends,
  create_production_backend_bindings,
  default_production_dependencies,
} from '../lifecycle/production_bindings.ts';
import type {
  PreparedProductionBackends,
  ProductionBackendBindings,
} from '../lifecycle/production_bindings.ts';
import { start_production_server } from '../lifecycle/production.ts';
import * as installation from '../environments/updates/installation.ts';
import * as mac_composition from '../environments/updates/mac/composition.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  syncBuiltinESMExports();
});

function private_production_environment(
  database: ReturnType<typeof fixture>,
  root = dirname(fixture_path(database)),
  app_root = resolve(import.meta.dirname, '../..'),
) {
  const path = fixture_path(database),
    bin = join(root, 'private-agent-bin');
  mkdirSync(bin, { recursive: true });
  const claude_path = join(root, 'external-claude');
  for (const home of ['home', 'codex', 'claude']) mkdirSync(join(root, home), { recursive: true });
  for (const [key, value] of Object.entries({
    HOME: join(root, 'home'),
    CODEX_HOME: join(root, 'codex'),
    CLAUDE_CONFIG_DIR: join(root, 'claude'),
    PLAN_DB_PATH: path,
    PLAN_CONFIG_PATH: join(root, 'absent-config.yaml'),
    PLAN_WORKSPACE_ROOT: root,
    PLAN_PORT: '0',
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_EXECUTOR_ID: '',
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'credential.key'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'lifecycle.lock'),
    PLAN_CLAUDE_EXECUTABLE: claude_path,
    PATH: bin,
    PLAN_APP_ROOT: app_root,
  }))
    vi.stubEnv(key, value);
  vi.stubEnv('PLAN_SERVER_LISTENER_FD', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_ROOT', undefined);
  vi.stubEnv('PLAN_TICKET_SCRATCH_DATABASE', undefined);
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'old-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'old-runtime')");
  });
  return { path, root, claude_path };
}

test('configured real bindings share owners and durable memory while backend cleanup retains server ownership', async () => {
  const began = performance.now(),
    database = fixture();
  const { path, root, claude_path } = private_production_environment(database);
  // Launch custody comes from the composed config, even without either ambient variable.
  writeFileSync(join(root, 'absent-config.yaml'), JSON.stringify({ db_path: path }));
  vi.stubEnv('PLAN_DB_PATH', undefined);
  vi.stubEnv('PLAN_WORKER_PROCESS_ROOT', undefined);
  // Keep I/O traps for the entire lifetime, including a mistakenly deferred probe.
  const forbidden = (): never => {
    throw new Error('Unexpected production I/O');
  };
  const traps = [
    vi.spyOn(subprocess, 'spawn').mockImplementation(forbidden),
    vi.spyOn(http, 'request').mockImplementation(forbidden),
    vi.spyOn(https, 'request').mockImplementation(forbidden),
    vi.spyOn(globalThis, 'fetch').mockImplementation(forbidden),
  ];
  syncBuiltinESMExports();
  const markers = ['VITEST', 'PYTEST_CURRENT_TEST'] as const;
  function without_runner<T>(mode: string, operation: () => T): T {
    const previous = markers.map((key) => process.env[key]);
    const test_mode = process.env.PLAN_TEST_MODE;
    try {
      markers.forEach((key) => vi.stubEnv(key, undefined));
      vi.stubEnv('PLAN_TEST_MODE', mode);
      return operation();
    } finally {
      markers.forEach((key, index) => vi.stubEnv(key, previous[index]));
      vi.stubEnv('PLAN_TEST_MODE', test_mode);
    }
  }
  async function without_runner_until<T>(mode: string, operation: () => Promise<T>): Promise<T> {
    const previous = markers.map((key) => process.env[key]);
    const test_mode = process.env.PLAN_TEST_MODE;
    try {
      markers.forEach((key) => vi.stubEnv(key, undefined));
      vi.stubEnv('PLAN_TEST_MODE', mode);
      return await operation();
    } finally {
      markers.forEach((key, index) => vi.stubEnv(key, previous[index]));
      vi.stubEnv('PLAN_TEST_MODE', test_mode);
    }
  }
  const constructors = [
    vi.spyOn(claude, 'create_claude_backend_child_factory'),
    vi.spyOn(codex, 'create_codex_backend_child_factory'),
  ] as const;
  const http_runtime = vi.spyOn(snapshot, 'create_backend_runtime');
  let release!: (error?: Error) => void, entered!: () => void;
  const gate = new Promise<void>((yes, no) => {
    release = (error) => (error ? no(error) : yes());
  });
  void gate.catch(() => {});
  const stopping = new Promise<void>((yes) => {
    entered = yes;
  });
  let context!: ConversationCompositionContext, server: StartedServer | undefined;
  let owned_http!: BackendHttpRuntime;
  let real!: ProductionBackendBindings, prepared!: PreparedProductionBackends;
  let codex_dispose!: MockInstance<DriverInstance['dispose']>;
  let original_codex_dispose!: DriverInstance['dispose'];
  const stop: StopOptions = { deadline: performance.now() / 1000 + 30 };
  const firstStop: StopOptions = { deadline: stop.deadline - 1 };
  try {
    server = await startServer({
      async prepareConversationComposition(value, signal) {
        real = create_production_backend_bindings();
        expect(constructors.every((spy) => spy.mock.calls.length === 0)).toBe(true);
        // The instances are production ones: they own the catalogs, sign-in and usage.
        const host = real.driver_host({ ...value, config: { ...value.config, test_mode: false } });
        const configs = default_instance_configs(real.drivers);
        const registry = await without_runner_until('0', () =>
          create_driver_registry({
            drivers: real.drivers,
            configs,
            host,
            signal,
          }),
        );
        prepared = { registry, host };
        const codex_instance = registry.require_live('codex');
        original_codex_dispose = codex_instance.dispose;
        codex_dispose = vi.spyOn(codex_instance, 'dispose');
      },
      createBackendServices(value) {
        context = value;
        owned_http = without_runner(
          '0',
          () =>
            compose_production_backends(
              {
                ...value,
                config: { ...value.config, test_mode: false, executor_id: 'synthetic-executor' },
              },
              prepared,
              real,
            ).http,
        );
        return owned_http;
      },
    });
    expect(constructors.map((spy) => spy.mock.calls.length)).toEqual([1, 1]);
    const claude_call = constructors[0].mock.calls[0]!,
      codex_call = constructors[1].mock.calls[0]!;
    const worker_process_root = join(dirname(realpathSync(path)), 'worker-processes');
    expect(process.env.PLAN_DB_PATH).toBeUndefined();
    expect(process.env.PLAN_WORKER_PROCESS_ROOT).toBeUndefined();
    expect(context.config.db_path).toBe(path);
    // One host per invocation carries the URL and the canonical Worker root.
    expect(real.driver_host(context)).toBe(prepared.host);
    expect([prepared.host.panels_server_url, prepared.host.worker_process_root]).toEqual([
      'http://127.0.0.1:0',
      worker_process_root,
    ]);
    for (const call of [claude_call, codex_call])
      expect(Object.fromEntries(call[0].environment_overrides ?? []).PLAN_WORKER_PROCESS_ROOT).toBe(
        worker_process_root,
      );
    expect(claude_call).toEqual([
      {
        claude_executable: claude_path,
        environment_overrides: [
          ['PLAN_SERVER_URL', 'http://127.0.0.1:0'],
          ['PLAN_WORKER_PROCESS_ROOT', worker_process_root],
        ],
      },
      {
        child_processes: context.child_processes,
        one_shot_children: context.one_shot_children,
        worker_supervision: context.worker_supervision,
      },
    ]);
    expect(codex_call[1].child_processes).toBe(context.child_processes);
    expect(codex_call[0].argv).toEqual(['codex', 'app-server']);
    const memory = codex_call[1].remembered_login_rejection;
    const other = create_durable_codex_login_rejection(context.database, () => context.clock.now());
    const generation = { account_id: 'synthetic', last_refresh: '2026-11-13T00:00:00Z' };
    memory.remember(generation);
    expect(other.applies_to(generation)).toBe(true);
    other.forget(generation);
    expect(memory.applies_to(generation)).toBe(false);
    expect(http_runtime.mock.results[0]!.value).toBe(owned_http);
    const retained = prepared.registry.require_live('codex').status;
    const recorded = new RecordingBackendProbeEnvironment();
    recorded.executables.set('codex', '/synthetic/codex');
    await expect(
      without_runner_until('1', () =>
        retained.probe({
          environment: recorded,
          refresh: true,
          discover_update_advisory: false,
          now: new Date(0),
          model_version: () => [],
        }),
      ),
    ).rejects.toThrow('Production login reads are unavailable in tests.');
    const failure = new Error('Retained Codex cleanup failed');
    const prior_failure = new Error('Initial catalog cleanup failed');
    const retry_cleanup = vi.fn(async () => {});
    const retry_failure = new ModelCatalogCleanupFailed(undefined, [prior_failure], retry_cleanup);
    codex_dispose.mockImplementationOnce(async () => {
      throw retry_failure;
    });
    const first_failure = (await owned_http.shutdown!(firstStop).catch(
      (error: unknown) => error,
    )) as AggregateError;
    expect(first_failure.errors[0]).toBe(prior_failure);
    expect((first_failure.errors[1] as AggregateError).errors).toEqual([retry_failure]);
    let raw_settled = false;
    void owned_http.settled().then(
      () => {
        raw_settled = true;
      },
      () => {
        raw_settled = true;
      },
    );
    await new Promise<void>((yes) => setImmediate(yes));
    expect(raw_settled).toBe(false);
    expect(retry_cleanup).not.toHaveBeenCalled();
    expect(codex_dispose).toHaveBeenCalledExactlyOnceWith(firstStop);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    codex_dispose.mockClear();
    codex_dispose.mockImplementationOnce(async (options) => {
      await retry_failure.retry_cleanup();
      await original_codex_dispose(options);
      entered();
      await gate;
    });
    const closing = server.close(stop);
    void closing.catch(() => {});
    let settled = false;
    void closing
      .finally(() => {
        settled = true;
      })
      .catch(() => {});
    await Promise.race([
      stopping,
      closing.then(() => {
        throw new Error('Codex dispose was skipped');
      }),
    ]);
    await new Promise<void>((yes) => setImmediate(yes));
    expect(settled).toBe(false);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    expect(context.database.read((sql) => sql.get('SELECT 1 AS alive'))).toEqual({ alive: 1 });
    release(failure);
    await expect(owned_http.settled()).rejects.toMatchObject({ errors: [prior_failure, failure] });
    await expect(closing).rejects.toThrow('Shutdown failed after settlement');
    expect(codex_dispose).toHaveBeenCalledExactlyOnceWith(stop);
    expect(retry_cleanup).toHaveBeenCalledTimes(1);
    expect(retry_failure.pending).toBe(false);
    const successor = acquireRuntimeOwnership(path);
    successor.close();
    for (const trap of traps) expect(trap).not.toHaveBeenCalled();
  } finally {
    release();
    await server?.close(stop).catch(() => {});
    console.info(
      'REAL_BINDINGS_COMPOSITION',
      JSON.stringify({ milliseconds: performance.now() - began }),
    );
  }
});

/** The scripted backend host above does not prove that the default table activates the real factory. */
test('default production startup binds the real installer and environment runtime to its invocation', async () => {
  const began = performance.now(),
    database = fixture(),
    path = fixture_path(database);
  const root = join(dirname(path), 'default-production'),
    application_root = join(root, 'app');
  mkdirSync(application_root, { recursive: true });
  private_production_environment(database, root, application_root);
  vi.stubEnv('PLAN_APP_SHA', undefined);
  vi.stubEnv('PLAN_UPDATE_REPOSITORY', undefined);
  vi.stubEnv('PLAN_UPDATE_TOKEN_FILE', undefined);
  const installer = vi.spyOn(installation, 'create_installer_operations');
  const mac = vi.spyOn(mac_composition, 'create_mac_update_composition');
  const backend = vi.spyOn(snapshot, 'create_backend_runtime');
  const stop: StopOptions = { deadline: performance.now() / 1000 + 30 };
  let server: StartedServer | undefined;
  try {
    server = await start_production_server(default_production_dependencies());
    expect(server.address).toMatch(/^http:\/\/127\.0\.0\.1:[1-9]\d*$/);
    const health = await fetch(`${server.address}/api/health`, {
      signal: AbortSignal.timeout(2000),
    });
    expect(health.status).toBe(200);
    expect(await health.json()).toEqual({ ready: true, app_sha: null });
    expect(installer).toHaveBeenCalledTimes(1);
    expect(mac).toHaveBeenCalledTimes(1);
    expect(backend).toHaveBeenCalledTimes(1);
    const inputs = installer.mock.calls[0]![0];
    const [mac_context, executor] = mac.mock.calls[0]!;
    expect(inputs).toMatchObject({
      application_root: realpathSync(application_root),
      node_executable: process.execPath,
      admitted_legacy_null_sha: null,
    });
    expect(executor).toBeInstanceOf(VpsExecutorRegistry);
    expect(executor!.configured()).toBe(false);
    expect(executor!.health_payload()).toBeNull();
    expect(inputs.child_processes).toBe(mac_context.child_processes);
    expect(inputs.signal).toBe(mac_context.signal);
    expect(inputs.signal).toBe(backend.mock.calls[0]![0].one_shot_children!.signal);
    expect(inputs.signal.aborted).toBe(false);
    expect(backend.mock.calls[0]![0].config.test_mode).toBe(true);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    await server.close(stop);
    await server.shutdown_settled;
    expect(inputs.signal.aborted).toBe(true);
    expect(server.shutdown_state.kind).toBe('closed');
    const successor = acquireRuntimeOwnership(path);
    successor.close();
  } finally {
    await server?.close(stop).catch(() => {});
    console.info(
      'DEFAULT_PRODUCTION_COMPOSITION',
      JSON.stringify({ milliseconds: performance.now() - began }),
    );
  }
});
