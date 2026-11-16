/** Resolve installed, case-sensitive ZoneInfo keys to an ICU formatter key. */
import { closeSync, openSync, readSync, realpathSync, statSync } from 'node:fs';
import { delimiter, isAbsolute, join, relative } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';

const STANDARD_ROOTS = [
  '/usr/share/zoneinfo',
  '/usr/lib/zoneinfo',
  '/usr/share/lib/zoneinfo',
  '/etc/zoneinfo',
];
function roots(): readonly string[] {
  const configured = process.env.PYTHONTZPATH;
  return configured === undefined ? STANDARD_ROOTS : configured.split(delimiter).filter(isAbsolute);
}
function validFile(path: string): boolean {
  if (!statSync(path).isFile()) return false;
  const handle = openSync(path, 'r');
  try {
    const header = Buffer.alloc(4);
    if (readSync(handle, header, 0, header.length, 0) !== 4 || header.toString('ascii') !== 'TZif')
      throw new RangeError('Invalid usage time zone');
    return true;
  } finally {
    closeSync(handle);
  }
}
export function timezone(key: string): string {
  if (
    typeof key !== 'string' ||
    isAbsolute(key) ||
    key.includes('\0') ||
    key.split('/').some((part) => part === '' || part === '.' || part === '..')
  )
    throw new RangeError('Invalid usage time zone');
  const directories = roots();
  let selected: string | undefined;
  for (const root of directories) {
    const path = join(root, key);
    try {
      if (validFile(path)) {
        selected = realpathSync(path);
        break;
      }
    } catch (error) {
      if (!(
        error instanceof Error &&
        'code' in error &&
        ['ENOENT', 'ENOTDIR'].includes(String(error.code))
      ))
        throw error;
    }
  }
  if (selected === undefined) throw new RangeError('Invalid usage time zone');
  const candidates = [key];
  for (const root of directories) {
    try {
      const canonical = realpathSync(root);
      if (path_is_within(canonical, selected)) candidates.push(relative(canonical, selected));
    } catch {
      /* A missing alternative root does not invalidate the selected file. */
    }
  }
  for (const candidate of candidates) {
    const stripped = candidate.replace(/^(?:(?:right|posix)\/)+/, '');
    const compatible = stripped === 'Factory' ? 'Etc/UTC' : stripped;
    try {
      new Intl.DateTimeFormat('en', { timeZone: compatible }).format();
      return compatible;
    } catch {
      /* Try the canonical symlink target. */
    }
  }
  // Accepted zone-data deviation: retain admission of an installed key even
  // when Node's ICU cannot name it, and bucket it in UTC instead of failing.
  return 'UTC';
}
