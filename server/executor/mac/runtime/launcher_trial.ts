/** The old installed supervisor starts the candidate before it accepts any lease. A
 * launchd-owned transaction pairs that candidate with the signed app at the fixed path.
 * It outlives both runtimes and can restore the old bundle if the trial disappears. */
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { userInfo } from 'node:os';
import { lstat, open, readFile, rm, unlink } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { exchange_names, flock } from '../../../core/locks/flock.ts';
import { digest_machine_build } from '../../../environments/release/machine_identity.ts';
import { run_release_command } from '../../../environments/release/process.ts';
import { fsync_path } from '../../../core/fsync.ts';
import {
  atomic_write_json,
  replace_existing_text,
} from '../../../environments/updates/attempts.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { copy_environment } from '../../../core/environment.ts';
import { WORKER_LAUNCHER_MODE } from '../../../platform_runtime/darwin/launchd/configuration.ts';
import { MAC_RUNTIME_READY_TIMEOUT_SECONDS } from '../configuration.ts';
import { MAC_RUNTIME_LAUNCHER_APP, mac_installation_paths, plist } from '../platform.ts';

const helper = fileURLToPath(new URL('./launcher_trial_main.ts', import.meta.url));
type RecordFields = {
  request_id?: string;
  runtime_path?: string;
  old?: string;
  next?: string;
  error?: string;
  ready?: boolean;
  signing_requirement_changed?: boolean;
};

