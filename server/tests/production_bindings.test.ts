/** The server composes pure driver rules and machine services. */
import { afterEach, expect, test, vi } from 'vitest';
import { VpsExecutorRegistry } from '../executor/registry.ts';
import { dirname, join, resolve } from 'node:path';
import { mkdirSync, realpathSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { fixture, fixture_path } from './ticket_fixture.ts';
import type { StartedServer } from '../core/start.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import * as snapshot from '../conversation/snapshot/composition.ts';
import {
  prepare_machine_backends,
  create_production_backend_bindings,
  default_production_dependencies,
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

// Risk: server startup used driver.create(), which created native agent and provider owners
// even though recorded conversations were routed to machines. Existing registry tests prove
// native construction, not its absence from server composition.
test('server backend preparation uses pure driver rules without native instances', () => {
  const bindings = create_production_backend_bindings();
  const creates = bindings.drivers.map((driver) => vi.spyOn(driver, 'create'));
  const prepared = prepare_machine_backends(bindings.drivers);
  expect(prepared.registry.list()).toHaveLength(bindings.drivers.length);
  for (const driver of bindings.drivers) {
    const instance = prepared.registry.require_live(driver.driver_kind);
    expect(instance.capabilities).toBe(driver.capabilities);
    expect(instance.plan_selection_transition).toBe(driver.plan_selection_transition);
    expect(instance.supervises_local_launches).toBeUndefined();
  }
  expect(creates.every((create) => create.mock.calls.length === 0)).toBe(true);
});

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
    expect(backend).not.toHaveBeenCalled();
    const inputs = installer.mock.calls[0]![0];
    const [mac_context, executor] = mac.mock.calls[0]!;
    expect(inputs).toMatchObject({
      application_root: realpathSync(application_root),
    });
    expect(executor).toBeInstanceOf(VpsExecutorRegistry);
    expect(executor!.configured()).toBe(false);
    expect(executor!.health_payload()).toBeNull();
    expect(inputs.child_processes).toBe(mac_context.child_processes);
    expect(inputs.signal).toBe(mac_context.signal);
    expect(inputs.signal.aborted).toBe(false);
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
