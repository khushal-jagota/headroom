/** Existing environment allowlist and explicit skill provisioning. No runtime launcher argv. */
import { statSync } from 'node:fs';
import { join } from 'node:path';
import { expand_path } from '../paths.ts';
import { configured_file_path } from '../../drivers/core/paths.ts';
import { managed_skills_home } from '../../skills/files.ts';
import { provision_account_skills } from '../../skills/account.ts';
import { PROVISION_SKILLS_HOME_OPTIONS } from '../command_contracts.ts';
import type { OperatorArguments, OperatorPathInputs } from '../command_contracts.ts';
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';
import type { EnvironmentRunOptions, ResolvedEnvironmentInstance } from './contracts.ts';
import { validate_environment_values } from './logic.ts';

const ALLOWED_AMBIENT = new Set(['LANG', 'LANGUAGE', 'PATH', 'TERM', 'TMPDIR']);

export function build_environment_run_env(
  instance: ResolvedEnvironmentInstance,
  options: EnvironmentRunOptions,
): Record<string, string> {
  const ambient: Record<string, string> = {};
  for (const [key, value] of Object.entries(options.ambient))
    if (value !== undefined && (ALLOWED_AMBIENT.has(key) || key.startsWith('LC_')))
      ambient[key] = value;
  return {
    ...ambient,
    ...validate_environment_values(options.credentials, instance.kind),
    PLAN_DB_PATH: instance.db_path,
    PLAN_PORT: String(options.runtime_port),
    PLAN_LOGS_DIR: instance.logs_dir,
    PLAN_DISPATCHER_LOCK_PATH: instance.dispatcher_lock_path,
    PLAN_SERVER_CONTROL_SOCKET: instance.server_control_socket_path,
    ...options.launcher_files,
    ...(options.ambient.HOME === undefined
      ? {}
      : { HOME: options.ambient.HOME, ...carried_homes(options.ambient.HOME) }),
  };
}
/**
 * The child uses the account's normal homes. A home named by a Panels setting (`PLAN_`) is
 * pinned to its default, because the launch rebuilds Panels settings instead of inheriting
 * them. A home named by the provider's own variable is left to the provider's default.
 */
function carried_homes(account_home: string): Record<string, string> {
  const homes: Record<string, string> = {};
  for (const { manifest } of BUILT_IN_MANIFESTS)
    if (manifest.home?.environment_name.startsWith('PLAN_'))
      homes[manifest.home.environment_name] = expand_path(manifest.home.default_path, account_home);
  return homes;
}
/** Each configured file a driver's executable sits beside, expanded against HOME. */
export function resolve_launcher_files(
  environment: Readonly<NodeJS.ProcessEnv> = process.env,
): Record<string, string> {
  const files: Record<string, string> = {};
  for (const { manifest } of BUILT_IN_MANIFESTS) {
    const file = manifest.executable.configured_beside;
    if (file)
      files[file.environment_name] = expand_path(
        configured_file_path(file, environment),
        environment.HOME,
      );
  }
  return files;
}
export function build_test_environment_run_env(
  instance: ResolvedEnvironmentInstance,
  options: EnvironmentRunOptions,
): Record<string, string> {
  return {
    ...build_environment_run_env(instance, options),
    PLAN_TEST_MODE: '1',
    PLAN_FAKE_NOW: '2026-07-04T12:00:00+00:00',
    PLAN_TICK_SECONDS: '1',
  };
}
export function provision_environment_skills(
  options: OperatorArguments['provision-skills'],
  paths: OperatorPathInputs,
): string | null {
  const parent = paths.admit_path(paths.expand_path(options.database_parent));
  const root = paths.admit_path(managed_skills_home(parent));
  const homes = PROVISION_SKILLS_HOME_OPTIONS.map(({ destination, home: { skills_directory } }) => {
    const home = paths.admit_path(paths.expand_path(options[destination]));
    const skills = skills_directory ?? 'skills';
    // Link replacement can read the previous target before replacing the link.
    paths.admit_path(join(home, skills));
    return { path: home, skills_directory: skills };
  });
  let exists = false;
  try {
    exists = statSync(root).isDirectory();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  if (!exists)
    throw Object.assign(new Error(`managed Panels skills not found: ${root}`), {
      code: 'ENOENT',
    });
  return provision_account_skills(parent, homes);
}
