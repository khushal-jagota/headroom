import { closeSync, mkdirSync, openSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import koffi from 'koffi';
const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const flock = libc.func('int flock(int fd, int operation)');
export const managed_skills_home = (parent: string): string => join(parent, 'skills');
export function with_skill_publication_lock<T>(parent: string, action: (root: string) => T): T {
  const destination = resolve(managed_skills_home(parent));
  mkdirSync(destination, { recursive: true });
  const root = realpathSync(destination);
  const fd = openSync(join(root, '.publication.lock'), 'a+');
  try {
    if (flock(fd, 2) !== 0) throw new Error(`flock failed: errno ${koffi.errno()}`);
    return action(root);
  } finally { flock(fd, 8); closeSync(fd); }
}
export function atomic_replace_bytes(path: string, content: Buffer): void {
  mkdirSync(dirname(path), { recursive: true });
  const temp = join(dirname(path), `.${path.split('/').at(-1)}.tmp-${process.pid}-${randomUUID()}`);
  try { writeFileSync(temp, content, { flag: 'wx', mode: 0o600 }); renameSync(temp, path); }
  finally { rmSync(temp, { force: true }); }
}
export const atomic_replace_text = (path: string, text: string): void => atomic_replace_bytes(path, Buffer.from(text));
