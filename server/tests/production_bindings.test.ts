/** Adapter protocols do not prove real selection or the catalog's composed DB barrier. */
import { afterEach, expect, test, vi } from 'vitest';
import { dirname, join, resolve } from 'node:path';
import { mkdirSync } from 'node:fs';
import subprocess from 'node:child_process';
import http from 'node:http';
import https from 'node:https';
import { syncBuiltinESMExports } from 'node:module';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import type { ConversationCompositionContext, StartedServer } from '../core/start.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import { create_durable_codex_login_rejection } from '../conversation/backend_state.ts';
import * as claude from '../conversation/backends/claude_agent_sdk/adapter.ts';
import * as codex from '../conversation/backends/codex_app_server/adapter.ts';
import * as hermes from '../conversation/backends/hermes_acp/adapter.ts';
import { HermesModelCatalogOwner } from '../conversation/backends/hermes_acp/catalog.ts';
import { ModelCatalogCleanupFailed } from '../conversation/backends/catalog_cleanup.ts';
import * as snapshot from '../conversation/snapshot/composition.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import {
  compose_production_backends,
  create_production_backend_bindings,
} from '../lifecycle/production_bindings.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  syncBuiltinESMExports();
});

test('real bindings share owners and durable memory while Hermes cleanup retains server ownership', async () => {
  const began = performance.now(),
    database = fixture(),
    path = fixture_path(database);
  const root = dirname(path),
    bin = join(root, 'private-agent-bin');
  mkdirSync(bin);
  const claude_path = join(root, 'external-claude');
  const hermes_source = join(root, 'hermes-source');
  const hermes_python = join(hermes_source, 'venv/bin/python');
  for (const home of ['home', 'codex', 'claude', 'hermes']) mkdirSync(join(root, home));
  for (const [key, value] of Object.entries({
    HOME: join(root, 'home'),
    CODEX_HOME: join(root, 'codex'),
    CLAUDE_CONFIG_DIR: join(root, 'claude'),
    HERMES_HOME: join(root, 'hermes'),
    PLAN_DB_PATH: path,
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
    PLAN_HERMES_HOME: join(root, 'hermes'),
    PLAN_HERMES_PYTHON: hermes_python,
    PLAN_CLAUDE_EXECUTABLE: claude_path,
    PATH: bin,
    PLAN_APP_ROOT: resolve(import.meta.dirname, '../..'),
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
  const constructors = [
    vi.spyOn(claude, 'create_claude_backend_child_factory'),
    vi.spyOn(codex, 'create_codex_backend_child_factory'),
    vi.spyOn(hermes, 'create_hermes_backend_child_factory'),
  ] as const;
  const http_runtime = vi.spyOn(snapshot, 'create_backend_runtime');
  let release!: (error?: Error) => void,
    entered!: () => void,
    hold = false;
  const gate = new Promise<void>((yes, no) => {
    release = (error) => (error ? no(error) : yes());
  });
  void gate.catch(() => {});
  const stopping = new Promise<void>((yes) => {
    entered = yes;
  });
  const original_shutdown = HermesModelCatalogOwner.prototype.shutdown;
  const catalog_stop = vi
    .spyOn(HermesModelCatalogOwner.prototype, 'shutdown')
    .mockImplementation(function (this: HermesModelCatalogOwner) {
      const actual = original_shutdown.call(this);
      if (!hold) return actual;
      entered();
      return actual.then(() => gate);
    });
  let context!: ConversationCompositionContext, server: StartedServer | undefined;
  let owned_http!: BackendHttpRuntime;
  const stop: StopOptions = { deadline: performance.now() / 1000 + 30 };
  try {
    server = await startServer({
      createBackendServices(value) {
        context = value;
        const real = create_production_backend_bindings();
        const rejected = {
          ...value,
          config: { ...value.config, executor_id: 'no-router' },
          codex_login_rejection: create_durable_codex_login_rejection(value.database),
        };
        expect(() => real.claude(rejected)).toThrow(
          'Configured production executor router is not installed',
        );
        expect(constructors.every((spy) => spy.mock.calls.length === 0)).toBe(true);
        owned_http = without_runner(
          '0',
          () =>
            compose_production_backends(
              {
                ...value,
                config: { ...value.config, test_mode: false },
              },
              real,
            ).http,
        );
        return owned_http;
      },
    });
    expect(constructors.map((spy) => spy.mock.calls.length)).toEqual([1, 1, 1]);
    const claude_call = constructors[0].mock.calls[0]!,
      codex_call = constructors[1].mock.calls[0]!;
    const hermes_call = constructors[2].mock.calls[0]!;
    expect(claude_call).toEqual([
      {
        claude_executable: claude_path,
        environment_overrides: [['PLAN_SERVER_URL', 'http://127.0.0.1:0']],
      },
      { child_processes: context.child_processes, one_shot_children: context.one_shot_children },
    ]);
    expect([codex_call[1].child_processes, hermes_call[1].child_processes]).toEqual([
      context.child_processes,
      context.child_processes,
    ]);
    expect(codex_call[0].argv).toEqual(['codex', 'app-server']);
    expect(hermes_call[0].argv[0]).toBe(hermes_python);
    expect(Object.fromEntries(hermes_call[0].environment_overrides)).toMatchObject({
      HERMES_HOME: join(root, 'hermes'),
      HERMES_PYTHON_SRC_ROOT: hermes_source,
      PLAN_SERVER_URL: 'http://127.0.0.1:0',
    });
    const memory = codex_call[1].remembered_login_rejection;
    const other = create_durable_codex_login_rejection(context.database, () => context.clock.now());
    const generation = { account_id: 'synthetic', last_refresh: '2026-11-13T00:00:00Z' };
    memory.remember(generation);
    expect(other.applies_to(generation)).toBe(true);
    other.forget(generation);
    expect(memory.applies_to(generation)).toBe(false);
    const base = http_runtime.mock.results[0]!.value;
    const original_base_stop = base.shutdown!;
    const base_stop = vi.spyOn(base, 'shutdown');
    const retained = http_runtime.mock.calls[0]![0].providers.hermes!;
    await expect(without_runner('1', () => retained({ refresh: true }))).rejects.toThrow(
      'Production backend I/O is unavailable in tests. Inject recording adapters.',
    );
    const failure = new Error('Retained Hermes cleanup failed');
    const retry_cleanup = vi.fn(async () => {});
    const retry_failure = new ModelCatalogCleanupFailed(undefined, [failure], retry_cleanup);
    base_stop.mockImplementationOnce(async () => {
      throw retry_failure;
    });
    await expect(owned_http.shutdown!(stop)).rejects.toMatchObject({ errors: [retry_failure] });
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
    expect(base_stop).toHaveBeenCalledExactlyOnceWith(stop);
    expect(catalog_stop).toHaveBeenCalledTimes(1);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    base_stop.mockClear();
    catalog_stop.mockClear();
    base_stop.mockImplementationOnce(async (options: StopOptions) => {
      await retry_failure.retry_cleanup();
      await original_base_stop.call(base, options);
    });
    hold = true;
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
        throw new Error('Catalog stop was skipped');
      }),
    ]);
    await new Promise<void>((yes) => setImmediate(yes));
    expect(settled).toBe(false);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    expect(context.database.read((sql) => sql.get('SELECT 1 AS alive'))).toEqual({ alive: 1 });
    release(failure);
    await expect(owned_http.settled()).rejects.toMatchObject({ errors: [failure] });
    await expect(closing).rejects.toThrow('Shutdown failed after settlement');
    expect(catalog_stop).toHaveBeenCalledTimes(1);
    expect(base_stop).toHaveBeenCalledExactlyOnceWith(stop);
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
