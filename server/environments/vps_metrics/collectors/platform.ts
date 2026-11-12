/** Lazy libc statistics. Creating a read-only metrics runtime never calls these. */
import koffi from 'koffi';
import type { FilesystemStatistics } from './contracts.ts';
import { evidence_error } from './errors.ts';

interface NativeStatistics {
  clock_ticks(): number;
  page_size(): number;
  statvfs(path: string): FilesystemStatistics;
}
let configured: NativeStatistics | undefined;
let darwin_monotonic: (() => number) | undefined;
/** CPython uses mach_absolute_time on the Mac; current libuv includes sleep instead. */
export function monotonic_seconds(): number {
  if (process.platform !== 'darwin') return Number(process.hrtime.bigint()) / 1e9;
  if (!darwin_monotonic) {
    if (!['arm64', 'x64'].includes(process.arch))
      throw evidence_error('OSError', 'unsupported native clock ABI');
    const libc = koffi.load('/usr/lib/libSystem.B.dylib');
    const timebase = koffi.struct({ numer: 'uint32_t', denom: 'uint32_t' });
    const info = libc.func('mach_timebase_info', 'int', [koffi.out(koffi.pointer(timebase))]);
    const absolute = libc.func('uint64_t mach_absolute_time()');
    const base: Record<string, number> = {};
    if (info(base) !== 0 || !(base.numer > 0 && base.denom > 0))
      throw evidence_error('OSError', 'Mach clock time base unavailable');
    const numerator = BigInt(base.numer),
      denominator = BigInt(base.denom);
    darwin_monotonic = () =>
      Number((BigInt(absolute() as number | bigint) * numerator) / denominator) / 1e9;
  }
  return darwin_monotonic();
}
// https://github.com/python/cpython/blob/v3.13.5/Python/pytime.c#L1074
// https://github.com/libuv/libuv/blob/v1.51.0/src/unix/darwin.c#L56
function native_statistics(): NativeStatistics {
  if (configured) return configured;
  const darwin = process.platform === 'darwin';
  if (
    !(darwin && ['arm64', 'x64'].includes(process.arch)) &&
    !(process.platform === 'linux' && process.arch === 'x64')
  )
    throw evidence_error('OSError', 'unsupported native statistics ABI');
  const libc = koffi.load(darwin ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
  // Linux LP64: bits/statvfs.h and bits/confname.h in glibc.
  // Darwin: apple-oss-distributions/Libc include/sys/statvfs.h and include/unistd.h.
  // Darwin fsblkcnt_t/fsfilcnt_t are uint32_t (xnu/bsd/sys/_types.h), not Linux uint64_t.
  // https://github.com/apple-oss-distributions/Libc/blob/main/include/sys/statvfs.h
  // https://github.com/apple-oss-distributions/Libc/blob/main/include/unistd.h
  // https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/_types.h
  const count = darwin ? 'uint32_t' : 'uint64_t';
  const layout = koffi.struct({
    f_bsize: 'ulong',
    f_frsize: 'ulong',
    f_blocks: count,
    f_bfree: count,
    f_bavail: count,
    f_files: count,
    f_ffree: count,
    f_favail: count,
    f_fsid: 'ulong',
    f_flag: 'ulong',
    f_namemax: 'ulong',
    ...(darwin ? {} : { _spare: koffi.array('int', 6) }),
  });
  const statvfs = libc.func('statvfs', 'int', ['str', koffi.out(koffi.pointer(layout))]);
  const sysconf = libc.func('long sysconf(int name)');
  const system = (name: number): number => {
    const value = sysconf(name) as number;
    if (value < 0) throw evidence_error('OSError', 'system configuration unavailable');
    return value;
  };
  configured = {
    clock_ticks: () => system(darwin ? 3 : 2),
    page_size: () => system(darwin ? 29 : 30),
    statvfs: (path) => {
      const result: Record<string, number | bigint> = {};
      if (statvfs(path, result) !== 0) {
        const errno = koffi.errno();
        throw evidence_error(
          errno === 2
            ? 'FileNotFoundError'
            : errno === 13 || errno === 1
              ? 'PermissionError'
              : errno === 20
                ? 'NotADirectoryError'
                : 'OSError',
          `filesystem statistics unavailable: errno ${errno}`,
        );
      }
      return {
        blocks: result.f_blocks,
        available: result.f_bavail,
        fragment_size: result.f_frsize,
      };
    },
  };
  return configured;
}
export const clock_ticks = (): number => native_statistics().clock_ticks();
export const page_size = (): number => native_statistics().page_size();
export const statvfs = (path: string): FilesystemStatistics => native_statistics().statvfs(path);
