/** Kernel advisory locks shared with Python and the Mac executor. */
import { constants } from 'node:os';
import koffi from 'koffi';

const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const native_flock = libc.func('int flock(int fd, int operation)');
const flags = { exclusive: 2, 'try-exclusive': 2 | 4, unlock: 8 } as const;

/** Return zero on success or the native errno. Callers own contention and file policy. */
export function flock(descriptor: number, operation: keyof typeof flags): number {
  for (;;) {
    if (native_flock(descriptor, flags[operation]) === 0) return 0;
    const errno = koffi.errno();
    if (errno !== constants.errno.EINTR) return errno;
  }
}
