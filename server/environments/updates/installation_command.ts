/** Literal manager arguments and the explicitly selected installer environment. */
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
function unit_value(value: string): string {
  if (/[\x00-\x1f\x7f]/.test(value))
    throw new Error('Installer unit value contains a control character');
  return JSON.stringify(value.replaceAll('%', '%%'));
}
/** A regular user unit survives loss of the manager. The marker disappears when the trial settles. */
export function installer_unit(
  command: readonly string[],
  marker: string,
  environment: Readonly<NodeJS.ProcessEnv>,
): string {
  if (!marker.startsWith('/') || /[\x00-\x1f\x7f]/.test(marker))
    throw new Error('Invalid installer marker path');
  return [
    '[Unit]',
    'Description=Panels home update',
    'ConditionPathExists=' + marker.replaceAll('%', '%%'),
    '[Service]',
    'Type=simple',
    'Restart=on-abnormal',
    'SuccessExitStatus=2',
    'UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH VITEST PYTEST_CURRENT_TEST',
    ...Object.entries(selected_installer_environment(environment)).map(
      ([key, value]) => 'Environment=' + unit_value(key + '=' + value),
    ),
    'ExecStart=' + command.map((value) => unit_value(value.replaceAll('$', () => '$$'))).join(' '),
    '[Install]',
    'WantedBy=default.target',
    '',
  ].join('\n');
}
