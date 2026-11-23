/** One private copy lifetime proves selection, publication and immutable bytes, without a launch. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readlinkSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { createRequire, isBuiltin, syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
import { it } from 'vitest';
import ts from 'typescript';
import { inside } from '../../files/logic/paths.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import {
  VIEW_PROTOCOL_VERSION,
  VIEW_PROJECTION_VERSION,
} from '../../conversation/views/contracts.ts';
import {
  copy_installer_runtime,
  parse_installer_file_manifest,
  write_installer_file_manifest,
} from './installer_bundle.ts';
import {
  INSTALLER_ENTRY,
  INSTALLER_RECIPE_PATH,
  INSTALLER_SOURCE_FILES,
  DARWIN_HOME_INSTALLER_SOURCE_FILES,
  DARWIN_HOME_INSTALLER_PACKAGE_ROOTS,
  DARWIN_HOME_INSTALLER_KOFFI_OPTIONAL_ROOT,
} from './installer_bundle_configuration.ts';
import { MAC_HOME } from './contracts.ts';
import { compose_mac_home_payload } from './mac_home_payload.ts';
import { MAC_HOME_REQUIRED_FILES } from './mac_home_configuration.ts';
import { migration_frontier, read_migration_graph } from './migrations.ts';
import {
  GLIBC_LIBRARIES,
  SUPPORTED_CPU_FEATURES,
  SUPPORTED_PLATFORMS,
} from '../updates/logic/runtime_contract.ts';
import type { InstallerRequest } from '../updates/installer/contracts.ts';
import { run_release_command } from './process.ts';
import { BUILT_IN_DRIVERS } from '../../drivers/built_in.ts';
import {
  INSTALLER_KOFFI_OPTIONAL_ROOT,
  INSTALLER_PACKAGE_ROOTS,
} from './installer_bundle_configuration.ts';

const CHECKOUT = fileURLToPath(new URL('../../../', import.meta.url));
/**
 * The driver.ts files BUILT_IN_DRIVERS names. Their deferred import() may load a runtime outside
 * the frozen set: the installer reads only drivers' static fields, and check_driver_names.ts holds
 * driver.ts to static imports of core, manifest, client, models and tool_detail.
 */
