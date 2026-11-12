/** Selected Manager artifact inventory, with directories folded into openable entries. */
import { dirname, join, parse } from 'node:path';
import { existsSync, lstatSync, readdirSync, statSync } from 'node:fs';
interface Artifact {
  name: string;
  opens: string | null;
  modified_at: number;
  children: Artifact[];
}
function newest(path: string): number {
  return Math.max(
    0,
    ...readdirSync(path)
      .filter((name) => !lstatSync(join(path, name)).isSymbolicLink())
      .map((name) => {
        const child = join(path, name);
        const s = statSync(child);
        return s.isFile() ? s.mtimeMs / 1000 : s.isDirectory() ? newest(child) : 0;
      }),
  );
}
function fold(root: string, prefix: string): Artifact[] {
  if (!existsSync(root) || lstatSync(root).isSymbolicLink() || !statSync(root).isDirectory())
    return [];
  const entries: Artifact[] = [];
  for (const name of readdirSync(root).sort()) {
    const path = join(root, name);
    const s = lstatSync(path);
    if (s.isSymbolicLink()) continue;
    const opens = prefix + name;
    if (s.isFile()) entries.push({ name, opens, modified_at: s.mtimeMs / 1000, children: [] });
    else if (s.isDirectory()) {
      const index = join(path, 'index.html');
      if (existsSync(index) && !lstatSync(index).isSymbolicLink() && statSync(index).isFile())
        entries.push({
          name,
          opens: opens + '/index.html',
          modified_at: newest(path),
          children: [],
        });
      else {
        const children = fold(path, opens + '/');
        if (children.length)
          entries.push({
            name,
            opens: null,
            modified_at: Math.max(...children.map((child) => child.modified_at)),
            children,
          });
      }
    }
  }
  return entries.sort(
    (a, b) =>
      b.modified_at - a.modified_at ||
      (a.name.toLowerCase() < b.name.toLowerCase()
        ? -1
        : a.name.toLowerCase() > b.name.toLowerCase()
          ? 1
          : a.name < b.name
            ? -1
            : a.name > b.name
              ? 1
              : 0),
  );
}
export function list_artifact_entries(db_path: string, id: string): Artifact[] {
  const root = join(dirname(db_path), 'files', 'sprint-items', id);
  for (let path = root; ; path = dirname(path)) {
    if (existsSync(path) && lstatSync(path).isSymbolicLink()) return [];
    if (path === parse(path).root) break;
  }
  return fold(join(root, 'artifacts'), 'artifacts/');
}
