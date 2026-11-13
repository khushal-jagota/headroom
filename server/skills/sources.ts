import { closeSync, constants, openSync, existsSync, readFileSync, realpathSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import koffi from 'koffi';
import type { Database } from '../core/database/contracts.ts';
import { read_all_skill_sources } from './data.ts';
import { atomic_replace_bytes, managed_skills_home, with_skill_publication_lock } from './files.ts';
import {
  publish_native_backend_skills,
  require_synthetic_destination,
} from './native_publication.ts';
export {
  RETIRED_PANELS_SKILL_NAMES,
  WORKER_ONLY_SKILL_NAMES,
  require_synthetic_destination,
} from './native_publication.ts';
export function write_skill_home(database: Database, parent: string): string {
  return with_skill_publication_lock(parent, (root) => {
    const sources = database.read(read_all_skill_sources);
    for (const [name, source] of Object.entries(sources)) {
      const path = join(root, name, 'SKILL.md'),
        content = Buffer.from(source);
      if (existsSync(path) && readFileSync(path).equals(content)) continue;
      atomic_replace_bytes(path, content);
    }
    return root;
  });
}
export function provision_native_backend_skills(
  home: string,
  parent: string,
  options: { test_home?: string; required_skill_names?: readonly string[] } = {},
): string {
  return publish_native_backend_skills(home, managed_skills_home(parent), {
    ...options,
    policy: { kind: 'server' },
  });
}
const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const mkdirat = libc.func('int mkdirat(int dirfd, const char *path, unsigned int mode)');
const openat = libc.func('int openat(int dirfd, const char *path, int flags, ...)');
const symlinkat = libc.func('int symlinkat(const char *target, int dirfd, const char *path)');
const readlinkat = libc.func(
  'long readlinkat(int dirfd, const char *path, _Out_ void *buffer, size_t size)',
);
const unlinkat = libc.func('int unlinkat(int dirfd, const char *path, int flags)');
function operationError(operation: string): Error {
  return new Error(`${operation} failed: errno ${koffi.errno()}`);
}
export function provision_workspace_skills(
  workspace: string,
  parent: string,
  options: { test_home?: string } = {},
): string {
  if (options.test_home) require_synthetic_destination(options.test_home, workspace);
  return with_skill_publication_lock(parent, () => {
    const projection = join(parent, 'agent-skill-home');
    provision_native_backend_skills(projection, parent);
    const managed = realpathSync(managed_skills_home(parent));
    const source = realpathSync(join(projection, 'skills')),
      agents = join(workspace, '.agents'),
      target = join(agents, 'skills');
    const conflict = (path: string) =>
      new Error(
        `workspace skills conflict at ${path}; expected ${target} to link to ${source} through a real .agents directory`,
      );
    const flags = constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW;
    const workspaceFd = openSync(workspace, flags);
    try {
      if (mkdirat(workspaceFd, '.agents', 0o777) !== 0 && koffi.errno() !== 17)
        throw operationError('mkdirat');
      const parentFd = openat(workspaceFd, '.agents', flags, 'int', 0);
      if (parentFd < 0) {
        const errno = koffi.errno();
        if ([20, 40, 62].includes(errno)) throw conflict(agents);
        throw operationError('openat');
      }
      try {
        if (symlinkat(source, parentFd, 'skills') === 0) return source;
        if (koffi.errno() !== 17) throw operationError('symlinkat');
        // Read and replace through the held directory. A renamed .agents cannot
        // redirect either mutation to a foreign directory between these calls.
        const buffer = Buffer.alloc(65536);
        const length = Number(readlinkat(parentFd, 'skills', buffer, buffer.length));
        if (length < 0 || length === buffer.length) throw conflict(target);
        const link = buffer.subarray(0, length).toString('utf8');
        let current: string;
        try {
          current = realpathSync(isAbsolute(link) ? link : join(agents, link));
        } catch {
          throw conflict(target);
        }
        if (current === managed) {
          if (unlinkat(parentFd, 'skills', 0) !== 0) throw operationError('unlinkat');
          if (symlinkat(source, parentFd, 'skills') !== 0) throw operationError('symlinkat');
        } else if (current !== source) throw conflict(target);
      } finally {
        closeSync(parentFd);
      }
    } finally {
      closeSync(workspaceFd);
    }
    return source;
  });
}
