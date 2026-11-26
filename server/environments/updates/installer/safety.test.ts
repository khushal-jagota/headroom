import assert from 'node:assert/strict';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { it, vi } from 'vitest';
import { UpdateBarrier } from '../../update_barrier.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { create_installer_effects } from './commands.ts';
import { assert_installer_path } from './configuration.ts';
import { install } from './install.ts';
import { InstallationRecord } from './record.ts';
import type { InstallerRequest } from './contracts.ts';

it('keeps actual-test boundaries and stops before rename after a warning abort', async () => {
  // Existing algorithm parity cannot prove these safety boundaries. No native child starts.
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-safety-'));
  const from = 'a'.repeat(40),
    to = 'b'.repeat(40),
    owner = new ChildProcesses();
  const controller = new AbortController(),
    programming_error = new TypeError('unexpected owner bug');
  let spawns = 0;
  owner.spawn = (() => {
    spawns++;
    throw programming_error;
  }) as ChildProcesses['spawn'];
  const value: InstallerRequest = {
    current_root: root,
    database: join(root, 'custom.sqlite'),
    data_directory: join(root, 'records'),
    backup_directory: join(root, 'records'),
    attempt_id: 'safety',
    node_executable: process.execPath,
    service_unit: 'never-start.service',
    health_url: 'http://127.0.0.1:0',
    health_timeout_seconds: 1,
  };
  const execution = { child_processes: owner, signal: controller.signal, environment: {} };
  try {
    const outside = '/var/lib/panels-installer-guard-never-open';
    const link = join(root, 'outside-link');
    symlinkSync(outside, link);
    assert.throws(() => assert_installer_path(join(link, 'record'), {}), /Tests can only use/);
    assert.throws(() => assert_installer_path(root + '/../ambiguous', {}), /Tests can only use/);
    assert.throws(
      () => new InstallationRecord({ data_directory: outside, attempt_id: 'safety' }),
      /Tests can only use/,
    );
    assert.throws(
      () => create_installer_effects({ ...value, current_root: outside }, execution),
      /Tests can only use/,
    );
    mkdirSync(value.data_directory);
    mkdirSync(join(root, 'app'));
    mkdirSync(join(root, '.app-next-' + to));
    writeFileSync(join(root, 'app/manifest.json'), JSON.stringify({ app_sha: from }));
    writeFileSync(
      join(value.data_directory, 'update-attempt.json'),
      JSON.stringify({
        attempt_id: 'safety',
        state: 'installing',
        from_sha: from,
        to_sha: to,
        steps: {},
      }),
    );
    const real = create_installer_effects(value, execution);
    await assert.rejects(real.service('stop'), /unavailable in tests/);
    assert.equal(spawns, 0);
    await assert.rejects(real.capture(), (error) => error === programming_error);
    assert.equal(spawns, 1);
    const reason = new Error('warning callback aborted'),
      events: string[] = [];
    await assert.rejects(
      install(
        value,
        {
          async service(action) {
            events.push(action);
          },
          async capture() {
            return { kind: 'unavailable', detail: 'scripted capture failure' };
          },
          async snapshot() {
            throw new Error('this attempt requires no snapshot');
          },
          async schema_steps() {
            assert.fail('schema steps after warning abort');
          },
          async verify_snapshot() {
            throw new Error('this attempt staged no copy');
          },
          async restore() {
            assert.fail('restore after warning abort');
          },
          async healthy() {
            assert.fail('health after warning abort');
          },
        },
        { stderr: () => controller.abort(reason) },
        controller.signal,
      ),
      (error) => error === reason,
    );
    assert.deepEqual(events, ['stop']);
    assert.equal(JSON.parse(readFileSync(join(root, 'app/manifest.json'), 'utf8')).app_sha, from);
    assert(existsSync(join(root, '.app-next-' + to)));
    assert(!existsSync(join(root, '.app-previous-' + from)));
    const record = JSON.parse(
      readFileSync(join(value.data_directory, 'update-attempt.json'), 'utf8'),
    );
    assert.equal(record.state, 'installing');
    assert(!Object.hasOwn(record.steps, 'interruptions_captured'));
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
    rmSync(root, { recursive: true, force: true });
  }
});

