/** Darwin Unix socket paths use a stable hash when the encoded path exceeds sun_path. */
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { ServerLifecycleError } from '../contracts.ts';

export function darwin_control_socket_path(path: string, runtime_directory: string): string {
  if (Buffer.byteLength(path) < 104) return path;
  const digest = createHash('sha256').update(path).digest('hex').slice(0, 24);
  const short = join(runtime_directory, `s-${digest}.sock`);
  if (Buffer.byteLength(short) >= 104)
    throw new ServerLifecycleError('Panels runtime folder is too long for macOS Unix sockets');
  return short;
}
