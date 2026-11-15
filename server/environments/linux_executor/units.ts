/** The Linux user unit enters the existing shared executor CLI. */
import { join } from 'node:path';
import { validate_executor_launch } from '../../executor/mac/configuration.ts';
import { STOP_SECONDS } from '../../worker_supervision/config.ts';
import { validate_app_sha } from '../release/logic/manifest.ts';
import { unit_value } from '../release/units.ts';
import type { LinuxExecutorInstallation } from './contracts.ts';
import { LINUX_EXECUTOR_ENTRYPOINT } from './configuration.ts';
import { absolute_executor_path, linux_executor_path } from './logic/paths.ts';

export function render_service(configuration: LinuxExecutorInstallation, runtime: string): string {
  validate_executor_launch(
    configuration.executor_id,
    configuration.revision,
    configuration.server_url,
  );
  validate_app_sha(configuration.revision);
  for (const value of [
    configuration.installation,
    configuration.data,
    configuration.home,
    configuration.node_executable,
    runtime,
  ])
    absolute_executor_path(value);
  const environment = {
    HOME: configuration.home,
    PLAN_EXECUTOR_ROOT: configuration.data,
    PLAN_APP_ROOT: runtime,
    PLAN_APP_SHA: configuration.revision,
    PATH: linux_executor_path(runtime, configuration.node_executable, configuration.home),
  };
  const command = [
    configuration.node_executable,
    join(runtime, LINUX_EXECUTOR_ENTRYPOINT),
    'executor',
    'run',
    '--executor-id',
    configuration.executor_id,
    '--application-revision',
    configuration.revision,
    '--panels-server-url',
    configuration.server_url,
  ];
  return [
    '[Unit]',
    'Description=Panels extra machine',
    'After=network-online.target',
    '',
    '[Service]',
    'Type=simple',
    'UMask=0077',
    'UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH',
    ...Object.entries(environment).map(
      ([name, value]) => 'Environment=' + unit_value(`${name}=${value}`),
    ),
    'ExecStart=' + command.map((value) => unit_value(value.replaceAll('$', () => '$$'))).join(' '),
    'Restart=on-failure',
    'RestartSec=10',
    'Delegate=yes',
    'KillMode=mixed',
    `TimeoutStopSec=${STOP_SECONDS + 20}`,
    '',
    '[Install]',
    'WantedBy=default.target',
    '',
  ].join('\n');
}
