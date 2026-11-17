import { readdir, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import type { ArtifactEntry } from '../contracts.ts';
/** Missing entries read as empty; any other filesystem failure is logged and read as empty. */
function absent<T>(path: string, fallback: T): (error: unknown) => T {
  return (error) => {
    if (!['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? ''))
      console.warn('artifact listing could not read', path, error);
    return fallback;
  };
}
async function children(
  root: string,
): Promise<{ name: string; file: boolean; modified: number }[]> {
  const names = await readdir(root).catch(absent(root, [] as string[]));
  const result = [];
  for (const name of names.sort()) {
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
export async function foldDirectory(
  root: string,
  prefix = '',
  excluded_name: (name: string) => boolean = () => false,
): Promise<ArtifactEntry[]> {
  const info = await lstat(root).catch(absent(root, null));
  if (!info || info.isSymbolicLink() || !info.isDirectory()) return [];
  const entries: ArtifactEntry[] = [];
  for (const child of await children(root)) {
    if (excluded_name(child.name)) continue;
    const path = prefix + child.name,
      disk = join(root, child.name);
    if (child.file)
      entries.push({ name: child.name, opens: path, modified_at: child.modified, children: [] });
    else {
      const index = await lstat(join(disk, 'index.html')).catch(
        absent(join(disk, 'index.html'), null),
      );
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
  entries.sort((a, b) => {
    if (a.modified_at !== b.modified_at) return b.modified_at - a.modified_at;
    const left = a.name.toLowerCase(),
      right = b.name.toLowerCase();
    if (left !== right) return left < right ? -1 : 1;
    return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
  });
  return entries;
}
