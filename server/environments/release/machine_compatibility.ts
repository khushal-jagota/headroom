/** Receipt admission for native cross-version evidence from this exact publication attempt. */
import { isDeepStrictEqual } from 'node:util';
import { is_record } from '../../core/records.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';
import type { MachinePlatform } from './machine_compatibility_contracts.ts';
import { machine_baseline_pin } from './machine_baselines.ts';

export async function validate_machine_compatibility(
  value: unknown,
  run: PublicationSourceRun,
  platform: MachinePlatform,
  archive_sha256: string,
  build: unknown,
): Promise<string> {
  if (
    !is_record(value) ||
    value.version !== 1 ||
    value.platform !== platform ||
    !isDeepStrictEqual(value.source_run, run)
  )
    throw new Error('Machine compatibility proof differs from this publication attempt');
  if (
    !is_record(build) ||
    typeof build.build_id !== 'string' ||
    !/^[0-9a-f]{64}$/.test(build.build_id) ||
    build.repeat_build_id !== build.build_id
  )
    throw new Error('Machine publication requires matching independent build identities');
  if (
    !isDeepStrictEqual(value.candidate, {
      archive_sha256,
      build_id: build.build_id,
      repeat_build_id: build.repeat_build_id,
    })
  )
    throw new Error('Machine compatibility proof differs from the candidate archive');
  const pin = await machine_baseline_pin(platform);
  const oldest = {
    source_sha: pin.source_sha,
    archive_sha256: pin.archive_sha256,
    archive_size: pin.archive_size,
    zip_sha256: pin.zip_sha256,
  };
  if (!isDeepStrictEqual(value.oldest, oldest))
    throw new Error('Machine proof did not use the oldest supported build');
  if (
    !is_record(value.previous) ||
    typeof value.previous.source_sha !== 'string' ||
    !/^[0-9a-f]{40}$/.test(value.previous.source_sha) ||
    typeof value.previous.archive_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.previous.archive_sha256) ||
    typeof value.previous.zip_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.previous.zip_sha256) ||
    !Number.isSafeInteger(value.previous.archive_size) ||
    Number(value.previous.archive_size) < 1
  )
    throw new Error('Machine proof has no valid previous published baseline');
  if (
    !isDeepStrictEqual(value.checks, {
      previous_lease_run_result: 'passed',
      oldest_channel_update: 'passed',
      wire_compatibility: 'passed',
      channel_version: 1,
    }) ||
    typeof value.seconds !== 'number' ||
    !Number.isFinite(value.seconds) ||
    value.seconds <= 0
  )
    throw new Error('Machine compatibility checks did not pass');
  return build.build_id;
}
