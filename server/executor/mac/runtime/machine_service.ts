/** Discover the machine's own installed service and retain its native settings. */
import { basename, dirname, isAbsolute, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { userInfo } from 'node:os';
import { lstat, readFile, realpath, rm } from 'node:fs/promises';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { run_release_command } from '../../../environments/release/process.ts';
import { unit_value } from '../../../environments/release/units.ts';
import { replace_existing_text } from '../../../environments/updates/attempts.ts';
import { launchd_field } from '../../../environments/updates/darwin/logic/launchd.ts';
import { WORKER_LAUNCHER_MODE } from '../../../platform_runtime/darwin/launchd/configuration.ts';
import {
  darwin_worker_process_birth,
  MAC_RUNTIME_LAUNCHER_APP,
  mac_installation_paths,
  plist,
} from '../platform.ts';
import {
  LINUX_EXECUTOR_ENTRYPOINT,
  LINUX_EXECUTOR_SERVICE_NAME,
} from '../../../environments/linux_executor/configuration.ts';

import { MAC_RUNTIME_READY_TIMEOUT_SECONDS, MACHINE_WORKER_APP_ENV } from '../configuration.ts';
const linux_control = '/usr/bin/systemctl';
const mac_control = '/bin/launchctl';
const domain = () => 'gui/' + process.getuid!();
const unit_arg = (value: string) => unit_value(value.replaceAll('$', '$$$$'));

export type MachineService = {
  label: string;
  path: string;
  previous: string;
  candidate: string;
  installed_app?: string;
  previous_app?: string;
};

export async function machine_service_command(argv: string[], allow_nonzero = false) {
  const children = new ChildProcesses();
  try {
    return await run_release_command(
      argv,
      { timeout_seconds: 110, allow_nonzero },
      { child_processes: children, signal: new AbortController().signal, deadline: null },
    );
  } finally {
    children.seal();
    await children.settled();
  }
}

export async function installed_machine_service(
  root: string,
  runtime: string,
  args: string[],
): Promise<MachineService | null> {
  const command = machine_service_command;
  if (process.platform === 'linux') {
    const membership = (await readFile('/proc/self/cgroup', 'utf8'))
      .split('\n')
      .find((line) => line.startsWith('0::'))
      ?.slice(3);
    const label = basename(membership ?? '');
    if (label !== LINUX_EXECUTOR_SERVICE_NAME && !/^[\w.-]+\.machine\.service$/.test(label))
      return null;
    if (!membership?.includes('/user@' + process.getuid!() + '.service/'))
      throw new Error('The machine does not belong to the account systemd manager.');
    const group = (
      await command([linux_control, '--user', 'show', '-P', 'ControlGroup', label])
    ).stdout.trim();
    if (group !== membership)
      throw new Error('The loaded machine service belongs to a different cgroup.');
    const path = (
      await command([linux_control, '--user', 'show', '-P', 'FragmentPath', label])
    ).stdout.trim();
    if (!isAbsolute(path) || basename(path) !== label || !(await lstat(path)).isFile())
      throw new Error('Cannot locate the installed machine service.');
    const previous = await readFile(path, 'utf8');
    if (
      !previous.includes('Environment=' + unit_value('PLAN_EXECUTOR_ROOT=' + root)) ||
      [...previous.matchAll(/^ExecStart=.+$/gm)].length !== 1
    )
      throw new Error('The installed machine service has different startup inputs.');
    const start = [process.execPath, join(runtime, LINUX_EXECUTOR_ENTRYPOINT), ...args];
    const candidate = previous.replace(
      /^ExecStart=.+$/m,
      () => 'ExecStart=' + start.map(unit_arg).join(' '),
    );
    return { label, path, previous, candidate };
  }

  const joined = mac_installation_paths(userInfo().homedir);
  const at_joined_path = root === joined.state;
  const home_root = dirname(dirname(root));
  let label = joined.job;
  let path = joined.plist;
  if (!at_joined_path) {
    if (root !== join(home_root, 'machine/data')) return null;
    const home = JSON.parse(
      await readFile(join(home_root, 'data/mac-home-installation.json'), 'utf8'),
    );
    if (home.root !== home_root || !/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(home.label))
      throw new Error('Cannot identify the Mac home machine service.');
    label = home.label + '.machine';
    path = join(home_root, 'agents', label + '.plist');
  }
  const loaded = await command([mac_control, 'print', domain() + '/' + label], true);
  if (loaded.code !== 0 && at_joined_path && !(await file_exists(path))) return null;
  if (loaded.code !== 0) throw new Error('Cannot inspect the installed Mac machine service.');
  path = await realpath(path);
  if ((await realpath(launchd_field(loaded.stdout, 'path') ?? '')) !== path)
    throw new Error('The loaded Mac machine service has a different definition.');
  const previous = await readFile(path, 'utf8');
  const job = JSON.parse(
    (await command(['/usr/bin/plutil', '-convert', 'json', '-o', '-', path])).stdout,
  );
  if (job.Label !== label || job.EnvironmentVariables?.PLAN_EXECUTOR_ROOT !== root)
    throw new Error('The installed Mac machine service has different startup inputs.');
  const launcher = at_joined_path
    ? joined.app_executable
    : join(runtime, MAC_RUNTIME_LAUNCHER_APP, 'Contents/MacOS/Panels');
  const candidate = plist({
    ...job,
    ProgramArguments: [
      launcher,
      WORKER_LAUNCHER_MODE,
      join(runtime, 'node/bin/node'),
      join(runtime, LINUX_EXECUTOR_ENTRYPOINT),
      ...args,
    ],
    EnvironmentVariables: { ...job.EnvironmentVariables, [MACHINE_WORKER_APP_ENV]: launcher },
  });
  const previous_launcher = job.EnvironmentVariables[MACHINE_WORKER_APP_ENV];
  return {
    label,
    path,
    previous,
    candidate,
    ...(at_joined_path
      ? { installed_app: joined.app }
      : typeof previous_launcher === 'string'
        ? { previous_app: dirname(dirname(dirname(previous_launcher))) }
        : {}),
  };
}

async function file_exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
    throw error;
  }
}

