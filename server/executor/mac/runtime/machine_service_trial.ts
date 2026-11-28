/** A verified runtime enters here through the predecessor's ordinary pending selection.
 * The native update job remains alive while the machine service stops and restarts. */
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { open, readFile, rm } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { exchange_names, flock } from '../../../core/locks/flock.ts';
import { fsync_path } from '../../../core/fsync.ts';
import { digest_machine_build } from '../../../environments/release/machine_identity.ts';
import {
  atomic_write_json,
  replace_existing_text,
} from '../../../environments/updates/attempts.ts';
import type { MacRuntimeSelection } from '../../wire.generated.ts';
import { MACHINE_SERVICE_TRIAL_FILE, MAC_RUNTIME_READY_TIMEOUT_SECONDS } from '../configuration.ts';
import { WORKER_LAUNCHER_MODE } from '../../../platform_runtime/darwin/launchd/configuration.ts';
import { MAC_RUNTIME_LAUNCHER_APP } from '../platform.ts';
import {
  installed_machine_service,
  start_machine_coordinator,
  stop_machine_service,
  start_machine_service,
  remove_machine_coordinator,
  type MachineService,
} from './machine_service.ts';

type Launcher = {
  staged: string;
  previous: string;
  candidate: string;
  signing_requirement_changed: boolean;
};

type ServiceTrial = {
  service: MachineService;
  helper: string;
  pending: MacRuntimeSelection;
  previous: MacRuntimeSelection | null;
  launcher?: Launcher;
  started: boolean;
};

const service_trial_path = (updates: string) => join(updates, MACHINE_SERVICE_TRIAL_FILE);

async function read<T>(path: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(path, 'utf8')) as T;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}

async function remove_record(path: string) {
  await rm(path, { force: true });
  await fsync_path(dirname(path));
}

/** The real predecessor does not know about service trials. Its candidate can
 * hand the request to the native update job before it accepts any lease. */
export async function bridge_machine_service(
  root: string,
  runtime: string,
  args: string[],
): Promise<void> {
  const service = await installed_machine_service(root, runtime, args);
  if (!service) return;
  const updates = join(root, 'updates');
  const pending = await read<MacRuntimeSelection>(join(updates, 'pending.json'));
  if (!pending || pending.runtime_path !== runtime)
    throw new Error('The machine service trial does not match the pending runtime.');
  const path = service_trial_path(updates);
  const held = await open(path + '.lock', 'a+', 0o600);
  try {
    if (flock(held.fd, 'try-exclusive') !== 0)
      throw new Error('The previous machine service update still needs recovery.');
    const existing = await read<ServiceTrial>(path);
    if (existing?.started)
      throw new Error('The previous machine service update still needs recovery.');
    const key = createHash('sha256').update(pending.request_id).digest('hex').slice(0, 24);
    const helper =
      'panels-machine-update-' + key + (process.platform === 'linux' ? '.service' : '');
    let launcher: Launcher | undefined;
    if (process.platform === 'darwin') {
      const { check_launcher_identity, probe_signed_launcher, stage_signed_launcher } =
        await import('./launcher_trial.ts');
      const source = join(runtime, MAC_RUNTIME_LAUNCHER_APP);
      if (service.previous_app) await check_launcher_identity(source, service.previous_app);
      if (!service.installed_app) await probe_signed_launcher(source);
      if (service.installed_app) {
        const app = service.installed_app;
        const staged = join(dirname(app), '.' + helper + '.app');
        await rm(staged, { force: true, recursive: true });
        const changed = await stage_signed_launcher(source, staged, app);
        const candidate = await digest_machine_build(source);
        if ((await digest_machine_build(staged)) !== candidate)
          throw new Error('The staged Mac launcher differs from the verified package.');
        await probe_signed_launcher(staged);
        launcher = {
          staged,
          previous: await digest_machine_build(app),
          candidate,
          signing_requirement_changed: changed,
        };
      }
    }
    const trial = {
      service,
      helper,
      pending,
      launcher,
      started: false,
      previous: await read<MacRuntimeSelection>(join(updates, 'selection.json')),
    } satisfies ServiceTrial;
    atomic_write_json(path, trial);
    // On Mac the signed Panels app starts Node, so macOS names Panels and not Node.js Foundation.
    await start_machine_coordinator(service, helper, [
      ...(service.launcher ? [service.launcher, WORKER_LAUNCHER_MODE] : []),
      process.execPath,
      fileURLToPath(new URL('./machine_service_trial_main.ts', import.meta.url)),
      updates,
      pending.request_id,
    ]);
  } finally {
    await held.close();
  }
  // Successful registration stops this process as part of the old machine service.
  // A failed registration or service stop lets the old supervisor time out.
  await delay(MAC_RUNTIME_READY_TIMEOUT_SECONDS * 1000);
  throw new Error('The old machine service did not stop for its replacement.');
}

