/** Build-only Mach-O observations. Every native tool uses the invocation child owner. */
import { createHash } from 'node:crypto';
import { lstat, mkdir, open, realpath, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { macho_arm64, paths } from '../../executor/mac/packaging/build.ts';
import { path_is_within } from '../../core/path_containment.ts';
import { resolved_path } from '../paths.ts';
import type { DarwinNodeRuntimeContract, ReleaseExecution } from './contracts.ts';
import type { DarwinHomeInventory, DarwinHomeMachO } from './mac_home_contracts.ts';
import { native_format, hash_native_file } from './native/elf.ts';
import { assert_release_execution, run_release_command } from './process.ts';
import { validate_app_tree } from './identity.ts';
import { RUNTIME_CONTRACT_PATH, RUNTIME_INVENTORY_PATH } from './configuration.ts';
import {
  MAC_HOME_MINIMUM_OS,
  MAC_HOME_NATIVE_TOOL_SECONDS,
  MAC_HOME_SYSTEM_LIBRARIES,
} from './mac_home_configuration.ts';
import {
  mac_home_minimum_versions,
  mac_home_system_library,
  parse_mac_home_inventory,
} from './mac_home_inventory.ts';

function loader_path(value: string, binary: string, executable: string): string {
  if (value === '@loader_path') return dirname(binary);
  if (value.startsWith('@loader_path/'))
    return resolve(dirname(binary), value.slice('@loader_path/'.length));
  if (value === '@executable_path') return dirname(executable);
  if (value.startsWith('@executable_path/'))
    return resolve(dirname(executable), value.slice('@executable_path/'.length));
  if (isAbsolute(value)) return resolve(value);
  throw new Error(`Unsupported Mach-O loader path: ${value}`);
}
async function executable_context(path: string, app: string): Promise<string> {
  const file = await open(path, 'r');
  try {
    const header = Buffer.alloc(16),
      { bytesRead } = await file.read(header, 0, header.length, 0);
    if (bytesRead !== header.length) throw new Error('Truncated Mach-O header');
    return header.readUInt32LE(12) === 2 ? path : join(app, 'node/bin/node');
  } finally {
    await file.close();
  }
}
export async function collect_mac_home_inventory(
  app: string,
  execution: ReleaseExecution,
): Promise<DarwinHomeInventory> {
  app = await realpath(app);
  await validate_app_tree(app);
  const native_files: Array<DarwinHomeInventory['native_files'][number]> = [];
  const tool = async (flag: string, path: string) =>
    (
      await run_release_command(
        ['/usr/bin/otool', flag, path],
        {
          timeout_seconds: MAC_HOME_NATIVE_TOOL_SECONDS,
          environment: { PATH: '/usr/bin:/bin', LANG: 'C' },
        },
        execution,
      )
    ).stdout;
  for (const path of (await paths(app)).sort()) {
    assert_release_execution(execution);
    const info = await lstat(path);
    if (info.isSymbolicLink()) {
      if (!path_is_within(app, await realpath(path)))
        throw new Error(`Mac home link escapes the payload: ${path}`);
      continue;
    }
    if (!info.isFile()) continue;
    const format = await native_format(path);
    if (format === null) continue;
    const name = relative(app, path),
      sha256 = await hash_native_file(path);
    if (format !== 'Mach-O') {
      native_files.push({
        path: name,
        sha256,
        format,
        active: false,
        exclusion: format === 'ELF' ? 'Linux asset' : 'Windows asset',
      });
      continue;
    }
    await macho_arm64(path);
    const executable = await executable_context(path, app),
      load_commands = await tool('-l', path);
    const rpaths = [
      ...load_commands.matchAll(/cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)/g),
    ].map((match) => match[1]!);
    const search: string[] = [];
    for (const entry of rpaths) {
      const actual = await resolved_path(loader_path(entry, path, executable));
      if (!path_is_within(app, actual) && !mac_home_system_library(actual + '/'))
        throw new Error(`Mac home has an external native search path: ${entry}`);
      search.push(actual);
    }
    const identifiers = new Set(
      (await tool('-D', path))
        .split('\n')
        .slice(1)
        .map((line) => line.trim())
        .filter(Boolean),
    );
    const providers: Record<string, string> = {};
    for (const match of (await tool('-L', path)).matchAll(/^\s+(.+?) \(compatibility version /gm)) {
      const needed = match[1]!;
      if (identifiers.has(needed)) continue;
      if (mac_home_system_library(needed)) {
        providers[needed] = 'host:' + needed;
        continue;
      }
      const candidates = needed.startsWith('@rpath/')
        ? search.map((base) => join(base, needed.slice('@rpath/'.length)))
        : [loader_path(needed, path, executable)];
      let provider: string | undefined;
      for (const candidate of candidates) {
        try {
          const actual = await realpath(candidate);
          if (!path_is_within(app, actual) || !(await lstat(actual)).isFile()) continue;
          if ((await native_format(actual)) !== 'Mach-O')
            throw new Error('Mac native dependency is not Mach-O');
          provider = relative(app, actual);
          break;
        } catch (error) {
          if (!['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? ''))
            throw error;
        }
      }
      if (provider === undefined)
        throw new Error(`Mac home has an unresolved or external native dependency: ${needed}`);
      providers[needed] = provider;
    }
    const record: DarwinHomeMachO = {
      path: name,
      sha256,
      format,
      active: true,
      architectures: ['arm64'],
      minimum_macos: mac_home_minimum_versions(load_commands),
      rpaths,
      providers,
    };
    native_files.push(record);
  }
  return parse_mac_home_inventory({
    version: 2,
    system: 'darwin',
    architecture: 'arm64',
    role: 'home',
    node_major: 22,
    native_files,
  });
}
export async function complete_mac_home_runtime(
  app: string,
  execution: ReleaseExecution,
): Promise<DarwinNodeRuntimeContract> {
  const inventory = await collect_mac_home_inventory(app, execution);
  const bytes = Buffer.from(JSON.stringify(inventory, null, 2) + '\n');
  const contract: DarwinNodeRuntimeContract = {
    version: 2,
    system: 'darwin',
    architecture: 'arm64',
    role: 'home',
    minimum_os: MAC_HOME_MINIMUM_OS,
    node_major: 22,
    minimum_node_version: '22.18.0',
    inventory_sha256: createHash('sha256').update(bytes).digest('hex'),
    external_libraries: [...MAC_HOME_SYSTEM_LIBRARIES],
  };
  assert_release_execution(execution);
  await mkdir(join(app, 'runtime'), { recursive: true });
  await writeFile(join(app, RUNTIME_INVENTORY_PATH), bytes, { flag: 'wx', mode: 0o600 });
  assert_release_execution(execution);
  await writeFile(join(app, RUNTIME_CONTRACT_PATH), JSON.stringify(contract, null, 2) + '\n', {
    flag: 'wx',
    mode: 0o600,
  });
  return contract;
}
