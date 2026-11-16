/** Structural inputs only. These fixtures are not executable Darwin artifacts. */
import { expect, test } from 'vitest';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { ArchiveMembers } from '../../../environments/release/logic/archive_members.ts';
import { build_mac_package } from './build.ts';
import { MAC_REQUIRED_FILES } from './configuration.ts';

test('Mac package keeps its runtime/CLI assets separate and refuses a non-Darwin Node', async () => {
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
    await put(join(source, 'src/planner/skills/panels/SKILL.md'), '# seed asset');
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
    for (const [name, version] of Object.entries(dependencies))
      await put(join(modules, name, 'package.json'), JSON.stringify({ name, version }));
    await put(join(modules, 'better-sqlite3/prebuilds/darwin-arm64.node'), native);
    await put(join(modules, '@koromix/koffi-darwin-arm64/darwin_arm64/koffi.node'), native);
    const options = {
      source_root: source,
      node_distribution: node,
      production_node_modules: modules,
      output: join(root, 'output'),
      revision: '1'.repeat(40),
      home: '/Users/Owner & Test',
      executor_id: 'typescript-trial',
      server_url: 'https://127.0.0.1:9',
      backend_path: '/usr/bin:/bin',
    };
    const built = await build_mac_package(options);
    expect(built.paths.node).toBe(
      '/Users/Owner & Test/Deployments/PanelsExecutorTypeScript/supervisor/node/bin/node',
    );
    const manifest = JSON.parse(
      await readFile(join(options.output, 'runtime/mac-runtime.json'), 'utf8'),
    );
    expect(manifest).toEqual({
      schema_version: 1,
      application_revision: options.revision,
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
        'node/bin/node',
        'server/executor/mac/entrypoint.ts',
        'server/executor/wire.generated.ts',
        'server/executor/wire.schema.json',
        'src/planner/conversation/backends/hermes_acp_extension.py',
        'src/planner/worker_types/shipped_worker_types.json',
      ]),
    );
    expect(members).not.toEqual(expect.arrayContaining(['src/planner/executor/mac_service.py']));
    expect(
      await readFile(join(options.output, 'runtime/server/executor/wire.schema.json'), 'utf8'),
    ).toBe(wire_schema);
    expect(Buffer.concat(archived_schema).toString()).toBe(wire_schema);
    expect(members).not.toEqual(expect.arrayContaining(['server/executor/mac/service.test.ts']));
    expect(
      await readFile(join(options.output, 'assets/com.panels.executor.typescript.plist'), 'utf8'),
    ).toContain('Owner &amp; Test');
    await put(join(node, 'bin/node'), Buffer.from('7f454c4600000000', 'hex'));
    await expect(build_mac_package({ ...options, output: join(root, 'refused') })).rejects.toThrow(
      'Darwin arm64 Mach-O',
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
