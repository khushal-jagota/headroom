/** Signed launcher identity and recovery of the preceding release’s launcher trial. */
import { join } from 'node:path';
import { userInfo } from 'node:os';
import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { run_release_command } from '../../../environments/release/process.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { copy_environment } from '../../../core/environment.ts';
import { WORKER_LAUNCHER_MODE } from '../../../platform_runtime/darwin/launchd/configuration.ts';
import { MAC_RUNTIME_READY_TIMEOUT_SECONDS } from '../configuration.ts';
import { mac_installation_paths } from '../platform.ts';

async function read(path: string): Promise<{ error?: string } | null> {
  try {
    return JSON.parse(await readFile(path, 'utf8')) as { error?: string };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}
async function command(argv: string[], allow_nonzero = false) {
  const owner = new ChildProcesses();
  try {
    return await run_release_command(
      argv,
      { timeout_seconds: 15, allow_nonzero },
      { child_processes: owner, signal: new AbortController().signal, deadline: null },
    );
  } finally {
    owner.seal();
    await owner.settled();
  }
}
function verify_bundle(app: string) {
  return command([
    '/usr/bin/codesign',
    '--verify',
    '--strict',
    '-R',
    '=identifier "com.panels.executor.typescript.app"',
    app,
  ]);
}
export async function probe_signed_launcher(app: string) {
  await verify_bundle(app);
  const result = await command([
    join(app, 'Contents/MacOS/Panels'),
    WORKER_LAUNCHER_MODE,
    process.execPath,
    '-p',
    '"panels-worker-ready"',
  ]);
  if (result.stdout.trim() !== 'panels-worker-ready')
    throw new Error('The installed Mac app did not start a Worker.');
}

export class MacLauncherRecoveryBlocked extends Error {}

export async function wait_for_launcher_recovery(root: string): Promise<void> {
  const journal = join(root, 'updates/launcher-trial.json');
  const deadline = performance.now() + MAC_RUNTIME_READY_TIMEOUT_SECONDS * 1000;
  for (;;) {
    const trial = await read(journal);
    if (!trial) return;
    if (trial.error) throw new MacLauncherRecoveryBlocked(trial.error);
    if (performance.now() >= deadline)
      throw new Error('Still restoring the signed Mac app before starting its machine.');
    await delay(100);
  }
}

/** Preserve the supervisor's tracked PID across the handoff: exec the installed
 * requester in its signed Worker mode, which starts this exact Node and forwards
 * signals. The older launchd job can remain loaded while the new runtime and all
 * its work start under the new signed application. */
export async function enter_signed_runtime(
  root: string,
  environment: Readonly<NodeJS.ProcessEnv>,
  prepare: () => Promise<void>,
): Promise<void> {
  const installed = mac_installation_paths(userInfo().homedir);
  if (root !== installed.state || environment.PANELS_MACHINE_REQUESTER_PID === String(process.ppid))
    return;
  if (!process.execve)
    throw new Error('The bundled Node cannot start the signed machine requester.');
  await prepare();
  const env = copy_environment(environment);
  env.PANELS_MACHINE_REQUESTER_PID = String(process.pid);
  process.execve(
    installed.app_executable,
    [
      installed.app_executable,
      WORKER_LAUNCHER_MODE,
      process.execPath,
      ...process.execArgv,
      ...process.argv.slice(1),
    ],
    env,
  );
}

/** codesign reports an implicitly generated requirement as a source comment.
 * Ad-hoc signatures without a displayed requirement identify this exact CodeDirectory. */
export function mac_designated_requirement(display: string): string {
  const explicit = /^[ \t]*designated[ \t]*=>[ \t]*(\S[^\r\n]*)$/m.exec(display);
  const implied = /^[ \t]*#[ \t]*designated[ \t]*=>[ \t]*(\S[^\r\n]*)$/m.exec(display);
  const requirement = explicit ?? implied;
  if (requirement) return requirement[1]!.trim();
  const hash = /^CDHash=([a-f0-9]{40})$/im.exec(display)?.[1];
  if (/^Signature=adhoc$/m.test(display) && hash) return 'cdhash H"' + hash + '"';
  throw new Error('Cannot read the installed Mac signing requirement.');
}

/** Permit the published signing transition once. Later identities must satisfy
 * the installed designated requirement. Qualification can use two ad-hoc apps. */
export async function check_launcher_identity(source: string, installed: string): Promise<boolean> {
  await verify_bundle(installed);
  const signature = async (app: string) => {
    const output = await command(['/usr/bin/codesign', '-d', '--verbose=4', '-r-', app]);
    return output.stdout + '\n' + output.stderr;
  };
  const current = await signature(installed);
  const candidate = await signature(source);
  const team = (details: string) => /^TeamIdentifier=(.+)$/m.exec(details)?.[1]?.trim();
  if (team(current) !== team(candidate)) throw new Error('The Mac launcher signing team changed.');
  const equivalent = await command(
    ['/usr/bin/codesign', '--verify', '-R', '=' + mac_designated_requirement(current), source],
    true,
  );
  const changed = equivalent.code !== 0;
  const test_signatures =
    /^Signature=adhoc$/m.test(current) && /^Signature=adhoc$/m.test(candidate);
  if (changed && !test_signatures) {
    const transition =
      /^Authority=Apple Development:/m.test(current) &&
      (
        await command(
          [
            '/usr/bin/codesign',
            '--verify',
            '-R',
            '=anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists',
            source,
          ],
          true,
        )
      ).code === 0;
    if (!transition) throw new Error('The Mac launcher designated requirement changed.');
  }
  return changed && !test_signatures;
}

export async function stage_signed_launcher(
  source: string,
  destination: string,
  installed: string,
): Promise<boolean> {
  const changed = await check_launcher_identity(source, installed);
  await command(['/usr/bin/ditto', source, destination]);
  return changed;
}
