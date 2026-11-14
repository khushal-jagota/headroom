/** Lexical containment only. Callers retain their symlink and filesystem admission rules. */
import { isAbsolute, relative, sep } from 'node:path';

export function path_is_within(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix !== '..' && !suffix.startsWith(`..${sep}`) && !isAbsolute(suffix);
}