const BUILT_IN_DRIVER_FILES: ReadonlySet<string> = new Set(
  BUILT_IN_DRIVERS.map((driver) => {
    const name = `server/drivers/${driver.driver_kind}/driver.ts`;
    assert(existsSync(join(CHECKOUT, name)), 'a built-in driver has no driver.ts: ' + name);
    return name;
  }),
);
interface ClosureWalk {
  readonly linux: ReadonlySet<string>;
  readonly darwin: ReadonlySet<string>;
  readonly platform: 'linux' | 'darwin';
  readonly packages: ReadonlySet<string>;
  readonly entries: readonly string[];
  /** Source text in place of a file's bytes, for the rule's own negative cases. */
  readonly sources?: ReadonlyMap<string, string>;
}
const parsed = new Map<string, ts.SourceFile>();
/** Every closure file the entries reach. Throws on an import the frozen set cannot satisfy. */
function walk_installer_closure(options: ClosureWalk): Set<string> {
  const require = createRequire(join(CHECKOUT, 'server/package.json'));
  const { linux, darwin, packages } = options;
  const frozen = options.platform === 'linux' ? linux : darwin;
  const seen = new Set<string>();
  function visit(name: string): void {
    if (seen.has(name)) return;
    seen.add(name);
    const absolute = resolve(CHECKOUT, name);
    inside(CHECKOUT, absolute);
    const text = options.sources?.get(name) ?? readFileSync(absolute, 'utf8');
    const key = name + '\0' + text;
    let source = parsed.get(key);
    if (source === undefined) {
      source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
      parsed.set(key, source);
    }
    const deferred_requires = new Set<string>();
    function dependency(specifier: string, deferred = false, dynamic_import = false): void {
      if (isBuiltin(specifier)) return;
      if (specifier.startsWith('.')) {
        const path = resolve(dirname(absolute), specifier);
        inside(CHECKOUT, path);
        const dependency_name = relative(CHECKOUT, path).split(sep).join('/');
        const darwin_only = darwin.has(dependency_name) && !linux.has(dependency_name);
        assert(
          frozen.has(dependency_name) ||
            (deferred && darwin_only) ||
            (dynamic_import && BUILT_IN_DRIVER_FILES.has(name)),
          'unknown platform import: ' + dependency_name,
        );
        assert(
          !darwin_only || deferred || !linux.has(name),
          'common source eagerly imports Darwin-only source: ' + dependency_name,
        );
        if (frozen.has(dependency_name)) visit(dependency_name);
      } else {
        const package_name = specifier
          .split('/')
          .slice(0, specifier.startsWith('@') ? 2 : 1)
          .join('/');
        const selected = 'server/node_modules/' + package_name;
        assert(packages.has(selected), 'new bare package escapes copied closure: ' + specifier);
        inside(
          fs.realpathSync(join(CHECKOUT, selected)),
          fs.realpathSync(require.resolve(specifier)),
        );
      }
    }
    function walk(node: ts.Node): void {
      if (
        ts.isVariableDeclaration(node) &&
        ts.isIdentifier(node.name) &&
        node.initializer &&
        ts.isCallExpression(node.initializer) &&
        ts.isIdentifier(node.initializer.expression) &&
        node.initializer.expression.text === 'createRequire'
      ) {
        deferred_requires.add(node.name.text);
      } else if (ts.isImportDeclaration(node)) {
        const clause = node.importClause;
        const only_types =
          clause?.isTypeOnly ||
          (!clause?.name &&
            clause?.namedBindings &&
            ts.isNamedImports(clause.namedBindings) &&
            clause.namedBindings.elements.length > 0 &&
            clause.namedBindings.elements.every((item) => item.isTypeOnly));
        if (!only_types && ts.isStringLiteral(node.moduleSpecifier))
          dependency(node.moduleSpecifier.text);
      } else if (
        ts.isExportDeclaration(node) &&
        !node.isTypeOnly &&
        node.moduleSpecifier &&
        ts.isStringLiteral(node.moduleSpecifier)
      ) {
        const only_types =
          node.exportClause &&
          ts.isNamedExports(node.exportClause) &&
          node.exportClause.elements.length > 0 &&
          node.exportClause.elements.every((item) => item.isTypeOnly);
        if (!only_types) dependency(node.moduleSpecifier.text);
      } else if (
        ts.isCallExpression(node) &&
        node.expression.kind === ts.SyntaxKind.ImportKeyword &&
        node.arguments[0] &&
        ts.isStringLiteralLike(node.arguments[0])
      ) {
        dependency(node.arguments[0].text, true, true);
      } else if (
        ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        deferred_requires.has(node.expression.text) &&
        node.arguments[0] &&
        ts.isStringLiteralLike(node.arguments[0])
      ) {
        dependency(node.arguments[0].text, true);
      } else if (
        ts.isNewExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === 'URL' &&
        node.arguments?.length === 2 &&
        ts.isStringLiteralLike(node.arguments[0])
      ) {
        const base = node.arguments[1];
        if (
          ts.isPropertyAccessExpression(base) &&
          base.name.text === 'url' &&
          ts.isMetaProperty(base.expression) &&
          base.expression.keywordToken === ts.SyntaxKind.ImportKeyword
        ) {
          const path = fileURLToPath(new URL(node.arguments[0].text, pathToFileURL(absolute)));
          if (['.ts', '.json'].includes(extname(path))) {
            inside(CHECKOUT, path);
            visit(relative(CHECKOUT, path).split(sep).join('/'));
          }
        }
      }
      ts.forEachChild(node, walk);
    }
    walk(source);
  }
  for (const entry of options.entries) visit(entry);
  return seen;
}
const LINUX_PACKAGES = new Set<string>([...INSTALLER_PACKAGE_ROOTS, INSTALLER_KOFFI_OPTIONAL_ROOT]);

