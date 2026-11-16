import { fstatSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import {
  close_native_descriptor,
  directory_open_flags,
  open_directory_descriptor,
  open_relative,
  read_directory_names,
  rmdir_relative,
  unlink_relative,
} from '../../executor/mac/platform.ts';

type Identity = { dev: number; ino: number };

function check_identity(descriptor: number, expected?: Identity): void {
  if (!expected) return;
  const actual = fstatSync(descriptor);
  if (actual.dev !== expected.dev || actual.ino !== expected.ino)
    throw new Error('Workspace directory changed before removal');
}

/** Darwin has no procfs descriptor paths. Keep every removal relative to an open inode. */
export function remove_darwin_workspace(
  root: string,
  signal?: AbortSignal,
  identity?: Identity,
  parent_identity?: Identity,
  ancestors?: ReadonlyMap<string, Identity>,
): void {
  let parent = open_directory_descriptor('/');
  try {
    let cursor = '/';
    for (const part of dirname(resolve(root)).split('/').filter(Boolean)) {
      signal?.throwIfAborted();
      const next = open_relative(parent, part, directory_open_flags);
      const previous = parent;
      parent = next;
      close_native_descriptor(previous);
      cursor = join(cursor, part);
      check_identity(parent, ancestors?.get(cursor));
    }
    check_identity(parent, parent_identity);
    function remove_entry(parent: number, name: string, expected?: Identity): void {
      signal?.throwIfAborted();
      let directory: number;
      try {
        directory = open_relative(parent, name, directory_open_flags);
      } catch (error) {
        // An expected directory must remain the same directory. Other entries are
        // unlinked relative to their parent and never followed, including symlinks.
        if (expected || !['ENOTDIR', 'ELOOP'].includes((error as NodeJS.ErrnoException).code ?? ''))
          throw error;
        unlink_relative(parent, name);
        return;
      }
      try {
        check_identity(directory, expected);
        const opened = fstatSync(directory);
        for (const child of read_directory_names(directory)) remove_entry(directory, child);
        signal?.throwIfAborted();
        const attached = open_relative(parent, name, directory_open_flags);
        try {
          check_identity(attached, opened);
        } finally {
          close_native_descriptor(attached);
        }
        rmdir_relative(parent, name);
      } finally {
        close_native_descriptor(directory);
      }
    }
    remove_entry(parent, basename(root), identity);
  } finally {
    close_native_descriptor(parent);
  }
}
