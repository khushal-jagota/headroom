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
