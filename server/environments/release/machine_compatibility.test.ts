import { expect, test } from 'vitest';
import { validate_machine_compatibility } from './machine_compatibility.ts';
import { machine_baseline_pin } from './machine_baselines.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';

test('publication rejects absent, replayed, wrong-archive, and incomplete native machine evidence', async () => {
  const run: PublicationSourceRun = {
    repository: 'example/panels',
    source_sha: 'a'.repeat(40),
    run_id: '123',
    run_attempt: '2',
    event: 'push',
    ref: 'refs/heads/main',
    workflow_path: '.github/workflows/publish-typescript-release.yml',
    workflow_ref: 'example/panels/.github/workflows/publish-typescript-release.yml@refs/heads/main',
  };
  const pin = await machine_baseline_pin('linux');
  const baseline = {
    source_sha: pin.source_sha,
    archive_sha256: pin.archive_sha256,
    archive_size: pin.archive_size,
    zip_sha256: pin.zip_sha256,
  };
  const build = { build_id: 'b'.repeat(64), repeat_build_id: 'b'.repeat(64) };
  const proof = {
    version: 1,
    platform: 'linux',
    source_run: run,
    candidate: { archive_sha256: 'c'.repeat(64), ...build },
    oldest: baseline,
    previous: baseline,
    checks: {
      previous_lease_run_result: 'passed',
      oldest_channel_update: 'passed',
      wire_compatibility: 'passed',
      channel_version: 1,
    },
    seconds: 1,
  };
  await expect(
    validate_machine_compatibility(proof, run, 'linux', 'c'.repeat(64), build),
  ).resolves.toBe(build.build_id);
  await expect(
    validate_machine_compatibility(null, run, 'linux', 'c'.repeat(64), build),
  ).rejects.toThrow('publication attempt');
  await expect(
    validate_machine_compatibility(
      proof,
      { ...run, run_attempt: '3' },
      'linux',
      'c'.repeat(64),
      build,
    ),
  ).rejects.toThrow('publication attempt');
  await expect(
    validate_machine_compatibility(proof, run, 'linux', 'd'.repeat(64), build),
  ).rejects.toThrow('candidate archive');
  await expect(
    validate_machine_compatibility(
      { ...proof, checks: { ...proof.checks, oldest_channel_update: 'skipped' } },
      run,
      'linux',
      'c'.repeat(64),
      build,
    ),
  ).rejects.toThrow('did not pass');
  await expect(
    validate_machine_compatibility(proof, run, 'linux', 'c'.repeat(64), {
      ...build,
      repeat_build_id: 'e'.repeat(64),
    }),
  ).rejects.toThrow('independent build');
});
