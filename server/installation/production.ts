/** Native effects share the install invocation's canonical child owner. */
import { homedir } from 'node:os';
import { realpath } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { extract_app_archive } from '../environments/release/archive.ts';
import { admit_runtime } from '../environments/release/admission.ts';
import { run_release_command, assert_release_execution } from '../environments/release/process.ts';
import { MAC_HOME } from '../environments/release/contracts.ts';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { healthy } from '../environments/updates/installer/health.ts';
import { installer_test_mode } from '../environments/updates/installer/configuration.ts';
import { HOME_HEALTH_TIMEOUT_SECONDS } from './configuration.ts';
import { create_linux_home_service, remove_linux_home_service } from './linux.ts';
import { create_darwin_home_service, remove_darwin_home_service } from './darwin.ts';
import type { HomeInstallDependencies, HomeCommand, HomeMoveDependencies } from './contracts.ts';
import { prepare_first_machine, wait_for_first_machine } from './machine.ts';
import { check_moved_home_database, initialize_candidate_home_database } from './database.ts';

export async function production_home_dependencies(
  execution: ReleaseExecution,
  environment: Readonly<NodeJS.ProcessEnv> = process.env,
): Promise<HomeInstallDependencies> {
  const system = process.platform,
    architecture = process.arch === 'x64' ? 'x86_64' : process.arch;
  if (!(
    (system === 'linux' && architecture === 'x86_64') ||
    (system === 'darwin' && architecture === 'arm64')
  ))
    throw new Error('Use an Apple Silicon Mac or a Linux x86_64 machine');
  const account_home = await realpath(environment.HOME || homedir()),
    uid = process.getuid?.();
  if (uid === undefined) throw new Error('Home installation requires a Unix account');
  const command_environment: NodeJS.ProcessEnv = {
    HOME: account_home,
    PATH: environment.PATH || '/usr/local/bin:/usr/bin:/bin',
    LANG: 'C.UTF-8',
    ...(environment.XDG_RUNTIME_DIR ? { XDG_RUNTIME_DIR: environment.XDG_RUNTIME_DIR } : {}),
    ...(environment.DBUS_SESSION_BUS_ADDRESS
      ? { DBUS_SESSION_BUS_ADDRESS: environment.DBUS_SESSION_BUS_ADDRESS }
      : {}),
  };
  const command: HomeCommand = (argv, scope, timeout_seconds, allow_nonzero = false) => {
    if (installer_test_mode(environment) && ['systemctl', 'launchctl'].includes(basename(argv[0]!)))
      throw new Error('Home service actions are unavailable in tests');
    return run_release_command(
      argv,
      { timeout_seconds, allow_nonzero, environment: command_environment },
      scope,
    );
  };
  const node_executable = await realpath(process.execPath);
  return {
    execution,
    environment,
    system,
    architecture,
    account_home,
    node_executable,
    uid,
    command,
    extract: (archive, destination, digest, revision, scope) =>
      extract_app_archive(
        archive,
        destination,
        digest,
        revision,
        scope.child_processes,
        undefined,
        scope,
        system === 'darwin' ? MAC_HOME : undefined,
      ),
    async admit(app, manifest, scope) {
      assert_release_execution(scope);
      if (manifest.runtime_contract?.version !== 2)
        throw new Error('First home installation requires a sealed Node version-2 release');
      await admit_runtime(app, manifest, scope.child_processes);
      assert_release_execution(scope);
    },
    initialize_database: (app, path, scope, machine_root) =>
      initialize_candidate_home_database(
        app,
        path,
        scope,
        join(app, 'node/bin/node'),
        machine_root,
      ),
    prepare_machine: prepare_first_machine,
    machine_healthy: (request, scope, started_ms) =>
      wait_for_first_machine(
        request,
        { system, architecture, account_home, node_executable, uid, environment, command },
        scope,
        started_ms,
      ),
    healthy: (port, revision, signal) =>
      healthy(
        `http://127.0.0.1:${port}/api/health`,
        revision,
        HOME_HEALTH_TIMEOUT_SECONDS,
        signal,
        { ...environment },
        true,
      ),
    service: (request, host) =>
      system === 'darwin'
        ? create_darwin_home_service(request, host)
        : create_linux_home_service(request, host),
  };
}
/** The move mode shares the first install's host, extraction, admission and health. */
export async function production_move_dependencies(
  execution: ReleaseExecution,
  environment: Readonly<NodeJS.ProcessEnv> = process.env,
): Promise<HomeMoveDependencies> {
  const home = await production_home_dependencies(execution, environment);
  const { system, architecture, account_home, node_executable, uid, command } = home;
  return {
    execution,
    environment,
    system,
    architecture,
    account_home,
    node_executable,
    uid,
    command,
    extract: home.extract,
    admit: home.admit,
    healthy: home.healthy,
    service: (request, host) =>
      system === 'darwin'
        ? create_darwin_home_service(request, host, false)
        : create_linux_home_service(request, host, false),
    check_database: (app, path, scope) =>
      check_moved_home_database(app, path, scope, join(app, 'node/bin/node')),
    remove_service: system === 'darwin' ? remove_darwin_home_service : remove_linux_home_service,
  };
}
