/** Hosted-only proof of the exact sealed home archive, using the shared boot entry. */
import { constants } from 'node:fs';
import { copyFile, lstat, mkdir, realpath, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import type { ReleaseExecution } from './contracts.ts';
import { MAC_HOME } from './contracts.ts';
import type { MacHomeBuildResult, MacHomePublicationBuild } from './mac_home_contracts.ts';
import { MAC_HOME_PUBLICATION_METADATA } from './mac_home_configuration.ts';
import { extract_app_archive, hash_archive, remove_owned_tree } from './archive.ts';
import { admit_runtime } from './admission.ts';
import { RuntimeProbeUnsettled } from './admission_process.ts';
import { validate_app_manifest } from './identity.ts';
import { assert_release_execution, run_release_command } from './process.ts';
import { publication_id } from './publication_identity.ts';
import { PUBLICATION_MAC_VALIDATION_SECONDS } from './publication_configuration.ts';
import { sha256_file } from '../backup/evidence.ts';

export async function prove_mac_home(
  result: MacHomeBuildResult,
  source_root: string,
  output_directory: string,
  execution: ReleaseExecution,
): Promise<MacHomePublicationBuild> {
  if (process.platform !== 'darwin' || process.arch !== 'arm64')
    throw new Error('Mac home proof requires a native arm64 Mac');
  const fence = () => assert_release_execution(execution);
  fence();
  if (execution.deadline === null || !Number.isFinite(execution.deadline))
    throw new Error('Mac home proof requires the original finite publication deadline');
  const source = await realpath(source_root),
    output = await realpath(output_directory);
  const run_id =
    process.env.GITHUB_RUN_ID === undefined
      ? null
      : publication_id(process.env.GITHUB_RUN_ID, 'build run ID');
  const run_attempt =
    process.env.GITHUB_RUN_ATTEMPT === undefined
      ? null
      : publication_id(process.env.GITHUB_RUN_ATTEMPT, 'build run attempt');
  if ((run_id === null) !== (run_attempt === null))
    throw new Error('Mac home build requires both run ID and attempt when supplied');
  const work = join(output, 'proof-work'),
    sealed = join(work, 'sealed');
  await mkdir(work, { mode: 0o700 });
  const unchanged_archive = async () => {
    fence();
    if (
      (await hash_archive(result.archive)) !== result.archive_sha256 ||
      (await stat(result.archive)).size !== result.archive_size
    )
      throw new Error('Mac home archive changed during its proof');
    fence();
  };
  const validate = async () => {
    const actual = await validate_app_manifest(join(sealed, 'manifest.json'), {
      expected_sha: result.manifest.app_sha,
      expected_target: MAC_HOME,
      require_runtime: true,
    });
    if (!isDeepStrictEqual(actual, result.manifest))
      throw new Error('Mac home sealed manifest differs from its build');
    fence();
  };
  // The source candidate cannot supply bytes to either proof after this point.
  await remove_owned_tree(join(output, 'app'));
  await unchanged_archive();
  const began = performance.now();
  let sealed_identity: { readonly dev: number; readonly ino: number } | undefined;
  try {
    const extracted = await extract_app_archive(
      result.archive,
      sealed,
      result.archive_sha256,
      result.manifest.app_sha,
      execution.child_processes,
      undefined,
      execution,
      MAC_HOME,
    );
    const extracted_root = await lstat(sealed);
    if (!extracted_root.isDirectory() || extracted_root.isSymbolicLink())
      throw new Error('Mac home extraction root is not an owned directory');
    sealed_identity = { dev: extracted_root.dev, ino: extracted_root.ino };
    if (!isDeepStrictEqual(extracted, result.manifest))
      throw new Error('Mac home extraction differs from its build');
    await validate();
    await admit_runtime(sealed, result.manifest, execution.child_processes);
    await validate();
    await unchanged_archive();
    await writeFile(
      join(output, 'admission.json'),
      JSON.stringify(
        {
          status: 'passed',
          requested_sha: result.manifest.app_sha,
          archive_sha256: result.archive_sha256,
          manifest: result.manifest,
          seconds: (performance.now() - began) / 1000,
        },
        null,
        2,
      ) + '\n',
      { flag: 'wx', mode: 0o600 },
    );
  } catch (error) {
    // A timeout observer cannot authorize removal under the actual probe flight.
    if (error instanceof RuntimeProbeUnsettled) {
      try {
        await error.settled;
      } catch (cleanup) {
        throw new AggregateError([error, cleanup], 'Mac home admission cleanup failed', {
          cause: error,
        });
      }
    }
    throw error;
  } finally {
    if (sealed_identity) {
      const current_root = await lstat(sealed);
      if (
        !current_root.isDirectory() ||
        current_root.isSymbolicLink() ||
        current_root.dev !== sealed_identity.dev ||
        current_root.ino !== sealed_identity.ino
      )
        throw new Error('Mac home admission root changed. Retain its replacement.');
      await remove_owned_tree(sealed);
    }
  }
  fence();
  const boot_root = join(work, 'boot'),
    request = join(work, 'boot-request.json');
  await writeFile(
    request,
    JSON.stringify({
      archive_path: result.archive,
      archive_sha256: result.archive_sha256,
      requested_sha: result.manifest.app_sha,
      work_root: boot_root,
    }) + '\n',
    { flag: 'wx', mode: 0o600 },
  );
  const boot = await run_release_command(
    [
      process.execPath,
      join(source, 'server/environments/release/publication_boot_main.ts'),
      request,
    ],
    {
      cwd: source,
      timeout_seconds: PUBLICATION_MAC_VALIDATION_SECONDS,
      allow_nonzero: true,
      environment: { PATH: dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin' },
    },
    execution,
  );
  await writeFile(join(work, 'boot.stdout.log'), boot.stdout, { flag: 'wx', mode: 0o600 });
  await writeFile(join(work, 'boot.stderr.log'), boot.stderr, { flag: 'wx', mode: 0o600 });
  if (boot.code !== 0)
    throw new Error('Mac home foreground boot proof failed. See proof-work logs.', {
      cause: boot,
    });
  // The fixed entry reports only after exact ready health and actual clean supervisor close.
  await copyFile(
    join(boot_root, 'boot-proof.json'),
    join(output, 'boot-proof.json'),
    constants.COPYFILE_EXCL,
  );
  await unchanged_archive();
  const report: MacHomePublicationBuild = {
    ...result,
    version: 1,
    build_run: { source_sha: result.manifest.app_sha, run_id, run_attempt },
    admission: {
      filename: 'admission.json',
      sha256: await sha256_file(join(output, 'admission.json')),
    },
    boot: {
      filename: 'boot-proof.json',
      sha256: await sha256_file(join(output, 'boot-proof.json')),
    },
  };
  // The boot entry creates this root before the app runs. Its original identity is
  // not ours to prove, so retain the diagnostic tree rather than adopt a replacement.
  fence();
  await writeFile(
    join(output, MAC_HOME_PUBLICATION_METADATA),
    JSON.stringify(report, null, 2) + '\n',
    { flag: 'wx', mode: 0o600 },
  );
  fence();
  return report;
}
