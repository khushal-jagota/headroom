/** Linux executor runtimes reuse the application's owned archive and admission path. */
import { open, mkdir, mkdtemp, rename, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { exists, is_file } from '../backup/evidence.ts';
import { is_record } from '../../core/records.ts';
import { admit_runtime } from '../release/admission.ts';
import { RuntimeProbeUnsettled } from '../release/admission_process.ts';
import { extract_app_archive, remove_owned_tree } from '../release/archive.ts';
import { AppValidationError, LINUX_EXECUTOR } from '../release/contracts.ts';
import type { ReleaseExecution, RuntimeTarget } from '../release/contracts.ts';
import { validate_app_manifest } from '../release/identity.ts';
import { require_runtime_target } from '../release/logic/manifest.ts';
import { assert_release_execution } from '../release/process.ts';
import {
  MAC_RUNTIME_MANIFEST_NAME,
  MAX_MAC_RUNTIME_ARCHIVE_BYTES,
  MAX_MAC_RUNTIME_MANIFEST_BYTES,
} from '../updates/mac/configuration.ts';
import { LINUX_EXECUTOR_ENTRYPOINT } from './configuration.ts';
import type { LinuxExecutorArchiveApproval, LinuxExecutorArchiveOperations } from './contracts.ts';

export async function read_runtime_revision(
  runtime: string,
  expected_revision?: string,
): Promise<string> {
  const file = await open(join(runtime, MAC_RUNTIME_MANIFEST_NAME), 'r');
  let bytes: Buffer;
  try {
    bytes = Buffer.alloc(MAX_MAC_RUNTIME_MANIFEST_BYTES + 1);
    let count = 0;
    while (count < bytes.length) {
      const read = await file.read(bytes, count, bytes.length - count, null);
      if (!read.bytesRead) break;
      count += read.bytesRead;
    }
    if (count > MAX_MAC_RUNTIME_MANIFEST_BYTES)
      throw new AppValidationError('Linux executor manifest exceeds its size bound');
    bytes = bytes.subarray(0, count);
  } finally {
    await file.close();
  }
  const value: unknown = JSON.parse(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes),
  );
  if (!is_record(value)) throw new AppValidationError('Linux executor manifest must be an object');
  require_runtime_target(
    { system: value.platform, architecture: value.architecture, role: value.role } as RuntimeTarget,
    LINUX_EXECUTOR,
  );
  const revision = value.application_revision;
  if (
    value.schema_version !== 1 ||
    typeof revision !== 'string' ||
    revision.length !== 40 ||
    !/^[0-9a-f]{40}$/.test(revision) ||
    (expected_revision !== undefined && revision !== expected_revision)
  )
    throw new AppValidationError('Linux executor manifest differs from the approved release');
  const manifest = await validate_app_manifest(join(runtime, 'manifest.json'), {
    expected_sha: revision,
    expected_target: LINUX_EXECUTOR,
    require_runtime: true,
    verify_artifact_digest: false,
  });
  if (manifest.runtime_contract?.version !== 2 || manifest.runtime_contract.system !== 'linux')
    throw new AppValidationError('Linux executor requires its version-2 Linux runtime contract');
  if (!(await is_file(join(runtime, LINUX_EXECUTOR_ENTRYPOINT))))
    throw new AppValidationError('Linux executor lacks its shared Node entrypoint');
  return revision;
}

const native_operations: LinuxExecutorArchiveOperations = {
  admit: (runtime, manifest, execution) =>
    admit_runtime(runtime, manifest, execution.child_processes, LINUX_EXECUTOR),
};

export async function extract_runtime(
  archive: string,
  releases: string,
  approval: LinuxExecutorArchiveApproval,
  execution: ReleaseExecution,
  operations: LinuxExecutorArchiveOperations = native_operations,
): Promise<string> {
  assert_release_execution(execution);
  const { expected_sha256, expected_size, expected_revision } = approval;
  if (
    !/^[0-9a-f]{64}$/.test(expected_sha256) ||
    expected_sha256.length !== 64 ||
    !/^[0-9a-f]{40}$/.test(expected_revision) ||
    expected_revision.length !== 40 ||
    !Number.isSafeInteger(expected_size) ||
    expected_size <= 0 ||
    expected_size > MAX_MAC_RUNTIME_ARCHIVE_BYTES ||
    (await stat(archive)).size !== expected_size
  )
    throw new AppValidationError('Linux executor archive size or identity differs from approval');
  assert_release_execution(execution);
  await mkdir(releases, { recursive: true, mode: 0o700 });
  const destination = join(releases, `${expected_revision}-${expected_sha256}`);
  if (await exists(destination)) {
    await read_runtime_revision(destination, expected_revision);
    assert_release_execution(execution);
    return destination;
  }
  assert_release_execution(execution);
  const temporary = await mkdtemp(join(releases, '.install-'));
  let failure: unknown;
  try {
    assert_release_execution(execution);
    const payload = join(temporary, 'runtime');
    const manifest = await extract_app_archive(
      archive,
      payload,
      expected_sha256,
      expected_revision,
      execution.child_processes,
      undefined,
      execution,
      LINUX_EXECUTOR,
    );
    await read_runtime_revision(payload, expected_revision);
    assert_release_execution(execution);
    await operations.admit(payload, manifest, execution);
    assert_release_execution(execution);
    await rename(payload, destination);
  } catch (error) {
    failure = error;
    if (error instanceof RuntimeProbeUnsettled) {
      try {
        await error.settled;
      } catch (cleanup) {
        failure = new AggregateError([error, cleanup], 'Linux executor admission cleanup failed', {
          cause: error,
        });
      }
    }
  }
  try {
    await remove_owned_tree(temporary);
  } catch (cleanup) {
    if (failure !== undefined)
      throw new AggregateError([failure, cleanup], 'Linux executor extraction cleanup failed', {
        cause: failure,
      });
    throw cleanup;
  }
  if (failure !== undefined) throw failure;
  return destination;
}
