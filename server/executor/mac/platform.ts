/** The Mac executor's only bridge to operating-system-specific paths and libc calls. */
import { constants, openSync, closeSync, fstatSync } from 'node:fs';
import { constants as os_constants, homedir } from 'node:os';
import { join, isAbsolute } from 'node:path';
import { getSystemErrorName } from 'node:util';
import { isMainThread } from 'node:worker_threads';
import koffi from 'koffi';

for (const name of ['O_NOFOLLOW', 'O_DIRECTORY', 'O_CREAT', 'O_EXCL', 'O_NONBLOCK'] as const) {
  if (typeof constants[name] !== 'number' || constants[name] <= 0)
    throw new Error(`executor requires the native ${name} flag`);
}

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
  const openat = native('int openat(int dirfd, const char *path, int flags, ...)');
  return checked('openat', () => openat(parent, name, flags | close_on_exec, 'int', mode));
}
export function mkdir_relative(parent: number, name: string, mode = 0o700): void {
  require_relative_component(name);
  const mode_type = native_platform() === 'darwin' ? 'uint16_t' : 'unsigned int';
  const mkdirat = native(`int mkdirat(int dirfd, const char *path, ${mode_type} mode)`);
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
  if (isMainThread) throw new Error('Executor directory flushes require the owned files worker.');
  const fsync = native('int fsync(int fd)');
  checked('fsync', () => fsync(descriptor));
}
/** Python requires the full flush to succeed. Missing symbols and failures propagate. */
export function sync_file_descriptor(descriptor: number): void {
  if (isMainThread) throw new Error('Executor full flushes require the owned files worker.');
  sync_directory_descriptor(descriptor);
  if (native_platform() === 'darwin') {
    const fcntl = native('int fcntl(int fd, int command, ...)');
    checked('fcntl(F_FULLFSYNC)', () => fcntl(descriptor, 51, 'int', 0));
  }
}
export function flock_descriptor(
  descriptor: number,
  operation: 'try-exclusive' | 'unlock',
): boolean {
  const flock = native('int flock(int fd, int operation)');
  const flags = operation === 'unlock' ? 8 : 2 | 4;
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

/** Shared retained-inode walk. Creation/full flush remains worker-only. */
export function open_directory_descriptor(
  path: string,
  options: { create?: boolean } = {},
): number {
  if (options.create && isMainThread)
    throw new Error('Executor directory creation requires the owned files worker.');
  if (path.includes('\0'))
    throw Object.assign(new Error('embedded null byte'), { name: 'ValueError' });
  if (path.split('/').includes('..'))
    throw Object.assign(new Error('unsafe directory component'), { name: 'ValueError' });
  const raw = path.split('/');
  const parts = (isAbsolute(path) ? raw : [...process.cwd().split('/'), ...raw]).filter(
    (part) => part !== '' && part !== '.',
  );
  let descriptor = openSync('/', directory_open_flags);
  try {
    for (const part of parts) {
      if (options.create)
        try {
          mkdir_relative(descriptor, part);
          sync_file_descriptor(descriptor);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        }
      const child = open_relative(descriptor, part, directory_open_flags);
      const previous = descriptor;
      descriptor = child;
      closeSync(previous);
    }
    return descriptor;
  } catch (error) {
    closeSync(descriptor);
    throw error;
  }
}
/** The SQLite owner retains this lease independently of file-worker lifetime. */
export function acquire_process_lock(root: string): { close(): void } {
  const parent = open_directory_descriptor(root);
  let lock: number | undefined;
  try {
    lock = open_relative(
      parent,
      'lock',
      constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      0o666,
    );
    if (!fstatSync(lock).isFile()) throw new Error('outbox lock is not regular file');
    if (!flock_descriptor(lock, 'try-exclusive'))
      throw Object.assign(new Error('outbox is already owned'), {
        code: 'EAGAIN',
        name: 'BlockingIOError',
      });
  } catch (error) {
    if (lock !== undefined) {
      const failed = lock;
      lock = undefined;
      closeSync(failed);
    }
    throw error;
  } finally {
    try {
      closeSync(parent);
    } catch (error) {
      if (lock !== undefined) closeSync(lock);
      throw error;
    }
  }
  const descriptor = lock!;
  let closed = false;
  return {
    close() {
      if (!closed) {
        closeSync(descriptor);
        closed = true;
      }
    },
  };
}

export function default_executor_root(home: string): string {
  return join(home, 'Library', 'Application Support', 'Panels', 'executor-typescript');
}
export function python_executor_root(home: string): string {
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
