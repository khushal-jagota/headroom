/** A service receives manager environment properties, not its client's inherited map. */
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import {
  installer_unit,
  selected_installer_environment,
  supervised_installer,
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
    const text = installer_unit(command, join(root, 'marker'), environment);
    assert(text.includes('WantedBy=default.target'));
    assert(text.includes('Restart=on-abnormal'));
    assert(text.includes('ConditionPathExists=' + join(root, 'marker')));
    assert(text.includes('Environment=' + JSON.stringify('HOME=' + environment.HOME)));
    assert(
      text.includes('Environment=' + JSON.stringify('PLAN_DB_PATH=' + environment.PLAN_DB_PATH)),
    );
    assert(
      text.includes(
        'UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH VITEST PYTEST_CURRENT_TEST',
      ),
    );
    assert(!text.includes('must not travel'));
    assert(text.includes('ExecStart="/bin/sh" "-c"'));
    assert(text.includes(command.map((v) => JSON.stringify(v)).join(' ')));
    assert(
      installer_unit(['/path/$foo/%t'], join(root, 'marker'), {}).includes('"/path/$$foo/%%t"'),
    );
    assert.deepEqual(Object.keys(selected_installer_environment(environment)).sort(), [
      'DBUS_SESSION_BUS_ADDRESS',
      'HOME',
      'LANG',
      'LC_NUMERIC',
      'PLAN_DB_PATH',
      'PLAN_TEST_MODE',
    ]);
    assert.throws(
      () => installer_unit(['line\nbreak'], join(root, 'marker'), {}),
      /control character/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it('runs the exact retained command a second time only after failure and honors a second failure', () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-coordinator-retry-'));
  try {
    for (const [initial, final, count] of [
      [0, 1, 1],
      [2, 1, 1],
      [1, 0, 2],
      [1, 1, 2],
    ] as const) {
      const path = join(root, `argument with $ and spaces ${initial} ${final}`);
      const child = `const fs=require('node:fs'), path=process.argv[1]; const first=!fs.existsSync(path); fs.appendFileSync(path,'x'); process.exit(first?${initial}:${final})`;
      const [shell, ...args] = supervised_installer([process.execPath, '-e', child, path]);
      assert.equal(spawnSync(shell!, args).status, count === 1 ? initial : final);
      assert.equal(readFileSync(path, 'utf8'), 'x'.repeat(count));
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
