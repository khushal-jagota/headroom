/** Build the selected Node payload's native closure, then seal its final bytes. */
import { createHash } from 'node:crypto';
import { lstat, mkdir, realpath, writeFile } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import { is_directory, is_file } from '../backup/evidence.ts';
import { isDeepStrictEqual } from 'node:util';
import type {
  CopiedNativeComponent,
  ElfObject,
  InactiveNativeFile,
  NativeElfFile,
  NativeInventory,
  NodeRuntimeContract,
} from './contracts.ts';
import { RUNTIME_CONTRACT_PATH, RUNTIME_INVENTORY_PATH } from './configuration.ts';
import { app_tree_paths, validate_app_tree } from './identity.ts';
import {
  assert_release_execution,
  find_release_executable,
  run_release_command,
} from './process.ts';
import type { ReleaseExecution } from './contracts.ts';
import {
  GLIBC_LIBRARIES,
  MINIMUM_NODE_VERSION,
  RuntimeContractError,
  SUPPORTED_CPU_FEATURES,
  SUPPORTED_PLATFORMS,
} from '../updates/logic/runtime_contract.ts';
import {
  elf_executable,
  hash_native_file,
  inactive_musl,
  native_format,
  read_dynamic,
  read_elf,
} from './native/elf.ts';
import {
  copied_component,
  copy_with_metadata,
  host_library_cache,
  relative_loader_paths,
  resolve_provider,
} from './native/providers.ts';
import {
  GLIBC_INTERPRETER,
  MAXIMUM_GLIBC,
  NATIVE_TOOL_SECONDS,
  PRIVATE_LIBRARY_DIRECTORY,
} from './native/configuration.ts';

