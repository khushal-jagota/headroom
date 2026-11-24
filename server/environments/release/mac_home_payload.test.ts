import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, lstatSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { it } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { MAC_HOME_REQUIRED_FILES } from './mac_home_configuration.ts';
import { compose_mac_home_payload } from './mac_home_payload.ts';
import { migration_frontier, read_migration_graph } from './migrations.ts';

it('composes the Mac home from the selected files and schema frontier', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-mac-payload-'));
  const owner = new ChildProcesses(), controller = new AbortController();
  const execution = {child_processes:owner, signal:controller.signal, deadline:performance.now()/1000+10};
  try {
    const checkout = fileURLToPath(new URL('../../../', import.meta.url));
    const payload = join(root, 'payload'),
      modules = join(root, 'payload-modules'),
      providers = join(root, 'payload-providers'),
      web = join(root, 'payload-web');
    const put = (path: string, bytes: string) => {
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, bytes);
    };
    const dependencies = JSON.parse(readFileSync(join(checkout, 'server/package.json'), 'utf8'))
      .dependencies as Record<string, string>;
    for (const [name, version] of Object.entries(dependencies))
      put(join(modules, name, 'package.json'), JSON.stringify({ name, version }));
    const sqlite = join(checkout, 'server/node_modules/better-sqlite3');
    const package_bytes: Record<string, string> = {
      '.package-lock.json': '{"packages":{}}',
      'better-sqlite3/package.json': readFileSync(join(sqlite, 'package.json'), 'utf8'),
      'better-sqlite3/lib/index.js': readFileSync(join(sqlite, 'lib/index.js'), 'utf8'),
      'better-sqlite3/lib/binding.js': readFileSync(join(sqlite, 'lib/binding.js'), 'utf8'),
      'better-sqlite3/LICENSE': 'retained SQLite license\n',
      'better-sqlite3/prebuilds/darwin-arm64.node': 'copy-only arm64 bytes',
      'better-sqlite3/prebuilds/darwin-x64.node': 'copy-only x64 bytes',
      'better-sqlite3/prebuilds/linux-x64.node': 'copy-only Linux bytes',
      'better-sqlite3/prebuilds/win32-x64.node': 'copy-only Windows bytes',
    };
    for (const [name, bytes] of Object.entries(package_bytes)) put(join(modules, name), bytes);
    put(join(providers, 'package.json'), '{"name":"provider-copy-fixture"}');
    for (const name of ['@anthropic-ai/claude-code', '@openai/codex'])
      put(join(providers, 'node_modules', name, 'package.json'), JSON.stringify({ name }));
    put(join(web, 'index.html'), '<!doctype html>copied web input');
    const original_graph = await read_migration_graph(checkout);
    const schema_path = 'server/core/database/application_schema.json';
    const schema_bytes = readFileSync(join(checkout, schema_path));
    const payload_began = performance.now();
    await compose_mac_home_payload(
      {
        exported_source: checkout,
        app: payload,
        production_node_modules: modules,
        agent_backends_root: providers,
        web_dist: web,
      },
      execution,
    );
    assert.deepEqual(await read_migration_graph(payload), original_graph);
    assert.deepEqual(readFileSync(join(payload, schema_path)), schema_bytes);
    assert.deepEqual(
      [...migration_frontier(original_graph)],
      [JSON.parse(schema_bytes.toString()).alembicRevision],
    );
    for (const name of MAC_HOME_REQUIRED_FILES)
      assert(lstatSync(join(payload, name)).isFile(), 'composed payload lacks ' + name);
    for (const name of [
      'server/skills/seeds/panels/SKILL.md',
      'server/worker_types/shipped_worker_types.json',
      'web/src/lib/conversation/view/contracts.ts',
      'assets/tokens.css',
      'assets/app.css',
      'assets/fonts/InterVariable.woff2',
      'assets/fonts/InterVariable-Italic.woff2',
    ])
      assert.deepEqual(readFileSync(join(payload, name)), readFileSync(join(checkout, name)));
    for (const [name, bytes] of Object.entries(package_bytes)) {
      assert.equal(readFileSync(join(modules, name), 'utf8'), bytes, 'copy changed its input');
      if (name === 'better-sqlite3/prebuilds/darwin-x64.node')
        assert(!existsSync(join(payload, 'server/node_modules', name)));
      else assert.equal(readFileSync(join(payload, 'server/node_modules', name), 'utf8'), bytes);
    }
    assert.deepEqual(await read_migration_graph(checkout), original_graph);
    assert.deepEqual(readFileSync(join(checkout, schema_path)), schema_bytes);
    console.log(
      JSON.stringify({
        proof: 'actual home payload composition and bound migration source',
        seconds: (performance.now() - payload_began) / 1000,
      }),
    );
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
    rmSync(root, {recursive:true,force:true});
  }
}, 15000);
