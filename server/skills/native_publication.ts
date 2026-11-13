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
export function publish_native_backend_skills(
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
