/** The Linux executor uses the shared native builder and proves its relocated archive. */
import { mkdir, rename, stat, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join } from 'node:path';
import { build_exported_app } from '../release/build.ts';
import { create_app_archive, hash_archive, remove_owned_tree } from '../release/archive.ts';
import { AppValidationError, LINUX_EXECUTOR } from '../release/contracts.ts';
import type { ReleaseExecution } from '../release/contracts.ts';
import { digest_app_artifact, validate_app_manifest } from '../release/identity.ts';
import { validate_app_sha } from '../release/logic/manifest.ts';
import { require_minimum_node } from '../release/logic/host.ts';
import { assert_release_execution, run_release_command } from '../release/process.ts';
import { probe_environment } from '../release/admission_process.ts';
import { APP_FORMAT } from '../release/configuration.ts';
import { replace_existing_json } from '../updates/attempts.ts';
import { MAC_RUNTIME_MANIFEST_NAME } from '../updates/mac/configuration.ts';
import { extract_runtime, read_runtime_revision } from './archive.ts';
import { LINUX_EXECUTOR_ENTRYPOINT } from './configuration.ts';
import type { LinuxExecutorBuildOptions, LinuxExecutorPublication } from './contracts.ts';

export async function build_linux_executor(
  options: LinuxExecutorBuildOptions,
  execution: ReleaseExecution,
): Promise<LinuxExecutorPublication> {
  const fence = () => assert_release_execution(execution);
  fence();
  if (process.platform !== 'linux' || process.arch !== 'x64')
    throw new AppValidationError('Linux executor builds require Linux x86_64');
  if (execution.deadline === null || !Number.isFinite(execution.deadline))
    throw new AppValidationError('Linux executor builds require a finite original deadline');
  const revision = validate_app_sha(options.requested_sha, 'requested SHA');
  for (const path of [options.source_root, options.output_directory])
    if (!isAbsolute(path) || path.includes('\0'))
      throw new AppValidationError('Linux executor build paths must be absolute');
  const output = options.output_directory,
    runtime = join(output, 'runtime'),
    archive = join(output, 'panels-linux-executor.tar.gz'),
    proof = join(output, 'relocation-proof'),
    hidden = join(output, 'inaccessible-build');
  let owned = false;
  try {
    await mkdir(output, { mode: 0o700 });
    owned = true;
    fence();
    const built = await build_exported_app(
      options.source_root,
      { requested_sha: revision, candidate_app: runtime, install_dependencies: true },
      { public_cli_entrypoint: 'server/cli/main.ts' },
      execution,
    );
    if (built.runtime_contract?.version !== 2 || built.runtime_contract.system !== 'linux')
      throw new AppValidationError('Linux executor requires its version-2 Linux runtime contract');
    require_minimum_node(process.versions.node, built.runtime_contract);
    fence();
    await writeFile(
      join(runtime, MAC_RUNTIME_MANIFEST_NAME),
      JSON.stringify({
        schema_version: 1,
        application_revision: revision,
        platform: 'linux',
        architecture: 'x86_64',
        role: 'executor',
      }) + '\n',
    );
    const manifest = { ...built, artifact_digest: await digest_app_artifact(runtime) };
    fence();
    await writeFile(
      join(runtime, 'manifest.json'),
      JSON.stringify({ format: APP_FORMAT, ...manifest }) + '\n',
    );
    await read_runtime_revision(runtime, revision);
    fence();
    const archive_sha256 = await create_app_archive(runtime, archive, LINUX_EXECUTOR);
    const archive_size = (await stat(archive)).size;
    fence();
    await rename(runtime, hidden);
    let failure: unknown;
    try {
      const proved = await extract_runtime(
        archive,
        proof,
        {
          expected_sha256: archive_sha256,
          expected_size: archive_size,
          expected_revision: revision,
        },
        execution,
      );
      const state = join(output, 'cli-state');
      await mkdir(state, { mode: 0o700 });
      await writeFile(join(state, 'config.yaml'), '{}\n', { mode: 0o600 });
      await run_release_command(
        [process.execPath, join(proved, LINUX_EXECUTOR_ENTRYPOINT), 'executor', '--help'],
        {
          timeout_seconds: 30,
          cwd: proved,
          environment: {
            ...probe_environment(state),
            PATH: dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin',
            PLAN_APP_ROOT: proved,
            PLAN_APP_SHA: revision,
          },
        },
        execution,
      );
      await validate_app_manifest(join(proved, 'manifest.json'), {
        expected_sha: revision,
        expected_target: LINUX_EXECUTOR,
        require_runtime: true,
      });
    } catch (error) {
      failure = error;
    }
    try {
      await rename(hidden, runtime);
    } catch (cleanup) {
      if (failure !== undefined)
        throw new AggregateError([failure, cleanup], 'Linux executor build restoration failed', {
          cause: failure,
        });
      throw cleanup;
    }
    if (failure !== undefined) throw failure;
    await remove_owned_tree(proof);
    await remove_owned_tree(join(output, 'cli-state'));
    fence();
    if (
      (await hash_archive(archive)) !== archive_sha256 ||
      (await stat(archive)).size !== archive_size
    )
      throw new AppValidationError('Linux executor archive changed after admission');
    fence();
    const receipt: LinuxExecutorPublication = {
      schema_version: 1,
      proof: { native_startup: 'passed', relocated_archive: 'passed', executor_cli: 'passed' },
      application_revision: revision,
      archive_sha256,
      archive_size,
      platform: 'linux',
      architecture: 'x86_64',
      role: 'executor',
    };
    replace_existing_json(
      join(output, 'linux-executor-publication.json'),
      receipt as unknown as Record<string, unknown>,
    );
    fence();
    return receipt;
  } catch (failure) {
    if (owned) {
      try {
        await remove_owned_tree(output);
      } catch (cleanup) {
        throw new AggregateError([failure, cleanup], 'Linux executor build cleanup failed', {
          cause: failure,
        });
      }
    }
    throw failure;
  }
}
