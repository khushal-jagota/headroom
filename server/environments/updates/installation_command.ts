/** Literal manager arguments and the explicitly selected installer environment. */
import { installer_attempt_id } from '../release/installer_bundle.ts';
import { assert_installer_path } from './installer/configuration.ts';
import { AppValidationError } from '../release/contracts.ts';
import { installer_unit_environment } from '../../core/config.ts';

export function selected_installer_environment(
  environment: Readonly<NodeJS.ProcessEnv>,
): NodeJS.ProcessEnv {
  const selection = installer_unit_environment(environment);
  for (const value of Object.values(selection.environment))
    if (value?.includes('\0')) throw new AppValidationError('installer environment contains NUL');
  for (const path of selection.paths) assert_installer_path(path, environment);
  const selected = selection.environment;
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
    '--property=UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH VITEST PYTEST_CURRENT_TEST',
    ...Object.keys(selected)
      .sort()
      .map((name) => '--setenv=' + name + '=' + selected[name]),
    '--',
    ...command,
  ];
}
