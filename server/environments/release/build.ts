/** Exact-SHA source export and transactional Node application construction. */
import { chmod, mkdir, mkdtemp, readFile, rename, writeFile } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import type {
  AppBuildOptions,
  AppManifest,
  ReleaseBuildDependencies,
  ReleaseExecution,
} from './contracts.ts';
import { AppValidationError } from './contracts.ts';
import { APP_FORMAT } from './configuration.ts';
import {
  digest_app_artifact,
  digest_app_source,
  validate_app_manifest,
  validate_app_sha,
  validate_app_tree,
} from './identity.ts';
import { complete_node_runtime } from './native_inventory.ts';
import { validate_public_cli_entrypoint, write_app_entrypoints } from './entrypoints.ts';
import { assert_release_execution, run_release_command } from './process.ts';
import { probe_environment } from './admission_process.ts';
import { extract_git_export, remove_owned_tree } from './archive.ts';
import { read_migration_graph, migration_frontier } from './migrations.ts';
import { resolved_path, expand_path } from '../paths.ts';
import { stringify_python_json } from '../../core/http/numeric_json.ts';
import { exists, is_symlink } from '../backup/evidence.ts';
import { write_installer_file_manifest } from './installer_bundle.ts';

const BUILD_PATH = dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin';
function git_environment(object_directory?: string, home?: string): NodeJS.ProcessEnv {
  return {
    PATH: BUILD_PATH,
    LANG: 'C.UTF-8',
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_SYSTEM: '/dev/null',
    GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_ATTR_NOSYSTEM: '1',
    GIT_NO_REPLACE_OBJECTS: '1',
    ...(home ? { HOME: home, XDG_CONFIG_HOME: join(home, 'config') } : {}),
    ...(object_directory ? { GIT_OBJECT_DIRECTORY: object_directory } : {}),
  };
}
async function git_output(
  source: string,
  execution: ReleaseExecution,
  ...args: string[]
): Promise<string> {
  try {
    return (
      await run_release_command(
        ['git', '-c', 'core.attributesFile=/dev/null', '-C', source, ...args],
        { timeout_seconds: null, environment: git_environment() },
        execution,
      )
    ).stdout.trim();
  } catch (error) {
    assert_release_execution(execution);
    if (error instanceof AppValidationError || (error instanceof Error && 'code' in error))
      throw new AppValidationError('source checkout is not a usable Git checkout');
    throw error;
  }
}
async function install_app_dependencies(
  root: string,
  home: string,
  execution: ReleaseExecution,
): Promise<void> {
  for (const name of [
    '',
    'config',
    'cache',
    'codex',
    'claude',
    'hermes',
    'data',
    'logs',
    'backups',
    'app',
    'workspace',
    'scratch',
  ]) {
    assert_release_execution(execution);
    await mkdir(join(home, name), { recursive: true });
  }
  assert_release_execution(execution);
  await writeFile(join(home, 'config.yaml'), '{}\n');
  for (const name of ['user.npmrc', 'global.npmrc']) {
    assert_release_execution(execution);
    await writeFile(join(home, name), '', { flag: 'wx', mode: 0o600 });
  }
  const environment = {
    ...probe_environment(home),
    PATH: BUILD_PATH,
    NPM_CONFIG_USERCONFIG: join(home, 'user.npmrc'),
    NPM_CONFIG_GLOBALCONFIG: join(home, 'global.npmrc'),
    NPM_CONFIG_CACHE: join(home, 'cache/npm'),
  };
  const run = (argv: readonly string[]) =>
    run_release_command(
      [argv[0]!, '--ignore-scripts=false', ...argv.slice(1)],
      { timeout_seconds: null, environment, cwd: root },
      execution,
    );
  await run(['npm', 'ci', '--omit=dev', '--prefix', join(root, 'server')]);
  if (await exists(join(root, 'web/package-lock.json')))
    await run(['npm', 'ci', '--prefix', join(root, 'web')]);
  if (await exists(join(root, 'agent_backends/package.json')))
    await run([
      'npm',
      'install',
      '--prefix',
      join(root, 'agent_backends'),
      '--package-lock=false',
      '--save=false',
      '@anthropic-ai/claude-code@latest',
      '@openai/codex@latest',
    ]);
  if (await exists(join(root, 'web/package.json')))
    await run(['npm', 'run', 'build', '--prefix', join(root, 'web')]);
}
export async function build_exported_app(
  source_root: string,
  options: AppBuildOptions,
  dependencies: ReleaseBuildDependencies,
  execution: ReleaseExecution,
): Promise<AppManifest> {
  assert_release_execution(execution);
  const source = await resolved_path(expand_path(source_root));
  const requested = validate_app_sha(options.requested_sha, 'requested SHA');
  const actual = validate_app_sha(
    await git_output(source, execution, 'rev-parse', 'HEAD'),
    'checkout HEAD',
  );
  if (actual !== requested)
    throw new AppValidationError('checkout HEAD does not match requested SHA');
  const candidate_input = expand_path(options.candidate_app);
  const symlink_input =
    candidate_input.replace(/\/+$/, '') ||
    (candidate_input.startsWith('/') ? '/' : candidate_input);
  if (await is_symlink(symlink_input))
    throw new AppValidationError('candidate app must not be a symlink');
  const destination = await resolved_path(candidate_input);
  if (await exists(destination)) {
    const manifest = await validate_app_manifest(join(destination, 'manifest.json'), {
      expected_sha: requested,
      require_runtime: options.install_dependencies,
    });
    await validate_public_cli_entrypoint(destination, dependencies.public_cli_entrypoint);
    assert_release_execution(execution);
    return manifest;
  }
  assert_release_execution(execution);
  await mkdir(dirname(destination), { recursive: true });
  assert_release_execution(execution);
  const workspace = await mkdtemp(join(dirname(destination), `.${basename(destination)}-`));
  const payload = join(dirname(destination), `.${basename(destination)}-payload-${randomUUID()}`),
    archive = join(workspace, 'source.tar'),
    git_directory = join(workspace, 'git');
  let own_payload = false,
    manifest: AppManifest;
  const fail_after_cleanup = async (failure: unknown, paths: readonly string[]): Promise<never> => {
    const errors: unknown[] = [];
    for (const path of paths) {
      try {
        await remove_owned_tree(path);
      } catch (error) {
        errors.push(error);
      }
    }
    if (errors.length)
      throw new AggregateError(
        [failure, ...errors],
        `app build cleanup failed; owned paths: ${paths.join(', ')}`,
        { cause: failure },
      );
    throw failure;
  };
  try {
    const object_directory = await git_output(
      source,
      execution,
      'rev-parse',
      '--path-format=absolute',
      '--git-path',
      'objects',
    );
    await run_release_command(
      ['git', 'init', '--bare', '--template=', git_directory],
      { timeout_seconds: null, environment: git_environment(undefined, workspace) },
      execution,
    );
    await run_release_command(
      [
        'git',
        '-c',
        'core.attributesFile=/dev/null',
        '--git-dir=' + git_directory,
        'archive',
        '--format=tar',
        `--output=${archive}`,
        requested,
      ],
      { timeout_seconds: null, environment: git_environment(object_directory, workspace) },
      execution,
    );
    await extract_git_export(archive, payload, execution);
    own_payload = true;
    await validate_app_tree(payload);
    const source_digest = await digest_app_source(payload);
    let runtime_contract: AppManifest['runtime_contract'] = null;
    if (options.install_dependencies) {
      const graph = await read_migration_graph(payload);
      const seed: unknown = JSON.parse(
        new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
          await readFile(join(payload, 'server/core/database/application_schema.json')),
        ),
      );
      const heads = migration_frontier(graph);
      if (
        seed === null ||
        typeof seed !== 'object' ||
        !('alembicRevision' in seed) ||
        heads.size !== 1 ||
        !heads.has(String(seed.alembicRevision))
      )
        throw new AppValidationError('migration graph differs from the generated schema frontier');
      await install_app_dependencies(payload, join(workspace, 'npm-home'), execution);
      runtime_contract = await complete_node_runtime(payload, execution);
    }
    assert_release_execution(execution);
    await write_app_entrypoints(payload, requested, dependencies, execution);
    if (options.install_dependencies)
      await write_installer_file_manifest(payload, requested, execution);
    manifest = {
      app_sha: requested,
      source_digest,
      artifact_digest: await digest_app_artifact(payload),
      runtime_contract,
    };
    assert_release_execution(execution);
    await writeFile(
      join(payload, 'manifest.json'),
      stringify_python_json(
        { format: APP_FORMAT, ...manifest },
        { sort_keys: true, indent: 2, ensure_ascii: true },
      ) + '\n',
    );
    assert_release_execution(execution);
    await chmod(payload, 0o755);
    // Validate before publication. The final path is never a partially built application.
    await validate_app_manifest(join(payload, 'manifest.json'), {
      expected_sha: requested,
      require_runtime: options.install_dependencies,
    });
  } catch (error) {
    return fail_after_cleanup(error, own_payload ? [workspace, payload] : [workspace]);
  }
  try {
    await remove_owned_tree(workspace);
    assert_release_execution(execution);
    // Publication is the final fallible operation. No successful-path cleanup follows it.
    await rename(payload, destination);
  } catch (error) {
    return fail_after_cleanup(error, [payload]);
  }
  return manifest;
}
