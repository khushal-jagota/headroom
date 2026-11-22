/** Build and prove the sealed Mac archive on the native hosted runner. */
import { mkdir, readFile, realpath, rename, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { extract } from 'tar';
import { hash_archive, remove_owned_tree } from '../../../environments/release/archive.ts';
import {
  digest_machine_build,
  machine_build_differences,
} from '../../../environments/release/machine_identity.ts';
import { with_publication_entry } from '../../../environments/release/publication_entry.ts';
import { run_release_command } from '../../../environments/release/process.ts';
import { publication_id } from '../../../environments/release/publication_identity.ts';
import type { MacRuntimePublication } from '../../../environments/release/mac_publication_contracts.ts';
import { build_mac_package } from './build.ts';
import { MAC_RUNTIME_LAUNCHER_APP } from '../platform.ts';
import { PUBLICATION_MAC_VALIDATION_SECONDS } from '../../../environments/release/publication_configuration.ts';

export function publication_main(args: string[]): Promise<0 | 1> {
  return with_publication_entry(async (execution) => {
    if (process.platform !== 'darwin' || process.arch !== 'arm64')
      throw new Error('Mac publication requires Darwin arm64');
    const names = [
      'source',
      'node-distribution',
      'node-modules',
      'repeat-node-modules',
      'output',
      'revision',
      'launcher-app',
      'run-id',
      'run-attempt',
    ] as const;
    const { values } = parseArgs({
      args,
      options: Object.fromEntries(names.map((name) => [name, { type: 'string' as const }])),
      strict: true,
    });
    const text = (name: (typeof names)[number]) => {
      const value = values[name];
      if (typeof value !== 'string' || !value) throw new Error(`Required option: --${name}`);
      return value;
    };
    const run_id = publication_id(text('run-id'), 'run ID'),
      run_attempt = publication_id(text('run-attempt'), 'run attempt');
    const package_options = {
      source_root: text('source'),
      node_distribution: text('node-distribution'),
      production_node_modules: text('node-modules'),
      output: text('output'),
      revision: text('revision'),
      launcher_app: text('launcher-app'),
    };
    const repeat_node_modules = text('repeat-node-modules');
    if (
      (await realpath(package_options.production_node_modules)) ===
      (await realpath(repeat_node_modules))
    )
      throw new Error('Darwin repeat builds require independent dependency inputs');
    const built = await build_mac_package(package_options);
    const repeat_output = text('output') + '-repeat';
    // The owner treats the checked signed launcher as a fixed input, like the Node distribution.
    // Its exact bytes remain part of both machine identities.
    const repeated = await build_mac_package({
      ...package_options,
      production_node_modules: repeat_node_modules,
      output: repeat_output,
    });
    if (built.build_id !== repeated.build_id) {
      const differences = await machine_build_differences(
        join(text('output'), 'runtime'),
        join(repeat_output, 'runtime'),
      );
      console.error(
        'Darwin repeat build differences (%d):\n%s',
        differences.length,
        differences.slice(0, 30).join('\n'),
      );
      throw new Error('Independent Darwin machine builds produced different identities');
    }
    await remove_owned_tree(repeat_output);
    const output = text('output'),
      archive = join(output, 'panels-mac-runtime.tar.gz');
    // Retire the assembly path. All execution below uses a fresh unpack of the sealed bytes.
    await rename(join(output, 'runtime'), join(output, 'unproved-assembly'));
    const relocated = join(output, 'relocated'),
      work = join(output, 'proof-work');
    await mkdir(relocated);
    await mkdir(work);
    await extract({ file: archive, cwd: relocated, strict: true });
    const node = join(relocated, 'node/bin/node');
    const started = performance.now();
    const environment = {
      PATH: dirname(node) + ':/usr/bin:/bin',
      HOME: process.env.HOME,
      TMPDIR: process.env.TMPDIR,
      PLAN_SERVER_URL: 'https://127.0.0.1:9',
      PLAN_TEST_MODE: '0',
    };
    const cli = await run_release_command(
      [join(relocated, 'bin/panels'), 'executor', '--help'],
      {
        timeout_seconds: PUBLICATION_MAC_VALIDATION_SECONDS,
        cwd: work,
        environment,
        allow_nonzero: true,
      },
      execution,
    );
    await writeFile(join(work, 'cli.log'), cli.stdout + cli.stderr);
    const launcher = await run_release_command(
      [
        '/usr/bin/codesign',
        '--verify',
        '--strict',
        '--verbose=2',
        join(relocated, MAC_RUNTIME_LAUNCHER_APP),
      ],
      {
        timeout_seconds: PUBLICATION_MAC_VALIDATION_SECONDS,
        cwd: work,
        environment,
        allow_nonzero: true,
      },
      execution,
    );
    if (launcher.code !== 0)
      throw new Error('The unpacked launcher signature failed: ' + launcher.stderr);
    if (cli.code !== 0) throw new Error('Packaged executor CLI failed: ' + cli.stderr);
    const native = await run_release_command(
      [node, join(relocated, 'server/executor/mac/packaging/native_probe.ts'), work],
      {
        timeout_seconds: PUBLICATION_MAC_VALIDATION_SECONDS,
        cwd: work,
        environment,
        allow_nonzero: true,
      },
      execution,
    );
    await writeFile(join(work, 'native.log'), native.stdout + native.stderr);
    if (native.code !== 0) throw new Error('Packaged native probe failed: ' + native.stderr);
    const probe = JSON.parse(native.stdout);
    if (
      probe.node_version !== built.node_version ||
      probe.native_files !== 'passed' ||
      probe.sqlite !== 'passed' ||
      probe.host_import !== 'passed'
    )
      throw new Error('Relocated native proof differs from the package');
    const node_sha256 = await hash_archive(node);
    if (node_sha256 !== built.node_sha256 || (await hash_archive(archive)) !== built.archive_sha256)
      throw new Error('Mac package changed during native proof');
    const manifest = JSON.parse(await readFile(join(relocated, 'mac-runtime.json'), 'utf8'));
    if (
      manifest.application_revision !== text('revision') ||
      manifest.build_id !== built.build_id ||
      (await digest_machine_build(relocated)) !== built.build_id ||
      (await digest_machine_build(join(relocated, MAC_RUNTIME_LAUNCHER_APP))) !==
        built.launcher_sha256
    )
      throw new Error('Relocated revision differs');
    const receipt: MacRuntimePublication = {
      schema_version: 1,
      application_revision: text('revision'),
      build_id: built.build_id,
      launcher_sha256: built.launcher_sha256,
      repeat_build_id: repeated.build_id,
      platform: 'darwin',
      architecture: 'arm64',
      publication_run_id: run_id,
      run_attempt,
      archive_sha256: built.archive_sha256,
      archive_size: (await stat(archive)).size,
      node_version: probe.node_version,
      node_sha256,
      proof: {
        relocated_archive: 'passed',
        executor_cli: 'passed',
        native_files: 'passed',
        sqlite: 'passed',
        host_import: 'passed',
        duration_seconds: (performance.now() - started) / 1000,
      },
    };
    await writeFile(
      join(output, 'mac-runtime-publication.json'),
      JSON.stringify(receipt, null, 2) + '\n',
    );
    process.stdout.write(JSON.stringify(receipt) + '\n');
  });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await publication_main(process.argv.slice(2));