export async function stop_machine_service(service: MachineService) {
  if (process.platform === 'linux') {
    await machine_service_command([linux_control, '--user', 'stop', service.label]);
  } else {
    const target = domain() + '/' + service.label;
    const loaded = await machine_service_command([mac_control, 'print', target], true);
    if (loaded.code !== 0 && /Could not find service/i.test(loaded.stderr)) return;
    if (
      loaded.code !== 0 ||
      (await realpath(launchd_field(loaded.stdout, 'path') ?? '')) !== service.path
    )
      throw new Error('Cannot identify the loaded Mac machine service for stop.');
    const pid = Number(launchd_field(loaded.stdout, 'pid'));
    const birth = pid > 0 ? darwin_worker_process_birth(pid) : null;
    await machine_service_command([mac_control, 'bootout', target]);
    const deadline = performance.now() + MAC_RUNTIME_READY_TIMEOUT_SECONDS * 1000;
    for (;;) {
      const label_state = await machine_service_command([mac_control, 'print', target], true);
      if (
        label_state.code !== 0 &&
        /Could not find service/i.test(label_state.stderr) &&
        (birth === null || darwin_worker_process_birth(pid) !== birth)
      )
        return;
      if (performance.now() >= deadline)
        throw new Error('The previous Mac machine job did not stop.');
      await delay(100);
    }
  }
}

export async function start_machine_service(service: MachineService) {
  if (process.platform === 'linux') {
    await machine_service_command([linux_control, '--user', 'daemon-reload']);
    await machine_service_command([linux_control, '--user', 'start', service.label]);
  } else await machine_service_command([mac_control, 'bootstrap', domain(), service.path]);
}

/** The coordinator belongs to its own job so a machine stop cannot terminate it. */
export async function start_machine_coordinator(
  service: MachineService,
  helper_label: string,
  args: string[],
) {
  if (process.platform === 'linux') {
    const path = join(dirname(service.path), helper_label);
    replace_existing_text(
      path,
      '[Unit]\nDescription=Panels machine update\n[Service]\nType=exec\n' +
        'ExecStart=' +
        args.map(unit_arg).join(' ') +
        '\nRestart=on-failure\nRestartSec=2\n[Install]\nWantedBy=default.target\n',
    );
    await machine_service_command([linux_control, '--user', 'enable', '--now', '--no-block', path]);
  } else {
    const path = join(userInfo().homedir, 'Library/LaunchAgents', helper_label + '.plist');
    replace_existing_text(
      path,
      plist({
        Label: helper_label,
        ProgramArguments: args,
        RunAtLoad: true,
        KeepAlive: { SuccessfulExit: false },
      }),
    );
    await machine_service_command([mac_control, 'bootstrap', domain(), path]);
  }
}

export async function remove_machine_coordinator(service: MachineService, helper_label: string) {
  if (process.platform === 'linux') {
    await machine_service_command([linux_control, '--user', 'disable', helper_label]);
    await rm(join(dirname(service.path), helper_label), { force: true });
    await machine_service_command([linux_control, '--user', 'daemon-reload']);
  } else {
    await rm(join(userInfo().homedir, 'Library/LaunchAgents', helper_label + '.plist'), {
      force: true,
    });
    await machine_service_command([mac_control, 'bootout', domain() + '/' + helper_label], true);
  }
}
