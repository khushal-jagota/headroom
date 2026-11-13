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
