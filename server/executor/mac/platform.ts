import { ExecutorValidationError } from '../protocol_errors.ts';
/** The Mac executor's only bridge to operating-system-specific paths and libc calls. */
import { constants, fstatSync } from 'node:fs';
import { constants as os_constants, homedir } from 'node:os';
import { getPriority, setPriority } from 'node:os';
import { join, isAbsolute } from 'node:path';
import { getSystemErrorName } from 'node:util';
import { isMainThread } from 'node:worker_threads';
import koffi from 'koffi';
import { BackendCommands } from '../../conversation/snapshot/commands.ts';
import { backend_io_is_test } from '../../conversation/snapshot/environment.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { monotonic_seconds } from '../../lifecycle/configuration.ts';
import { MAC_AGENT_PROCESS_PROBE_TIMEOUT_SECONDS } from '../configuration.ts';
import { shell_quote } from '../../conversation/worker_unit/logic.ts';
import { flock } from '../../core/locks/flock.ts';
import {
  absolute_executor_path,
  default_linux_executor_root,
} from '../../environments/linux_executor/logic/paths.ts';

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
function checked(operation: string, call: () => number, retry_interrupted = true): number {
  for (;;) {
    const result = call();
    if (result !== -1) return result;
    const errno = koffi.errno();
    if (errno === os_constants.errno.EINTR && retry_interrupted) continue;
    throw Object.assign(new Error(`${operation} failed: ${getSystemErrorName(-errno)}`), {
      code: getSystemErrorName(-errno),
      errno: -errno,
      syscall: operation,
    });
  }
}
/** Pair libc opens with libc close; Node never registered these descriptors. */
export function close_native_descriptor(descriptor: number): void {
  // close may already consume the descriptor on EINTR. Never retry a reused number.
  checked('close', () => native('int close(int fd)')(descriptor), false);
}
export function require_relative_component(name: string): void {
  if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\0'))
    throw new ExecutorValidationError('unsafe directory component');
}