/** Only the frozen Linux installer source files and their package roots, copied under `root`. */
function copy_linux_installer_closure(root: string): void {
  for (const path of INSTALLER_SOURCE_FILES) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    cpSync(join(CHECKOUT, path), join(root, path));
  }
  for (const path of LINUX_PACKAGES) {
    if (!existsSync(join(CHECKOUT, path))) {
      assert.equal(path, INSTALLER_KOFFI_OPTIONAL_ROOT);
      continue;
    }
    mkdirSync(dirname(join(root, path)), { recursive: true });
    cpSync(join(CHECKOUT, path), join(root, path), { recursive: true, verbatimSymlinks: true });
  }
}
/** A child environment whose homes, state paths and temporary files all sit under `root`. */
function isolated_node_environment(root: string, loaded: string): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {
    PATH: '/usr/bin:/bin',
    LANG: 'C.UTF-8',
    PLAN_TEST_MODE: '1',
    VITEST: 'true',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    TMPDIR: root,
    TMP: root,
    TEMP: root,
  };
  for (const name of [
    'HOME',
    'CODEX_HOME',
    'CLAUDE_CONFIG_DIR',
    'PLAN_LOGS_DIR',
    'PLAN_BACKUP_DIR',
    'PLAN_WORKSPACE_ROOT',
    'PLAN_TICKET_SCRATCH_ROOT',
  ]) {
    environment[name] = join(root, 'homes', name);
    mkdirSync(environment[name]!, { recursive: true });
  }
  for (const name of [
    'PLAN_CONFIG_PATH',
    'PLAN_DB_PATH',
    'PLAN_TICKET_SCRATCH_DATABASE',
    'PLAN_DISPATCHER_LOCK_PATH',
    'PLAN_SERVER_CONTROL_SOCKET',
    'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
    'PLAN_CREDENTIAL_KEY_PATH',
  ])
    environment[name] = join(root, name);
  environment.PLAN_APP_ROOT = loaded;
  writeFileSync(environment.PLAN_CONFIG_PATH!, '{}\n');
  return environment;
}

it('keeps the frozen payload complete for static installer and trusted admission imports', () => {
  const linux = new Set<string>(INSTALLER_SOURCE_FILES),
    darwin = new Set<string>(DARWIN_HOME_INSTALLER_SOURCE_FILES);
  for (const platform of ['linux', 'darwin'] as const) {
    const frozen = platform === 'linux' ? linux : darwin;
    const seen = walk_installer_closure({
      linux,
      darwin,
      platform,
      packages:
        platform === 'linux'
          ? LINUX_PACKAGES
          : new Set<string>([
              ...DARWIN_HOME_INSTALLER_PACKAGE_ROOTS,
              DARWIN_HOME_INSTALLER_KOFFI_OPTIONAL_ROOT,
            ]),
      entries: [
        INSTALLER_ENTRY,
        'server/environments/release/admission.ts',
        ...(platform === 'darwin' ? ['server/environments/updates/darwin/launch.ts'] : []),
      ],
    });
    assert(seen.has('server/core/path_containment.ts'));
    assert.deepEqual([...seen].filter((name) => !frozen.has(name)).sort(), []);
  }
});

