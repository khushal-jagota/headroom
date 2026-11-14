/** Assemble caller-supplied native inputs. Never download, execute, sign or install them. */
import {
  chmod,
  cp,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  readlink,
  realpath,
  stat,
  writeFile,
} from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative } from 'node:path';
import { create as create_tar } from 'tar';
import { hash_archive } from '../../../environments/release/archive.ts';
import { ARCHIVE_GZIP_LEVEL } from '../../../environments/release/configuration.ts';
import { inside } from '../../../environments/ticket_scratch/logic.ts';
import { mac_installation_assets } from '../platform.ts';
import {
  MAC_RUNTIME_ARCHIVE_NAME,
  MAX_MAC_RUNTIME_ARCHIVE_BYTES,
  MAX_MAC_RUNTIME_EXPANDED_BYTES,
  validate_executor_launch,
} from '../configuration.ts';
import { MAC_STATIC_FILES, MAC_REQUIRED_FILES, MAC_RUNTIME_CONSOLE } from './configuration.ts';

export interface MacPackageOptions {
  readonly source_root: string;
  readonly node_distribution: string;
  readonly production_node_modules: string;
  readonly output: string;
  readonly revision: string;
  readonly home: string;
  readonly executor_id: string;
  readonly server_url: string;
  readonly backend_path: string;
}
async function macho_arm64(path: string, executable = false): Promise<void> {
  const info = await lstat(path);
  if (!info.isFile() || (executable && !(info.mode & 0o111)))
    throw new Error(`Required native file is not regular/executable: ${path}`);
  const file = await open(path, 'r');
  try {
    const bytes = Buffer.alloc(8),
      { bytesRead } = await file.read(bytes, 0, 8, 0);
    if (
      bytesRead !== 8 ||
      bytes.readUInt32LE(0) !== 0xfeedfacf ||
      bytes.readUInt32LE(4) !== 0x100000c
    )
      throw new Error(`Expected thin Darwin arm64 Mach-O: ${path}`);
  } finally {
    await file.close();
  }
}
async function paths(root: string): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    result.push(path);
    if (entry.isDirectory()) result.push(...(await paths(path)));
  }
  return result;
}
async function node_version(root: string): Promise<string> {
  const header = await readFile(join(root, 'include/node/node_version.h'), 'utf8');
  const numbers = ['MAJOR', 'MINOR', 'PATCH'].map((name) =>
    Number(new RegExp(`NODE_${name}_VERSION\\s+(\\d+)`).exec(header)?.[1] ?? NaN),
  );
  if (
    !(numbers[0] === 24 || (numbers[0] === 22 && numbers[1]! >= 18)) ||
    !numbers.every(Number.isSafeInteger)
  )
    throw new Error('Mac runtime needs Node 22.18+ or Node 24.');
  return numbers.join('.');
}
export async function build_mac_package(options: MacPackageOptions) {
  if (!/^[0-9a-f]{40}$/.test(options.revision))
    throw new Error('An exact source revision is required.');
  validate_executor_launch(options.executor_id, options.revision, options.server_url);
  const source = await realpath(options.source_root),
    node = await realpath(options.node_distribution),
    modules = await realpath(options.production_node_modules);
  if (!isAbsolute(options.output)) throw new Error('Package output must be absolute.');
  const output = join(await realpath(dirname(options.output)), basename(options.output));
  if ([source, node, modules].some((root) => inside(output, root)))
    throw new Error('Package output must be outside its inputs.');
  await macho_arm64(join(node, 'bin/node'), true);
  const version = await node_version(node);
  const package_json = JSON.parse(await readFile(join(source, 'server/package.json'), 'utf8')) as {
    dependencies: Record<string, string>;
  };
  const installed = JSON.parse(await readFile(join(modules, '.package-lock.json'), 'utf8')) as {
    packages: Record<string, { dev?: boolean }>;
  };
  if (Object.values(installed.packages).some((entry) => entry.dev))
    throw new Error('Provide node_modules prepared with npm ci --omit=dev.');
  for (const [name, version] of Object.entries(package_json.dependencies)) {
    const dependency = JSON.parse(await readFile(join(modules, name, 'package.json'), 'utf8')) as {
      version: string;
    };
    if (dependency.version !== version)
      throw new Error(`Production dependency does not match the source pin: ${name}`);
  }
  for (const path of MAC_REQUIRED_FILES)
    if (!(await lstat(join(source, path))).isFile())
      throw new Error(`Missing runtime source: ${path}`);
  const { home, revision, executor_id, server_url, backend_path } = options;
  const assets = mac_installation_assets({ home, revision, executor_id, server_url, backend_path });
  await mkdir(output, { mode: 0o700 });
  const runtime = join(output, 'runtime');
  await mkdir(runtime);
  const excluded = new Set(['node_modules', 'testing', 'tests', '.git']);
  await cp(join(source, 'server'), join(runtime, 'server'), {
    recursive: true,
    verbatimSymlinks: true,
    filter: (path) => {
      const name = relative(join(source, 'server'), path),
        parts = name.split('/');
      return (
        !parts.some((part) => excluded.has(part)) &&
        !name.endsWith('.test.ts') &&
        name !== 'executor/wire.schema.json' &&
        name !== 'test_host.ts'
      );
    },
  });
  await cp(modules, join(runtime, 'server/node_modules'), {
    recursive: true,
    verbatimSymlinks: true,
  });
  await mkdir(join(runtime, 'node/bin'), { recursive: true });
  await cp(join(node, 'bin/node'), join(runtime, 'node/bin/node'));
  await cp(join(node, 'LICENSE'), join(runtime, 'node/LICENSE'));
  for (const path of MAC_STATIC_FILES) {
    await mkdir(dirname(join(runtime, path)), { recursive: true });
    await cp(join(source, path), join(runtime, path));
  }
  await cp(join(source, 'src/planner/skills'), join(runtime, 'src/planner/skills'), {
    recursive: true,
    verbatimSymlinks: true,
  });
  await mkdir(join(runtime, 'bin'));
  await writeFile(join(runtime, 'bin/panels'), MAC_RUNTIME_CONSOLE, { mode: 0o755 });
  await writeFile(
    join(runtime, 'mac-runtime.json'),
    JSON.stringify({
      schema_version: 1,
      application_revision: options.revision,
      platform: 'darwin',
      architecture: 'arm64',
    }) + '\n',
  );
  const native_modules = (await paths(join(runtime, 'server/node_modules'))).filter((path) =>
    path.endsWith('.node'),
  );
  const sqlite = [
    'prebuilds/darwin-arm64.node',
    'build/Debug/better_sqlite3.node',
    'build/Release/better_sqlite3.node',
  ].map((name) => join(runtime, 'server/node_modules/better-sqlite3', name));
  const sqlite_binary = sqlite.find((path) => native_modules.includes(path));
  const koffi_binary = [
    '@koromix/koffi-darwin-arm64/darwin_arm64/koffi.node',
    'koffi/build/koffi/darwin_arm64/koffi.node',
  ]
    .map((name) => join(runtime, 'server/node_modules', name))
    .find((path) => native_modules.includes(path));
  if (!sqlite_binary || !koffi_binary)
    throw new Error('Darwin arm64 SQLite and Koffi addons are required.');
  await macho_arm64(sqlite_binary);
  await macho_arm64(koffi_binary);
  let expanded = 0;
  for (const path of await paths(runtime)) {
    if (relative(runtime, path).includes('\\'))
      throw new Error(`Unsupported runtime name: ${path}`);
    const info = await lstat(path);
    if (info.isSymbolicLink()) {
      if (isAbsolute(await readlink(path)) || !inside(await realpath(path), runtime))
        throw new Error(`Runtime link escapes the package: ${path}`);
    } else if (info.isFile()) expanded += info.size;
    else if (!info.isDirectory()) throw new Error(`Unsupported runtime entry: ${path}`);
    if (info.mode & 0o7000) throw new Error(`Runtime entry has special permission bits: ${path}`);
  }
  if (expanded > MAX_MAC_RUNTIME_EXPANDED_BYTES)
    throw new Error('Mac runtime exceeds its expanded size limit.');
  for (const [name, file] of Object.entries(assets.files)) {
    const path = join(output, 'assets', name);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.text, { mode: file.mode });
    await chmod(path, file.mode);
  }
  await cp(
    join(source, 'ops/mac-executor/typescript-installation.md'),
    join(output, 'INSTALLATION.md'),
  );
  const archive = join(output, MAC_RUNTIME_ARCHIVE_NAME);
  await create_tar(
    { cwd: runtime, file: archive, gzip: { level: ARCHIVE_GZIP_LEVEL }, portable: true },
    ['bin', 'node', 'server', 'src', 'pyproject.toml', 'mac-runtime.json'],
  );
  if ((await stat(archive)).size > MAX_MAC_RUNTIME_ARCHIVE_BYTES)
    throw new Error('Mac runtime exceeds its archive size limit.');
  const result = {
    application_revision: options.revision,
    node_version: version,
    node_sha256: await hash_archive(join(runtime, 'node/bin/node')),
    archive_sha256: await hash_archive(archive),
    paths: assets.paths,
  };
  await writeFile(join(output, 'build.json'), JSON.stringify(result, null, 2) + '\n');
  return result;
}