/** Linux represents undecodable filename bytes as their reversible low-surrogate escapes. */
export function decode_native_path(bytes: Buffer): string {
  try {
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch (error) {
    if (native_platform() !== 'linux') throw error;
  }
  let value = '';
  for (let offset = 0; offset < bytes.length;) {
    const first = bytes[offset]!;
    let length =
      first < 0x80
        ? 1
        : first >= 0xc2 && first <= 0xdf
          ? 2
          : first >= 0xe0 && first <= 0xef
            ? 3
            : first >= 0xf0 && first <= 0xf4
              ? 4
              : 0;
    if (offset + length > bytes.length) length = 0;
    for (let index = 1; index < length; index++)
      if ((bytes[offset + index]! & 0xc0) !== 0x80) length = 0;
    const second = bytes[offset + 1]!;
    if (
      (first === 0xe0 && second < 0xa0) ||
      (first === 0xed && second >= 0xa0) ||
      (first === 0xf0 && second < 0x90) ||
      (first === 0xf4 && second >= 0x90)
    )
      length = 0;
    if (length) {
      value += bytes.subarray(offset, offset + length).toString('utf8');
      offset += length;
    } else {
      value += String.fromCharCode(0xdc00 + first);
      offset++;
    }
  }
  return value;
}
/** Node filesystem calls accept these bytes without UTF-8 replacement of escaped names. */
export function native_path_bytes(path: string): Buffer {
  if (native_platform() !== 'linux') return Buffer.from(path);
  const pieces: Buffer[] = [];
  let text = '';
  for (const character of path) {
    const code = character.charCodeAt(0);
    if (character.length === 1 && code >= 0xdc80 && code <= 0xdcff) {
      if (text) pieces.push(Buffer.from(text));
      text = '';
      pieces.push(Buffer.from([code - 0xdc00]));
    } else text += character;
  }
  if (text) pieces.push(Buffer.from(text));
  return Buffer.concat(pieces);
}
function native_path_argument(path: string): string | Buffer {
  if (path.includes('\0')) throw new ExecutorValidationError('embedded null byte');
  if (native_platform() === 'darwin') return path;
  return Buffer.concat([native_path_bytes(path), Buffer.from([0])]);
}

/** openat does not inherit Node openSync's automatic close-on-exec behavior. */
export function open_relative(parent: number, name: string, flags: number, mode = 0): number {
  require_relative_component(name);
  const close_on_exec = native_platform() === 'darwin' ? 0x1000000 : 0x80000;
  const openat = native('int openat(int dirfd, const char *path, int flags, ...)');
  return checked('openat', () =>
    openat(parent, native_path_argument(name), flags | close_on_exec, 'int', mode),
  );
}
export function mkdir_relative(parent: number, name: string, mode = 0o700): void {
  require_relative_component(name);
  const mode_type = native_platform() === 'darwin' ? 'uint16_t' : 'unsigned int';
  const mkdirat = native(`int mkdirat(int dirfd, const char *path, ${mode_type} mode)`);
  checked('mkdirat', () => mkdirat(parent, native_path_argument(name), mode));
}
export function unlink_relative(parent: number, name: string, missing_ok = false): void {
  require_relative_component(name);
  const unlinkat = native('int unlinkat(int dirfd, const char *path, int flags)');
  try {
    checked('unlinkat', () => unlinkat(parent, native_path_argument(name), 0));
  } catch (error) {
    if (!missing_ok || (error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
}
/** Remove only a directory entry under its retained parent, without path traversal. */
export function rmdir_relative(parent: number, name: string): void {
  require_relative_component(name);
  const unlinkat = native('int unlinkat(int dirfd, const char *path, int flags)');
  const remove_directory = native_platform() === 'darwin' ? 0x80 : 0x200;
  checked('unlinkat', () => unlinkat(parent, native_path_argument(name), remove_directory));
}
/** Enumeration opens only dot relative to the held inode and retains the stream until close succeeds. */
const directory_readers = new Set<() => void>();
export function cleanup_directory_readers(): void {
  for (const close of directory_readers) close();
}
export function owns_directory_readers(): boolean {
  return directory_readers.size > 0;
}
export function read_directory_names(descriptor: number): string[] {
  if (process.arch !== 'x64' && process.arch !== 'arm64')
    throw new Error('Directory enumeration requires a supported 64-bit native ABI.');
  const darwin = native_platform() === 'darwin',
    close_on_exec = darwin ? 0x1000000 : 0x80000;
  const duplicate = checked('openat', () =>
    native('int openat(int dirfd, const char *path, int flags, ...)')(
      descriptor,
      '.',
      directory_open_flags | close_on_exec,
      'int',
      0,
    ),
  );
  let stream: unknown = null;
  const close = () => {
    if (stream === null) close_native_descriptor(duplicate);
    else checked('closedir', () => native('int closedir(void *directory)')(stream));
    directory_readers.delete(close);
  };
  directory_readers.add(close);
  try {
    // arm64 has only the 64-bit Darwin ABI. x86_64 selects its explicit inode64 symbols.
    const suffix = darwin && process.arch === 'x64' ? '$INODE64' : '';
    const fdopendir = libc().func('fdopendir' + suffix, 'void *', ['int']);
    const readdir = libc().func('readdir' + suffix, 'void *', ['void *']);
    stream = fdopendir(duplicate);
    if (stream === null) throw new Error(`fdopendir failed: errno ${koffi.errno()}`);
    const names: string[] = [];
    for (;;) {
      koffi.errno(0);
      const entry = readdir(stream);
      if (entry === null) {
        const errno = koffi.errno();
        if (errno === os_constants.errno.EINTR) continue;
        if (errno !== 0) throw new Error(`readdir failed: errno ${errno}`);
        break;
      }
      // XNU bsd/sys/dirent.h inode64: name offset 21; Linux dirent64: offset 19.
      const offset = darwin ? 21 : 19,
        length = Number(koffi.decode(entry, 16, 'uint16_t'));
      // Reject records larger than the verified native structs before decoding their names.
      if (length <= offset || length > (darwin ? 1048 : 280))
        throw new Error('Unsupported native directory record length.');
      const bytes = Buffer.from(
        koffi.decode(entry, offset, 'uint8_t', length - offset) as Uint8Array,
      );
      const end = bytes.indexOf(0);
      if (
        end < 1 ||
        end > (darwin ? 1023 : 255) ||
        (darwin && end !== Number(koffi.decode(entry, 18, 'uint16_t')))
      )
        throw new Error('Invalid native directory name length.');
      const name = decode_native_path(bytes.subarray(0, end));
      if (name !== '.' && name !== '..') {
        require_relative_component(name);
        names.push(name);
      }
    }
    return names;
  } finally {
    close();
  }
}
/** Undefined means absent, null means an existing entry that is not a link. */
export function readlink_relative(parent: number, name: string): string | null | undefined {
  require_relative_component(name);
  const buffer = Buffer.alloc(65536);
  const readlinkat = native(
    'long readlinkat(int dirfd, const char *path, _Out_ void *buffer, size_t size)',
  );
  let length: number;
  try {
    length = checked('readlinkat', () =>
      Number(readlinkat(parent, native_path_argument(name), buffer, buffer.length)),
    );
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') return undefined;
    if (code === 'EINVAL') return null;
    throw error;
  }
  if (length === buffer.length) throw new Error('Native symbolic link target exceeds its buffer.');
  return decode_native_path(buffer.subarray(0, length));
}
export function symlink_relative(parent: number, name: string, target: string): void {
  require_relative_component(name);
  const symlinkat = native('int symlinkat(const char *target, int dirfd, const char *path)');
  checked('symlinkat', () =>
    symlinkat(native_path_argument(target), parent, native_path_argument(name)),
  );
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
  checked('renameat', () =>
    renameat(
      source_parent,
      native_path_argument(source),
      target_parent,
      native_path_argument(target),
    ),
  );
}
/** Directory flushes deliberately avoid libuv's Darwin full-flush/fallback policy. */
export function sync_directory_descriptor(descriptor: number): void {
  if (isMainThread) throw new Error('Executor directory flushes require the owned files worker.');
  const fsync = native('int fsync(int fd)');
  checked('fsync', () => fsync(descriptor));
}
/** Full flushes must succeed. Missing symbols and failures propagate. */
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
  const errno = flock(descriptor, operation);
  if (errno === 0) return true;
  const code = getSystemErrorName(-errno);
  if (operation === 'try-exclusive' && ['EAGAIN', 'EWOULDBLOCK'].includes(code)) return false;
  throw Object.assign(new Error(`flock failed: ${code}`), {
    code,
    errno: -errno,
    syscall: 'flock',
  });
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
  if (path.includes('\0')) throw new ExecutorValidationError('embedded null byte');
  if (path.split('/').includes('..'))
    throw new ExecutorValidationError('unsafe directory component');
  const raw = path.split('/');
  const parts = (isAbsolute(path) ? raw : [...process.cwd().split('/'), ...raw]).filter(
    (part) => part !== '' && part !== '.',
  );
  const darwin = native_platform() === 'darwin';
  let descriptor = checked('openat', () =>
    native('int openat(int dirfd, const char *path, int flags, ...)')(
      darwin ? -2 : -100,
      '/',
      directory_open_flags | (darwin ? 0x1000000 : 0x80000),
      'int',
      0,
    ),
  );
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
      close_native_descriptor(previous);
    }
    return descriptor;
  } catch (error) {
    close_native_descriptor(descriptor);
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
      });
  } catch (error) {
    if (lock !== undefined) {
      const failed = lock;
      lock = undefined;
      close_native_descriptor(failed);
    }
    throw error;
  } finally {
    try {
      close_native_descriptor(parent);
    } catch (error) {
      if (lock !== undefined) close_native_descriptor(lock);
      throw error;
    }
  }
  const descriptor = lock!;
  let closed = false;
  return {
    close() {
      if (!closed) {
        close_native_descriptor(descriptor);
        closed = true;
      }
    },
  };
}