// Gap: the one relaxation of the closure rule must not spread. Every closure file, and a
// driver.ts that BUILT_IN_DRIVERS does not name, still fails on a deferred import that leaves it.
it('lets a deferred import leave the frozen set only from a driver.ts BUILT_IN_DRIVERS names', () => {
  const drivers = [...BUILT_IN_DRIVER_FILES, 'server/drivers/core/driver.ts'];
  const linux = new Set<string>([...INSTALLER_SOURCE_FILES, ...drivers]),
    darwin = new Set<string>([...DARWIN_HOME_INSTALLER_SOURCE_FILES, ...drivers]);
  const outside = "\nexport const later = () => import('./outside_the_frozen_set.ts');\n";
  const stray = 'server/drivers/not_built_in/driver.ts';
  assert(!existsSync(join(CHECKOUT, stray)));
  const covered: Record<string, string[]> = {};
  for (const platform of ['linux', 'darwin'] as const) {
    const walk = (
      sources?: ReadonlyMap<string, string>,
      extra: readonly string[] = [],
      entries?: readonly string[],
    ) =>
      walk_installer_closure({
        linux: platform === 'linux' ? new Set([...linux, ...extra]) : linux,
        darwin: platform === 'darwin' ? new Set([...darwin, ...extra]) : darwin,
        platform,
        packages:
          platform === 'linux'
            ? LINUX_PACKAGES
            : new Set<string>([
                ...DARWIN_HOME_INSTALLER_PACKAGE_ROOTS,
                DARWIN_HOME_INSTALLER_KOFFI_OPTIONAL_ROOT,
              ]),
        // Every listed source file, so a file reached only through types is checked too.
        entries:
          entries ??
          [...(platform === 'linux' ? linux : darwin), ...extra].filter((name) =>
            name.endsWith('.ts'),
          ),
        sources,
      });
    const closure = [...walk()].filter((name) => name.endsWith('.ts'));
    assert.deepEqual(
      [...(platform === 'linux' ? linux : darwin)].filter(
        (name) => name.endsWith('.ts') && !closure.includes(name),
      ),
      [],
    );
    for (const name of closure) {
      // The rule depends only on the importing file, so the walk starts at that file.
      const leaving = () =>
        walk(new Map([[name, readFileSync(join(CHECKOUT, name), 'utf8') + outside]]), [], [name]);
      if (BUILT_IN_DRIVER_FILES.has(name)) leaving();
      else
        assert.throws(
          leaving,
          /unknown platform import: .*outside_the_frozen_set\.ts/,
          platform + ' ' + name,
        );
    }
    covered[platform] = closure;
    assert.throws(
      () =>
        walk(new Map([[stray, "export const create = () => import('./runtime/instance.ts');\n"]]), [
          stray,
        ]),
      /unknown platform import: server\/drivers\/not_built_in\/runtime\/instance\.ts/,
    );
  }
  console.log(
    JSON.stringify({
      closure_files_checked: { linux: covered.linux!.length, darwin: covered.darwin!.length },
    }),
  );
});

// Gap: the closure walk lets a driver.ts defer its runtime, and it cannot see when a deferred
// import runs. This loads the installer and admission from the frozen files alone and reads every
// built-in driver's static fields, so an import that fires at load or during that read fails.
it('loads the installer from the frozen files alone and reads every built-in manifest', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-load-'));
  const owner = new ChildProcesses(),
    controller = new AbortController();
  try {
    const loaded = join(root, 'frozen');
    copy_linux_installer_closure(loaded);
    const read_every_field =
      "import{pathToFileURL}from'node:url';const[admission,entry,manifests]=process.argv.slice(1);process.argv=[process.execPath];await import(pathToFileURL(admission).href);await import(pathToFileURL(entry).href);const{BUILT_IN_MANIFESTS}=await import(pathToFileURL(manifests).href);const seen=new Set(),pending=[];const read=(value)=>{if(value===null||!['object','function'].includes(typeof value)||seen.has(value))return;seen.add(value);if(typeof value.then==='function')pending.push(value);for(const key of Reflect.ownKeys(value))read(Reflect.get(value,key));};read(BUILT_IN_MANIFESTS);await Promise.all(pending);await new Promise((settle)=>setTimeout(settle,50));process.stdout.write(JSON.stringify(BUILT_IN_MANIFESTS.map(({driver_kind,manifest})=>({driver_kind,manifest}))));";
    const result = await run_release_command(
      [
        process.execPath,
        '--input-type=module',
        '-e',
        read_every_field,
        join(loaded, 'server/environments/release/admission.ts'),
        join(loaded, INSTALLER_ENTRY),
        join(loaded, 'server/drivers/built_in_manifests.ts'),
      ],
      {
        timeout_seconds: null,
        environment: isolated_node_environment(root, loaded),
        allow_nonzero: true,
      },
      {
        child_processes: owner,
        signal: controller.signal,
        deadline: performance.now() / 1000 + 10,
      },
    );
    assert.equal(result.stderr, '');
    assert.equal(result.code, 0);
    assert.deepEqual(
      JSON.parse(result.stdout),
      JSON.parse(
        JSON.stringify(
          BUILT_IN_DRIVERS.map(({ driver_kind, manifest }) => ({ driver_kind, manifest })),
        ),
      ),
    );
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
    rmSync(root, { recursive: true, force: true });
  }
}, 15000);

