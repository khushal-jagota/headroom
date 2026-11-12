/** Resolve installed, case-sensitive ZoneInfo keys to an ICU formatter key. */
import { closeSync, openSync, readSync, realpathSync, statSync } from 'node:fs';
import { delimiter, isAbsolute, join, relative, sep } from 'node:path';

export class UnmappedUsageTimeZone extends Error {
  readonly key: string;
  constructor(key: string) {
    super(`Installed ZoneInfo key has no ICU mapping: ${key}`);
    this.key = key;
  }
}
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
      const suffix = relative(realpathSync(root), selected);
      if (suffix !== '..' && !suffix.startsWith('..' + sep) && !isAbsolute(suffix))
        candidates.push(suffix);
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
  // A real source key must not silently become UTC or an ordinary invalid-input answer.
  throw new UnmappedUsageTimeZone(key);
}
