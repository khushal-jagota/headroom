/** Account skill publication has no database, lock, or native workspace imports. */
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  symlinkSync,
} from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
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
function publish_server_skills(
  home: string,
  source_directory: string,
  options: { test_home?: string; required_skill_names?: readonly string[] } = {},
): string {
  if (options.test_home) require_synthetic_destination(options.test_home, home);
  const source = realExisting(source_directory);
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

/** The server retains Python replacement semantics. The Mac can mutate only its own links. */
export interface NativePublicationDirectory {
  list(): { name: string; target: string | null }[];
  read(name: string): string | null | undefined;
  assert_current(): void;
  unlink(name: string): void;
  symlink(name: string, target: string): void;
  rename(source: string, target: string): void;
}
export type NativePublicationPolicy =
  | { kind: 'server' }
  | {
      kind: 'mac-owned';
      state_root: string;
      source_names: readonly string[];
      directory: NativePublicationDirectory;
    };
export interface NativePublicationOptions {
  test_home?: string;
  required_skill_names?: readonly string[];
  policy: NativePublicationPolicy;
}
function resolve_mac_path(path: string, active = new Set<string>()): string {
  let current = '/';
  for (const part of path.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      current = dirname(current);
      continue;
    }
    const candidate = join(current, part);
    if (stat(candidate)?.isSymbolicLink()) {
      if (active.has(candidate)) throw new Error(`symbolic link loop: ${candidate}`);
      active.add(candidate);
      const target = readlinkSync(candidate);
      current = resolve_mac_path(isAbsolute(target) ? target : current + '/' + target, active);
      active.delete(candidate);
    } else current = candidate;
  }
  return current;
}
const TEMPORARY_SKILL_LINK =
  /^\.panels-skill-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;
export function prepare_native_skill_publication(
  home: string,
  source_directory: string,
  options: NativePublicationOptions,
): { publish(): string } {
  const policy = options.policy;
  if (policy.kind === 'server')
    return { publish: () => publish_server_skills(home, source_directory, options) };
  const directory = policy.directory,
    state = resolve(policy.state_root),
    source = resolve(source_directory);
  if (!within(state, source))
    throw new Error('Canonical skills source escapes the executor state root.');
  const hidden = new Set([
    ...RETIRED_PANELS_SKILL_NAMES,
    ...[...WORKER_ONLY_SKILL_NAMES].filter((name) => !options.required_skill_names?.includes(name)),
  ]);
  const wanted = new Set(policy.source_names.filter((name) => !hidden.has(name)));
  const reserved = new Map([...hidden, ...wanted].map((name) => [name.toLowerCase(), name]));
  const conflict = (name: string): never => {
    throw new Error(`native skill publication refuses entry: ${name}`);
  };
  const owned = (target: string) =>
    within(state, resolve_mac_path(isAbsolute(target) ? target : home + '/skills/' + target));
  const scan = () => {
    directory.assert_current();
    const entries = directory
      .list()
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    const remove: { name: string; target: string }[] = [];
    for (const entry of entries) {
      const name = reserved.get(entry.name.toLowerCase());
      const ours = entry.target !== null && owned(entry.target); // Resolve every link, including unrelated loops, before mutation.
      if (name !== undefined && (entry.name !== name || !ours)) conflict(entry.name);
      if (ours && (!wanted.has(entry.name) || TEMPORARY_SKILL_LINK.test(entry.name)))
        remove.push({ name: entry.name, target: entry.target! });
    }
    return { entries, remove };
  };
  const initial = scan(),
    original = JSON.stringify(initial.entries);
  const check = (name: string, expected: string | undefined) => {
    directory.assert_current();
    if (expected !== undefined && !owned(expected)) conflict(name);
    if (directory.read(name) !== expected) conflict(name);
  };
  return {
    publish() {
      if (JSON.stringify(scan().entries) !== original)
        throw new Error('Native skills folder changed before publication.');
      for (const entry of initial.remove) {
        check(entry.name, entry.target);
        directory.unlink(entry.name);
      }
      for (const name of wanted) {
        const expected = initial.entries.find((entry) => entry.name === name)?.target;
        const target = join(source, name);
        if (!owned(target)) conflict(name);
        if (expected === target) continue;
        if (expected === null) return conflict(name);
        check(name, expected);
        const temporary = '.panels-skill-' + randomUUID();
        directory.symlink(temporary, target);
        let installed = false;
        try {
          check(temporary, target);
          // This immediate recheck is not a conditional-inode rename or an account lock.
          check(name, expected);
          directory.rename(temporary, name);
          installed = true;
        } finally {
          if (!installed && directory.read(temporary) === target) {
            check(temporary, target);
            directory.unlink(temporary);
          }
        }
      }
      return source;
    },
  };
}
export function publish_native_backend_skills(
  home: string,
  source_directory: string,
  options: NativePublicationOptions,
): string {
  return prepare_native_skill_publication(home, source_directory, options).publish();
}
