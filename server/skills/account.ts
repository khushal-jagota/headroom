/** Machine code links the provider account skill folders. The server removes only its own old links. */
import { constants, closeSync, fstatSync, lstatSync, openSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { flock } from '../core/locks/flock.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
import type { BuiltInDriverFacts } from '../drivers/built_in_manifests.ts';
import { home_path } from '../drivers/core/paths.ts';
import { with_skill_publication_lock } from './files.ts';
import { resolve_native_skill_path } from './native_publication.ts';
import {
  close_native_descriptor,
  native_path_bytes,
  directory_open_flags,
  open_directory_components,
  open_directory_descriptor,
  open_relative,
  read_directory_names,
  relative_directory_components,
  readlink_relative,
  unlink_relative,
} from '../executor/mac/platform.ts';
/** A provider home, and the one folder inside it that holds the linked skills. */
export interface AccountSkillHome {
  readonly path: string;
  /** Relative to the home, one or more components. */
  readonly skills_directory: string;
}
/** A bare path is a home that keeps its skills in 'skills'. */
export type AccountSkillHomeInput = string | AccountSkillHome;
/** One home per built-in driver that has one, in BUILT_IN_DRIVERS order. */
export function account_skill_homes(
  environment: Readonly<NodeJS.ProcessEnv> = process.env,
  drivers: readonly Pick<BuiltInDriverFacts, 'manifest'>[] = BUILT_IN_MANIFESTS,
): AccountSkillHome[] {
  const account_home = environment.HOME || homedir();
  return drivers.flatMap(({ manifest: { home } }) =>
    home
      ? [
          {
            path: home_path(home, environment, account_home, {
              unset: '||',
              expand_configured: true,
            }),
            skills_directory: home.skills_directory ?? 'skills',
          },
        ]
      : [],
  );
}
function stat(path: string) {
  try {
    return lstatSync(native_path_bytes(path));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    return undefined;
  }
}
const canonical = (path: string) => resolve_native_skill_path(resolve(path));
/** Each distinct home once, by canonical path, in path order. Refuses an unsafe skills folder. */
function canonical_homes(homes: readonly AccountSkillHomeInput[]): AccountSkillHome[] {
  const distinct = new Map<string, AccountSkillHome>();
  for (const home of homes) {
    const { path, skills_directory } =
      typeof home === 'string' ? { path: home, skills_directory: 'skills' } : home;
    relative_directory_components(skills_directory);
    const resolved = { path: canonical(path), skills_directory };
    distinct.set(JSON.stringify(resolved), resolved);
  }
  return [...distinct.values()].sort(
    (a, b) => compare(a.path, b.path) || compare(a.skills_directory, b.skills_directory),
  );
}
const compare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
function same_directory(path: string, fd: number): void {
  const actual = lstatSync(path),
    expected = fstatSync(fd);
  if (!actual.isDirectory() || actual.dev !== expected.dev || actual.ino !== expected.ino)
    throw new Error(`Account skill directory changed: ${path}`);
}
/** Unlinks the entry only while it is the same link and resolves to expected. */
function remove_owned_link(parent: number, path: string, name: string, expected: string): void {
  const entry = join(path, name),
    before = stat(entry),
    link = readlink_relative(parent, name);
  if (!before?.isSymbolicLink() || typeof link !== 'string') return;
  if (canonical(isAbsolute(link) ? link : join(path, link)) !== expected) return;
  const after = stat(entry);
  if (!after || before.dev !== after.dev || before.ino !== after.ino) return;
  unlink_relative(parent, name);
}
/** True when the skills folder holds a link into source. Read-only, never through a linked folder. */
function has_server_links(home: AccountSkillHome, source: string): boolean {
  const components = relative_directory_components(home.skills_directory);
  for (let depth = 1; depth <= components.length; depth++)
    if (!stat(join(home.path, ...components.slice(0, depth)))?.isDirectory()) return false;
  const target = join(home.path, home.skills_directory);
  return readdirSync(target).some(
    (name) =>
      stat(join(target, name))?.isSymbolicLink() &&
      canonical(join(target, name)) === join(source, name),
  );
}
/**
 * Machine code refuses an entry that it does not own, so the server's old links would stop its
 * first Ticket start. Remove each link that points into this server's own copies. Keep every
 * other entry. Provider locks serialize independent Panels homes before the ownership recheck.
 */
export function remove_server_account_skill_links(
  parent: string,
  native_homes: readonly AccountSkillHomeInput[] = account_skill_homes(),
): string | null {
  const locks: number[] = [];
  try {
    const homes = canonical_homes(native_homes);
    return with_skill_publication_lock(parent, (source) => {
      // A home without the server's links gets no lock file.
      const linked = homes.filter((home) => has_server_links(home, source));
      // One lock per home directory, even where two skills folders share it.
      for (const home of new Set(linked.map((home) => home.path))) {
        const path = join(home, '.panels-skills.lock');
        const fd = openSync(
          path,
          constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW,
          0o600,
        );
        locks.push(fd);
        const metadata = fstatSync(fd);
        if (!metadata.isFile() || metadata.uid !== process.getuid?.())
          return removal_failed(path, 'the lock is not a file that this account owns');
        const errno = flock(fd, 'exclusive');
        if (errno !== 0) throw new Error(`flock failed: errno ${errno}`);
      }
      for (const { path: home, skills_directory } of linked) {
        const parents = relative_directory_components(skills_directory),
          folder = parents.pop()!;
        const home_fd = open_directory_descriptor(home);
        let parent_fd = home_fd;
        try {
          same_directory(home, home_fd);
          if (parents.length) parent_fd = open_directory_components(home_fd, parents);
          const target = join(home, ...parents, folder);
          const fd = open_relative(parent_fd, folder, directory_open_flags);
          try {
            for (const name of read_directory_names(fd)) {
              same_directory(target, fd);
              remove_owned_link(fd, target, name, join(source, name));
            }
          } finally {
            close_native_descriptor(fd);
          }
        } finally {
          try {
            if (parent_fd !== home_fd) close_native_descriptor(parent_fd);
          } finally {
            close_native_descriptor(home_fd);
          }
        }
      }
      return null;
    });
  } catch (error) {
    return removal_failed(
      (error as NodeJS.ErrnoException)?.path || parent,
      error instanceof Error ? error.message : String(error),
    );
  } finally {
    for (const fd of locks.reverse()) {
      try {
        flock(fd, 'unlock');
      } finally {
        closeSync(fd);
      }
    }
  }
}
function removal_failed(path: string, reason: string): string {
  return `Panels did not remove its old skill links from the provider account folders: ${path} (${reason}). Machine code can refuse those links when a Ticket starts.`;
}
