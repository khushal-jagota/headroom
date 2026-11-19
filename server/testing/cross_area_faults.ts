/** Fresh-Python direct proofs. No production module imports this driver. */
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { loadConfig, testModeFromEnvironment } from '../core/config.ts';
import { assert_test_database_path } from '../core/test_paths.ts';
import {
  backend_io_is_test,
  SubprocessBackendProbeEnvironment,
} from '../conversation/snapshot/environment.ts';
import { http_get_usage } from '../conversation/backend_usage/http.ts';
import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import type { DriverHost } from '../drivers/core/host.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';

const [mode, argument] = process.argv.slice(2);
let result: unknown;
if (mode === 'guards') {
  delete process.env.VITEST;
  delete process.env.PYTEST_CURRENT_TEST;
  process.env.PLAN_TEST_MODE = argument;
  globalThis.fetch = async () => {
    throw new Error('Unexpected external HTTP');
  };
  assert.equal(testModeFromEnvironment(), true);
  assert.equal(loadConfig('/nonexistent-cross-area-config.yaml').test_mode, true);
  assert.equal(backend_io_is_test(), true);
  const refusal = /Production backend I\/O is unavailable in tests/;
  const children = new OneShotChildren();
  try {
    assert.throws(() => new SubprocessBackendProbeEnvironment(children), refusal);
  } finally {
    children.seal();
    await children.settled();
  }
  const door = Object.create(
    SubprocessBackendProbeEnvironment.prototype,
  ) as SubprocessBackendProbeEnvironment;
  assert.throws(() => door.executable_path('nonexistent-cross-area'), refusal);
  assert.throws(() => door.real_path('/synthetic'), refusal);
  assert.throws(() => door.modified_time_ms('/synthetic'), refusal);
  assert.throws(() => door.prefix_is_owned_and_writable('/synthetic'), refusal);
  await assert.rejects(door.run(['nonexistent-cross-area'], { timeout_seconds: 1 }), refusal);
  await assert.rejects(door.latest_released_version('synthetic', { timeout_seconds: 1 }), refusal);
  await assert.rejects(http_get_usage('https://synthetic.invalid', {}, 1), refusal);
  // A host that claims production still gets test instances: no sign-in or usage reads.
  const host = {
    test_mode: false,
    environment: {},
    database: {},
    child_processes: {},
    one_shot_children: {},
    worker_supervision: {},
    panels_server_url: 'http://127.0.0.1:1',
    worker_process_root: '/synthetic',
    now: () => new Date(0),
    executable_on_path: (name: string) => name,
  } as unknown as DriverHost;
  for (const driver of BUILT_IN_DRIVERS) {
    const instance = await driver.create(
      { instance_id: parse_backend_instance_id(driver.driver_kind), config: null },
      host,
      new AbortController().signal,
    );
    assert.equal(instance.sign_in, undefined);
    assert.equal(instance.allowance_usage, undefined);
  }
  assert.throws(
    () => assert_test_database_path('/outside-synthetic-root/database.sqlite'),
    /Tests can only use databases/,
  );
  assert.equal(assert_test_database_path(process.env.PLAN_DB_PATH!), process.env.PLAN_DB_PATH);
  result = true;
} else if (mode === 'host') {
  process.env.PLAN_TEST_MODE = argument;
  const admitted = new Error('cross-area-host-admitted');
  globalThis.__cross_area_admitted = admitted;
  const hooks = registerHooks({
    load(url, context, nextLoad) {
      if (url === new URL('../core/start.ts', import.meta.url).href)
        return {
          format: 'module',
          source: 'export function startServer() { throw globalThis.__cross_area_admitted; }',
          shortCircuit: true,
        };
      return nextLoad(url, context);
    },
  });
  try {
    await import('../test_host.ts');
    throw new Error('Host did not reach its launch boundary');
  } catch (error) {
    assert.equal(error, admitted);
    result = true;
  } finally {
    hooks.deregister();
    delete globalThis.__cross_area_admitted;
  }
} else throw new Error(`Unknown proof mode: ${mode}`);
process.stdout.write(JSON.stringify(result));

declare global {
  var __cross_area_admitted: Error | undefined;
}
