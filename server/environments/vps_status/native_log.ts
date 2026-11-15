/** Truncate the retained launchd log inode without following replacement links. */
import { closeSync, constants, fstatSync, ftruncateSync, lstatSync, openSync } from 'node:fs';
import { basename, dirname } from 'node:path';
import { close_native_descriptor, open_relative } from '../../executor/mac/platform.ts';
import type { CleanupCandidate, VpsStatusPolicy } from './contracts.ts';

export function truncate_native_log(candidate: CleanupCandidate, policy: VpsStatusPolicy): boolean {
  if (dirname(candidate.path) !== candidate.root) return false;
  let directory: number | undefined, descriptor: number | undefined, current: number | undefined;
  try {
    const root_before = lstatSync(candidate.root, { bigint: true });
    const before = lstatSync(candidate.path, { bigint: true });
    directory = openSync(
      candidate.root,
      constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
    );
    const root = fstatSync(directory, { bigint: true });
    if (root.dev !== root_before.dev || root.ino !== root_before.ino) return false;
    descriptor = open_relative(
      directory,
      basename(candidate.path),
      constants.O_WRONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    const opened = fstatSync(descriptor, { bigint: true });
    current = open_relative(
      directory,
      basename(candidate.path),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    const observed = fstatSync(current, { bigint: true });
    if (
      !opened.isFile() ||
      opened.nlink !== 1n ||
      opened.size < BigInt(policy.log_warning_bytes) ||
      opened.dev !== before.dev ||
      opened.ino !== before.ino ||
      opened.dev !== observed.dev ||
      opened.ino !== observed.ino
    )
      return false;
    ftruncateSync(descriptor, 0);
    return true;
  } catch (error) {
    if (error instanceof Error && 'code' in error) return false;
    throw error;
  } finally {
    try {
      if (current !== undefined) close_native_descriptor(current);
    } finally {
      try {
        if (descriptor !== undefined) close_native_descriptor(descriptor);
      } finally {
        if (directory !== undefined) closeSync(directory);
      }
    }
  }
}
