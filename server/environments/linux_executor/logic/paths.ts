/** Shared Linux state and launch paths, without filesystem effects. */
import { dirname, isAbsolute, join } from 'node:path';
import { LINUX_EXECUTOR_DIRECTORY } from '../configuration.ts';

export function absolute_executor_path(value: string): string {
  if (/[\n\r\0]/u.test(value))
    throw new Error('service values must not contain control characters');
  if (!isAbsolute(value)) throw new Error('executor installation paths must be absolute');
  return value;
}
/** The join root contains separate installation/ and data/ directories. */
export function default_linux_installation_root(home: string, xdg_data_home?: string): string {
  absolute_executor_path(home);
  return join(
    absolute_executor_path(xdg_data_home || join(home, '.local/share')),
    LINUX_EXECUTOR_DIRECTORY,
  );
}
export function default_linux_executor_root(home: string, xdg_data_home?: string): string {
  return join(default_linux_installation_root(home, xdg_data_home), 'data');
}
export function linux_executor_path(
  runtime: string,
  node_executable: string,
  home: string,
): string {
  for (const value of [runtime, node_executable, home]) absolute_executor_path(value);
  return [
    join(runtime, 'bin'),
    join(runtime, 'agent_backends/node_modules/.bin'),
    dirname(node_executable),
    join(home, '.local/bin'),
    '/usr/local/bin',
    '/usr/bin',
    '/bin',
  ].join(':');
}