type Pending = readonly [string, ElfObject, readonly ElfObject[]];
const external = new Set<string>(GLIBC_LIBRARIES);
export async function complete_node_runtime(
  app_root: string,
  execution: ReleaseExecution,
): Promise<NodeRuntimeContract> {
  assert_release_execution(execution);
  app_root = await realpath(app_root);
  // Admit every existing write target before mkdir, dependency copies, or patchelf.
  await validate_app_tree(app_root);
  if (process.platform !== 'linux' || process.arch !== 'x64')
    throw new RuntimeContractError('Node runtime packaging requires Linux x86_64');
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major !== 22 || minor < 18)
    throw new RuntimeContractError(
      `Node runtime packaging requires host Node ${MINIMUM_NODE_VERSION} or newer in major 22`,
    );
  const readelf = await find_release_executable('readelf'),
    patchelf = await find_release_executable('patchelf');
  if (readelf === null || patchelf === null)
    throw new RuntimeContractError('packaging requires build-only patchelf and readelf');
  const library_root = join(app_root, PRIVATE_LIBRARY_DIRECTORY);
  // Candidate links must not redirect the builder's copies outside its app tree.
  const { resolved_path } = await import('../paths.ts');
  if (!path_is_within(app_root, await resolved_path(library_root)))
    throw new RuntimeContractError('native library directory escapes archive');
  assert_release_execution(execution);
  await mkdir(library_root, { recursive: true });
  const cache = await host_library_cache(execution),
    objects = new Map<string, ElfObject>(),
    origins = new Map<string, string>();
  async function source_object(source: string): Promise<ElfObject> {
    source = await realpath(source);
    let object = objects.get(source);
    if (object === undefined) {
      object = { path: source, dynamic: await read_dynamic(source, execution) };
      objects.set(source, object);
    }
    return object;
  }
  const node = await source_object(process.execPath);
  let node_ancestors: readonly ElfObject[] = [node];
  for (const name of node.dynamic.needed)
    if (/^libnode\.so(?:\.\d+)*$/.test(name))
      node_ancestors = [await source_object(await resolve_provider(name, node, [], cache)), node];
  const pending: Pending[] = [],
    orphans: Pending[] = [];
  for (const path of await app_tree_paths(app_root)) {
    const info = await lstat(path);
    if (!info.isFile() || (await native_format(path)) !== 'ELF') continue;
    const object = await source_object(path),
      header = await read_elf(path, '-hW', execution);
    const executable = elf_executable(header, object.dynamic.interpreter);
    const entry: Pending = [
      path,
      object,
      path.endsWith('.node') && !executable ? node_ancestors : [],
    ];
    if (executable || path.endsWith('.node')) pending.push(entry);
    else orphans.push(entry);
  }
  const visited = new Set<string>();
  const providers_by_path = new Map<string, Record<string, string>>();
  const drafts = new Map<string, Omit<NativeElfFile, 'sha256' | 'loader_paths'>>();
  const rewrites = new Map<string, readonly [readonly string[], boolean]>();
  while (pending.length || orphans.length) {
    if (!pending.length) {
      const orphan = orphans.pop()!;
      if (providers_by_path.has(orphan[0])) continue;
      pending.push(orphan);
    }
    const [path, object, ancestors] = pending.pop()!;
    const context = JSON.stringify([path, ancestors.map((owner) => owner.path)]);
    if (visited.has(context)) continue;
    visited.add(context);
    const name = relative(app_root, path),
      dynamic = object.dynamic;
    const paths = dynamic.runpath ?? dynamic.rpath;
    const header = await read_elf(path, '-hW', execution);
    if (!header.includes('Advanced Micro Devices X86-64'))
      throw new RuntimeContractError(`unknown ELF architecture: ${name}`);
    const inactive = await inactive_musl(path, dynamic.needed, dynamic.interpreter);
    if (dynamic.interpreter !== null && !inactive && dynamic.interpreter !== GLIBC_INTERPRETER)
      throw new RuntimeContractError(`unknown ELF loader: ${name}: ${dynamic.interpreter}`);
    const notes = await read_elf(path, '-nW', execution);
    if (!inactive && notes.includes('x86-64-v4'))
      throw new RuntimeContractError(`ELF requires unsupported x86-64-v4: ${name}`);
    const providers: Record<string, string> = Object.create(null);
    if (!inactive) {
      for (const needed of dynamic.needed) {
        if (external.has(needed)) {
          providers[needed] = 'host:' + needed;
          continue;
        }
        const provider_source = await resolve_provider(needed, object, ancestors, cache);
        const provider_object = await source_object(provider_source);
        let provider = provider_source;
        if (!path_is_within(app_root, provider)) {
          const destination = join(library_root, needed);
          if ((await is_file(destination)) || (await is_directory(destination))) {
            const original = origins.get(relative(app_root, destination)) ?? destination;
            if ((await hash_native_file(original)) !== (await hash_native_file(provider)))
              throw new RuntimeContractError(`conflicting bundled library: ${needed}`);
          } else {
            await copy_with_metadata(provider, destination, execution);
            origins.set(relative(app_root, destination), provider);
          }
          provider = destination;
        }
        providers[needed] = relative(app_root, provider);
        if (
          provider_object.path !== object.path &&
          !ancestors.some((owner) => owner.path === provider_object.path)
        )
          pending.push([provider, provider_object, [object, ...ancestors]]);
      }
      if (
        dynamic.needed.some((needed) => !external.has(needed)) ||
        paths.some((entry) => !entry.startsWith('$ORIGIN') && !entry.startsWith('${ORIGIN}'))
      )
        rewrites.set(path, [paths, dynamic.rpath.length > 0 && dynamic.runpath === null]);
    }
    const recorded = providers_by_path.get(path);
    if (recorded !== undefined) {
      if (!isDeepStrictEqual(recorded, providers))
        throw new RuntimeContractError(`conflicting loader contexts for native file: ${name}`);
      continue;
    }
    providers_by_path.set(path, providers);
    const version_notes = await read_elf(path, '-VW', execution);
    const versions = [
      ...new Set([...version_notes.matchAll(/\bGLIBC_(\d+\.\d+)\b/g)].map((match) => match[1])),
    ].sort();
    if (
      !inactive &&
      versions.some((version) => {
        const [major, minor] = version.split('.').map(Number);
        return major > MAXIMUM_GLIBC[0] || (major === MAXIMUM_GLIBC[0] && minor > MAXIMUM_GLIBC[1]);
      })
    )
      throw new RuntimeContractError(`ELF exceeds supported glibc 2.39 boundary: ${name}`);
    drafts.set(path, {
      path: name,
      format: 'ELF',
      active: !inactive,
      exclusion: inactive ? 'musl dynamic variant selected only on musl hosts' : null,
      role: path.endsWith('.node')
        ? 'addon'
        : elf_executable(header, dynamic.interpreter)
          ? 'executable'
          : 'library',
      origin: origins.get(name) ?? 'installed payload',
      providers,
      interpreter: dynamic.interpreter,
      glibc_versions: versions,
      gnu_property_notes: notes.split('\n'),
    });
  }
  // Preserve every original source object's loader context until discovery is complete.
  for (const [path, [paths, preserve_rpath]] of rewrites)
    await run_release_command(
      [
        patchelf,
        ...(preserve_rpath ? ['--force-rpath'] : []),
        '--set-rpath',
        relative_loader_paths(paths, path, library_root).join(':'),
        path,
      ],
      { timeout_seconds: NATIVE_TOOL_SECONDS },
      execution,
    );
  const inventory: (NativeElfFile | InactiveNativeFile)[] = [];
  for (const [path, draft] of drafts) {
    const dynamic = await read_dynamic(path, execution),
      paths = dynamic.runpath ?? dynamic.rpath;
    if (draft.active) {
      for (const entry of paths)
        if (!entry.startsWith('$ORIGIN') && !entry.startsWith('${ORIGIN}'))
          throw new RuntimeContractError(`nonrelative final loader path: ${path}: ${entry}`);
      for (const needed of dynamic.needed) {
        if (external.has(needed)) continue;
        const provider = await resolve_provider(needed, { path, dynamic }, [], new Map());
        if (!path_is_within(app_root, provider))
          throw new RuntimeContractError(`native dependency escapes archive: ${path}: ${needed}`);
        if (provider !== (await realpath(join(app_root, draft.providers[needed]))))
          throw new RuntimeContractError(`final native provider changed: ${path}: ${needed}`);
      }
    }
    inventory.push({ ...draft, loader_paths: paths, sha256: await hash_native_file(path) });
  }
  for (const path of await app_tree_paths(app_root)) {
    if (!(await lstat(path)).isFile()) continue;
    const format = await native_format(path);
    if (format === 'PE')
      inventory.push({
        path: relative(app_root, path),
        format,
        active: false,
        exclusion: 'Windows asset',
        sha256: await hash_native_file(path),
      });
    if (format === 'Mach-O')
      inventory.push({
        path: relative(app_root, path),
        format,
        active: false,
        exclusion: 'macOS asset',
        sha256: await hash_native_file(path),
      });
  }
  const copied_components: Record<string, CopiedNativeComponent> = {};
  for (const [path, source] of origins)
    copied_components[path] = await copied_component(source, app_root, execution);
  const document: NativeInventory = {
    version: 2,
    node_major: 22,
    package_metadata: 'npm package.json files remain in payload',
    cpu_policy: 'conservative x86-64-v3 support boundary, not vendor minimum',
    native_files: inventory.sort((a, b) =>
      Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)),
    ),
    copied_components,
    packaging_tools: ['readelf', 'patchelf'],
  };
  const bytes = Buffer.from(JSON.stringify(document));
  assert_release_execution(execution);
  await writeFile(join(app_root, RUNTIME_INVENTORY_PATH), bytes);
  const contract: NodeRuntimeContract = {
    version: 2,
    system: 'linux',
    architecture: 'x86_64',
    platforms: SUPPORTED_PLATFORMS.map((platform) => ({ ...platform })),
    node_major: 22,
    minimum_node_version: MINIMUM_NODE_VERSION,
    cpu_features: [...SUPPORTED_CPU_FEATURES],
    external_libraries: [...GLIBC_LIBRARIES],
    external_executables: ['sh', 'node'],
    inventory_sha256: createHash('sha256').update(bytes).digest('hex'),
  };
  assert_release_execution(execution);
  await writeFile(join(app_root, RUNTIME_CONTRACT_PATH), JSON.stringify(contract));
  assert_release_execution(execution);
  return contract;
}
