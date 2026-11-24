/** Enroll only the missing local machine of a previously installed home. */
import { randomBytes, randomUUID } from 'node:crypto';
import { link, lstat, mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { Database } from '../core/database/contracts.ts';
import { plist } from '../executor/mac/platform.ts';
import { ensure_home_login_link } from '../environments/updates/darwin/jobs.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import {
  enroll_missing_home_machine,
  has_home_credential,
} from '../machines/executor_credentials.ts';
import { credential_path, read_credential, write_credential } from '../executor/credential_file.ts';
import { atomic_write_json } from '../environments/updates/attempts.ts';
import { selected_release } from '../environments/updates/installer/selection.ts';
import type { HomeInstallDependencies, HomeInstallRequest } from './contracts.ts';
import { HOME_SERVICE_COMMAND_TIMEOUT_SECONDS } from './configuration.ts';

const absent = (error: unknown) => (error as NodeJS.ErrnoException)?.code === 'ENOENT';

export async function ensure_upgrade_machine(
  root: string,
  service_unit: string,
  port: number,
  revision: string,
  database: Database,
  deps: HomeInstallDependencies,
): Promise<void> {
  const pending = join(root, 'data/home-machine-enrollment.json');
  let resume = false;
  let machine_revision = revision;
  try {
    const record = JSON.parse(await readFile(pending, 'utf8'));
    if (
      record.machine_id !== HOME_MACHINE_ID ||
      typeof record.revision !== 'string' ||
      !/^[0-9a-f]{40}$/.test(record.revision)
    )
      throw new Error('An unfinished local machine enrollment belongs to a different release');
    if (record.complete === true && database.read(has_home_credential)) return;
    machine_revision = record.revision;
    resume = true;
  } catch (error) {
    if (!absent(error)) throw error;
  }
  if (!resume && database.read(has_home_credential)) return;
  const machine = join(root, 'machine');
  if (!resume) {
    try {
      await lstat(machine);
      throw new Error('The local machine folder already exists without an enrolled identity');
    } catch (error) {
      if (!absent(error)) throw error;
    }
    atomic_write_json(pending, { machine_id: HOME_MACHINE_ID, revision });
  }
  await mkdir(machine, { recursive: true, mode: 0o700 });
  const runtime = join(machine, 'runtime');
  try {
    if (
      !(await lstat(runtime)).isDirectory() ||
      JSON.parse(await readFile(join(runtime, 'manifest.json'), 'utf8')).app_sha !==
        machine_revision
    )
      throw new Error('The local machine runtime differs from the home release');
  } catch (error) {
    if (!absent(error)) throw error;
    machine_revision = revision;
    atomic_write_json(pending, { machine_id: HOME_MACHINE_ID, revision: machine_revision });
    const temporary = join(machine, '.runtime-installing');
    await rm(temporary, { recursive: true, force: true });
    try {
      await deps.prepare_machine(selected_release(root), temporary, deps.execution);
      await rename(temporary, runtime);
    } finally {
      await rm(temporary, { recursive: true, force: true });
    }
  }
  const state = join(machine, 'data');
  let credential = await read_credential(credential_path(state));
  if (!credential) {
    credential = randomBytes(32).toString('base64url');
    await write_credential(credential_path(state), credential);
  }
  const saved = credential;
  database.write((tx) => enroll_missing_home_machine(tx, deps.system, saved));
  atomic_write_json(join(state, 'identity.json'), { machine_id: HOME_MACHINE_ID });
  const service =
    deps.system === 'darwin'
      ? service_unit.replace(/^gui\/\d+\//, '')
      : service_unit.replace(/\.service$/, '');
  const request: HomeInstallRequest = {
    root,
    service,
    revision: machine_revision,
    port,
    isolated_providers: false,
    archive: '',
    archive_sha256: '',
  };
  const plan = await deps.service(request, deps);
  const name = service + (deps.system === 'darwin' ? '.machine.plist' : '.machine.service');
  const file = plan.files.find((file) => file.path.endsWith('/' + name));
  if (!file) throw new Error('Home installation has no local machine service definition');
  await mkdir(dirname(file.path), { recursive: true });
  if (file.format === 'plist-json') {
    const link = plan.links.find((link) => link.target === file.path);
    if (!link) throw new Error('Local Mac machine has no login job link');
    await write_owned(file.path, plist(JSON.parse(file.content)), file.mode);
    await mkdir(dirname(link.path), { recursive: true });
    await ensure_home_login_link(
      deps.account_home,
      service + '.machine',
      file.path,
      deps.execution,
    );
    const job = await deps.command(
      ['/bin/launchctl', 'print', `gui/${deps.uid}/${service}.machine`],
      deps.execution,
      HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
      true,
    );
    if (job.code !== 0) await plan.start_machine(deps.execution);
    else if (!job.stdout.split('\n').some((line) => line.trim() === 'path = ' + file.path))
      throw new Error('Loaded Mac machine belongs to a different installation');
  } else {
    await write_owned(file.path, file.content, file.mode);
    await deps.command(
      ['systemctl', '--user', 'daemon-reload'],
      deps.execution,
      HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
    );
    await plan.start_machine(deps.execution);
  }
  // The credential stays owned after a database rollback. A later upgrade reuses
  // it and the baseline machine service even if it targets a different release.
  atomic_write_json(pending, {
    machine_id: HOME_MACHINE_ID,
    revision: machine_revision,
    complete: true,
  });
}

async function write_owned(path: string, content: string, mode: number): Promise<void> {
  // An interrupted write must never leave a truncated OS service file at its final name.
  const temporary = path + '.' + randomUUID();
  await writeFile(temporary, content, { flag: 'wx', mode, flush: true });
  try {
    await link(temporary, path);
  } catch (error) {
    if (
      (error as NodeJS.ErrnoException).code !== 'EEXIST' ||
      !(await lstat(path)).isFile() ||
      (await readFile(path, 'utf8')) !== content
    )
      throw error;
  } finally {
    await rm(temporary, { force: true });
  }
}
