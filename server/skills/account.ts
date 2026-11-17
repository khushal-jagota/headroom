/** Account publication preserves entries owned by another home or by the user. */
import {
  constants,
  closeSync,
  existsSync,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  readdirSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { flock } from '../core/locks/flock.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
import type { BuiltInDriverFacts } from '../drivers/built_in_manifests.ts';
import { home_path } from '../drivers/core/paths.ts';
import { managed_skills_home, with_skill_publication_lock } from './files.ts';
import {
  RETIRED_PANELS_SKILL_NAMES,
  WORKER_ONLY_SKILL_NAMES,
  resolve_native_skill_path,
} from './native_publication.ts';
import {
  close_native_descriptor,
  native_path_bytes,
  directory_open_flags,
  mkdir_relative,
  open_directory_descriptor,
  open_relative,
  read_directory_names,
  readlink_relative,
  symlink_relative,
  unlink_relative,
} from '../executor/mac/platform.ts';

/** A provider home, and the one folder inside it that holds the linked skills. */
export interface AccountSkillHome {
  readonly path: string;
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
/** Each distinct home once, by canonical path, in path order. */
function canonical_homes(homes: readonly AccountSkillHomeInput[]): AccountSkillHome[] {
  const distinct = new Map<string, AccountSkillHome>();
  for (const home of homes) {
    const { path, skills_directory } =
      typeof home === 'string' ? { path: home, skills_directory: 'skills' } : home;
    const resolved = { path: canonical(path), skills_directory };
    distinct.set(JSON.stringify(resolved), resolved);
  }
  return [...distinct.values()].sort(
    (a, b) => compare(a.path, b.path) || compare(a.skills_directory, b.skills_directory),
  );
}
const compare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
function warning(path: string, owner: string): string {
  return `Panels supports one instance per OS account. Account skill provisioning was skipped: ${path} belongs to ${owner}. Existing provider skills were preserved. The server can start, but this home cannot own its Worker skills. Use a separate OS account for another Panels home.`;
}
function unreadable(parent: string, error: unknown): string {
  return warning(
    (error as NodeJS.ErrnoException)?.path || parent,
    `an unreadable or conflicting account entry (${error instanceof Error ? error.message : String(error)})`,
  );
}
/** Read-only preflight also serves setup before any persistent provider effects. */
export function account_skill_provisioning_conflict(
  parent: string,
  homes: readonly AccountSkillHomeInput[],
  skill_names?: readonly string[],
): string | null {
  try {
    const source = canonical(managed_skills_home(parent));
    const names = new Set([
      ...(skill_names ??
        readdirSync(source, { withFileTypes: true })
          .filter((entry) => entry.isDirectory())
          .map((entry) => entry.name)),
      ...RETIRED_PANELS_SKILL_NAMES,
      ...WORKER_ONLY_SKILL_NAMES,
    ]);
    for (const home of canonical_homes(homes)) {
      const target = join(home.path, home.skills_directory),
        metadata = stat(target);
      if (metadata?.isSymbolicLink()) {
        const owner = canonical(target);
        if (owner !== source) return warning(target, owner);
        continue;
      }
      if (metadata && !metadata.isDirectory()) return warning(target, 'an unowned entry');
      for (const name of [...names].sort()) {
        const entry = join(target, name),
          metadata = stat(entry);
        if (metadata?.isSymbolicLink()) {
          const owner = canonical(entry);
          if (owner !== join(source, name)) return warning(entry, owner);
        } else if (metadata) return warning(entry, 'an unowned entry');
      }
    }
    return null;
  } catch (error) {
    return warning(
      (error as NodeJS.ErrnoException)?.path || parent,
      `unreadable ownership (${error instanceof Error ? error.message : String(error)})`,
    );
  }
}
function same_directory(path: string, fd: number): void {
  const actual = lstatSync(path),
    expected = fstatSync(fd);
  if (!actual.isDirectory() || actual.dev !== expected.dev || actual.ino !== expected.ino)
    throw new Error(`Account skill directory changed: ${path}`);
}
function owned_link(parent: number, path: string, name: string, expected: string): boolean {
  const entry = join(path, name),
    before = stat(entry),
    link = readlink_relative(parent, name);
  if (!before?.isSymbolicLink() || typeof link !== 'string') return false;
  if (canonical(isAbsolute(link) ? link : join(path, link)) !== expected) return false;
  const after = stat(entry);
  if (!after || before.dev !== after.dev || before.ino !== after.ino) return false;
  unlink_relative(parent, name);
  return true;
}
/** Provider locks serialize independent Panels homes before the ownership recheck. */
export function provision_account_skills(
  parent: string,
  native_homes: readonly AccountSkillHomeInput[] = account_skill_homes(),
): string | null {
  const locks: number[] = [];
  try {
    const homes = canonical_homes(native_homes);
    return with_skill_publication_lock(parent, (source) => {
      let conflict = account_skill_provisioning_conflict(parent, homes);
      if (conflict) return conflict;
      // One lock per home directory, even where two skills folders share it.
      for (const home of new Set(homes.map((home) => home.path))) {
        mkdirSync(home, { recursive: true });
        const path = join(home, '.panels-skills.lock');
        const fd = openSync(
          path,
          constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW,
          0o600,
        );
        locks.push(fd);
        const metadata = fstatSync(fd);
        if (!metadata.isFile() || metadata.uid !== process.getuid?.())
          return warning(path, 'an unowned lock');
        const errno = flock(fd, 'exclusive');
        if (errno !== 0) throw new Error(`flock failed: errno ${errno}`);
      }
      conflict = account_skill_provisioning_conflict(parent, homes);
      if (conflict) return conflict;
      const hidden = new Set([...RETIRED_PANELS_SKILL_NAMES, ...WORKER_ONLY_SKILL_NAMES]);
      const names = readdirSync(source, { withFileTypes: true })
        .filter(
          (entry) =>
            entry.isDirectory() &&
            !hidden.has(entry.name) &&
            existsSync(join(source, entry.name, 'SKILL.md')),
        )
        .map((entry) => entry.name)
        .sort();
      for (const { path: home, skills_directory } of homes) {
        const home_fd = open_directory_descriptor(home);
        try {
          same_directory(home, home_fd);
          const target = join(home, skills_directory);
          if (
            stat(target)?.isSymbolicLink() &&
            !owned_link(home_fd, home, skills_directory, source)
          )
            return warning(target, 'an unowned legacy link');
          try {
            mkdir_relative(home_fd, skills_directory, 0o777);
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
          }
          const fd = open_relative(home_fd, skills_directory, directory_open_flags);
          try {
            same_directory(target, fd);
            for (const name of read_directory_names(fd)) {
              const link = readlink_relative(fd, name);
              if (
                typeof link !== 'string' ||
                (!hidden.has(name) && existsSync(native_path_bytes(join(target, name))))
              )
                continue;
              same_directory(target, fd);
              if (
                canonical(isAbsolute(link) ? link : join(target, link)) === join(source, name) &&
                !owned_link(fd, target, name, join(source, name))
              )
                return warning(join(target, name), 'a changed account link');
            }
            for (const name of names) {
              same_directory(target, fd);
              try {
                symlink_relative(fd, name, join(source, name));
              } catch (error) {
                if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
                const link = readlink_relative(fd, name);
                if (typeof link !== 'string')
                  return warning(join(target, name), 'an unowned entry');
                const owner = canonical(isAbsolute(link) ? link : join(target, link));
                if (owner !== join(source, name)) return warning(join(target, name), owner);
              }
            }
          } finally {
            close_native_descriptor(fd);
          }
        } finally {
          close_native_descriptor(home_fd);
        }
      }
      return null;
    });
  } catch (error) {
    return unreadable(parent, error);
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
