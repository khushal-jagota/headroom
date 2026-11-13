/** Bypass libuv's microsecond truncation only inside the owned unpack child. */
import { createRequire } from 'node:module';
import type koffi from 'koffi';
import { AppValidationError } from './contracts.ts';
import { archive_timespec } from './logic/archive_time.ts';

type Timespec = { tv_sec: bigint; tv_nsec: number };
let futimens_errno: ((fd: number, times: Timespec[]) => number) | undefined;
function native_futimens(): NonNullable<typeof futimens_errno> {
  if (futimens_errno) return futimens_errno;
  const darwin = process.platform === 'darwin';
  if (
    !(darwin && ['arm64', 'x64'].includes(process.arch)) &&
    !(process.platform === 'linux' && process.arch === 'x64')
  )
    throw new AppValidationError('unsupported archive timestamp ABI');
  const ffi = createRequire(import.meta.url)('koffi') as typeof koffi;
  if (ffi.sizeof('long') !== 8) throw new AppValidationError('unsupported archive timestamp ABI');
  const libc = ffi.load(darwin ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
  // glibc x64 and Darwin LP64: signed 64-bit time_t, followed by long tv_nsec.
  const timespec = ffi.struct({ tv_sec: 'int64_t', tv_nsec: 'long' });
  const call = libc.func('futimens', 'int', ['int', ffi.pointer(timespec)]);
  futimens_errno = (fd, times) => (call(fd, times) === 0 ? 0 : ffi.errno());
  return futimens_errno;
}

export function set_archive_times(fd: number, value: number, fence: () => void): void {
  const [tv_sec, tv_nsec] = archive_timespec(value);
  const call = native_futimens();
  const times = [
    { tv_sec, tv_nsec },
    { tv_sec, tv_nsec },
  ];
  for (;;) {
    fence();
    const errno = call(fd, times);
    if (errno === 0) return;
    if (errno !== 4) throw new AppValidationError(`app archive futimens failed: errno ${errno}`);
  }
}
