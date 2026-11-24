/** Existing environment allowlist. No runtime launcher argv. */
import { expand_path } from '../paths.ts';
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
