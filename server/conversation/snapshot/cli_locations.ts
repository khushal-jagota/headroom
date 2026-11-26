/**
 * Where an owner's own Node tools keep global commands, when the service PATH misses them.
 *
 * The service PATH is fixed at install time, so a CLI installed with nvm, fnm, volta, mise,
 * bun, pnpm or a custom npm prefix is not on it. Panels does not ask a login shell for the
 * owner's PATH: that runs the owner's rc files at every lookup. It reads file names in
 * known folders under the account's home and runs nothing.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join, isAbsolute } from 'node:path';

const VERSIONED_MANAGER_FOLDERS: readonly (readonly string[])[] = [
  ['.nvm', 'versions', 'node'],
  ['.local', 'share', 'fnm', 'node-versions'],
  ['Library', 'Application Support', 'fnm', 'node-versions'],
  ['.local', 'share', 'mise', 'installs', 'node'],
];
const FIXED_FOLDERS: readonly (readonly string[])[] = [
  ['.volta', 'bin'],
  ['.local', 'share', 'mise', 'shims'],
  ['.bun', 'bin'],
  ['.local', 'share', 'pnpm'],
  ['Library', 'pnpm'],
  ['.npm-global', 'bin'],
];

const version_parts = (name: string) => name.replace(/^v/, '').split('.').map(Number);
function newest_first(first: string, second: string): number {
  const [a, b] = [version_parts(first), version_parts(second)];
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    const difference = (b[index] ?? 0) - (a[index] ?? 0);
    if (difference !== 0 && !Number.isNaN(difference)) return difference;
  }
  return 0;
}
function list(path: string): string[] {
  try {
    return readdirSync(path);
  } catch {
    return [];
  }
}
/** The `prefix=` an owner set in ~/.npmrc, as the folder that holds its commands. */
function npmrc_prefix_folder(home: string): string[] {
  let text: string;
  try {
    text = readFileSync(join(home, '.npmrc'), 'utf8');
  } catch {
    return [];
  }
  const value = /^\s*prefix\s*=\s*(.+?)\s*$/m.exec(text)?.[1]?.replace(/^~(?=\/)/, home);
  return value !== undefined && isAbsolute(value) ? [join(value, 'bin')] : [];
}
/** Candidate command folders under `home`, most likely first. Folders may not exist. */
export function cli_search_folders(home: string): string[] {
  const versioned = VERSIONED_MANAGER_FOLDERS.flatMap((parts) => {
    const root = join(home, ...parts);
    const bin = parts.includes('fnm') ? ['installation', 'bin'] : ['bin'];
    return list(root)
      .sort(newest_first)
      .map((version) => join(root, version, ...bin));
  });
  return [
    ...FIXED_FOLDERS.map((parts) => join(home, ...parts)),
    ...npmrc_prefix_folder(home),
    ...versioned,
  ];
}
