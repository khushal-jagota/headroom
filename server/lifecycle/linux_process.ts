/** Lazy managed-Linux x64 ABI. Foreground entrypoints never import this module. */
import koffi from 'koffi';
import { closeSync } from 'node:fs';
import { ManagedStopError } from './contracts.ts';
import type { PinnedSupervisor } from './contracts.ts';
import { monotonic_seconds } from './configuration.ts';

export function managed_process_support(): { pin(pid: number): PinnedSupervisor } {
  if (process.platform !== 'linux' || process.arch !== 'x64')
    throw new ManagedStopError('Managed Panels stopping requires the admitted Linux x64 runtime');
  let open: (pid: number, flags: number) => number;
  let send: (fd: number, signal: number, info: null, flags: number) => number;
  let poll: (entry: Buffer, count: number, timeout: number) => number;
  try {
    const libc = koffi.load('libc.so.6');
    open = libc.func('int pidfd_open(int pid, unsigned int flags)');
    send = libc.func('int pidfd_send_signal(int fd, int signum, void *info, unsigned int flags)');
    poll = libc.func('int poll(void *fds, unsigned long nfds, int timeout)');
  } catch {
    throw new ManagedStopError('Required Linux process-handle functions are unavailable');
  }
  const failure = (operation: string, errno = koffi.errno()) =>
    new ManagedStopError(`${operation} failed (errno ${errno})`);
  const exited = (fd: number): boolean => {
    // LP64 Linux x64 pollfd: int fd, short events, short revents.
    const entry = Buffer.alloc(8);
    entry.writeInt32LE(fd);
    entry.writeInt16LE(1, 4);
    const result = poll(entry, 1, 0);
    if (result < 0) throw failure('Process exit poll');
    const flags = entry.readInt16LE(6);
    if (flags & 32) throw new ManagedStopError('Process handle is no longer valid');
    return result > 0 && (flags & (1 | 8 | 16)) !== 0;
  };
  const pin = (pid: number): PinnedSupervisor => {
    if (!Number.isSafeInteger(pid) || pid <= 0)
      throw new ManagedStopError('Invalid supervisor process identity');
    const fd = open(pid, 0);
    if (fd < 0) throw failure('Open supervisor process handle');
    let closed = false;
    const live = () => {
      if (closed) throw new ManagedStopError('Process handle is closed');
    };
    return {
      pid,
      signal_term() {
        live();
        if (send(fd, 15, null, 0) !== 0) {
          const errno = koffi.errno();
          if (errno !== 3 || !exited(fd)) throw failure('Signal supervisor process handle', errno);
        }
      },
      async wait_for_exit(deadline) {
        live();
        while (!exited(fd)) {
          const remaining = deadline - monotonic_seconds();
          if (remaining <= 0)
            throw new ManagedStopError(
              'Panels supervisor did not exit before the stop command deadline',
            );
          await new Promise((resolve) => setTimeout(resolve, Math.min(25, remaining * 1000)));
        }
      },
      close() {
        if (!closed) {
          closed = true;
          closeSync(fd);
        }
      },
    };
  };
  // Admission is harmless but exercises all three ABIs before any state publication.
  const fd = open(process.pid, 0);
  if (fd < 0) throw failure('Linux process-handle admission');
  try {
    if (send(fd, 0, null, 0) !== 0) throw failure('Linux process-signal admission');
    if (exited(fd)) throw new ManagedStopError('Linux process-exit admission failed');
  } finally {
    closeSync(fd);
  }
  return { pin };
}

/** Worker signals pin a process before a fresh birth check. Managed stopping stays separate. */
export interface LinuxWorkerProcessSupport {
  signal_exact(
    pid: number,
    birth: string,
    signum: number,
    read_current: () => { birth: string; zombie: boolean } | null,
  ): boolean;
  session(pid: number): number;
  group(): number;
  prepare_session(): void;
}
let worker_support: LinuxWorkerProcessSupport | undefined;
export function linux_worker_process_support(): LinuxWorkerProcessSupport {
  if (worker_support) return worker_support;
  if (process.platform !== 'linux') throw new Error('Worker pidfd support requires Linux');
  const library = koffi.load('libc.so.6');
  const open = library.func('int pidfd_open(int pid, unsigned int flags)');
  const send = library.func(
    'int pidfd_send_signal(int fd, int signum, void *info, unsigned int flags)',
  );
  const getsid = library.func('int getsid(int pid)');
  const getpgrp = library.func('int getpgrp(void)');
  const setsid = library.func('int setsid(void)');
  const failure = (operation: string, errno = koffi.errno()) =>
    Object.assign(new Error(`${operation} failed (errno ${errno})`), {
      errno,
      code: errno === 3 ? 'ESRCH' : 'EIO',
    });
  worker_support = {
    signal_exact(pid, birth, signum, read_current) {
      const descriptor = open(pid, 0);
      if (descriptor < 0) {
        if (koffi.errno() === 3) return true;
        throw failure('Open Worker process handle');
      }
      try {
        const current = read_current();
        if (current === null || current.birth !== birth || current.zombie) return true;
        if (send(descriptor, signum, null, 0) !== 0 && koffi.errno() !== 3)
          throw failure('Signal Worker process handle');
        return true;
      } finally {
        closeSync(descriptor);
      }
    },
    session(pid) {
      const result = getsid(pid);
      if (result < 0) throw failure('Read Worker session');
      return result;
    },
    group() {
      const result = getpgrp();
      if (result < 0) throw failure('Read Worker process group');
      return result;
    },
    prepare_session() {
      if (this.session(0) !== process.pid || this.group() !== process.pid)
        if (setsid() < 0) throw failure('Isolate Worker session');
    },
  };
  return worker_support;
}
