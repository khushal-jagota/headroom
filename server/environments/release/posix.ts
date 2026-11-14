/** POSIX replacement retains inherited descriptors without a spawned wrapper. */
import { createRequire } from 'node:module';
import type koffi from 'koffi';
import { getSystemErrorName } from 'node:util';
import { EnvironmentValidationError } from '../registry/contracts.ts';

export function execve(
  file: string,
  argv: readonly string[],
  environment: Readonly<Record<string, string>>,
): never {
  if (process.platform !== 'linux' && process.platform !== 'darwin')
    throw new EnvironmentValidationError('application launch requires Linux or macOS');
  const ffi = createRequire(import.meta.url)('koffi') as typeof koffi;
  const libc = ffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
  const replace = libc.func(
    'int execve(const char *path, const char **argv, const char **environment)',
  );
  const strerror = libc.func('const char *strerror(int error)');
  // Node marks initialized stdio descriptors close-on-exec too.
  for (const fd of [0, 1, 2]) inherit_descriptor(fd);
  replace(
    file,
    [...argv, null],
    [...Object.entries(environment).map(([key, value]) => `${key}=${value}`), null],
  );
  const errno = ffi.errno();
  throw Object.assign(new Error(`[Errno ${errno}] ${strerror(errno)}`), {
    name: 'OSError',
    errno: -errno,
    code: getSystemErrorName(-errno),
  });
}

/** A reserved libuv listener otherwise closes when POSIX exec replaces this process. */
export function inherit_descriptor(fd: number): void {
  const ffi = createRequire(import.meta.url)('koffi') as typeof koffi;
  const libc = ffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
  const fcntl = libc.func('int fcntl(int fd, int command, int value)');
  const flags = fcntl(fd, 1, 0);
  if (flags < 0 || fcntl(fd, 2, flags & ~1) < 0)
    throw new EnvironmentValidationError('could not preserve an application descriptor');
}
