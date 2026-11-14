/** Bind the exact publication receipt before an app becomes the retained stage. */
import { createWriteStream } from 'node:fs';
import { lstat, mkdtemp, readdir, rename, rm, stat, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { isDeepStrictEqual } from 'node:util';
import { is_os_error } from '../../core/system_errors.ts';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import type { AppManifest, ReleaseStaging } from './contracts.ts';
import { AppValidationError } from '../release/contracts.ts';
import { validate_app_manifest } from '../release/identity.ts';
import { extract_app_archive, remove_owned_tree } from '../release/archive.ts';
import { APP_ARCHIVE_NAME } from '../release/configuration.ts';
import { admit_runtime } from '../release/admission.ts';
import { RuntimeProbeUnsettled } from '../release/admission_process.ts';
import { STAGED_APP_PREFIX } from './configuration.ts';
import { with_zip_member } from './zip.ts';

export type RuntimeAdmission = (root: string, manifest: AppManifest) => Promise<void>;

async function directory(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isDirectory();
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      (error.code === 'ENOENT' || error.code === 'ENOTDIR')
    )
      return false;
    throw error;
  }
}
async function remove_directory(path: string): Promise<void> {
  if ((await lstat(path)).isSymbolicLink())
    throw new Error('Cannot call rmtree on a symbolic link');
  await rm(path, { recursive: true });
}
async function staged_manifest(path: string, app_sha: string): Promise<AppManifest | null> {
  try {
    return await validate_app_manifest(join(path, 'manifest.json'), {
      expected_sha: app_sha,
      require_runtime: true,
      verify_artifact_digest: false,
    });
  } catch (error) {
    if (error instanceof AppValidationError || is_os_error(error)) return null;
    throw error;
  }
}

export function create_release_staging(
  child_processes: OwnedChildProcesses,
  signal: AbortSignal,
  admit: RuntimeAdmission = (root, manifest) => admit_runtime(root, manifest, child_processes),
): ReleaseStaging {
  const admit_candidate: RuntimeAdmission = async (root, manifest) => {
    try {
      signal.throwIfAborted();
      await admit(root, manifest);
      signal.throwIfAborted();
    } catch (error) {
      if (error instanceof RuntimeProbeUnsettled) {
        try {
          await error.settled;
        } catch (cleanup) {
          throw new AggregateError([error, cleanup], 'Staged runtime cleanup failed');
        }
      }
      throw error;
    }
  };
  return {
    async stage(provider, release, current_root) {
      signal.throwIfAborted();
      const app_sha = release.manifest.app_sha;
      const staged = join(current_root, STAGED_APP_PREFIX + app_sha);
      for (const entry of await readdir(current_root, { withFileTypes: true })) {
        signal.throwIfAborted();
        if (!(entry.name.startsWith(STAGED_APP_PREFIX) || entry.name.startsWith('.app-download-')))
          continue;
        const path = join(current_root, entry.name);
        if (path !== staged && entry.isDirectory() && !entry.isSymbolicLink())
          await remove_directory(path);
      }
      signal.throwIfAborted();
      if (await directory(staged)) {
        signal.throwIfAborted();
        const manifest = await staged_manifest(staged, app_sha);
        signal.throwIfAborted();
        if (isDeepStrictEqual(manifest, release.manifest)) {
          await admit_candidate(staged, release.manifest);
          return staged;
        }
        await remove_directory(staged);
      }
      signal.throwIfAborted();
      const work = await mkdtemp(join(current_root, '.app-download-'));
      let failure: unknown;
      try {
        signal.throwIfAborted();
        const artifact = join(work, 'artifact.zip');
        const archive = join(work, APP_ARCHIVE_NAME);
        await provider.download_artifact(release.artifact_id, artifact, signal);
        signal.throwIfAborted();
        await with_zip_member(artifact, APP_ARCHIVE_NAME, (input) =>
          pipeline(input, createWriteStream(archive, { flags: 'wx' }), { signal }),
        );
        signal.throwIfAborted();
        await unlink(artifact);
        signal.throwIfAborted();
        const unpacked = join(work, 'app');
        const manifest = await extract_app_archive(
          archive,
          unpacked,
          release.archive_sha256,
          app_sha,
          child_processes,
        );
        signal.throwIfAborted();
        await unlink(archive);
        signal.throwIfAborted();
        if (!isDeepStrictEqual(manifest, release.manifest))
          throw new AppValidationError('The downloaded app differs from its publication receipt.');
        await admit_candidate(unpacked, manifest);
        signal.throwIfAborted();
        await rename(unpacked, staged);
        return staged;
      } catch (error) {
        failure = error;
        throw error;
      } finally {
        // Only this exclusive workspace gets permission-restoring cleanup.
        try {
          await remove_owned_tree(work);
        } catch (cleanup) {
          if (failure !== undefined)
            throw new AggregateError([failure, cleanup], 'Staged app cleanup failed');
          throw cleanup;
        }
      }
    },
  };
}
