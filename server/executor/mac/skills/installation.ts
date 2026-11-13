/** Blocking publication runs only in the owned worker, before provider creation. */
import { createHash, randomUUID } from 'node:crypto';
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { isMainThread } from 'node:worker_threads';
import { setTimeout as pause } from 'node:timers/promises';
import { BackendSpawnFailed } from '../../../conversation/backends/contracts.ts';
import { skill_description } from '../../../skills/logic.ts';
import {
  publish_native_backend_skills,
  RETIRED_PANELS_SKILL_NAMES,
} from '../../../skills/native_publication.ts';
import {
  flock_descriptor,
  open_directory_descriptor,
  open_relative,
  mkdir_relative,
  rename_relative,
  unlink_relative,
  rmdir_relative,
  directory_open_flags,
} from '../platform.ts';
import { python_string_repr as python_repr } from '../../../core/python_repr.ts';
import type { CanonicalSkillSnapshot } from '../../wire.generated.ts';
import { SKILL_LOCK_RETRY_MS } from './contracts.ts';
import type { SkillInstallation } from './contracts.ts';

if (isMainThread) throw new Error('Canonical skill publication requires its owned worker.');
const retained_descriptors = new Set<number>();
function retain(fd: number): number {
  retained_descriptors.add(fd);
  return fd;
}
function open_directory_at(parent: number, name: string): number {
  return retain(open_relative(parent, name, directory_open_flags));
}
function close_handle(fd: number): void {
  closeSync(fd);
  retained_descriptors.delete(fd);
}
export function cleanup_handles(): void {
  for (const fd of retained_descriptors) close_handle(fd);
}
export function owns_handles(): boolean {
  return retained_descriptors.size > 0;
}
process.once('uncaughtExceptionMonitor', () => {
  try {
    cleanup_handles();
  } catch {}
});
process.once('exit', () => {
  try {
    cleanup_handles();
  } catch {}
});
function invalid(message: string): never {
  throw new Error(message);
}
export function validate_skill_snapshot(snapshot: CanonicalSkillSnapshot): void {
  try {
    const names = new Set<string>(),
      folded = new Set<string>();
    for (const source of snapshot.sources) {
      const name = source.skill_name;
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]*(?![\s\S])/u.test(name)) invalid('unsafe skill name');
      if (folded.has(name.toLowerCase())) invalid('duplicate skill name');
      names.add(name);
      folded.add(name.toLowerCase());
      if (/\p{Surrogate}/u.test(source.source_text)) {
        const points = [...source.source_text],
          index = points.findIndex((point) => /\p{Surrogate}/u.test(point));
        let end = index + 1;
        while (end < points.length && /\p{Surrogate}/u.test(points[end]!)) end++;
        const position =
          end === index + 1
            ? `character '\\u${points[index]!.charCodeAt(0).toString(16).padStart(4, '0')}' in position ${index}`
            : `characters in position ${index}-${end - 1}`;
        invalid(`'utf-8' codec can't encode ${position}: surrogates not allowed`);
      }
      skill_description(source.source_text, name);
    }
    const required = snapshot.required_skill_names;
    if (new Set(required).size !== required.length) invalid('duplicate required skill name');
    if (!required.includes('panels') || !required.includes('panels-worker'))
      invalid('base Worker skills are not required');
    if (required.some((name) => RETIRED_PANELS_SKILL_NAMES.has(name)))
      invalid('required canonical skill is retired');
    if (required.some((name) => !names.has(name))) invalid('required canonical skill is absent');
  } catch (error) {
    throw new BackendSpawnFailed(
      `invalid canonical skill snapshot: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
function stat(path: string) {
  try {
    return lstatSync(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    return null;
  }
}
function owned_directory(path: string): void {
  if (stat(path)?.isSymbolicLink()) invalid(`executor skill directory is a symlink: ${path}`);
  mkdirSync(path, { recursive: false });
}
function require_owned_directory(path: string): void {
  try {
    owned_directory(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
  }
  if (!stat(path)?.isDirectory()) invalid(`executor skill path is not a directory: ${path}`);
}
function generation_digest(snapshot: CanonicalSkillSnapshot): string {
  const digest = createHash('sha256');
  for (const source of [...snapshot.sources].sort((a, b) =>
    a.skill_name < b.skill_name ? -1 : a.skill_name > b.skill_name ? 1 : 0,
  ))
    for (const value of [source.skill_name, source.source_text]) {
      const bytes = Buffer.from(value, 'utf8'),
        length = Buffer.alloc(8);
      length.writeBigUInt64BE(BigInt(bytes.length));
      digest.update(length);
      digest.update(bytes);
    }
  return digest.digest('hex');
}
function check_generation(
  generation: string,
  parent: number,
  name: string,
  snapshot: CanonicalSkillSnapshot,
): void {
  const root = join(generation, 'skills');
  if (
    stat(generation)?.isSymbolicLink() ||
    stat(root)?.isSymbolicLink() ||
    !stat(root)?.isDirectory()
  )
    invalid('unsafe canonical skill generation');
  const names = new Set(snapshot.sources.map((source) => source.skill_name)),
    actual = readdirSync(root);
  if (actual.length !== names.size || actual.some((name) => !names.has(name)))
    invalid('canonical skill generation content differs');
  const generation_fd = open_directory_at(parent, name),
    source_fd = open_directory_at(generation_fd, 'skills');
  try {
    for (const source of snapshot.sources) {
      const folder = join(root, source.skill_name),
        file = join(folder, 'SKILL.md');
      if (stat(folder)?.isSymbolicLink() || stat(file)?.isSymbolicLink())
        invalid('canonical skill generation contains symlinks');
      const entries = readdirSync(folder);
      if (entries.length !== 1 || entries[0] !== 'SKILL.md')
        invalid('canonical skill directory content differs');
      if (!stat(file)?.isFile()) invalid('canonical skill generation bytes differ');
      const folder_fd = open_directory_at(source_fd, source.skill_name);
      try {
        const file_fd = retain(
          open_relative(
            folder_fd,
            'SKILL.md',
            constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
          ),
        );
        try {
          if (
            !fstatSync(file_fd).isFile() ||
            !readFileSync(file_fd).equals(Buffer.from(source.source_text))
          )
            invalid('canonical skill generation bytes differ');
        } finally {
          close_handle(file_fd);
        }
      } finally {
        close_handle(folder_fd);
      }
    }
  } finally {
    close_handle(source_fd);
    close_handle(generation_fd);
  }
}
function create_generation(parent: number, name: string, snapshot: CanonicalSkillSnapshot): void {
  const temporary = '.stage-' + randomUUID();
  mkdir_relative(parent, temporary, 0o700);
  const stage = open_directory_at(parent, temporary);
  let source: number | undefined,
    installed = false;
  const folders: string[] = [];
  try {
    mkdir_relative(stage, 'skills', 0o777);
    source = open_directory_at(stage, 'skills');
    for (const value of snapshot.sources) {
      mkdir_relative(source, value.skill_name, 0o777);
      folders.push(value.skill_name);
      const folder = open_directory_at(source, value.skill_name);
      try {
        const file = retain(
          open_relative(
            folder,
            'SKILL.md',
            constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
            0o666,
          ),
        );
        try {
          writeFileSync(file, value.source_text, { encoding: 'utf8' });
        } finally {
          close_handle(file);
        }
      } finally {
        close_handle(folder);
      }
    }
    rename_relative(parent, temporary, parent, name);
    installed = true;
  } finally {
    if (!installed && source !== undefined) {
      for (const name of folders) {
        const folder = open_directory_at(source, name);
        try {
          unlink_relative(folder, 'SKILL.md', true);
        } finally {
          close_handle(folder);
        }
        rmdir_relative(source, name);
      }
      rmdir_relative(stage, 'skills');
    }
    if (source !== undefined) close_handle(source);
    close_handle(stage);
    if (!installed) rmdir_relative(parent, temporary);
  }
}
function resolve_link(path: string): string {
  try {
    return realpathSync(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ELOOP')
      invalid(`Symlink loop from ${python_repr(path)}`);
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    // The Python non-strict resolve follows dangling links too.
    const entry = stat(path);
    if (entry?.isSymbolicLink()) return resolve_link(resolve(dirname(path), readlinkSync(path)));
    const parent = dirname(path);
    return parent === path
      ? path
      : join(resolve_link(parent), path.slice(parent.length + (parent === '/' ? 0 : 1)));
  }
}
export async function install_native_skill_snapshot(
  input: SkillInstallation,
  signal: AbortSignal,
): Promise<string> {
  validate_skill_snapshot(input.snapshot);
  let lock: number | undefined, parent: number | undefined;
  try {
    signal.throwIfAborted();
    const { snapshot, native_home, root } = input,
      skills = join(root, 'skills');
    mkdirSync(dirname(root), { recursive: true });
    require_owned_directory(root);
    require_owned_directory(skills);
    parent = retain(open_directory_descriptor(skills));
    lock = retain(
      open_relative(
        parent,
        '.install.lock',
        constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW | constants.O_NONBLOCK,
        0o600,
      ),
    );
    if (!fstatSync(lock).isFile()) invalid('canonical skill lock is not a regular file');
    while (!flock_descriptor(lock, 'try-exclusive'))
      await pause(SKILL_LOCK_RETRY_MS, undefined, { signal });
    signal.throwIfAborted();
    const digest = generation_digest(snapshot),
      generation = join(skills, digest);
    if (stat(generation)) check_generation(generation, parent, digest, snapshot);
    else create_generation(parent, digest, snapshot);
    mkdirSync(native_home, { recursive: true });
    publish_native_backend_skills(native_home, join(generation, 'skills'), {
      required_skill_names: snapshot.required_skill_names,
    });
    const names = new Set(snapshot.sources.map((source) => source.skill_name)),
      generations = realpathSync(skills);
    for (const name of readdirSync(join(native_home, 'skills'))) {
      const entry = join(native_home, 'skills', name);
      if (!names.has(name) && stat(entry)?.isSymbolicLink()) {
        const rel = relative(generations, resolve_link(entry));
        if (rel === '' || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith('../')))
          rmSync(entry);
      }
    }
    return generation;
  } catch (error) {
    if (
      signal.aborted &&
      (error === signal.reason || (error instanceof Error && error.name === 'AbortError'))
    )
      throw signal.reason;
    throw new BackendSpawnFailed(
      `cannot install canonical skill snapshot: ${error instanceof Error ? error.message : String(error)}`,
    );
  } finally {
    if (lock !== undefined) close_handle(lock);
    if (parent !== undefined) close_handle(parent);
  }
}
