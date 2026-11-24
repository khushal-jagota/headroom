/** Enroll only the missing local machine of a previously installed home. */
import { randomBytes } from 'node:crypto';
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readlink,
  rename,
  rm,
  symlink,
  unlink,
  writeFile,
} from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { Database } from '../core/database/contracts.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { enroll_missing_home_machine } from '../machines/executor_credentials.ts';
import { credential_path, read_credential, write_credential } from '../executor/credential_file.ts';
import { atomic_write_json } from '../environments/updates/attempts.ts';
import { selected_release } from '../environments/updates/installer/selection.ts';
import type { HomeInstallDependencies, HomeInstallRequest } from './contracts.ts';
import { HOME_SERVICE_COMMAND_TIMEOUT_SECONDS } from './configuration.ts';

const absent = (error: unknown) => (error as NodeJS.ErrnoException)?.code === 'ENOENT';

export function has_home_credential(database: Database): boolean {
  return database.read((sql) =>
    Boolean(
      sql.get<{ credential_sha256: string | null }>(
        'SELECT credential_sha256 FROM machine_executor_credentials WHERE machine_id=?',
        [HOME_MACHINE_ID],
      )?.credential_sha256,
    ),
  );
}

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
  try {
    const record = JSON.parse(await readFile(pending, 'utf8'));
    if (record.machine_id !== HOME_MACHINE_ID || record.revision !== revision)
      throw new Error('An unfinished local machine enrollment belongs to a different release');
    resume = true;
  } catch (error) {
    if (!absent(error)) throw error;
  }
  if (!resume && has_home_credential(database)) return;
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
      JSON.parse(await readFile(join(runtime, 'manifest.json'), 'utf8')).app_sha !== revision
    )
      throw new Error('The local machine runtime differs from the home release');
  } catch (error) {
    if (!absent(error)) throw error;
    const temporary = await mkdtemp(join(machine, '.stage-'));
    try {
      await deps.prepare_machine(
        selected_release(root),
        join(temporary, 'runtime'),
        deps.execution,
      );
      await rename(join(temporary, 'runtime'), runtime);
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
    revision,
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
    const scratch = await mkdtemp(join(machine, '.job-'));
    let expected: string;
    try {
      const json = join(scratch, 'machine.json');
      await writeFile(json, file.content);
      expected = (
        await deps.command(
          ['/usr/bin/plutil', '-convert', 'xml1', '-o', '-', json],
          deps.execution,
          HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
        )
      ).stdout;
    } finally {
      await rm(scratch, { recursive: true, force: true });
    }
    await write_owned(file.path, expected, file.mode);
    await mkdir(dirname(link.path), { recursive: true });
    try {
      await symlink(link.target, link.path);
    } catch (error) {
      if (
        (error as NodeJS.ErrnoException).code !== 'EEXIST' ||
        (await readlink(link.path)) !== link.target
      )
        throw error;
    }
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
  await unlink(pending);
}

async function write_owned(path: string, content: string, mode: number): Promise<void> {
  try {
    await writeFile(path, content, { flag: 'wx', mode });
  } catch (error) {
    if (
      (error as NodeJS.ErrnoException).code !== 'EEXIST' ||
      !(await lstat(path)).isFile() ||
      (await readFile(path, 'utf8')) !== content
    )
      throw error;
  }
}
