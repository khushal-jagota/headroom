/** Literal manager arguments and the explicitly selected installer environment. */
import { installer_attempt_id } from '../release/installer_bundle.ts';
import { assert_installer_path } from './installer/configuration.ts';
import { AppValidationError } from '../release/contracts.ts';

const environment_names = new Set([
  'HOME',
  'CODEX_HOME',
  'CLAUDE_CONFIG_DIR',
  'HERMES_HOME',
  'PLAN_HERMES_HOME',
  'PATH',
  'LANG',
  'LANGUAGE',
  'TERM',
  'TMPDIR',
  'TMP',
  'TEMP',
  'USER',
  'LOGNAME',
  'XDG_RUNTIME_DIR',
  'DBUS_SESSION_BUS_ADDRESS',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_APP_ROOT',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_WORKSPACE_ROOT',
  'PLAN_TICKET_SCRATCH_ROOT',
  'PLAN_TICKET_SCRATCH_DATABASE',
  'PLAN_CREDENTIAL_KEY_PATH',
  'PLAN_DISPATCHER_LOCK_PATH',
  'PLAN_SERVER_CONTROL_SOCKET',
  'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
  'PLAN_SERVER_URL',
  'PLAN_TEST_MODE',
  'VITEST',
  'PYTEST_CURRENT_TEST',
]);
const path_names = new Set([
  'HOME',
  'CODEX_HOME',
  'CLAUDE_CONFIG_DIR',
  'HERMES_HOME',
  'PLAN_HERMES_HOME',
  'TMPDIR',
  'TMP',
  'TEMP',
  'XDG_RUNTIME_DIR',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_APP_ROOT',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_WORKSPACE_ROOT',
  'PLAN_TICKET_SCRATCH_ROOT',
  'PLAN_TICKET_SCRATCH_DATABASE',
  'PLAN_CREDENTIAL_KEY_PATH',
  'PLAN_DISPATCHER_LOCK_PATH',
  'PLAN_SERVER_CONTROL_SOCKET',
  'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
]);
export function selected_installer_environment(
  environment: Readonly<NodeJS.ProcessEnv>,
): NodeJS.ProcessEnv {
  const selected: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(environment)) {
    if (!environment_names.has(name) && !/^LC_[A-Z_]+$/.test(name)) continue;
    if (value === undefined) continue;
    if (value.includes('\0')) throw new AppValidationError('installer environment contains NUL');
    if (path_names.has(name) && value) assert_installer_path(value, environment);
    selected[name] = value;
  }
  return selected;
}
/** Pure argv construction is testable without a service-manager exception or flag. */
export function installer_transient_command(
  attempt_id: string,
  command: readonly string[],
  environment: Readonly<NodeJS.ProcessEnv>,
): readonly string[] {
  const selected = selected_installer_environment(environment);
  return [
    'systemd-run',
    '--user',
    '--collect',
    '--quiet',
    '--unit=panels-update-' + installer_attempt_id(attempt_id),
    '--expand-environment=no',
    '--property=UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH',
    ...Object.keys(selected)
      .sort()
      .map((name) => '--setenv=' + name + '=' + selected[name]),
    '--',
    ...command,
  ];
}
