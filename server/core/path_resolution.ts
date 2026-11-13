/** Filesystem resolution for database-adjacent folders that follow Python Path.resolve. */
import { lstatSync, readlinkSync } from 'node:fs';
import { dirname, isAbsolute } from 'node:path';

/** Path.resolve(strict=False) follows links before a later '..', and permits missing tails. */
export function resolve_path_sync(
  path: string,
  options: { strict?: boolean; require_directory_traversal?: boolean } = {},
): string {
  function walk(value: string, base: string, active: Set<string>): string {
    let current = isAbsolute(value) ? '/' : base;
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      // Mac workspace traversal follows Python 3.13's directory requirement. Other
      // callers retain Python 3.12's lexical cancellation below.
      if (options.require_directory_traversal && !lstatSync(current).isDirectory())
        throw Object.assign(new Error('Not a directory'), { code: 'ENOTDIR', path: current });
      if (part === '..') {
        // Python 3.12 realpath cancels '..' before lstat, including in strict mode.
        current = dirname(current);
        continue;
      }
      const candidate = (current === '/' ? '' : current) + '/' + part;
      let entry;
      try {
        entry = lstatSync(candidate);
      } catch (error) {
        if (options.strict) throw error;
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
