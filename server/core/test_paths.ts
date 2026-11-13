import { testModeFromEnvironment } from './config.ts';
/** Test processes must never open ambient application data. */
import { lstatSync, readlinkSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, relative, resolve, join, sep } from 'node:path';

const testRoots = new Set<string>();

function inside(path: string, root: string): boolean {
  const suffix = relative(root, path);
  return suffix !== '..' && !suffix.startsWith(`..${sep}`) && !isAbsolute(suffix);
}

/** Register a synthetic fixture root when a host cannot use the OS temp folder. */
export function register_test_root(root: string): () => void {
  const path = realpathSync(resolve(root));
  testRoots.add(path);
  return () => {
    testRoots.delete(path);
  };
}

/** Return the exact permitted path to open, without following outside symlinks. */
export function assert_test_database_path(path: string): string {
  if (
    process.env.VITEST !== 'true' &&
    process.env.PYTEST_CURRENT_TEST === undefined &&
    !testModeFromEnvironment()
  )
    return path;
  return assert_temporary_path(path);
}

/** The same physical constraint, even when only an explicit invocation is in test mode. */
export function assert_temporary_path(path: string): string {
  const roots = [resolve(tmpdir()), ...testRoots];
  const canonicalRoots = roots.map((root) => realpathSync(root));
  const refuse = (): never => {
    throw new Error(
      'Tests can only use databases inside the OS temp folder or a registered test root',
    );
  };
  function checked(candidate: string, links = 0): string {
    if (links > 40) refuse();
    const index = [...roots, ...canonicalRoots].findIndex((root) => inside(candidate, root));
    if (index < 0) refuse();
    const root = [...roots, ...canonicalRoots][index];
    let current = canonicalRoots[index % roots.length];
    const parts = relative(root, candidate).split(sep).filter(Boolean);
    for (let i = 0; i < parts.length; i++) {
      const next = join(current, parts[i]);
      try {
        if (lstatSync(next).isSymbolicLink()) {
          // Read the link itself, then admit its target before any access to it.
          current = checked(resolve(current, readlinkSync(next)), links + 1);
        } else {
          current = next;
        }
      } catch (error) {
        if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
        return join(next, ...parts.slice(i + 1));
      }
    }
    return current;
  }
  // Refuse ambiguous traversal before normalization can erase a symlink followed by '..'.
  if (path.split(sep).includes('..')) refuse();
  return checked(resolve(path));
}