it('copies Node and its selected closure independently and refuses changed source', async () => {
  // Existing installer parity never copies packages, native provider edges, links, or licenses.
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-copy-'));
  const current = join(root, 'current'),
    app = join(current, 'app'),
    data = join(current, 'data');
  const owner = new ChildProcesses(),
    controller = new AbortController();
  const hash = (bytes: string): string => createHash('sha256').update(bytes).digest('hex');
  const write = (name: string, value: string, mode = 0o644) => {
    const path = join(app, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, value, { mode });
  };
  try {
    mkdirSync(data, { recursive: true });
    for (const path of INSTALLER_SOURCE_FILES) write(path, '// structural source: ' + path + '\n');
    write('server/package.json', '{"type":"module"}');
    for (const name of ['koffi', 'yaml']) {
      write('server/node_modules/' + name + '/package.json', '{"name":"' + name + '"}');
      write('server/node_modules/' + name + '/index.js', 'export default 7;\n');
      write('server/node_modules/' + name + '/LICENSE', name + ' fixture license\n');
    }
    symlinkSync('index.js', join(app, 'server/node_modules/yaml/alias.js'));
    write('server/node_modules/koffi/addon.node', 'structural native addon');
    write('runtime/lib/libfixture.so', 'structural provider');
    write('node/bin/node', 'structural Node', 0o755);
    write('node/LICENSE', 'Node license');
    write('runtime/licenses/fixture.txt', 'copied provider license\n');
    write('server/node_modules/unselected/private.js', 'must not travel');
    const record = (path: string, bytes: string, providers: Record<string, string>) => ({
      path,
      sha256: hash(bytes),
      format: 'ELF',
      active: true,
      exclusion: null,
      role: path.endsWith('.node') ? 'addon' : 'library',
      origin: 'fixture',
      providers,
      interpreter: null,
      loader_paths: ['$ORIGIN'],
      glibc_versions: [],
      gnu_property_notes: [],
    });
    const inventory = JSON.stringify({
      version: 2,
      node_major: 22,
      package_metadata: 'npm package.json files remain in payload',
      cpu_policy: 'conservative x86-64-v3 support boundary, not vendor minimum',
      packaging_tools: ['readelf', 'patchelf'],
      native_files: [
        record('node/bin/node', 'structural Node', {
          'libfixture.so': 'runtime/lib/libfixture.so',
        }),
        record('server/node_modules/koffi/addon.node', 'structural native addon', {
          'libfixture.so': 'runtime/lib/libfixture.so',
        }),
        record('runtime/lib/libfixture.so', 'structural provider', {
          'libc.so.6': 'host:libc.so.6',
        }),
      ],
      copied_components: {
        'runtime/lib/libfixture.so': {
          package: 'fixture',
          version: '1',
          license: 'runtime/licenses/fixture.txt',
          source: '/structural/build-only/source',
          source_sha256: hash('original provider'),
        },
      },
    });
    const contract = {
      version: 2,
      system: 'linux',
      architecture: 'x86_64',
      platforms: SUPPORTED_PLATFORMS,
      node_major: 22,
      minimum_node_version: '22.18.0',
      cpu_features: SUPPORTED_CPU_FEATURES,
      external_libraries: GLIBC_LIBRARIES,
      external_executables: ['sh', 'node'],
      inventory_sha256: hash(inventory),
    };
    write('runtime/inventory.json', inventory);
    write('runtime/contract.json', JSON.stringify(contract));
    write(
      'manifest.json',
      JSON.stringify({
        format: 'panels-app-v1',
        app_sha: 'a'.repeat(40),
        source_digest: 'b'.repeat(64),
        artifact_digest: 'c'.repeat(64),
        runtime_contract: contract,
      }),
    );
    const execution = {
      child_processes: owner,
      signal: controller.signal,
      deadline: performance.now() / 1000 + 10,
    };
    // Recipe parsers cannot prove that the producer copies the graph's bound source data.
    // Reuse this filesystem lifetime to exercise its real pre-native composition phase.
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
    for (const name of MAC_HOME_REQUIRED_FILES.filter((name) => name !== INSTALLER_RECIPE_PATH))
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
    await write_installer_file_manifest(app, 'a'.repeat(40), execution);
    const recipe = parse_installer_file_manifest(
      JSON.parse(readFileSync(join(app, INSTALLER_RECIPE_PATH), 'utf8')),
    );
    assert(!recipe.files.some((entry) => entry.path.includes('unselected')));
    assert(recipe.files.some((entry) => entry.path === 'runtime/licenses/fixture.txt'));
    const request: InstallerRequest = {
      current_root: current,
      database: join(root, 'database/custom.sqlite'),
      data_directory: data,
      backup_directory: data,
      attempt_id: 'first',
      node_executable: process.execPath,
      service_unit: 'fixture.service',
      health_url: 'http://127.0.0.1:0',
      health_timeout_seconds: 3,
    };
    const command = copy_installer_runtime(app, request, controller.signal);
    assert.equal(command[0], join(data, 'update-installer-first/runtime/node/bin/node'));
    assert.equal(command[1], join(data, 'update-installer-first/runtime', INSTALLER_ENTRY));
    const runtime = join(data, 'update-installer-first/runtime');
    assert.equal(lstatSync(runtime).mode & 0o777, 0o700);
    assert.equal(lstatSync(command[2]).mode & 0o777, 0o600);
    assert.equal(JSON.parse(readFileSync(command[2], 'utf8')).database, request.database);
    assert.equal(
      readFileSync(join(runtime, 'runtime/lib/libfixture.so'), 'utf8'),
      'structural provider',
    );
    assert.equal(
      readFileSync(join(runtime, 'runtime/licenses/fixture.txt'), 'utf8'),
      'copied provider license\n',
    );
    assert.equal(readlinkSync(join(runtime, 'server/node_modules/yaml/alias.js')), 'index.js');
    assert(!existsSync(join(runtime, 'runtime/inventory.json')));
    assert(!existsSync(join(runtime, 'server/node_modules/unselected')));
    assert.throws(() => copy_installer_runtime(app, request, controller.signal), /EEXIST/);
    const retired = join(current, 'retired');
    renameSync(app, retired);
    assert.equal(readFileSync(command[0], 'utf8'), 'structural Node');
    assert.equal(lstatSync(command[0]).mode & 0o777, 0o755);
    assert.equal(
      readFileSync(command[1], 'utf8'),
      '// structural source: ' + INSTALLER_ENTRY + '\n',
    );
    renameSync(retired, app);
    const original_open = fs.openSync,
      original_write = fs.writeSync;
    let failure_seen = false;
    let target_descriptor: number | undefined;
    fs.openSync = (path, flags, mode) => {
      const descriptor = original_open(path, flags, mode);
      if (path === join(data, 'update-installer-copy-failure/work/server/core/config.ts'))
        target_descriptor = descriptor;
      return descriptor;
    };
    fs.writeSync = ((descriptor: number, ...arguments_: unknown[]) => {
      if (descriptor === target_descriptor) {
        assert(existsSync(join(data, 'update-installer-copy-failure/work/server/core')));
        failure_seen = true;
        throw Object.assign(new Error('injected owned copy failure'), { code: 'EIO' });
      }
      return Reflect.apply(original_write, fs, [descriptor, ...arguments_]);
    }) as typeof fs.writeSync;
    syncBuiltinESMExports();
    try {
      assert.throws(
        () =>
          copy_installer_runtime(
            app,
            { ...request, attempt_id: 'copy-failure' },
            controller.signal,
          ),
        /injected owned copy failure/,
      );
      assert(failure_seen);
      assert(!existsSync(join(data, 'update-installer-copy-failure')));
      assert.equal(
        readFileSync(command[1], 'utf8'),
        '// structural source: ' + INSTALLER_ENTRY + '\n',
      );
      assert.equal(
        readFileSync(join(app, INSTALLER_ENTRY), 'utf8'),
        '// structural source: ' + INSTALLER_ENTRY + '\n',
      );
    } finally {
      fs.openSync = original_open;
      fs.writeSync = original_write;
      syncBuiltinESMExports();
    }
    write('server/node_modules/koffi/index.js', 'tampered');
    assert.throws(
      () => copy_installer_runtime(app, { ...request, attempt_id: 'tamper' }, controller.signal),
      /complete selected closure/,
    );
    assert(!existsSync(join(data, 'update-installer-tamper')));
    // Reuse this copy lifetime for Darwin's distinct inventory and synchronous pure-ESM decoder.
    // These native bytes remain structural. The hosted package proves the real Darwin ABI.
    const mac_app = join(current, '.app-next-' + 'd'.repeat(40));
    const mac_write = (name: string, content: string) => {
      mkdirSync(dirname(join(mac_app, name)), { recursive: true });
      writeFileSync(join(mac_app, name), content);
    };
    for (const name of DARWIN_HOME_INSTALLER_SOURCE_FILES) {
      mkdirSync(dirname(join(mac_app, name)), { recursive: true });
      cpSync(join(checkout, name), join(mac_app, name));
    }
    for (const name of ['koffi', 'yaml']) {
      mac_write('server/node_modules/' + name + '/package.json', '{"name":"' + name + '"}');
      mac_write('server/node_modules/' + name + '/LICENSE', name + ' fixture license\n');
    }
    mac_write('server/node_modules/koffi/addon.node', 'structural Mac addon');
    mac_write('runtime/lib/libfixture.dylib', 'structural Mac provider');
    mac_write('node/bin/node', 'structural Mac Node');
    mac_write('node/LICENSE', 'Node license');
    const mac_record = (path: string, bytes: string, providers: Record<string, string>) => ({
      path,
      sha256: hash(bytes),
      format: 'Mach-O',
      active: true,
      architectures: ['arm64'],
      minimum_macos: ['15.0'],
      rpaths: ['@loader_path'],
      providers,
    });
    const mac_inventory = JSON.stringify({
      version: 2,
      system: 'darwin',
      architecture: 'arm64',
      role: 'home',
      node_major: 22,
      native_files: [
        mac_record('node/bin/node', 'structural Mac Node', {}),
        mac_record('server/node_modules/koffi/addon.node', 'structural Mac addon', {
          '@rpath/libfixture.dylib': 'runtime/lib/libfixture.dylib',
        }),
        mac_record('runtime/lib/libfixture.dylib', 'structural Mac provider', {
          '/usr/lib/libSystem.B.dylib': 'host:/usr/lib/libSystem.B.dylib',
        }),
      ],
    });
    const mac_contract = {
      version: 2,
      system: 'darwin',
      architecture: 'arm64',
      role: 'home',
      minimum_os: '15.0',
      node_major: 22,
      minimum_node_version: '22.18.0',
      inventory_sha256: hash(mac_inventory),
      external_libraries: ['/usr/lib/', '/System/Library/'],
    };
    mac_write('runtime/inventory.json', mac_inventory);
    mac_write('runtime/contract.json', JSON.stringify(mac_contract));
    mac_write(
      'manifest.json',
      JSON.stringify({
        format: 'panels-app-v1',
        app_sha: 'a'.repeat(40),
        source_digest: 'b'.repeat(64),
        artifact_digest: 'c'.repeat(64),
        runtime_contract: mac_contract,
      }),
    );
    await write_installer_file_manifest(mac_app, 'a'.repeat(40), execution);
    const mac_recipe = parse_installer_file_manifest(
      JSON.parse(readFileSync(join(mac_app, INSTALLER_RECIPE_PATH), 'utf8')),
      MAC_HOME,
    );
    assert(mac_recipe.files.some((entry) => entry.path === 'runtime/lib/libfixture.dylib'));
    assert(!mac_recipe.files.some((entry) => entry.path.includes('koffi-linux-x64')));
    const mac_command = copy_installer_runtime(
      mac_app,
      { ...request, attempt_id: 'darwin' },
      controller.signal,
    );
    assert.equal(
      readFileSync(
        join(data, 'update-installer-darwin/runtime/runtime/lib/libfixture.dylib'),
        'utf8',
      ),
      'structural Mac provider',
    );
    const mac_decoder = join(
      data,
      'update-installer-darwin/runtime/server/environments/release/mac_home_inventory.ts',
    );
    renameSync(mac_app, join(current, 'retired-Mac-source'));

    // Real source/dependency import coverage is separate from the structural inventory fixture.
    // No inventory or native-admission result is fabricated for this import-only tree.
    const import_source = join(root, 'import-source'),
      loaded = join(root, 'import-copy');
    copy_linux_installer_closure(import_source);
    cpSync(import_source, loaded, { recursive: true, verbatimSymlinks: true });
    renameSync(import_source, join(root, 'retired-import-source'));
    const invalid_request = join(root, 'invalid-request.json');
    writeFileSync(invalid_request, '{}');
    const environment = isolated_node_environment(root, loaded);
    const spawn = owner.spawn.bind(owner);
    let spawns = 0,
      closes = 0;
    owner.spawn = ((...arguments_: Parameters<ChildProcesses['spawn']>) => {
      const child = spawn(...arguments_);
      spawns++;
      child.once('close', () => {
        closes++;
      });
      return child;
    }) as ChildProcesses['spawn'];
    const bootstrap =
      "import{pathToFileURL}from'node:url';import{createRequire}from'node:module';const[admission,entry,request,mac,view,protocol,projection]=process.argv.slice(1);await import(pathToFileURL(admission).href);if(typeof createRequire(import.meta.url)(mac).parse_mac_home_inventory!=='function')throw Error('copied Mac decoder missing');const versions=await import(pathToFileURL(view).href);if(versions.VIEW_PROTOCOL_VERSION!==Number(protocol)||versions.VIEW_PROJECTION_VERSION!==Number(projection))throw Error('copied conversation view protocol differs');process.argv=[process.execPath,entry,request];await import(pathToFileURL(entry).href);";
    const started = performance.now();
    const result = await run_release_command(
      [
        process.execPath,
        '--input-type=module',
        '-e',
        bootstrap,
        join(loaded, 'server/environments/release/admission.ts'),
        join(loaded, INSTALLER_ENTRY),
        invalid_request,
        mac_decoder,
        join(payload, 'server/conversation/views/contracts.ts'),
        String(VIEW_PROTOCOL_VERSION),
        String(VIEW_PROJECTION_VERSION),
      ],
      { timeout_seconds: null, environment, allow_nonzero: true },
      execution,
    );
    assert.equal(result.code, 1);
    assert.equal(result.stdout, '');
    assert.match(
      result.stderr,
      /The update stopped unexpectedly \(Error: Invalid installer health timeout\)/,
    );
    assert.equal(spawns, 1);
    assert.equal(closes, 1);
    console.log(
      JSON.stringify({
        proof:
          'copied Linux installer, trusted admission, Darwin inventory decoder, and home shared protocol imports',
        spawns,
        closes,
        seconds: (performance.now() - started) / 1000,
      }),
    );
  } finally {
    owner.seal();
    controller.abort();
    await owner.settled();
    rmSync(root, { recursive: true, force: true });
  }
}, 15000);
