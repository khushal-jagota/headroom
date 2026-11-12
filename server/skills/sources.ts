import {
  closeSync,
  constants,
  openSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  symlinkSync,
} from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import koffi from 'koffi';
import type { Database } from '../core/database/contracts.ts';
import { read_all_skill_sources } from './data.ts';
import { atomic_replace_bytes, managed_skills_home, with_skill_publication_lock } from './files.ts';
export const RETIRED_PANELS_SKILL_NAMES = new Set([
  'panels-rollover',
  'panels-sprint-planning',
  'panels-ticket-management',
  'panels-update-chief-of-staff',
]);
export const WORKER_ONLY_SKILL_NAMES = new Set([
  'panels-worker-personal-task',
  'panels-worker-planning-day',
  'panels-worker-planning-midday-check',
  'panels-worker-planning-sprint',
]);
function realExisting(path: string): string {
  try {
    return realpathSync(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    const parent = dirname(path);
    if (parent === path) throw error;
    return join(realExisting(parent), path.slice(parent.length + (parent === '/' ? 0 : 1)));
  }
}
function stat(path: string) {
  try {
    return lstatSync(path);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e;
    return null;
  }
}
function within(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === '' || (!rel.startsWith('../') && rel !== '..' && !isAbsolute(rel));
}
/** Test composition supplies a fresh owned HOME. Validate before any directory/link mutation. */
export function require_synthetic_destination(home: string, destination: string): void {
  const canonical = realpathSync(home),
    absolute = resolve(destination);
  if (!within(resolve(home), absolute) || !within(canonical, realExisting(absolute)))
    throw new Error(`synthetic destination escapes owned home: ${destination}`);
}
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
  if (options.test_home) require_synthetic_destination(options.test_home, home);
  const source = realExisting(managed_skills_home(parent));
  const hidden = new Set([
    ...RETIRED_PANELS_SKILL_NAMES,
    ...[...WORKER_ONLY_SKILL_NAMES].filter((n) => !options.required_skill_names?.includes(n)),
  ]);
  const target = join(home, 'skills');
  if (options.test_home) require_synthetic_destination(options.test_home, target);
  mkdirSync(dirname(target), { recursive: true });
  const names = new Set(
    readdirSync(source, { withFileTypes: true })
      .filter(
        (e) =>
          !hidden.has(e.name) && e.isDirectory() && existsSync(join(source, e.name, 'SKILL.md')),
      )
      .map((e) => e.name),
  );
  if (stat(target)?.isSymbolicLink()) {
    const previous = realExisting(target);
    rmSync(target);
    mkdirSync(target);
    if (existsSync(previous) && stat(previous)?.isDirectory())
      for (const entry of readdirSync(previous)) {
        if (!names.has(entry) && !hidden.has(entry))
          symlinkSync(join(previous, entry), join(target, entry));
      }
  }
  if (!existsSync(target)) mkdirSync(target, { recursive: true });
  if (!lstatSync(target).isDirectory())
    throw new Error(`native backend skills path is not a directory: ${target}`);
  for (const name of hidden) rmSync(join(target, name), { recursive: true, force: true });
  for (const name of readdirSync(target)) {
    const link = join(target, name);
    if (
      stat(link)?.isSymbolicLink() &&
      !existsSync(link) &&
      realExisting(dirname(resolve(target, readlinkSync(link)))) === source
    )
      rmSync(link);
  }
  for (const name of names) {
    const link = join(target, name),
      origin = join(source, name);
    if (stat(link)?.isSymbolicLink() && realExisting(link) === origin) continue;
    rmSync(link, { recursive: true, force: true });
    symlinkSync(origin, link, 'dir');
  }
  return source;
}
const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const mkdirat = libc.func('int mkdirat(int dirfd, const char *path, unsigned int mode)');
const openat = libc.func('int openat(int dirfd, const char *path, int flags, unsigned int mode)');
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
      const parentFd = openat(workspaceFd, '.agents', flags, 0);
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