function darwin_executor_root(home: string): string {
  return join(home, 'Library', 'Application Support', 'Panels', 'executor-typescript');
}
export function default_executor_root(home: string, xdg_data_home?: string): string {
  return process.platform === 'linux'
    ? default_linux_executor_root(home, xdg_data_home)
    : darwin_executor_root(home);
}
export function python_executor_root(home: string, xdg_data_home?: string): string {
  if (process.platform === 'linux')
    return join(
      absolute_executor_path(xdg_data_home || join(home, '.local/share')),
      'panels/executor',
    );
  return join(home, 'Library', 'Application Support', 'Panels', 'executor');
}
export function executor_socket_path(root: string): string {
  return join(root, 'run', 'executor.sock');
}
let named_home: ((name: string) => string | undefined) | undefined;
function account_home(name: string): string | undefined {
  if (name.includes('\0')) throw new ExecutorValidationError('embedded null byte');
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
  if (home === undefined) throw new Error('Could not determine home directory.');
  return home.replace(/\/+$/, '') + (slash < 0 ? '' : value.slice(slash)) || '/';
}

export interface AgentProcessIdentity {
  readonly process_id: number;
  readonly started: string;
}
export interface AgentProcessSnapshot extends AgentProcessIdentity {
  readonly parent_process_id: number;
}
export interface AgentProcessPlatform {
  snapshot(signal: AbortSignal): Promise<readonly AgentProcessSnapshot[]>;
  lower_priority(
    identity: AgentProcessIdentity,
    nice: number,
    signal: AbortSignal,
  ): Promise<boolean>;
  signal(
    identity: AgentProcessIdentity,
    signal: 'SIGTERM' | 'SIGKILL',
    cancellation: AbortSignal,
  ): Promise<void>;
}
export function parse_agent_process_snapshot(text: string): AgentProcessSnapshot[] {
  const result: AgentProcessSnapshot[] = [];
  for (const line of text.split('\n')) {
    const fields = /^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s*$/.exec(line);
    if (!fields || fields[3]!.startsWith('Z')) continue;
    const process_id = Number(fields[1]),
      parent_process_id = Number(fields[2]);
    if (
      Number.isSafeInteger(process_id) &&
      process_id > 0 &&
      Number.isSafeInteger(parent_process_id)
    )
      result.push({ process_id, parent_process_id, started: fields[4]! });
  }
  return result;
}
/** Production probes use the invocation's existing command and child ownership. */
export function mac_agent_process_platform(children: OwnedOneShotChildren): AgentProcessPlatform {
  if (native_platform() !== 'darwin' || backend_io_is_test())
    throw new Error('Native Mac agent process probes require a production Darwin host.');
  const commands = new BackendCommands(children);
  const run = (args: readonly string[], signal: AbortSignal) =>
    commands.run(['/bin/ps', ...args], {
      timeout_seconds: MAC_AGENT_PROCESS_PROBE_TIMEOUT_SECONDS,
      environment_overrides: { LC_ALL: 'C' },
      signal,
    });
  const current = async (identity: AgentProcessIdentity, signal: AbortSignal) => {
    const result = await run(['-o', 'stat=,lstart=', '-p', String(identity.process_id)], signal);
    const fields = /^(\S+)\s+(.+)$/.exec(result.standard_output.trim());
    return (
      result.exit_code === 0 &&
      fields !== null &&
      !fields[1]!.startsWith('Z') &&
      fields[2]!.trim() === identity.started
    );
  };
  return {
    async snapshot(signal) {
      const result = await run(['-A', '-o', 'pid=,ppid=,stat=,lstart='], signal);
      if (result.exit_code !== 0) throw new Error('Mac process snapshot failed.');
      return parse_agent_process_snapshot(result.standard_output);
    },
    async lower_priority(identity, nice, signal) {
      if (!(await current(identity, signal))) return false;
      signal.throwIfAborted();
      try {
        if (getPriority(identity.process_id) < nice) setPriority(identity.process_id, nice);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ESRCH') return false;
        throw error;
      }
      return true;
    },
    async signal(identity, signum, cancellation) {
      if (!(await current(identity, cancellation))) return;
      cancellation.throwIfAborted();
      try {
        process.kill(identity.process_id, signum);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
      }
    },
  };
}

