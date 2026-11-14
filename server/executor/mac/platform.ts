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
import { MAC_AGENT_PROCESS_PROBE_TIMEOUT_SECONDS } from '../configuration.ts';
import { shell_quote } from '../../conversation/worker_unit/logic.ts';
import { flock } from '../../core/locks/flock.ts';

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
/** Remove only a directory entry under its retained parent, without path traversal. */
export function rmdir_relative(parent: number, name: string): void {
  require_relative_component(name);
  const unlinkat = native('int unlinkat(int dirfd, const char *path, int flags)');
  const remove_directory = native_platform() === 'darwin' ? 0x80 : 0x200;
  checked('unlinkat', () => unlinkat(parent, name, remove_directory));
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
      const name = new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, end));
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
    length = checked('readlinkat', () => Number(readlinkat(parent, name, buffer, buffer.length)));
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') return undefined;
    if (code === 'EINVAL') return null;
    throw error;
  }
  if (length === buffer.length) throw new Error('Native symbolic link target exceeds its buffer.');
  return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, length));
}
export function symlink_relative(parent: number, name: string, target: string): void {
  require_relative_component(name);
  const symlinkat = native('int symlinkat(const char *target, int dirfd, const char *path)');
  checked('symlinkat', () => symlinkat(target, parent, name));
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
        name: 'BlockingIOError',
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
    state = default_executor_root(home);
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
