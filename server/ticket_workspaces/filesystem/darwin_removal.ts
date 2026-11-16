import { constants, fstatSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { setImmediate } from 'node:timers/promises';
import {
  close_native_descriptor,
  directory_open_flags,
  open_directory_descriptor,
  open_relative,
  read_directory_names,
  rmdir_relative,
  stat_darwin_entry,
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
export async function remove_darwin_workspace(
  root: string,
  signal?: AbortSignal,
  identity?: Identity,
  parent_identity?: Identity,
  ancestors?: ReadonlyMap<string, Identity>,
): Promise<void> {
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
    async function remove_entry(parent: number, name: string, expected?: Identity): Promise<void> {
      await setImmediate(undefined, { signal });
      signal?.throwIfAborted();
      const entry = stat_darwin_entry(parent, name);
      if ((entry.mode & constants.S_IFMT) !== constants.S_IFDIR) {
        if (expected) throw new Error('Workspace directory changed before removal');
        // All non-directory types, including sockets, FIFOs and symlinks, use
        // unlinkat without opening or following the entry.
        unlink_relative(parent, name);
        return;
      }
      const directory = open_relative(parent, name, directory_open_flags);
      try {
        check_identity(directory, expected);
        check_identity(directory, entry);
        const opened = fstatSync(directory);
        await setImmediate(undefined, { signal });
        signal?.throwIfAborted();
        for (const child of read_directory_names(directory)) await remove_entry(directory, child);
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
    await remove_entry(parent, basename(root), identity);
  } finally {
    close_native_descriptor(parent);
  }
}
