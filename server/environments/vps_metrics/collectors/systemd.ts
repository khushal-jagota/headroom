/** One bounded service-manager command. */
import { bounded_command, launcher } from './command.ts';
import type { LaunchCommand } from './command.ts';
export type LaunchServiceQuery = LaunchCommand;
export function query_services(
  scope: string,
  timeout: number,
  output_bytes: number,
  execute: LaunchServiceQuery = launcher('systemctl'),
): Promise<string> {
  const args = [
    '--' + scope,
    'show',
    '*.service',
    '--no-pager',
    '--property=Id,ActiveState,SubState,NRestarts,ControlGroup',
  ];
  return bounded_command(execute, args, timeout, output_bytes, 'service query');
}