it('refuses non-loopback health URLs despite a supplied normal-mode environment', async () => {
  // Existing path/service guards do not protect the health URL boundary. No network call runs.
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-health-guard-'));
  const owner = new ChildProcesses(),
    controller = new AbortController();
  const value: InstallerRequest = {
    current_root: root,
    database: join(root, 'database.sqlite'),
    data_directory: root,
    backup_directory: root,
    attempt_id: 'health-guard',
    node_executable: process.execPath,
    service_unit: 'never-start.service',
    health_url: 'https://example.com/health',
    health_timeout_seconds: 1,
  };
  const execution = {
    child_processes: owner,
    signal: controller.signal,
    environment: { PLAN_TEST_MODE: '0' },
  };
  try {
    assert.throws(() => create_installer_effects(value, execution), /loopback host in tests/);
    assert.doesNotThrow(() =>
      create_installer_effects({ ...value, health_url: 'http://127.0.0.1:0/health' }, execution),
    );
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
    rmSync(root, { recursive: true, force: true });
  }
});

it('stores the install decision before opening writes', async () => {
  // Observe the on-disk barrier at the instant the installer records its decision.
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-terminal-'));
  const original = InstallationRecord.prototype.record;
  try {
    for (const outcome of ['success', 'rollback', 'rollback_failed']) {
      const rollback = outcome !== 'success';
      const current = join(root, outcome);
      const data = join(current, 'records');
      const from = 'a'.repeat(40),
        to = 'b'.repeat(40);
      mkdirSync(data, { recursive: true });
      mkdirSync(join(current, 'app'));
      mkdirSync(join(current, '.app-next-' + to));
      writeFileSync(join(current, 'app/manifest.json'), JSON.stringify({ app_sha: from }));
      writeFileSync(
        join(data, 'update-attempt.json'),
        JSON.stringify({
          attempt_id: 'terminal',
          state: 'installing',
          from_sha: from,
          to_sha: to,
          steps: {},
        }),
      );
      const barrier = new UpdateBarrier(join(data, 'update-barrier.json'));
      barrier.freeze('terminal');
      const writes: string[] = [];
      let healthy_recorded = false;
      const record = vi.spyOn(InstallationRecord.prototype, 'record').mockImplementation(function (
        this: InstallationRecord,
        step,
        state,
        detail,
      ) {
        if (step === 'healthy' || step === 'rollback_healthy') healthy_recorded = true;
        if (state === 'done' || state === 'failed')
          assert(existsSync(barrier.path), 'new writes before commit');
        original.call(this, step, state, detail);
        if (state === 'done' || state === 'failed') writes.push(state);
      });
      try {
        const code = await install(
          {
            current_root: current,
            database: join(data, 'database.sqlite'),
            data_directory: data,
            backup_directory: data,
            attempt_id: 'terminal',
            node_executable: process.execPath,
            service_unit: 'never-start.service',
            health_url: 'http://127.0.0.1:0',
            health_timeout_seconds: 1,
          },
          {
            async service() {},
            async capture() {
              return { kind: 'returned', returncode: 0 };
            },
            async snapshot() {
              throw new Error('this attempt requires no snapshot');
            },
            async schema_steps() {},
            async verify_snapshot() {
              throw new Error('this attempt staged no copy');
            },
            async restore() {
              assert.fail('this scenario does not restore a snapshot');
            },
            async healthy(sha, trial) {
              assert.equal(trial, 'terminal');
              assert(existsSync(barrier.path), 'barrier released before health finished');
              return !rollback || (sha === from && outcome !== 'rollback_failed');
            },
          },
          { stderr() {} },
          new AbortController().signal,
        );
        assert.equal(code, rollback ? 1 : 0);
        assert.deepEqual(writes, [rollback ? 'failed' : 'done']);
        assert.equal(healthy_recorded, outcome !== 'rollback_failed');
        assert.equal(existsSync(barrier.path), outcome === 'rollback_failed');
      } finally {
        record.mockRestore();
      }
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
