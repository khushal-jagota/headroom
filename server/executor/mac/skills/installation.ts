/** Blocking publication runs only in the owned worker, before provider creation. */
import { createHash, randomUUID } from 'node:crypto';
import {
  constants,
  fstatSync,
  lstatSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { isMainThread } from 'node:worker_threads';
import { setTimeout as pause } from 'node:timers/promises';
import { BackendSpawnFailed } from '../../../conversation/backends/contracts.ts';
import { prepare_native_skill_publication } from '../../../skills/native_publication.ts';
import {
  close_native_descriptor,
  flock_descriptor,
  open_directory_descriptor,
  open_relative,
  mkdir_relative,
  rename_relative,
  unlink_relative,
  rmdir_relative,
  directory_open_flags,
  read_directory_names,
  readlink_relative,
  symlink_relative,
  sync_file_descriptor,
  sync_directory_descriptor,
  cleanup_directory_readers,
  owns_directory_readers,
} from '../platform.ts';
import type { CanonicalSkillSnapshot } from '../../wire.generated.ts';
import { SKILL_LOCK_RETRY_MS } from './contracts.ts';
import { validate_skill_snapshot } from '../../skills.ts';
export { validate_skill_snapshot } from '../../skills.ts';
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
  close_native_descriptor(fd);
  retained_descriptors.delete(fd);
}
export function cleanup_handles(): void {
  cleanup_directory_readers();
  for (const fd of retained_descriptors) close_handle(fd);
}
export function owns_handles(): boolean {
  return retained_descriptors.size > 0 || owns_directory_readers();
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

function stat(path: string) {
  try {
    return lstatSync(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    return null;
  }
}
function no_linked_state_ancestors(root: string): void {
  if (!isAbsolute(root) || resolve(root) !== root)
    invalid('executor state root must be an absolute normalized path');
  let current = '/';
  for (const component of root.split('/').filter(Boolean)) {
    current = join(current, component);
    if (stat(current)?.isSymbolicLink())
      invalid(`executor state root contains a linked ancestor: ${current}`);
  }
}
function require_same_directory(path: string, descriptor: number): void {
  const expected = fstatSync(descriptor, { bigint: true }),
    actual = lstatSync(path, { bigint: true });
  if (
    actual.isSymbolicLink() ||
    !actual.isDirectory() ||
    expected.dev !== actual.dev ||
    expected.ino !== actual.ino
  )
    invalid(`directory changed during canonical skill publication: ${path}`);
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
function check_generation(parent: number, name: string, snapshot: CanonicalSkillSnapshot): void {
  const generation = open_directory_at(parent, name);
  try {
    const source = open_directory_at(generation, 'skills');
    try {
      const names = new Set(snapshot.sources.map((value) => value.skill_name)),
        actual = read_directory_names(source);
      if (actual.length !== names.size || actual.some((name) => !names.has(name)))
        invalid('canonical skill generation content differs');
      for (const value of snapshot.sources) {
        const folder = open_directory_at(source, value.skill_name);
        try {
          const entries = read_directory_names(folder);
          if (entries.length !== 1 || entries[0] !== 'SKILL.md')
            invalid('canonical skill directory content differs');
          const file = retain(
            open_relative(
              folder,
              'SKILL.md',
              constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
            ),
          );
          try {
            if (
              !fstatSync(file).isFile() ||
              !readFileSync(file).equals(Buffer.from(value.source_text))
            )
              invalid('canonical skill generation bytes differ');
          } finally {
            close_handle(file);
          }
        } finally {
          close_handle(folder);
        }
      }
    } finally {
      close_handle(source);
    }
  } finally {
    close_handle(generation);
  }
}
function create_generation(
  parent: number,
  name: string,
  snapshot: CanonicalSkillSnapshot,
  replace_invalid: boolean,
): void {
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
          sync_file_descriptor(file);
        } finally {
          close_handle(file);
        }
        sync_directory_descriptor(folder);
      } finally {
        close_handle(folder);
      }
    }
    sync_directory_descriptor(source);
    sync_directory_descriptor(stage);
    if (replace_invalid) rename_relative(parent, name, parent, `.invalid-${name}-${randomUUID()}`);
    rename_relative(parent, temporary, parent, name);
    installed = true;
    sync_directory_descriptor(parent);
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
export async function install_native_skill_snapshot(
  input: SkillInstallation,
  signal: AbortSignal,
): Promise<string> {
  validate_skill_snapshot(input.snapshot);
  let root_fd: number | undefined,
    parent: number | undefined,
    lock: number | undefined,
    home_fd: number | undefined,
    target_fd: number | undefined;
  try {
    signal.throwIfAborted();
    const { snapshot, root, native_home } = input;
    no_linked_state_ancestors(root);
    // This argument comes from the trusted adapter host, never from the command snapshot.
    if (
      !isAbsolute(native_home) ||
      !stat(native_home)?.isDirectory() ||
      stat(native_home)?.isSymbolicLink()
    )
      invalid(`native provider home must be an absolute existing real directory: ${native_home}`);
    const home = resolve(native_home);
    if (realpathSync(native_home) !== home)
      invalid(`configured native provider home resolves elsewhere: ${native_home}`);
    home_fd = retain(open_directory_descriptor(home));
    try {
      target_fd = open_directory_at(home_fd, 'skills');
    } catch {
      invalid(
        `native provider skills entry must be an existing real directory: ${join(home, 'skills')}`,
      );
    }
    const held_home = home_fd,
      held_target = target_fd;
    const assert_current = () => {
      no_linked_state_ancestors(root);
      // Metadata rechecks never supply mutation authority. All mutations keep held_target.
      require_same_directory(home, held_home);
      require_same_directory(join(home, 'skills'), held_target);
      if (root_fd !== undefined) require_same_directory(root, root_fd);
    };
    const digest = generation_digest(snapshot),
      skills = join(root, 'skills'),
      generation = join(skills, digest);
    // Plan and inspect every account entry before even a derived state directory is created.
    const publication = prepare_native_skill_publication(home, join(generation, 'skills'), {
      required_skill_names: snapshot.required_skill_names,
      policy: {
        kind: 'mac-owned',
        state_root: root,
        source_names: snapshot.sources.map((value) => value.skill_name),
        directory: {
          assert_current,
          list: () =>
            read_directory_names(held_target).map((name) => {
              const target = readlink_relative(held_target, name);
              if (target === undefined) invalid(`native skills entry changed: ${name}`);
              return { name, target };
            }),
          read: (name) => readlink_relative(held_target, name),
          unlink: (name) => unlink_relative(held_target, name),
          symlink: (name, target) => symlink_relative(held_target, name, target),
          rename: (source, target) => rename_relative(held_target, source, held_target, target),
        },
      },
    });
    root_fd = retain(open_directory_descriptor(root, { create: true }));
    try {
      mkdir_relative(root_fd, 'skills', 0o777);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    }
    sync_directory_descriptor(root_fd);
    parent = open_directory_at(root_fd, 'skills');
    lock = retain(
      open_relative(
        parent,
        '.install.lock',
        constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW | constants.O_NONBLOCK,
        0o600,
      ),
    );
    if (!fstatSync(lock).isFile()) invalid('canonical skill lock is not a regular file');
    // Root-local serialization only. Other installations do not share this account lock.
    while (!flock_descriptor(lock, 'try-exclusive'))
      await pause(SKILL_LOCK_RETRY_MS, undefined, { signal });
    signal.throwIfAborted();
    let rebuild = false;
    const before_check = new Set(retained_descriptors);
    try {
      check_generation(parent, digest, snapshot);
    } catch (error) {
      if ([...retained_descriptors].some((fd) => !before_check.has(fd)) || owns_directory_readers())
        throw error;
      rebuild = readlink_relative(parent, digest) !== undefined;
      create_generation(parent, digest, snapshot, rebuild);
    }
    publication.publish();
    sync_directory_descriptor(held_target);
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
    if (root_fd !== undefined) close_handle(root_fd);
    if (target_fd !== undefined) close_handle(target_fd);
    if (home_fd !== undefined) close_handle(home_fd);
  }
}
