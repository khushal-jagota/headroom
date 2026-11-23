/** Host-independent file composition. Native inspection starts after this phase. */
import { cp, mkdir, readFile, readdir } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { MAC_STATIC_FILES } from '../../executor/mac/packaging/configuration.ts';
import { is_record } from '../../core/records.ts';
import type { ReleaseExecution } from './contracts.ts';
import type { MacHomePayloadOptions } from './mac_home_contracts.ts';
import { require_generated_schema_frontier } from './migrations.ts';
import { assert_release_execution } from './process.ts';

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
export async function compose_mac_home_payload(
  options: MacHomePayloadOptions,
  execution: ReleaseExecution,
): Promise<void> {
  const {
    exported_source: exported,
    app,
    production_node_modules: modules,
    agent_backends_root: providers,
    web_dist: web,
  } = options;
  await production_packages(exported, modules);
  const copy = async (from: string, to: string) => {
    assert_release_execution(execution);
    await mkdir(dirname(to), { recursive: true });
    await cp(from, to, {
      recursive: true,
      verbatimSymlinks: true,
      errorOnExist: true,
      force: false,
      // better-sqlite3 13.1.1 selects darwin-arm64 in its default loader.
      // Its unused x64 prebuild cannot enter this arm64-only Mach-O closure.
      filter: (path) => path !== join(modules, 'better-sqlite3/prebuilds/darwin-x64.node'),
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
  await copy(join(exported, 'assets'), join(app, 'assets'));
  // The server re-exports these generated protocol values at runtime.
  const view_contract = 'web/src/lib/conversation/view/contracts.ts';
  await copy(join(exported, view_contract), join(app, view_contract));
  // The server tree above already carries the static files under server/.
  for (const name of MAC_STATIC_FILES)
    if (!name.startsWith('server/')) await copy(join(exported, name), join(app, name));
  for (const name of await readdir(exported))
    if (/^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)([._-].+)?$/i.test(name))
      await copy(join(exported, name), join(app, name));
  await require_generated_schema_frontier(app);
  assert_release_execution(execution);
}
