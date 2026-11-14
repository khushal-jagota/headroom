import { provider_launch_settings } from '../../core/config.ts';
/** Existing environment allowlist and explicit skill provisioning. No runtime launcher argv. */
import { statSync } from 'node:fs';
import { join } from 'node:path';
import { expand_path } from '../paths.ts';
import { managed_skills_home } from '../../skills/files.ts';
import { publish_native_backend_skills } from '../../skills/native_publication.ts';
import type { OperatorPathInputs } from '../command_contracts.ts';
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
    PLAN_HERMES_PYTHON: options.hermes_python,
    ...(options.ambient.HOME === undefined
      ? {}
      : {
          HOME: options.ambient.HOME,
          PLAN_HERMES_HOME: options.ambient.HOME + '/.hermes',
        }),
  };
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
export function resolve_hermes_python(
  value?: string | null,
  environment: Readonly<NodeJS.ProcessEnv> = process.env,
): string {
  return expand_path(
    value || provider_launch_settings(environment).hermes_python,
    environment.HOME,
  );
}
export function provision_environment_skills(
  options: {
    database_parent: string;
    hermes_home: string;
    codex_home: string;
    claude_home: string;
  },
  paths: OperatorPathInputs,
): void {
  const parent = paths.admit_path(paths.expand_path(options.database_parent));
  const root = paths.admit_path(managed_skills_home(parent));
  const homes = [options.hermes_home, options.codex_home, options.claude_home].map((value) => {
    const home = paths.admit_path(paths.expand_path(value));
    // Python replaces this link itself, but can read its previous target first.
    paths.admit_path(join(home, 'skills'));
    return home;
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
  for (const home of homes)
    publish_native_backend_skills(home, root, { policy: { kind: 'server' } });
}
