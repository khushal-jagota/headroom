/** Withhold the disposable Mac home's enrolled machine while its candidate is on trial. */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, realpath } from 'node:fs/promises';
import { join, sep } from 'node:path';
import { promisify } from 'node:util';
import { HOME_MACHINE_ID } from '../../../execution_identity.ts';
import { credential_path, read_credential } from '../../../executor/credential_file.ts';
import { authenticate_loaded_job } from '../../updates/darwin/logic/launchd.ts';
import type { DarwinHomeJob } from '../../updates/darwin/contracts.ts';
import { accepted_lease } from '../../linux_executor/logic/lease.ts';

const execute = promisify(execFile);
export async function published_mac_home_fault(base: string, service: string, predecessor: string) {
  if (process.platform !== 'darwin') return null;
  const inputs = JSON.parse(
    await readFile(join(base, 'evidence/qualification-inputs.json'), 'utf8'),
  );
  if (inputs.predecessor.native_build_run) return null;
  assert.ok(Number.isSafeInteger(inputs.predecessor.release_id));
  assert.equal(inputs.predecessor.sha, predecessor);
  assert.equal(process.env.RUNNER_ENVIRONMENT, 'github-hosted');
  assert.ok((await realpath(base)).startsWith((await realpath(process.env.RUNNER_TEMP!)) + sep));
  assert.equal(service, 'com.panels.first-install-proof');
  const root = join(base, 'installed');
  const machine = join(root, 'machine');
  const state = join(machine, 'data');
  const identity = join(state, 'identity.json');
  const credential = await read_credential(credential_path(state));
  assert.ok(credential);
  assert.equal(JSON.parse(await readFile(identity, 'utf8')).machine_id, HOME_MACHINE_ID);
  const label = service + '.machine';
  const plist = join(root, 'agents', label + '.plist');
  const definition = await readFile(plist);
  const job: DarwinHomeJob = JSON.parse(
    (await execute('/usr/bin/plutil', ['-convert', 'json', '-o', '-', plist])).stdout,
  );
  assert.equal(job.Label, label);
  assert.equal(job.WorkingDirectory, machine);
  assert.equal(job.EnvironmentVariables.PLAN_EXECUTOR_ROOT, state);
  assert.ok(job.ProgramArguments.includes(identity));
  const domain = 'gui/' + process.getuid!();
  const target = domain + '/' + label;
  const launchctl = (...args: string[]) => execute('/bin/launchctl', args, { timeout: 30_000 });
  const inspect = async () =>
    authenticate_loaded_job((await launchctl('print', target)).stdout, plist, job, [
      'PLAN_EXECUTOR_ROOT',
    ]);
  const health = async () =>
    JSON.parse(
      (
        await execute(join(root, 'app/bin/panels'), ['executor', 'health'], {
          env: { ...process.env, PLAN_EXECUTOR_ROOT: state },
          timeout: 5_000,
        })
      ).stdout,
    );
  const first = await health();
  const protocol = first.identity.protocol_version;
  assert.ok(Number.isSafeInteger(protocol) && protocol > 0);
  assert.ok(accepted_lease(first, predecessor, 0, protocol), 'Home machine has no accepted lease');
  let restoreTime = 0;
  let stopped = false;
  return {
    label,
    async stop() {
      assert.ok(await inspect(), 'Expected the authenticated home machine job to be running');
      await launchctl('bootout', target);
      stopped = true;
    },
    async restore() {
      if (!stopped) return;
      assert.deepEqual(await readFile(plist), definition);
      assert.ok(
        (await read_credential(credential_path(state))) === credential,
        'Home enrollment credential changed',
      );
      restoreTime = Date.now();
      await launchctl('bootstrap', domain, plist);
      stopped = false;
    },
    async reconnected() {
      if (!(await inspect())) return false;
      const current = await health().catch(() => null);
      return accepted_lease(current, predecessor, restoreTime, protocol);
    },
  };
}
