/** The Mac executor's only bridge to operating-system-specific paths and libc calls. */
import { constants } from 'node:fs';
import { constants as os_constants, homedir } from 'node:os';
import { join } from 'node:path';
import { getSystemErrorName } from 'node:util';
import koffi from 'koffi';

function native_platform(): 'darwin' | 'linux' {
  if (process.platform !== 'darwin' && process.platform !== 'linux')
    throw new Error(`unsupported executor platform: ${process.platform}`);
  return process.platform;
}
let library: ReturnType<typeof koffi.load> | undefined;
function libc(): ReturnType<typeof koffi.load> {
  return (library ??= koffi.load(
    native_platform() === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6',
  ));
}
const functions = new Map<string, ReturnType<ReturnType<typeof koffi.load>['func']>>();
function native(signature: string) {
  let callable = functions.get(signature);
  if (callable === undefined) {
    callable = libc().func(signature);
    functions.set(signature, callable);
  }
  return callable;
}
function checked(operation: string, call: () => number): number {
  for (;;) {
    const result = call();
    if (result !== -1) return result;
    const errno = koffi.errno();
    if (errno === os_constants.errno.EINTR) continue;
    throw Object.assign(new Error(`${operation} failed: ${getSystemErrorName(-errno)}`), {
      code: getSystemErrorName(-errno),
      errno: -errno,
      syscall: operation,
    });
  }
}
export function require_relative_component(name: string): void {
  if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\0'))
    throw Object.assign(new Error('unsafe directory component'), { name: 'ValueError' });
}

/** openat does not inherit Node openSync's automatic close-on-exec behavior. */
export function open_relative(parent: number, name: string, flags: number, mode = 0): number {
  require_relative_component(name);
  const close_on_exec = native_platform() === 'darwin' ? 0x1000000 : 0x80000;
  const openat = native('int openat(int dirfd, const char *path, int flags, unsigned int mode)');
  return checked('openat', () => openat(parent, name, flags | close_on_exec, mode));
}
export function mkdir_relative(parent: number, name: string, mode = 0o700): void {
  require_relative_component(name);
  const mkdirat = native('int mkdirat(int dirfd, const char *path, unsigned int mode)');
  checked('mkdirat', () => mkdirat(parent, name, mode));
}
export function unlink_relative(parent: number, name: string, missing_ok = false): void {
  require_relative_component(name);
  const unlinkat = native('int unlinkat(int dirfd, const char *path, int flags)');
  try {
    checked('unlinkat', () => unlinkat(parent, name, 0));
  } catch (error) {
    if (!missing_ok || (error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
}
export function rename_relative(
  source_parent: number,
  source: string,
  target_parent: number,
  target: string,
): void {
  require_relative_component(source);
  require_relative_component(target);
  const renameat = native(
    'int renameat(int source_dirfd, const char *source, int target_dirfd, const char *target)',
  );
  checked('renameat', () => renameat(source_parent, source, target_parent, target));
}
/** Directory flushes deliberately avoid libuv's Darwin full-flush/fallback policy. */
export function sync_directory_descriptor(descriptor: number): void {
  const fsync = native('int fsync(int fd)');
  checked('fsync', () => fsync(descriptor));
}
/** Python requires the full flush to succeed. Missing symbols and failures propagate. */
export function sync_file_descriptor(descriptor: number): void {
  sync_directory_descriptor(descriptor);
  if (native_platform() === 'darwin') {
    const fcntl = native('int fcntl(int fd, int command)');
    checked('fcntl(F_FULLFSYNC)', () => fcntl(descriptor, 51));
  }
}
export function flock_descriptor(
  descriptor: number,
  operation: 'exclusive' | 'try-exclusive' | 'unlock',
): boolean {
  const flock = native('int flock(int fd, int operation)');
  const flags = operation === 'unlock' ? 8 : operation === 'try-exclusive' ? 2 | 4 : 2;
  try {
    checked('flock', () => flock(descriptor, flags));
    return true;
  } catch (error) {
    if (
      operation === 'try-exclusive' &&
      ['EAGAIN', 'EWOULDBLOCK'].includes((error as NodeJS.ErrnoException).code ?? '')
    )
      return false;
    throw error;
  }
}
export const directory_open_flags =
  constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW;

export function default_executor_root(home: string): string {
  return join(home, 'Library', 'Application Support', 'Panels', 'executor');
}
export function executor_socket_path(root: string): string {
  return join(root, 'run', 'executor.sock');
}
let named_home: ((name: string) => string | undefined) | undefined;
function account_home(name: string): string | undefined {
  if (name.includes('\0'))
    throw Object.assign(new Error('embedded null byte'), { name: 'ValueError' });
  if (named_home === undefined) {
    const darwin = native_platform() === 'darwin';
    const passwd = koffi.struct({
      pw_name: 'str',
      pw_passwd: 'str',
      pw_uid: 'uint32_t',
      pw_gid: 'uint32_t',
      ...(darwin ? { pw_change: 'int64_t', pw_class: 'str' } : {}),
      pw_gecos: 'str',
      pw_dir: 'str',
      pw_shell: 'str',
      ...(darwin ? { pw_expire: 'int64_t', pw_fields: 'int' } : {}),
    });
    const getpwnam = libc().func('getpwnam', koffi.pointer(passwd), ['str']);
    // Decode static libc storage before any subsequent account lookup can replace it.
    named_home = (username) => {
      const pointer = getpwnam(username);
      return pointer === null ? undefined : (koffi.decode(pointer, passwd).pw_dir as string);
    };
  }
  return named_home(name);
}
export function expand_executor_path(
  value: string,
  environment: Readonly<Record<string, string | undefined>> = process.env,
): string {
  if (!value.startsWith('~')) return value;
  const slash = value.indexOf('/');
  const username = value.slice(1, slash < 0 ? undefined : slash);
  const home = username ? account_home(username) : (environment.HOME ?? homedir());
  if (home === undefined)
    throw Object.assign(new Error('Could not determine home directory.'), { name: 'RuntimeError' });
  return home.replace(/\/+$/, '') + (slash < 0 ? '' : value.slice(slash)) || '/';
}