export function mac_installation_paths(home: string) {
  if (!isAbsolute(home) || home.includes('\0'))
    throw new Error('Mac installation HOME must be absolute.');
  const install = join(home, 'Deployments/PanelsExecutorTypeScript');
  const supervisor = join(install, 'supervisor'),
    state = darwin_executor_root(home);
  const app = join(home, 'Applications/Panels TypeScript.app');
  return {
    home,
    install,
    supervisor,
    state,
    app,
    node: join(supervisor, 'node/bin/node'),
    bootstrap: join(supervisor, 'server/executor/mac/entrypoint.ts'),
    console: join(install, 'bin/panels'),
    app_executable: join(app, 'Contents/MacOS/Panels'),
    plist: join(home, 'Library/LaunchAgents/com.panels.executor.typescript.plist'),
    job: 'com.panels.executor.typescript',
    bundle_id: 'com.panels.executor.typescript.app',
    stdout: join(state, 'logs/launch-agent.stdout.log'),
    stderr: join(state, 'logs/launch-agent.stderr.log'),
    socket: executor_socket_path(state),
  };
}
type PlistValue = string | number | boolean | PlistValue[] | { [key: string]: PlistValue };
function plist(value: Record<string, PlistValue>): string {
  const escape = (text: string) =>
    text
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&apos;');
  const element = (item: PlistValue): string => {
    if (typeof item === 'string') return `<string>${escape(item)}</string>`;
    if (typeof item === 'boolean') return item ? '<true/>' : '<false/>';
    if (typeof item === 'number') return `<integer>${item}</integer>`;
    if (Array.isArray(item)) return `<array>${item.map(element).join('')}</array>`;
    return `<dict>${Object.entries(item)
      .map(([key, child]) => `<key>${escape(key)}</key>${element(child)}`)
      .join('')}</dict>`;
  };
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">' +
    element(value) +
    '</plist>\n'
  );
}
/** Generate candidate assets only. No compiler, signer, installation or launchctl runs. */
export function mac_installation_assets(options: {
  home: string;
  revision: string;
  executor_id: string;
  server_url: string;
  backend_path: string;
}) {
  const paths = mac_installation_paths(options.home);
  if (!options.backend_path.split(':').every((part) => isAbsolute(part)))
    throw new Error('Backend PATH must contain absolute directories.');
  const info = plist({
    CFBundleIdentifier: paths.bundle_id,
    CFBundleName: 'Panels TypeScript',
    CFBundleDisplayName: 'Panels TypeScript',
    CFBundleExecutable: 'Panels',
    CFBundlePackageType: 'APPL',
    CFBundleVersion: '1',
    PanelsApplicationRevision: options.revision,
    LSUIElement: true,
    NSDesktopFolderUsageDescription: 'Panels accesses your selected files on Desktop.',
    NSDocumentsFolderUsageDescription: 'Panels accesses your selected files in Documents.',
    NSDownloadsFolderUsageDescription: 'Panels accesses your selected files in Downloads.',
  });
  const launcher = `/* Fixed native requester. execve preserves PID, signals and argument boundaries. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
extern char **environ;
int main(int argc, char **argv) {
  unsetenv("NODE_OPTIONS"); unsetenv("NODE_PATH");
  char **args = calloc((size_t)argc + 2, sizeof(char *));
  if (!args) { perror("Panels: arguments"); return 1; }
  args[0] = ${JSON.stringify(paths.node)};
  args[1] = ${JSON.stringify(paths.bootstrap)};
  for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
  execve(args[0], args, environ);
  perror("Panels: fixed Node runtime"); free(args); return 1;
}
`;
  const launch_agent = plist({
    Label: paths.job,
    AssociatedBundleIdentifiers: [paths.bundle_id],
    ProgramArguments: [
      paths.app_executable,
      'executor',
      'run',
      '--application-revision',
      options.revision,
      '--executor-id',
      options.executor_id,
      '--panels-server-url',
      options.server_url,
    ],
    LimitLoadToSessionType: 'Aqua',
    RunAtLoad: true,
    KeepAlive: { SuccessfulExit: false },
    ProcessType: 'Interactive',
    ThrottleInterval: 10,
    EnvironmentVariables: {
      HOME: paths.home,
      PLAN_EXECUTOR_ROOT: paths.state,
      PATH: join(paths.supervisor, 'node/bin') + ':' + options.backend_path,
    },
    StandardOutPath: paths.stdout,
    StandardErrorPath: paths.stderr,
  });
  const console =
    '#!/bin/sh\nset -eu\nunset NODE_OPTIONS NODE_PATH\n' +
    `if [ "\${PLAN_EXECUTOR_ROOT+x}" != x ]; then export PLAN_EXECUTOR_ROOT=${shell_quote(paths.state)}; fi\nif [ "\${PLAN_SERVER_URL+x}" != x ]; then export PLAN_SERVER_URL=${shell_quote(options.server_url)}; fi\nexec ${shell_quote(paths.app_executable)} "$@"\n`;
  return {
    paths,
    files: {
      'Panels TypeScript.app/Contents/Info.plist': { text: info, mode: 0o644 },
      'launcher.c': { text: launcher, mode: 0o644 },
      'bin/panels': { text: console, mode: 0o755 },
      'com.panels.executor.typescript.plist': { text: launch_agent, mode: 0o600 },
      'installation.json': {
        text: JSON.stringify({ ...options, paths }, null, 2) + '\n',
        mode: 0o600,
      },
    },
  };
}

