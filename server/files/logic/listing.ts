import { readdir, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import type { ArtifactEntry } from '../contracts.ts';
/** Python compares Unicode code points, including names outside the BMP. */
export function comparePythonText(a: string, b: string): number {
  const left = Array.from(a, (c) => c.codePointAt(0)!);
  const right = Array.from(b, (c) => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    if (left[i] !== right[i]) return left[i]! - right[i]!;
  }
  return left.length - right.length;
}
async function children(
  root: string,
): Promise<{ name: string; file: boolean; modified: number }[]> {
  const names = await readdir(root).catch(() => []);
  const result = [];
  for (const name of names.sort(comparePythonText)) {
    const info = await lstat(join(root, name));
    if (!info.isSymbolicLink() && (info.isFile() || info.isDirectory()))
      result.push({ name, file: info.isFile(), modified: info.mtimeMs / 1000 });
  }
  return result;
}
async function newest(root: string): Promise<number> {
  const times = await Promise.all(
    (await children(root)).map((c) => (c.file ? c.modified : newest(join(root, c.name)))),
  );
  return Math.max(0, ...times);
}
export async function foldDirectory(root: string, prefix = ''): Promise<ArtifactEntry[]> {
  const info = await lstat(root).catch(() => null);
  if (!info || info.isSymbolicLink() || !info.isDirectory()) return [];
  const entries: ArtifactEntry[] = [];
  for (const child of await children(root)) {
    const path = prefix + child.name,
      disk = join(root, child.name);
    if (child.file)
      entries.push({ name: child.name, opens: path, modified_at: child.modified, children: [] });
    else {
      const index = await lstat(join(disk, 'index.html')).catch(() => null);
      if (index?.isFile() && !index.isSymbolicLink())
        entries.push({
          name: child.name,
          opens: path + '/index.html',
          modified_at: await newest(disk),
          children: [],
        });
      else {
        const nested = await foldDirectory(disk, path + '/');
        if (nested.length)
          entries.push({
            name: child.name,
            opens: null,
            modified_at: Math.max(...nested.map((c) => c.modified_at)),
            children: nested,
          });
      }
    }
  }
  entries.sort(
    (a, b) =>
      b.modified_at - a.modified_at ||
      comparePythonText(a.name.toLowerCase(), b.name.toLowerCase()) ||
      comparePythonText(a.name, b.name),
  );
  return entries;
}
