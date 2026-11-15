/** Compose a complete native home from one exact source export and prepared native inputs. */
import { chmod, cp, lstat, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { macho_arm64, node_version } from '../../executor/mac/packaging/build.ts';
import { MAC_STATIC_FILES } from '../../executor/mac/packaging/configuration.ts';
import { path_is_within } from '../../core/path_containment.ts';
import type { AppManifest, ReleaseBuildDependencies, ReleaseExecution } from './contracts.ts';
import { MAC_HOME } from './contracts.ts';
import type { MacHomeBuildOptions, MacHomeBuildResult } from './mac_home_contracts.ts';
import { build_exported_app } from './build.ts';
import { write_app_entrypoints } from './entrypoints.ts';
import { create_app_archive, remove_owned_tree } from './archive.ts';
import { digest_app_artifact, validate_app_manifest, validate_app_tree } from './identity.ts';
import { assert_release_execution } from './process.ts';
import { complete_mac_home_runtime } from './mac_home_native.ts';
import { write_installer_file_manifest } from './installer_bundle.ts';
import { APP_FORMAT, APP_ARCHIVE_NAME } from './configuration.ts';
import { MAC_HOME_REQUIRED_FILES } from './mac_home_configuration.ts';
import { read_migration_graph, migration_frontier } from './migrations.ts';
import { resolved_path, expand_path } from '../paths.ts';
import { is_record } from '../../core/records.ts';
import { parse_mac_home_inventory } from './mac_home_inventory.ts';

async function production_packages(source: string, modules: string): Promise<void> {
  const declared: unknown = JSON.parse(await readFile(join(source, 'server/package.json'), 'utf8'));
  const installed: unknown = JSON.parse(
    await readFile(join(modules, '.package-lock.json'), 'utf8'),
  );
  if (
    !is_record(declared) ||
    !is_record(declared.dependencies) ||
    !is_record(installed) ||
    !is_record(installed.packages) ||
    Object.values(installed.packages).some((entry) => !is_record(entry) || entry.dev === true)
  )
    throw new Error('Provide production node_modules from npm ci --omit=dev');
  for (const [name, version] of Object.entries(declared.dependencies)) {
    const dependency: unknown = JSON.parse(
      await readFile(join(modules, name, 'package.json'), 'utf8'),
    );
    if (!is_record(dependency) || dependency.version !== version)
      throw new Error(`Production dependency differs from the source pin: ${name}`);
  }
}
async function require_schema_frontier(app: string): Promise<void> {
  const heads = migration_frontier(await read_migration_graph(app));
  const seed: unknown = JSON.parse(
    await readFile(join(app, 'server/core/database/application_schema.json'), 'utf8'),
  );
  if (!is_record(seed) || heads.size !== 1 || !heads.has(String(seed.alembicRevision)))
    throw new Error('Migration graph differs from the generated schema frontier');
}
export async function build_mac_home(
  options: MacHomeBuildOptions,
  dependencies: ReleaseBuildDependencies,
  execution: ReleaseExecution,
): Promise<MacHomeBuildResult> {
  if (process.platform !== 'darwin' || process.arch !== 'arm64')
    throw new Error('Mac home packaging requires a native arm64 Mac');
  assert_release_execution(execution);
  const host_node = await realpath(process.execPath);
  await macho_arm64(host_node, true);
  const version = await node_version(dirname(dirname(host_node)));
  if (!version.startsWith('22.') || version !== process.versions.node)
    throw new Error('Mac home packaging requires matching host Node 22.18+ headers and executable');
  const source = await realpath(expand_path(options.source_root)),
    modules = await realpath(expand_path(options.production_node_modules)),
    providers = await realpath(expand_path(options.agent_backends_root)),
    web = await realpath(expand_path(options.web_dist));
  const output_input = expand_path(options.output_directory);
  try {
    if ((await lstat(output_input)).isSymbolicLink())
      throw new Error('Mac home output must not be a symlink');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const output = await resolved_path(output_input);
  if (
    [source, modules, providers, web].some(
      (input) => path_is_within(input, output) || path_is_within(output, input),
    )
  )
    throw new Error('Mac home output must be separate from its inputs');
  assert_release_execution(execution);
  await mkdir(output, { mode: 0o700 });
  const exported = join(output, '.source'),
    app = join(output, 'app');
  try {
    const source_manifest = await build_exported_app(
      source,
      {
        requested_sha: options.requested_sha,
        candidate_app: exported,
        install_dependencies: false,
      },
      dependencies,
      execution,
    );
    await production_packages(exported, modules);
    const copy = async (from: string, to: string) => {
      assert_release_execution(execution);
      await mkdir(dirname(to), { recursive: true });
      await cp(from, to, {
        recursive: true,
        verbatimSymlinks: true,
        errorOnExist: true,
        force: false,
      });
    };
    await mkdir(app, { mode: 0o700 });
    const server = join(exported, 'server');
    await cp(server, join(app, 'server'), {
      recursive: true,
      verbatimSymlinks: true,
      filter(path) {
        const name = relative(server, path),
          parts = name.split('/');
        return (
          !parts.some((part) => ['node_modules', 'testing', 'tests', '.git'].includes(part)) &&
          !name.endsWith('.test.ts') &&
          name !== 'test_host.ts'
        );
      },
    });
    await copy(modules, join(app, 'server/node_modules'));
    for (const name of ['package.json', 'node_modules'])
      await copy(join(providers, name), join(app, 'agent_backends', name));
    await copy(web, join(app, 'web/dist'));
    for (const name of MAC_STATIC_FILES) await copy(join(exported, name), join(app, name));
    await copy(join(exported, 'src/planner/skills'), join(app, 'src/planner/skills'));
    const { readdir } = await import('node:fs/promises');
    for (const name of await readdir(exported))
      if (/^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)([._-].+)?$/i.test(name))
        await copy(join(exported, name), join(app, name));
    await require_schema_frontier(app);
    await validate_app_tree(app);
    await write_app_entrypoints(app, options.requested_sha, dependencies, execution);
    await writeFile(
      join(app, 'mac-runtime.json'),
      JSON.stringify({
        schema_version: 1,
        application_revision: options.requested_sha,
        platform: 'darwin',
        architecture: 'arm64',
        role: 'home',
      }) + '\n',
      { flag: 'wx', mode: 0o600 },
    );
    const contract = await complete_mac_home_runtime(app, execution);
    const inventory = parse_mac_home_inventory(
      JSON.parse(await readFile(join(app, 'runtime/inventory.json'), 'utf8')),
    );
    const active = inventory.native_files.filter(
      (entry) => entry.active && entry.path.endsWith('.node'),
    );
    if (
      !active.some((entry) => entry.path.startsWith('server/node_modules/better-sqlite3/')) ||
      !active.some((entry) =>
        /^server\/node_modules\/(?:koffi|@koromix\/koffi-darwin-arm64)\//.test(entry.path),
      )
    )
      throw new Error('Mac home requires native Darwin SQLite and Koffi addons');
    await write_installer_file_manifest(app, options.requested_sha, execution);
    for (const name of MAC_HOME_REQUIRED_FILES)
      if (!(await lstat(join(app, name))).isFile())
        throw new Error(`Mac home runtime file is missing: ${name}`);
    const manifest: AppManifest = {
      app_sha: options.requested_sha,
      source_digest: source_manifest.source_digest,
      artifact_digest: await digest_app_artifact(app),
      runtime_contract: contract,
    };
    await writeFile(
      join(app, 'manifest.json'),
      JSON.stringify({ format: APP_FORMAT, ...manifest }, null, 2) + '\n',
      { flag: 'wx', mode: 0o600 },
    );
    await chmod(app, 0o755);
    await validate_app_manifest(join(app, 'manifest.json'), {
      expected_sha: options.requested_sha,
      expected_target: MAC_HOME,
      require_runtime: true,
    });
    assert_release_execution(execution);
    await remove_owned_tree(exported);
    const archive = join(output, APP_ARCHIVE_NAME),
      archive_sha256 = await create_app_archive(app, archive, MAC_HOME);
    const result: MacHomeBuildResult = {
      manifest,
      archive,
      archive_sha256,
      archive_size: (await lstat(archive)).size,
      inventory_sha256: contract.inventory_sha256,
      node_version: version,
    };
    assert_release_execution(execution);
    return result;
  } catch (error) {
    try {
      await remove_owned_tree(output);
    } catch (cleanup) {
      throw new AggregateError([error, cleanup], `Mac home build cleanup failed: ${output}`, {
        cause: error,
      });
    }
    throw error;
  }
}