/** Worker custody uses libproc births with microsecond precision, independently of legacy probes. */
export interface DarwinWorkerProcessInfo {
  readonly pid: number;
  readonly birth: string;
  readonly parent: number;
  readonly group: number;
  readonly zombie: boolean;
}
let worker_process_library: ReturnType<typeof koffi.load> | undefined;
const worker_process_functions = new Map<
  string,
  ReturnType<ReturnType<typeof koffi.load>['func']>
>();
function worker_process_native(signature: string) {
  if (process.platform !== 'darwin' || !['x64', 'arm64'].includes(process.arch))
    throw new Error('Worker process inspection requires the Darwin LP64 ABI');
  let callable = worker_process_functions.get(signature);
  if (!callable) {
    worker_process_library ??= koffi.load('/usr/lib/libproc.dylib');
    callable = worker_process_library.func(signature);
    worker_process_functions.set(signature, callable);
  }
  return callable;
}
export function darwin_worker_process_info(pid: number): DarwinWorkerProcessInfo | null {
  // XNU proc_bsdinfo LP64 layout. Birth fields are uint64 and must never pass through Number.
  const info = Buffer.alloc(136);
  koffi.errno(0);
  const length = worker_process_native(
    'int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer, int buffersize)',
  )(pid, 3, 0, info, info.length);
  if (length !== info.length) {
    const errno = koffi.errno();
    if (errno === os_constants.errno.ESRCH) return null;
    throw Object.assign(new Error('Mac process identity is unreadable'), {
      code: errno > 0 ? getSystemErrorName(-errno) : 'EIO',
    });
  }
  return {
    pid,
    birth: `${info.readBigUInt64LE(120)}:${info.readBigUInt64LE(128)}`,
    parent: info.readUInt32LE(16),
    group: info.readUInt32LE(100),
    zombie: info.readUInt32LE(4) === 5,
  };
}
export function darwin_worker_process_snapshot(
  deadline: number,
  clock: () => number = monotonic_seconds,
): ReadonlyMap<number, import('../../worker_supervision/contracts.ts').WorkerProcess> {
  const list = worker_process_native(
    'int proc_listpids(uint32_t type, uint32_t typeinfo, void *buffer, int buffersize)',
  );
  const count = list(4, process.getuid!(), null, 0);
  if (count <= 0) throw new Error('Mac process inventory is unavailable');
  const pids = Buffer.alloc((Math.floor(count / 4) + 1024) * 4);
  const size = list(4, process.getuid!(), pids, pids.length);
  if (size <= 0 || size >= pids.length) throw new Error('Mac process inventory is incomplete');
  const result = new Map<number, import('../../worker_supervision/contracts.ts').WorkerProcess>();
  for (let offset = 0; offset < Math.floor(size / 4) * 4; offset += 4) {
    if (clock() >= deadline) throw new Error('Worker inspection deadline expired');
    const pid = pids.readInt32LE(offset);
    if (pid <= 0) continue;
    const info = darwin_worker_process_info(pid);
    if (info === null) continue;
    try {
      const session = checked('getsid', () => native('int getsid(int pid)')(pid));
      result.set(pid, { ...info, session });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
    }
  }
  return result;
}
export function darwin_worker_process_exists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ESRCH') return false;
    throw error;
  }
}
export function darwin_prepare_worker_session(): void {
  const session = checked('getsid', () => native('int getsid(int pid)')(0));
  const group = checked('getpgrp', () => native('int getpgrp(void)')());
  if (session !== process.pid || group !== process.pid)
    checked('setsid', () => native('int setsid(void)')());
}

