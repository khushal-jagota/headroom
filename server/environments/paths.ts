/** Existing Python Path.expanduser/resolve(strict=False) behavior for environment paths. */
import { lstat, readlink } from 'node:fs/promises';
import { dirname, isAbsolute } from 'node:path';
import { homedir, userInfo } from 'node:os';
import koffi from 'koffi';

let named_home: ((username: string) => string | undefined) | undefined;
function account_home(username: string): string | undefined {
  const current = userInfo();
  if (username === current.username) return current.homedir;
  if (!named_home) {
    const darwin = process.platform === 'darwin';
    const libc = koffi.load(darwin ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
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
    const getpwnam = libc.func('getpwnam', koffi.pointer(passwd), ['str']);
    // getpwnam's storage is static. Decode it synchronously before any other lookup.
    named_home = (name) => {
      const pointer = getpwnam(name);
      return pointer === null ? undefined : (koffi.decode(pointer, passwd).pw_dir as string);
    };
  }
  return named_home(username);
}
export function expand_path(path: string, home?: string): string {
  if (!path.startsWith('~')) return path;
  const slash = path.indexOf('/');
  const username = path.slice(1, slash < 0 ? undefined : slash);
  // CPython expanduser returns root for bare '~' when HOME is explicitly empty.
  if (!username && home === '') return slash < 0 ? '/' : path.slice(slash);
  const selected_home = username ? account_home(username) : (home ?? homedir());
  if (selected_home === undefined)
    throw Object.assign(new Error('Could not determine home directory.'), { name: 'RuntimeError' });
  return selected_home + (slash < 0 ? '' : path.slice(slash));
}
export async function resolved_path(path: string, home?: string): Promise<string> {
  async function walk(value: string, base: string, active: Set<string>): Promise<string> {
    let current = isAbsolute(value) ? '/' : base;
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') {
        current = dirname(current);
        continue;
      }
      const candidate = (current === '/' ? '' : current) + '/' + part;
      let entry;
      try {
        entry = await lstat(candidate);
      } catch (error) {
        if (!(error instanceof Error) || !('code' in error)) throw error;
        current = candidate;
        continue;
      }
      if (entry.isSymbolicLink()) {
        if (active.has(candidate))
          throw Object.assign(new Error('symbolic link loop'), { code: 'ELOOP' });
        active.add(candidate);
        try {
          current = await walk(await readlink(candidate), current, active);
        } finally {
          active.delete(candidate);
        }
      } else current = candidate;
    }
    return current;
  }
  return walk(expand_path(path, home), process.cwd(), new Set());
}