export async function supervise_machine_service(
  updates: string,
  request_id: string,
): Promise<void> {
  const path = service_trial_path(updates);
  const held = await open(path + '.lock', 'a+', 0o600);
  try {
    if (flock(held.fd, 'exclusive') !== 0) throw new Error('Cannot lock machine update recovery.');
    await apply_machine_service(updates, path, request_id);
  } finally {
    await held.close();
  }
}

async function apply_machine_service(updates: string, path: string, request_id: string) {
  const trial = await read<ServiceTrial>(path);
  if (trial?.pending.request_id !== request_id) return;
  const { service, launcher, pending } = trial;
  const id = pending.request_id;
  const selection_path = join(updates, 'selection.json');
  const pending_path = join(updates, 'pending.json');
  const failure_path = join(updates, 'failure.json');
  const selected = async () => (await read<MacRuntimeSelection>(selection_path))?.request_id === id;
  const wanted = async () => (await read<MacRuntimeSelection>(pending_path))?.request_id === id;
  const receipt = join(
    updates,
    'launcher-receipts',
    createHash('sha256').update(id).digest('hex') + '.json',
  );

  async function use_definition(target: 'previous' | 'candidate') {
    const previous = target === 'candidate' ? 'previous' : 'candidate';
    const current = await readFile(service.path, 'utf8');
    if (current !== service[target]) {
      if (current !== service[previous])
        throw new Error('The machine service changed during its update.');
      replace_existing_text(service.path, service[target]);
    }
    if (!launcher || !service.installed_app) return;
    const installed = await digest_machine_build(service.installed_app);
    if (installed === launcher[target]) return;
    if (
      installed !== launcher[previous] ||
      (await digest_machine_build(launcher.staged)) !== launcher[target]
    )
      throw new Error('The installed Mac launcher changed during its update.');
    exchange_names(launcher.staged, service.installed_app);
    await fsync_path(dirname(service.installed_app));
  }

  let failure = 'The replacement machine service did not confirm its connection.';
  if (!trial.started && (await wanted())) {
    atomic_write_json(path, { ...trial, started: true });
    trial.started = true;
    try {
      await stop_machine_service(service);
      await use_definition('candidate');
      // The predecessor can clear its own pending intent during shutdown.
      if ((await read<MacRuntimeSelection>(failure_path))?.request_id === id)
        await remove_record(failure_path);
      atomic_write_json(pending_path, pending);
      if (launcher)
        atomic_write_json(receipt, {
          ready: true,
          signing_requirement_changed: launcher.signing_requirement_changed,
        });
      await start_machine_service(service);
      const deadline = performance.now() + MAC_RUNTIME_READY_TIMEOUT_SECONDS * 1000;
      while (!(await selected()) && (await wanted()) && performance.now() < deadline)
        await delay(200);
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    }
  }

  if (trial.started && !(await selected())) {
    await stop_machine_service(service);
    // The supervisor can settle just as a deadline expires. Once it settles,
    // its process was allowed to resume work, so keep that decision.
    if (await selected()) {
      await start_machine_service(service);
    } else {
      await use_definition('previous');
      if (trial.previous) atomic_write_json(selection_path, trial.previous);
      else await remove_record(selection_path);
      await remove_record(pending_path);
      atomic_write_json(failure_path, { request_id: id, phase: 'reverted', detail: failure });
      await start_machine_service(service);
    }
  }
  if (launcher) await rm(launcher.staged, { force: true, recursive: true });
  await remove_record(path);
  await remove_machine_coordinator(service, trial.helper);
}