/** Read-only Darwin observations share the existing libproc and libc bridges. */
export function darwin_process_bytes(pid: number, flavor: number, size: number): Buffer {
  const bytes = Buffer.alloc(size);
  koffi.errno(0);
  const length = worker_process_native(
    'int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer, int buffersize)',
  )(pid, flavor, 0, bytes, size);
  if (length !== size)
    throw darwin_observation_error('Mac process evidence is missing or unreadable.');
  return bytes;
}
function darwin_observation_error(message: string): Error {
  const errno = koffi.errno();
  return Object.assign(new Error(message), {
    code: errno > 0 ? getSystemErrorName(-errno) : 'EIO',
  });
}
export function darwin_process_ids(limit: number, same_uid: boolean): number[] {
  const bytes = Buffer.alloc((limit + 1) * 4);
  const size = worker_process_native(
    'int proc_listpids(uint32_t type, uint32_t typeinfo, void *buffer, int buffersize)',
  )(same_uid ? 4 : 1, same_uid ? process.getuid!() : 0, bytes, bytes.length);
  if (size <= 0 || size >= bytes.length || size % 4)
    throw new Error('Native process inventory is unavailable or exceeds its limit');
  return [...new Set(Array.from({ length: size / 4 }, (_, index) => bytes.readInt32LE(index * 4)))]
    .filter((pid) => pid > 0)
    .sort((a, b) => a - b);
}
export function darwin_process_descriptors(pid: number, limit: number): number[] {
  const bytes = Buffer.alloc((limit + 1) * 8);
  koffi.errno(0);
  const size = worker_process_native(
    'int proc_pidinfo(int pid, int flavor, uint64_t arg, void *buffer, int buffersize)',
  )(pid, 1, 0, bytes, bytes.length);
  if (size === 0 && koffi.errno() === os_constants.errno.ESRCH) return [];
  if (size < 0 || (size === 0 && koffi.errno()))
    throw darwin_observation_error('Mac process descriptors are unreadable.');
  if (size % 8 || size >= bytes.length)
    throw new Error('Process descriptor inspection bound exceeded.');
  // Preserve non-socket descriptors in the budget. Negative entries encode their exclusion.
  return Array.from({ length: size / 8 }, (_, index) =>
    bytes.readUInt32LE(index * 8 + 4) === 2 ? bytes.readInt32LE(index * 8) : -1,
  );
}
export function darwin_process_socket(pid: number, descriptor: number): Buffer | null {
  const bytes = Buffer.alloc(792);
  koffi.errno(0);
  const length = worker_process_native(
    'int proc_pidfdinfo(int pid, int fd, int flavor, void *buffer, int buffersize)',
  )(pid, descriptor, 3, bytes, bytes.length);
  if (length !== bytes.length) {
    if ([os_constants.errno.EBADF, os_constants.errno.ESRCH].includes(koffi.errno())) return null;
    throw darwin_observation_error('Mac socket ownership is unreadable.');
  }
  return bytes;
}
export function darwin_system_bytes(name: string, size: number, exact = true): Buffer {
  if (native_platform() !== 'darwin') throw new Error('Native observations require macOS');
  const bytes = Buffer.alloc(size),
    length = Buffer.alloc(8);
  length.writeBigUInt64LE(BigInt(size));
  checked('sysctlbyname', () =>
    native('int sysctlbyname(const char *name, void *old, void *length, void *value, size_t size)')(
      name,
      bytes,
      length,
      null,
      0,
    ),
  );
  const observed = Number(length.readBigUInt64LE());
  if (observed > size || (exact && observed !== size))
    throw new Error('Native sysctl observation is truncated or exceeds its buffer');
  return bytes.subarray(0, observed);
}
let observation_host: number | undefined;
export function darwin_host_words(flavor: number, minimum: number, wide = false): number[] {
  if (native_platform() !== 'darwin') throw new Error('Native observations require macOS');
  observation_host ??= native('unsigned int mach_host_self(void)')();
  if (!observation_host) throw new Error('Native host port is unavailable');
  const bytes = Buffer.alloc(256 * 4),
    count = Buffer.alloc(4);
  count.writeUInt32LE(256);
  const call = native(
    `int ${wide ? 'host_statistics64' : 'host_statistics'}(unsigned int host, int flavor, void *buffer, void *count)`,
  );
  if (
    call(observation_host, flavor, bytes, count) !== 0 ||
    count.readUInt32LE() < minimum ||
    count.readUInt32LE() > 256
  )
    throw new Error('Native host observation is unavailable or truncated');
  return Array.from({ length: minimum }, (_, index) => bytes.readUInt32LE(index * 4));
}
export function darwin_cpu_time_scale(): number {
  if (native_platform() !== 'darwin') throw new Error('Native observations require macOS');
  const bytes = Buffer.alloc(8);
  if (
    native('int mach_timebase_info(void *info)')(bytes) !== 0 ||
    !bytes.readUInt32LE(0) ||
    !bytes.readUInt32LE(4)
  )
    throw new Error('Native CPU timebase is unavailable');
  return bytes.readUInt32LE(0) / bytes.readUInt32LE(4) / 1e9;
}
