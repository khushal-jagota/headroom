/** Filesystem resolution for database-adjacent folders that follow Python Path.resolve. */
import { lstatSync, readlinkSync } from 'node:fs';
import { dirname, isAbsolute } from 'node:path';

/** Path.resolve(strict=False) follows links before a later '..', and permits missing tails. */
export function resolve_path_sync(path: string): string {
  function walk(value: string, base: string, active: Set<string>): string {
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
        entry = lstatSync(candidate);
      } catch (error) {
        if (!(error instanceof Error && 'code' in error)) throw error;
        current = candidate;
        continue;
      }
      if (entry.isSymbolicLink()) {
        if (active.has(candidate))
          throw Object.assign(new Error('symbolic link loop'), { code: 'ELOOP' });
        active.add(candidate);
        try {
          current = walk(readlinkSync(candidate), current, active);
        } finally {
          active.delete(candidate);
        }
      } else current = candidate;
    }
    return current;
  }
  return walk(path, process.cwd(), new Set());
}
