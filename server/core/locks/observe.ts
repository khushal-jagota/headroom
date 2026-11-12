/** Read kernel authority without creating a file or acquiring its lock. */
import { closeSync, constants, fstatSync, openSync, readFileSync } from 'node:fs';
import koffi from 'koffi';

let darwin_query: ((descriptor: number) => 'free' | 'held') | undefined;
function observe_darwin(descriptor: number): 'free' | 'held' {
  if (!darwin_query) {
    if (!['arm64', 'x64'].includes(process.arch))
      throw new Error('Non-acquiring runtime lock observation has no supported Darwin ABI');
    // XNU bsd/sys/fcntl.h: F_GETLK=7; whole-file F_WRLCK=3, SEEK_SET=0.
    // Koffi lays out the documented fields, without an inferred byte-size constant.
    const layout = koffi.struct({
      l_start: 'int64_t',
      l_len: 'int64_t',
      l_pid: 'int32_t',
      l_type: 'short',
      l_whence: 'short',
    });
    const libc = koffi.load('/usr/lib/libSystem.B.dylib');
    // fcntl is variadic, including on arm64. Koffi requires a type/value pair per vararg.
    const query = libc.func('fcntl', 'int', ['int', 'int', '...']);
    const argument = koffi.inout(koffi.pointer(layout));
    darwin_query = (fd) => {
      const value = { l_start: 0, l_len: 0, l_pid: 0, l_type: 3, l_whence: 0 };
      let result: number;
      do {
        result = query(fd, 7, argument, value) as number;
      } while (result !== 0 && koffi.errno() === 4);
      if (result !== 0) throw new Error(`Darwin runtime lock query failed: errno ${koffi.errno()}`);
      if (value.l_type === 2) return 'free';
      // XNU documents l_pid=-1 for a flock owner. It is still kernel lock evidence.
      if (value.l_type === 1 || value.l_type === 3) return 'held';
      throw new Error('Darwin runtime lock query returned an unknown lock kind');
    };
  }
  return darwin_query(descriptor);
}

export function observe_existing_flock(path: string): 'free' | 'held' {
  if (!['linux', 'darwin'].includes(process.platform))
    throw new Error('Non-acquiring runtime lock observation is unavailable on this platform');
  let descriptor: number;
  try {
    descriptor = openSync(path, constants.O_RDONLY | constants.O_NONBLOCK);
  } catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return 'free';
    throw error;
  }
  try {
    const metadata = fstatSync(descriptor, { bigint: true });
    if (!metadata.isFile()) throw new Error('runtime lock evidence is not a regular file');
    if (process.platform === 'darwin') return observe_darwin(descriptor);
    const major = ((metadata.dev >> 8n) & 0xfffn) | ((metadata.dev >> 32n) & 0xfffff000n);
    const minor = (metadata.dev & 0xffn) | ((metadata.dev >> 12n) & 0xffffff00n);
    for (const line of readFileSync('/proc/locks', 'utf8').split('\n')) {
      if (!line.trim()) continue;
      const columns = line.trim().split(/\s+/);
      if (columns[1] !== 'FLOCK') continue; // Blocked waiters have not acquired authority.
      const identity = /^([0-9a-f]+):([0-9a-f]+):(\d+)$/.exec(columns[5] ?? '');
      if (
        !/^\d+:$/.test(columns[0] ?? '') ||
        columns.length < 8 ||
        !identity ||
        columns[2] !== 'ADVISORY' ||
        !['READ', 'WRITE'].includes(columns[3] ?? '')
      )
        throw new Error('kernel runtime lock evidence is unreadable');
      if (
        BigInt('0x' + identity[1]) === major &&
        BigInt('0x' + identity[2]) === minor &&
        BigInt(identity[3]!) === metadata.ino
      )
        return 'held';
    }
    return 'free';
  } finally {
    closeSync(descriptor);
  }
}
