/** A service receives manager environment properties, not its client's inherited map. */
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { it } from 'vitest';
import {
  installer_transient_command,
  selected_installer_environment,
} from './installation_command.ts';

it('keeps literal paths and production variables without forwarding test-runner markers', () => {
  // No existing command proof crosses the detached service environment boundary.
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-command-'));
  try {
    const environment = {
      HOME: join(root, 'literal $HOME'),
      PLAN_DB_PATH: join(root, 'custom database.sqlite'),
      PLAN_TEST_MODE: '1',
      VITEST: 'true',
      PYTEST_CURRENT_TEST: 'retained',
      LANG: 'C.UTF-8',
      DBUS_SESSION_BUS_ADDRESS: 'unix:path=' + join(root, 'bus'),
      LC_NUMERIC: 'C',
      GITHUB_TOKEN: 'must not travel',
      NODE_OPTIONS: 'must not travel',
      LD_LIBRARY_PATH: 'must not travel',
    };
    const command = ['/external/node', join(root, 'copy/main.ts'), join(root, 'request.json')];
    const argv = installer_transient_command('attempt-1', command, environment);
    assert.deepEqual(argv.slice(0, 7), [
      'systemd-run',
      '--user',
      '--collect',
      '--quiet',
      '--unit=panels-update-attempt-1',
      '--expand-environment=no',
      '--property=UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH VITEST PYTEST_CURRENT_TEST',
    ]);
    assert(argv.includes('--setenv=HOME=' + environment.HOME));
    assert(argv.includes('--setenv=PLAN_DB_PATH=' + environment.PLAN_DB_PATH));
    assert(!argv.some((part) => part.startsWith('--setenv=VITEST=')));
    assert(!argv.some((part) => part.startsWith('--setenv=PYTEST_CURRENT_TEST=')));
    assert.deepEqual(argv.slice(-4), ['--', ...command]);
    assert(!argv.some((part) => part.includes('must not travel')));
    assert.deepEqual(Object.keys(selected_installer_environment(environment)).sort(), [
      'DBUS_SESSION_BUS_ADDRESS',
      'HOME',
      'LANG',
      'LC_NUMERIC',
      'PLAN_DB_PATH',
      'PLAN_TEST_MODE',
    ]);
    assert.throws(
      () => installer_transient_command('attempt\n', command, environment),
      /invalid installer attempt/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
