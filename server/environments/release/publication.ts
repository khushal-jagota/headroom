/** Build, seal, and admit the actual Linux archive without publishing a receipt. */
import { createHash } from 'node:crypto';
import { mkdir, readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { build_exported_app } from './build.ts';
import {
  create_app_archive,
  extract_app_archive,
  hash_archive,
  remove_owned_tree,
} from './archive.ts';
import { admit_runtime } from './admission.ts';
import { RuntimeProbeUnsettled } from './admission_process.ts';
import { assert_release_execution } from './process.ts';
import { validate_app_manifest } from './identity.ts';
import { validate_app_sha } from './logic/manifest.ts';
import { read_migration_graph, migration_frontier } from './migrations.ts';
import { inventory_package_licenses } from './package_licenses.ts';
import { replace_existing_json } from '../updates/attempts.ts';
import { SUPPORTED_UPDATE_SCHEMA_FLOOR } from '../updates/configuration.ts';
import {
  APP_ARCHIVE_NAME,
  ARCHIVE_MAX_COMPRESSED_BYTES,
  RUNTIME_INVENTORY_PATH,
} from './configuration.ts';
import { AppValidationError } from './contracts.ts';
import type { ReleaseBuildDependencies, ReleaseExecution } from './contracts.ts';
import type {
  LinuxPublicationBuild,
  LinuxPublicationOptions,
  PublicationSourceRun,
} from './publication_contracts.ts';

export async function build_linux_publication(
  options: LinuxPublicationOptions,
  dependencies: ReleaseBuildDependencies,
  source_run: PublicationSourceRun,
  execution: ReleaseExecution,
): Promise<LinuxPublicationBuild> {
  const fence = () => assert_release_execution(execution);
  fence();
  if (execution.deadline === null || !Number.isFinite(execution.deadline))
    throw new AppValidationError('publication build requires a finite original deadline');
  const requested = validate_app_sha(options.requested_sha, 'requested SHA');
  if (source_run.source_sha !== requested)
    throw new AppValidationError('publication provenance differs from requested SHA');
  for (const path of [options.source_root, options.output_directory])
    if (!isAbsolute(path) || path.includes('\0'))
      throw new AppValidationError('publication paths must be absolute');
  const source = await realpath(options.source_root);
  fence();
  const output = options.output_directory,
    candidate = join(output, 'candidate'),
    sealed = join(output, 'sealed'),
    archive = join(output, APP_ARCHIVE_NAME);
  const phases: Record<string, number> = {},
    began = performance.now();
  const phase = async <T>(name: string, action: () => Promise<T>): Promise<T> => {
    fence();
    const started = performance.now();
    const value = await action();
    fence();
    phases[name] = (performance.now() - started) / 1000;
    return value;
  };
  let owned = false;
  try {
    await mkdir(output, { mode: 0o700 });
    owned = true;
    fence();
    const manifest = await phase('build', () =>
      build_exported_app(
        source,
        {
          requested_sha: requested,
          candidate_app: candidate,
          install_dependencies: true,
        },
        dependencies,
        execution,
      ),
    );
    const validate = async (root: string) => {
      const actual = await validate_app_manifest(join(root, 'manifest.json'), {
        expected_sha: requested,
        require_runtime: true,
      });
      if (actual.runtime_contract?.version !== 2 || !isDeepStrictEqual(actual, manifest))
        throw new AppValidationError('sealed publication manifest differs from its build');
    };
    await phase('validate_build', () => validate(candidate));
    const graph = await phase('frontier', () => read_migration_graph(candidate));
    const archive_sha256 = await phase('seal', () => create_app_archive(candidate, archive));
    const archive_size = (await phase('archive_size', () => stat(archive))).size;
    if (!Number.isSafeInteger(archive_size) || archive_size > ARCHIVE_MAX_COMPRESSED_BYTES)
      throw new AppValidationError('publication archive exceeds its compressed-byte limit');
    await phase('remove_candidate', () => remove_owned_tree(candidate));
    const extracted = await phase('extract', () =>
      extract_app_archive(
        archive,
        sealed,
        archive_sha256,
        requested,
        execution.child_processes,
        undefined,
        execution,
      ),
    );
    if (!isDeepStrictEqual(extracted, manifest))
      throw new AppValidationError('extracted publication manifest differs from its build');
    await phase('validate_sealed', () => validate(sealed));
    const inventory = await phase('licenses', () => inventory_package_licenses(sealed));
    // Capture the exact shared-writer encoding before candidate code can run.
    const inventory_bytes = Buffer.from(JSON.stringify(inventory) + '\n');
    const native_bytes = await phase('runtime_inventory', () =>
      readFile(join(sealed, RUNTIME_INVENTORY_PATH)),
    );
    const runtime_inventory_sha256 = createHash('sha256').update(native_bytes).digest('hex');
    await phase('admission', () => admit_runtime(sealed, manifest, execution.child_processes));
    await phase('revalidate', () => validate(sealed));
    if (
      (await phase('archive_rehash', () => hash_archive(archive))) !== archive_sha256 ||
      (await phase('archive_restat', () => stat(archive))).size !== archive_size
    )
      throw new AppValidationError('publication archive changed after admission');
    await phase('remove_sealed', () => remove_owned_tree(sealed));
    const license_filename = 'package-licenses.json',
      license_path = join(output, license_filename);
    replace_existing_json(license_path, inventory as unknown as Record<string, unknown>);
    const written = await phase('license_output', () => readFile(license_path));
    if (!written.equals(inventory_bytes))
      throw new AppValidationError('publication license output differs from pre-admission bytes');
    phases.total = (performance.now() - began) / 1000;
    const result: LinuxPublicationBuild = {
      version: 1,
      source_run,
      manifest,
      archive: { filename: APP_ARCHIVE_NAME, sha256: archive_sha256, size: archive_size },
      schema_frontier: [...migration_frontier(graph)].sort(),
      schema_floor: SUPPORTED_UPDATE_SCHEMA_FLOOR,
      runtime_inventory_sha256,
      licenses: {
        filename: license_filename,
        sha256: createHash('sha256').update(written).digest('hex'),
        package_count: inventory.packages.length,
        missing_own_licenses: inventory.missing_own_licenses,
      },
      admission: { status: 'passed', seconds: phases.admission! },
      producer_node: process.version,
      phases_seconds: phases,
      unproven: [
        'Default production backend/environment composition and public release commands',
        'Sealed Node-only startup, relocation, and usage/proxy shutdown',
        'Copied installer launch/rollback and installed service/OOM behavior',
        'Human source-bound iOS compatibility and eligible publication',
      ],
    };
    fence();
    replace_existing_json(
      join(output, 'linux-publication-build.json'),
      result as unknown as Record<string, unknown>,
    );
    fence();
    return result;
  } catch (error) {
    let failure = error;
    if (error instanceof RuntimeProbeUnsettled) {
      try {
        await error.settled;
      } catch (cleanup) {
        failure = new AggregateError([error, cleanup], 'publication admission cleanup failed', {
          cause: error,
        });
      }
    }
    if (owned) {
      try {
        await remove_owned_tree(output);
      } catch (cleanup) {
        failure = new AggregateError([failure, cleanup], 'publication build cleanup failed', {
          cause: failure,
        });
      }
    }
    throw failure;
  }
}
