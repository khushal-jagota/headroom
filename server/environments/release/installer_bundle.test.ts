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
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { it } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import {
  copy_installer_runtime,
  parse_installer_file_manifest,
  write_installer_file_manifest,
} from './installer_bundle.ts';
import {
  INSTALLER_ENTRY,
  INSTALLER_RECIPE_PATH,
  INSTALLER_SOURCE_FILES,
} from './installer_bundle_configuration.ts';
import {
  GLIBC_LIBRARIES,
  SUPPORTED_CPU_FEATURES,
  SUPPORTED_PLATFORMS,
} from '../updates/logic/runtime_contract.ts';
import type { InstallerRequest } from '../updates/installer/contracts.ts';
import { run_release_command } from './process.ts';
import {
  INSTALLER_KOFFI_OPTIONAL_ROOT,
  INSTALLER_PACKAGE_ROOTS,
} from './installer_bundle_configuration.ts';

it('copies a complete selected closure once and refuses changed source or an application-owned Node', async () => {
  // Existing installer parity never copies packages, native provider edges, links, or licenses.
  const root = mkdtempSync(join(tmpdir(), 'panels-installer-copy-'));
  const current = join(root, 'current'),
    app = join(current, 'app'),
    data = join(root, 'records');
  const owner = new ChildProcesses(),
    controller = new AbortController();
  const hash = (bytes: string): string => createHash('sha256').update(bytes).digest('hex');
  const write = (name: string, value: string, mode = 0o644) => {
    const path = join(app, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, value, { mode });
  };
  try {
    mkdirSync(data);
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
      attempt_id: 'first',
      node_executable: process.execPath,
      service_unit: 'fixture.service',
      health_url: 'http://127.0.0.1:0',
      health_timeout_seconds: 3,
      admitted_legacy_null_sha: null,
    };
    const command = copy_installer_runtime(app, request, controller.signal);
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
    assert.equal(
      readFileSync(command[1], 'utf8'),
      '// structural source: ' + INSTALLER_ENTRY + '\n',
    );
    renameSync(retired, app);
    const original_copy = fs.copyFileSync;
    let failure_seen = false;
    fs.copyFileSync = (source, destination, flags) => {
      if (destination === join(data, 'update-installer-copy-failure/work/server/core/config.ts')) {
        assert(existsSync(dirname(destination)));
        failure_seen = true;
        throw Object.assign(new Error('injected owned copy failure'), { code: 'EIO' });
      }
      return original_copy(source, destination, flags);
    };
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
      fs.copyFileSync = original_copy;
      syncBuiltinESMExports();
    }
    write('server/node_modules/koffi/index.js', 'tampered');
    assert.throws(
      () => copy_installer_runtime(app, { ...request, attempt_id: 'tamper' }, controller.signal),
      /complete selected closure/,
    );
    assert(!existsSync(join(data, 'update-installer-tamper')));
    write('fake-node', 'not executed', 0o755);
    const alias = join(root, 'node-alias');
    symlinkSync(join(app, 'fake-node'), alias);
    assert.throws(
      () =>
        copy_installer_runtime(
          app,
          { ...request, attempt_id: 'alias', node_executable: alias },
          controller.signal,
        ),
      /replaceable application/,
    );
    assert(!existsSync(join(data, 'update-installer-alias')));

    // Real source/dependency import coverage is separate from the structural inventory fixture.
    // No inventory or native-admission result is fabricated for this import-only tree.
    const checkout = fileURLToPath(new URL('../../../', import.meta.url));
    const import_source = join(root, 'import-source'),
      loaded = join(root, 'import-copy');
    for (const path of INSTALLER_SOURCE_FILES) {
      mkdirSync(dirname(join(import_source, path)), { recursive: true });
      cpSync(join(checkout, path), join(import_source, path));
    }
    for (const path of [...INSTALLER_PACKAGE_ROOTS, INSTALLER_KOFFI_OPTIONAL_ROOT]) {
      if (!existsSync(join(checkout, path))) {
        assert.equal(path, INSTALLER_KOFFI_OPTIONAL_ROOT);
        continue;
      }
      mkdirSync(dirname(join(import_source, path)), { recursive: true });
      cpSync(join(checkout, path), join(import_source, path), {
        recursive: true,
        verbatimSymlinks: true,
      });
    }
    cpSync(import_source, loaded, { recursive: true, verbatimSymlinks: true });
    renameSync(import_source, join(root, 'retired-import-source'));
    const invalid_request = join(root, 'invalid-request.json');
    writeFileSync(invalid_request, '{}');
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
      'HERMES_HOME',
      'PLAN_HERMES_HOME',
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
      "import{pathToFileURL}from'node:url';const[admission,entry,request]=process.argv.slice(1);await import(pathToFileURL(admission).href);process.argv=[process.execPath,entry,request];await import(pathToFileURL(entry).href);";
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
        proof: 'copied installer and trusted admission imports only',
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
