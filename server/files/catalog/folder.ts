/**
 * The file catalog lists one folder per call. Its cost follows the size of that folder,
 * not of the whole owner folder, so a large Ticket folder cannot stall the server.
 */
import { createHash } from 'node:crypto';
import type { Dirent } from 'node:fs';
import { lstat, readdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { absent } from '../logic/listing.ts';
import { symlink, is_private_ticket_name } from '../logic/paths.ts';
import type { CatalogEntry, FolderListing } from '../contracts.ts';
/** An owner folder under a symlink lists nothing. */
function unsafe_root(root: string): boolean {
  for (let path = root; ; path = dirname(path)) {
    if (symlink(path)) return true;
    if (dirname(path) === path) return false;
  }
}
/** Real files and folders in one folder. Symlinks never list. */
async function visible(path: string, excluded: (name: string) => boolean): Promise<Dirent[]> {
  const entries = await readdir(path, { withFileTypes: true }).catch(absent(path, [] as Dirent[]));
  return entries.filter(
    (entry) =>
      !entry.isSymbolicLink() && (entry.isFile() || entry.isDirectory()) && !excluded(entry.name),
  );
}
/** A folder with its own index.html opens as one page and does not list its contents. */
async function page(path: string): Promise<boolean> {
  const index = join(path, 'index.html');
  return Boolean((await lstat(index).catch(absent(index, null)))?.isFile());
}
function private_name(ticket: boolean, name: string): boolean {
  return ticket && is_private_ticket_name(name);
}
/** The named folder exists through real folders only, outside private names and pages. */
async function reachable(root: string, directory: string, ticket: boolean): Promise<boolean> {
  if (!directory) return true;
  const parts = directory.split('/');
  if (private_name(ticket, parts[0]!)) return false;
  let path = root;
  for (const part of parts) {
    path = join(path, part);
    if (!(await lstat(path).catch(absent(path, null)))?.isDirectory() || (await page(path)))
      return false;
  }
  return true;
}
export async function list_folder(
  root: string,
  prefix: string,
  directory: string,
): Promise<FolderListing> {
  const ticket = prefix.includes('/files/tickets/'),
    folder = join(root, directory);
  const listed =
    unsafe_root(root) || !(await reachable(root, directory, ticket))
      ? []
      : await visible(folder, (name) => !directory && private_name(ticket, name));
  const entries: { name: string; opens: string | null; modified: number; count: number }[] = [];
  for (const entry of listed) {
    const path = directory ? directory + '/' + entry.name : entry.name,
      disk = join(folder, entry.name);
    const info = await lstat(disk).catch(absent(disk, null));
    if (!info) continue;
    const modified = info.mtimeMs / 1000;
    if (entry.isFile()) {
      entries.push({ name: entry.name, opens: path, modified, count: 0 });
      continue;
    }
    const inside = await visible(disk, () => false);
    if (!inside.length) continue;
    const opens = inside.some((child) => child.name === 'index.html' && child.isFile());
    entries.push({
      name: entry.name,
      opens: opens ? path + '/index.html' : null,
      modified,
      count: opens ? 0 : inside.length,
    });
  }
  entries.sort((a, b) => {
    if (a.modified !== b.modified) return b.modified - a.modified;
    const left = a.name.toLowerCase(),
      right = b.name.toLowerCase();
    if (left !== right) return left < right ? -1 : 1;
    return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
  });
  const rows: CatalogEntry[] = entries.map((item) => {
    const path = directory ? directory + '/' + item.name : item.name;
    const dot = item.name.lastIndexOf('.');
    return {
      key: 'managed:' + path,
      path: item.opens ?? path,
      label: dot > 0 ? item.name.slice(0, dot) : item.name,
      kind: item.opens
        ? item.opens.split('/').at(-1)!.includes('.')
          ? item.opens.split('.').at(-1)!
          : 'file'
        : String(item.count),
      href: item.opens ? prefix + item.opens.split('/').map(encodeURIComponent).join('/') : null,
      directory: item.opens ? null : path,
      child_count: item.count,
    };
  });
  // Modified times enter the revision, so an edit to a listed file or folder changes it.
  return {
    rows,
    revision: createHash('sha256').update(JSON.stringify(entries)).digest('hex'),
  };
}
/**
 * Linked targets that some folder of the owner already lists, so the root does not show them
 * twice. A file deep inside can change this set without changing the root folder's rows.
 */
export async function listed_targets(
  root: string,
  prefix: string,
  targets: readonly string[],
): Promise<Set<string>> {
  const result = new Set<string>();
  if (unsafe_root(root)) return result;
  const ticket = prefix.includes('/files/tickets/');
  for (const target of targets) {
    if (!target.startsWith(prefix)) continue;
    let parts: string[];
    try {
      parts = target.slice(prefix.length).split('/').map(decodeURIComponent);
    } catch {
      continue;
    }
    if (private_name(ticket, parts[0]!)) continue;
    let path = root;
    for (const [index, part] of parts.entries()) {
      path = join(path, part);
      const info = await lstat(path).catch(absent(path, null));
      if (index === parts.length - 1) {
        if (info?.isFile()) result.add(target);
      } else if (!info?.isDirectory()) break;
      else if (await page(path)) {
        // A page lists as its index.html alone.
        if (index === parts.length - 2 && parts.at(-1) === 'index.html') result.add(target);
        break;
      }
    }
  }
  return result;
}
