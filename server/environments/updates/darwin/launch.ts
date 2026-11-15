/** The launchd installer job outlives the application and uses only its prepared copy. */
import { fstatSync, lstatSync, realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { is_record } from '../../../core/records.ts';
import { close_native_descriptor, directory_open_flags, mkdir_relative, open_directory_descriptor, open_relative } from '../../../executor/mac/platform.ts';
import { installer_attempt_id } from '../../release/installer_bundle.ts';
import type { ReleaseExecution } from '../../release/contracts.ts';
import { read_owned_home_file } from './identity.ts';
import { create_darwin_home_io, DarwinHomeService } from './service.ts';
import { read_home_job, write_home_job } from './jobs.ts';
import { DARWIN_HOME_COMMAND_SECONDS, DARWIN_HOME_OBSERVATION_SECONDS } from './configuration.ts';
import { launchd_field } from './logic/launchd.ts';
import type { DarwinHomeIO } from './contracts.ts';

export async function current_darwin_home(application_root: string, node: string, execution: ReleaseExecution, environment: Readonly<NodeJS.ProcessEnv>, io?: DarwinHomeIO): Promise<DarwinHomeService> {
  const config = environment.PLAN_CONFIG_PATH;
  if (!config) throw new Error('Mac updates require an installed home identity');
  const root = dirname(dirname(realpathSync(config))), selected_io = io ?? create_darwin_home_io(execution, environment);
  if (realpathSync(application_root) !== join(root, 'app')) throw new Error('Mac update application differs from its home');
  const value: unknown = JSON.parse(read_owned_home_file(join(root, 'data/mac-home-installation.json'), selected_io.uid).toString('utf8'));
  if (!is_record(value) || typeof value.label !== 'string') throw new Error('Mac home identity has no service label');
  const service = new DarwinHomeService(root, `gui/${selected_io.uid}/${value.label}`, node, selected_io);
  await service.validate_running_home(process.ppid);
  return service;
}
export async function launch_darwin_installer(service: DarwinHomeService, attempt: string, command: readonly string[]): Promise<void> {
  const attempt_id = installer_attempt_id(attempt), { identity, io } = service;
  await service.validate_running_home(process.ppid);
  const directory = join(identity.root, 'data/update-agent');
  const copy = join(identity.root, 'data', 'update-installer-' + attempt_id);
  const expected = [identity.installer_node, join(copy, 'runtime/server/environments/updates/installer/main.ts'), join(copy, 'request.json')];
  if (command.length !== expected.length || command.some((value, index) => value !== expected[index]))
    throw new Error('Installer command differs from its independent prepared copy');
  const parent = open_directory_descriptor(join(identity.root, 'data'));
  try {
    try { mkdir_relative(parent, 'update-agent', 0o700); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error; }
    const held = open_relative(parent, 'update-agent', directory_open_flags);
    try {
      const info = fstatSync(held);
      if (info.uid !== io.uid || (info.mode & 0o7777) !== 0o700) throw new Error('Mac update job folder must be private and owned');
    } finally { close_native_descriptor(held); }
  } finally { close_native_descriptor(parent); }
  const path = join(directory, 'installer.plist');
  let previous: Buffer | undefined;
  try { lstatSync(path); previous = read_owned_home_file(path, io.uid); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  if (previous) {
    const job = await read_home_job(path, io), prefix = identity.label + '.update-';
    if (!job.Label.startsWith(prefix)) throw new Error('Previous installer job identity differs from this home');
    installer_attempt_id(job.Label.slice(prefix.length));
    const target = `${identity.domain}/${job.Label}`;
    const loaded = await io.command(['/bin/launchctl', 'print', target], io.execution, DARWIN_HOME_OBSERVATION_SECONDS, true);
    if (loaded.code === 0) {
      if (launchd_field(loaded.stdout, 'pid') !== null) throw new Error('The previous installer still runs');
      if (launchd_field(loaded.stdout, 'path') !== path) throw new Error('Previous installer job has a foreign plist');
      await io.command(['/bin/launchctl', 'bootout', target], io.execution, DARWIN_HOME_COMMAND_SECONDS);
    } else if (!loaded.stderr.includes('Could not find service')) throw new Error('Previous installer job status is uncertain');
  }
  await write_home_job(path, {
    Label: identity.label + '.update-' + attempt_id, ProgramArguments: [...command], WorkingDirectory: join(identity.root, 'data'),
    RunAtLoad: true, LimitLoadToSessionType: 'Aqua', AbandonProcessGroup: false,
    EnvironmentVariables: { HOME: io.account_home, PATH: '/opt/homebrew/bin:/usr/bin:/bin' },
    StandardOutPath: join(identity.root, 'logs/update.stdout.log'), StandardErrorPath: join(identity.root, 'logs/update.stderr.log'),
  }, io, previous);
  await io.command(['/bin/launchctl', 'bootstrap', identity.domain, path], io.execution, DARWIN_HOME_COMMAND_SECONDS);
}
