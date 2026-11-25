/** Bind the native Node proof to the source, workflow attempt, and exact archive bytes. */
import { stat } from 'node:fs/promises';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { is_record } from '../../core/records.ts';
import { sha256_file } from '../backup/evidence.ts';
import type { PublishedMacUpdate } from '../updates/contracts.ts';
import {
  MAC_RUNTIME_ARCHIVE_NAME,
  MAX_MAC_RUNTIME_ARCHIVE_BYTES,
} from '../updates/mac/configuration.ts';
import { inspect_mac_archive } from './archive.ts';
import { MacArchiveInspectionUnsettled } from './contracts.ts';
import type { ReleaseExecution } from './contracts.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';
import { publication_json_file } from './publication_identity.ts';
import { assert_release_execution } from './process.ts';
import { PUBLICATION_MAC_VALIDATION_SECONDS } from './publication_configuration.ts';

export async function validate_mac_publication(
  directory: string,
  source: PublicationSourceRun,
  artifact_id: number,
  build_result: string,
  execution: ReleaseExecution,
  signed_promotion = false,
): Promise<PublishedMacUpdate> {
  assert_release_execution(execution);
  if (build_result !== 'success' || !Number.isSafeInteger(artifact_id) || artifact_id < 1)
    throw new Error('Mac publication requires a successful native build and artifact');
  const value = await publication_json_file(join(directory, 'mac-runtime-publication.json'));
  const archive = join(directory, MAC_RUNTIME_ARCHIVE_NAME),
    info = await stat(archive);
  const archive_sha256 = await sha256_file(archive);
  const proof = is_record(value) && is_record(value.proof) ? value.proof : null;
  if (
    !is_record(value) ||
    value.schema_version !== 1 ||
    typeof value.build_id !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.build_id) ||
    (signed_promotion
      ? Object.hasOwn(value, 'repeat_build_id')
      : value.repeat_build_id !== value.build_id) ||
    typeof value.launcher_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.launcher_sha256) ||
    !/^[0-9a-f]{40}$/.test(source.source_sha) ||
    value.application_revision !== source.source_sha ||
    value.platform !== 'darwin' ||
    value.architecture !== 'arm64' ||
    value.publication_run_id !== source.run_id ||
    value.run_attempt !== source.run_attempt ||
    value.archive_sha256 !== archive_sha256 ||
    value.archive_size !== info.size ||
    !info.isFile() ||
    !Number.isSafeInteger(info.size) ||
    info.size < 1 ||
    info.size > MAX_MAC_RUNTIME_ARCHIVE_BYTES ||
    typeof value.node_version !== 'string' ||
    !/^(22\.(?:1[89]|[2-9][0-9])|24\.\d+)\.\d+$/.test(value.node_version) ||
    typeof value.node_sha256 !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.node_sha256) ||
    proof === null ||
    ['relocated_archive', 'executor_cli', 'native_files', 'sqlite', 'host_import'].some(
      (key) => proof[key] !== 'passed',
    ) ||
    typeof proof.duration_seconds !== 'number' ||
    !Number.isFinite(proof.duration_seconds) ||
    proof.duration_seconds <= 0
  )
    throw new Error(
      'Mac publication differs from its source, attempt, archive, or native Node proof',
    );
  if (execution.deadline === null) throw new Error('Mac publication requires a finite deadline');
  let inspected;
  try {
    inspected = await inspect_mac_archive(archive, archive_sha256, info.size, {
      ...execution,
      deadline: Math.min(
        execution.deadline,
        performance.now() / 1000 + PUBLICATION_MAC_VALIDATION_SECONDS,
      ),
    });
  } catch (error) {
    if (error instanceof MacArchiveInspectionUnsettled) {
      await error.settled;
      throw error.cause;
    }
    throw error;
  }
  if (
    !isDeepStrictEqual(inspected.manifest, {
      schema_version: 1,
      build_id: value.build_id,
      application_revision: source.source_sha,
      platform: 'darwin',
      architecture: 'arm64',
    }) ||
    !inspected.node ||
    !inspected.entrypoint
  )
    throw new Error('Mac archive lacks its source-bound TypeScript runtime');
  assert_release_execution(execution);
  return { artifact_id, archive_sha256, archive_size: info.size };
}
