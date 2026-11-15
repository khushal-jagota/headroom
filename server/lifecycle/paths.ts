/** Native lifecycle paths and application custody policy. */
import { lstatSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { ServerLifecycleError } from './contracts.ts';
import { darwin_control_socket_path } from './logic/paths.ts';

export function application_process_detached(platform = process.platform): boolean {
  if (platform === 'linux') return true;
  // launchd must retain APP in its job process group, even after a supervisor crash.
  if (platform === 'darwin') return false;
  throw new ServerLifecycleError('Panels lifecycle requires Linux or macOS');
}
export function require_private_runtime_directory(path: string): string {
  try {
    mkdirSync(path, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
  }
  const metadata = lstatSync(path);
  if (
    !metadata.isDirectory() ||
    metadata.isSymbolicLink() ||
    metadata.uid !== process.getuid!() ||
    (metadata.mode & 0o7777) !== 0o700
  )
    throw new ServerLifecycleError(`Panels runtime folder must be private and owned: ${path}`);
  return path;
}
export function lifecycle_runtime_directory(): string {
  application_process_detached();
  const path = join(
    process.platform === 'darwin' ? '/var/tmp' : '/tmp',
    `panels-${process.getuid!()}`,
  );
  return process.platform === 'darwin' ? require_private_runtime_directory(path) : path;
}
export function native_control_socket_path(path: string): string {
  application_process_detached();
  if (process.platform !== 'darwin' || Buffer.byteLength(path) < 104) return path;
  return darwin_control_socket_path(path, lifecycle_runtime_directory());
}