async function read(path: string): Promise<RecordFields | null> {
  try {
    return JSON.parse(await readFile(path, 'utf8')) as RecordFields;
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
function transaction(id: string, home = userInfo().homedir) {
  const install = mac_installation_paths(home);
  const key = createHash('sha256').update(id).digest('hex');
  const root = join(install.state, 'updates');
  // No value received on the wire is used as a path component.
  const label = install.job + '.update.' + key;
  return {
    install,
    root,
    label,
    domain: 'gui/' + process.getuid!(),
    plist_path: join(dirname(install.plist), label + '.plist'),
    staged: join(dirname(install.app), '.' + label + '.app'),
    journal: join(root, 'launcher-trial.json'),
    receipt: join(root, 'launcher-receipts', key + '.json'),
  };
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
async function probe(app: string) {
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

/** Called by the candidate, before it opens the machine connection. */
export async function admit_launcher(root: string, runtime: string): Promise<void> {
  // An unpackaged headless host has no installed app. It must never touch the
  // separately enrolled account's launcher, even if both run on this Mac.
  if (root !== mac_installation_paths(userInfo().homedir).state) return;
  const pending = await read(join(root, 'updates/pending.json'));
  if (!pending?.request_id || pending.runtime_path !== runtime)
    throw new Error('The Mac launcher trial does not match the pending runtime.');
  const id = pending.request_id,
    t = transaction(id);
  const next = join(runtime, MAC_RUNTIME_LAUNCHER_APP);
  if (
    (await read(t.journal)) === null &&
    (await digest_machine_build(next)) === (await digest_machine_build(t.install.app))
  ) {
    await probe(t.install.app);
    return;
  }
  const args = [process.execPath, helper, id];
  const definition = plist({
    Label: t.label,
    ProgramArguments: args,
    RunAtLoad: true,
    KeepAlive: { SuccessfulExit: false },
    StandardErrorPath: t.install.stderr,
  });
  replace_existing_text(t.plist_path, definition);
  const boot = await command(['/bin/launchctl', 'bootstrap', t.domain, t.plist_path], true);
  if (boot.code !== 0) await command(['/bin/launchctl', 'print', t.domain + '/' + t.label]);
  const deadline = performance.now() + MAC_RUNTIME_READY_TIMEOUT_SECONDS * 1000;
  while (performance.now() < deadline) {
    const receipt = await read(t.receipt);
    if (receipt?.error) throw new Error(receipt.error);
    if (receipt?.ready) return;
    await delay(150);
  }
  throw new Error('The signed Mac launcher did not finish its trial.');
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

/** This helper is started from a verified release. launchd restarts it after an
 * unexpected exit, including on login. Journal before swap, observe actual bundle
 * digests on restart, and leave the old bundle intact until selection commits. */
export async function supervise_launcher(
  id: string,
  options = {
    runtime: fileURLToPath(new URL('../../../../', import.meta.url)).replace(/\/$/, ''),
    home: userInfo().homedir,
    probe,
    unregister: async (domain: string, label: string) => {
      // All files are final before unloading our own job. launchd can terminate
      // this helper during bootout without losing a recovery step.
      await command(['/bin/launchctl', 'bootout', domain + '/' + label], true);
    },
    stage: async (source: string, destination: string, installed: string) => {
      await verify_bundle(installed);
      const display = await command(['/usr/bin/codesign', '-d', '--verbose=4', '-r-', installed]);
      const details = display.stdout + '\n' + display.stderr;
      const currentTeam = /^TeamIdentifier=(.+)$/m.exec(details)?.[1]?.trim();
      if (!currentTeam && !/^Signature=adhoc$/m.test(details))
        throw new Error('Cannot read the installed Mac signing team.');
      if (currentTeam && currentTeam !== 'not set') {
        const candidate = await command(['/usr/bin/codesign', '-dv', '--verbose=4', source]);
        if (currentTeam !== /^TeamIdentifier=(.+)$/m.exec(candidate.stderr)?.[1]?.trim())
          throw new Error('The Mac launcher signing team changed.');
      }
      // macOS uses the old app's designated requirement to recognize continued identity.
      const requirement = mac_designated_requirement(details);
      const equivalent = await command(
        ['/usr/bin/codesign', '--verify', '-R', '=' + requirement, source],
        true,
      );
      await command(['/usr/bin/ditto', source, destination]);
      return equivalent.code !== 0;
    },
  },
): Promise<void> {
  const { runtime } = options;
  const t = transaction(id, options.home);
  const next = join(runtime, MAC_RUNTIME_LAUNCHER_APP);
  const app = t.install.app;
  let lock: Awaited<ReturnType<typeof open>> | undefined;
  let intent: RecordFields | null = null;
  try {
    lock = await open(join(t.root, 'launcher.lock'), 'a+', 0o600);
    if (flock(lock.fd, 'exclusive') !== 0) throw new Error('Cannot lock Mac launcher activation.');
    intent = await read(t.journal);
    if (intent !== null && intent.request_id !== id)
      throw new Error('A previous Mac launcher trial still needs recovery.');
    const pending = () => read(join(t.root, 'pending.json'));
    const selected = () => read(join(t.root, 'selection.json'));
    const is_pending = async () => {
      const current = await pending();
      return current?.request_id === id && current.runtime_path === runtime;
    };
    // A restart can find either journaled bundle installed.
    async function select_launcher(record: RecordFields, target: 'old' | 'next') {
      const installed = await digest_machine_build(app);
      if (installed === record[target]) return;
      const other = target === 'next' ? record.old : record.next;
      if (installed !== other || (await digest_machine_build(t.staged)) !== record[target])
        throw new Error(
          target === 'next'
            ? 'Mac launcher files changed during activation.'
            : 'Cannot identify the previous signed Mac launcher for rollback.',
        );
      exchange_names(t.staged, app);
      await fsync_path(dirname(app));
    }
    let failure: string | undefined;
    if (await is_pending()) {
      try {
        if (!intent) {
          // The archive admission already binds the entire directory to the release.
          if (!(await lstat(app)).isDirectory())
            throw new Error('Expected an installed Mac app directory.');
          await rm(t.staged, { recursive: true, force: true });
          const signing_requirement_changed = await options.stage(next, t.staged, app);
          const prepared = {
            request_id: id,
            signing_requirement_changed,
            old: await digest_machine_build(app),
            next: await digest_machine_build(next),
          };
          if ((await digest_machine_build(t.staged)) !== prepared.next)
            throw new Error('The staged launcher differs from its machine archive.');
          await options.probe(t.staged);
          atomic_write_json(t.journal, prepared);
          intent = prepared;
        }
        await select_launcher(intent, 'next');
        await options.probe(app);
        atomic_write_json(t.receipt, {
          ready: true,
          signing_requirement_changed: intent.signing_requirement_changed === true,
        });
        while ((await selected())?.request_id !== id && (await is_pending())) await delay(200);
      } catch (error) {
        failure = error instanceof Error ? error.message : String(error);
      }
    }
    if (intent && (failure || (await selected())?.request_id !== id)) {
      await select_launcher(intent, 'old');
      failure ??= 'The Mac runtime trial reverted.';
    }
    if (failure) atomic_write_json(t.receipt, { error: failure });
    // Until the transaction is resolved, keep both bundles and the restartable job.
    if (intent) {
      await unlink(t.journal);
      await fsync_path(t.root);
    }
    await rm(t.staged, { recursive: true, force: true });
  } catch (cause) {
    const detail = cause instanceof Error ? cause.message : String(cause);
    const error = `${detail} Launcher: ${app}. Retained copy: ${t.staged}.`;
    if (intent?.request_id === id) atomic_write_json(t.journal, { ...intent, error });
    atomic_write_json(t.receipt, { error });
    throw new Error(error, { cause });
  } finally {
    try {
      await lock?.close();
      await rm(t.plist_path, { force: true });
      await fsync_path(dirname(t.plist_path));
    } finally {
      // A handled error is terminal. An external crash still leaves the job for launchd to restart.
      await options.unregister(t.domain, t.label);
    }
  }
}
