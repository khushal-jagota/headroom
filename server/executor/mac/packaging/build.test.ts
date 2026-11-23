/** Structural inputs only. These fixtures are not executable Darwin artifacts. */
import { expect, test } from 'vitest';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { ArchiveMembers } from '../../../environments/release/logic/archive_members.ts';
import { build_mac_package } from './build.ts';
import { extract_publication_archive } from './publication_main.ts';
import { digest_machine_build } from '../../../environments/release/machine_identity.ts';
import { MAC_REQUIRED_FILES } from './configuration.ts';
import { mac_installation_assets, mac_launcher_bundle } from '../platform.ts';

test('Mac package carries the shared launcher, no account path, and refuses a non-Darwin Node', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-package-'));
  const source = join(root, 'source'),
    node = join(root, 'node'),
    modules = join(root, 'modules');
  const put = async (path: string, content: string | Uint8Array) => {
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, content);
  };
  const native = Buffer.alloc(32);
  native.writeUInt32LE(0xfeedfacf);
  native.writeUInt32LE(0x100000c, 4);
  try {
    for (const file of MAC_REQUIRED_FILES) await put(join(source, file), '// structural fixture');
    const dependencies = {
      koffi: '3.3.7',
      'better-sqlite3': '13.1.1',
      '@anthropic-ai/claude-agent-sdk': '0.3.316',
    };
    await put(
      join(source, 'server/package.json'),
      JSON.stringify({ dependencies, type: 'module' }),
    );
    const wire_schema = '{"fixture":"native wire schema bytes"}\n';
    await put(join(source, 'server/executor/wire.schema.json'), wire_schema);
    await put(join(source, 'server/executor/mac/service.test.ts'), '// excluded');
    await put(join(source, 'src/planner/executor/mac_service.py'), '# not shipped');
    await put(join(source, 'server/skills/seeds/panels/SKILL.md'), '# seed asset');
    await put(
      join(source, 'ops/mac-executor/typescript-installation.md'),
      'Structural fixture instructions',
    );
    await put(join(node, 'bin/node'), native);
    await chmod(join(node, 'bin/node'), 0o755);
    await put(join(node, 'LICENSE'), 'fixture');
    await put(
      join(node, 'include/node/node_version.h'),
      '#define NODE_MAJOR_VERSION 24\n#define NODE_MINOR_VERSION 1\n#define NODE_PATCH_VERSION 0\n',
    );
    await put(join(modules, '.package-lock.json'), '{"packages":{}}');
    await put(join(modules, 'better-sqlite3/build/Makefile'), '/scratch/first/native');
    await put(join(modules, 'better-sqlite3/build/config.gypi'), '{"prefix":"/scratch/first"}');
    for (const [name, version] of Object.entries(dependencies))
      await put(join(modules, name, 'package.json'), JSON.stringify({ name, version }));
    await put(join(modules, 'better-sqlite3/prebuilds/darwin-arm64.node'), native);
    await put(join(modules, '@koromix/koffi-darwin-arm64/darwin_arm64/koffi.node'), native);
    const launcher_app = join(root, 'launcher/Panels TypeScript.app');
    await put(join(launcher_app, 'Contents/Info.plist'), mac_launcher_bundle().info_plist);
    await put(join(launcher_app, 'Contents/MacOS/Panels'), native);
    await chmod(join(launcher_app, 'Contents/MacOS/Panels'), 0o755);
    const options = {
      source_root: source,
      node_distribution: node,
      production_node_modules: modules,
      output: join(root, 'output'),
      revision: '1'.repeat(40),
      launcher_app,
    };
    const built = await build_mac_package(options);
    const manifest = JSON.parse(
      await readFile(join(options.output, 'runtime/mac-runtime.json'), 'utf8'),
    );
    expect(manifest).toEqual({
      schema_version: 1,
      application_revision: options.revision,
      build_id: expect.stringMatching(/^[a-f0-9]{64}$/),
      architecture: 'arm64',
      platform: 'darwin',
    });
    const archived_schema: Buffer[] = [];
    const reader = new ArchiveMembers('extraction', (entry, member) => {
      if (member.path === 'server/executor/wire.schema.json')
        entry.on('data', (bytes: Buffer) => archived_schema.push(bytes));
    });
    reader.feed(gunzipSync(await readFile(join(options.output, 'panels-mac-runtime.tar.gz'))));
    reader.finish();
    const members = reader.members.map((entry) => entry.path);
    expect(members).toEqual(
      expect.arrayContaining([
        'bin/panels',
        'launcher/Panels TypeScript.app/Contents/Info.plist',
        'launcher/Panels TypeScript.app/Contents/MacOS/Panels',
        'node/bin/node',
        'server/node_modules/better-sqlite3/prebuilds/darwin-arm64.node',
        'server/executor/mac/entrypoint.ts',
        'server/executor/wire.generated.ts',
        'server/executor/wire.schema.json',
        'server/worker_types/shipped_worker_types.json',
      ]),
    );
    expect(members).not.toEqual(expect.arrayContaining(['src/planner/executor/mac_service.py']));
    expect(
      await readFile(join(options.output, 'runtime/server/executor/wire.schema.json'), 'utf8'),
    ).toBe(wire_schema);
    expect(Buffer.concat(archived_schema).toString()).toBe(wire_schema);
    expect(members).not.toEqual(expect.arrayContaining(['server/executor/mac/service.test.ts']));
    expect(members).not.toContain('server/node_modules/better-sqlite3/build/Makefile');
    expect(members).not.toContain('server/node_modules/better-sqlite3/build/config.gypi');
    const relocated = join(root, 'relocated');
    await mkdir(relocated, { mode: 0o700 });
    const previous_umask = process.umask(0o077);
    try {
      await extract_publication_archive(
        join(options.output, 'panels-mac-runtime.tar.gz'),
        relocated,
      );
      expect(process.umask()).toBe(0o077);
      expect(await digest_machine_build(relocated)).toBe(built.build_id);
    } finally {
      process.umask(previous_umask);
    }
    await put(join(modules, 'better-sqlite3/build/Makefile'), '/scratch/repeated/native');
    await put(join(modules, 'better-sqlite3/build/config.gypi'), '{"prefix":"/scratch/repeated"}');
    const repeated = await build_mac_package({ ...options, output: join(root, 'repeated') });
    expect(repeated.build_id).toBe(built.build_id);
    // The account appears only in the files a join writes on the machine.
    const account = mac_installation_assets({
      home: '/Users/Owner & Test',
      revision: options.revision,
      executor_id: '00000000-0000-4000-8000-000000000000',
      server_url: 'https://127.0.0.1:9',
      backend_path: '/usr/bin:/bin',
    });
    expect(account.launch_agent).toContain('Owner &amp; Test');
    expect(mac_launcher_bundle().launcher_source).not.toContain('/Users/');
    expect(mac_launcher_bundle().launcher_source).toContain('--panels-worker-exec-v1');
    const changed_launcher = Buffer.from(native);
    changed_launcher[16] = 1;
    await put(join(launcher_app, 'Contents/MacOS/Panels'), changed_launcher);
    const changed = await build_mac_package({ ...options, output: join(root, 'changed-launcher') });
    expect(changed.launcher_sha256).not.toBe(built.launcher_sha256);
    expect(changed.build_id).not.toBe(built.build_id);
    await put(join(node, 'bin/node'), Buffer.from('7f454c4600000000', 'hex'));
    await expect(build_mac_package({ ...options, output: join(root, 'refused') })).rejects.toThrow(
      'Darwin arm64 Mach-O',
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
