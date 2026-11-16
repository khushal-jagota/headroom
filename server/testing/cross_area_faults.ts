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
import { production_backend_providers } from '../conversation/snapshot/production.ts';
import type { CodexProviderContext } from '../conversation/backends/codex_app_server/providers.ts';
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
  assert.throws(() => door.configured_executable_path('hermes'), refusal);
  assert.throws(() => door.real_path('/synthetic'), refusal);
  assert.throws(() => door.prefix_is_owned_and_writable('/synthetic'), refusal);
  await assert.rejects(door.run(['nonexistent-cross-area'], { timeout_seconds: 1 }), refusal);
  await assert.rejects(door.latest_released_version('synthetic', { timeout_seconds: 1 }), refusal);
  await assert.rejects(http_get_usage('https://synthetic.invalid', {}, 1), refusal);
  const unusedContext = new Proxy({} as CodexProviderContext, {
    get() {
      throw new Error('Test mode must not read production provider context');
    },
  });
  assert.equal(production_backend_providers(false, unusedContext).claude, null);
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
