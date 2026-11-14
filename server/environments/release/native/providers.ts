/** Source loader lookup and distribution attribution for copied native libraries. */
import { chmod, copyFile, mkdir, realpath, stat, utimes } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative } from 'node:path';
import type { CopiedNativeComponent, ElfObject, ReleaseExecution } from '../contracts.ts';
import { is_file } from '../../backup/evidence.ts';
import { path_is_within } from '../../../core/path_containment.ts';
import { RuntimeContractError } from '../../updates/logic/runtime_contract.ts';
import {
  assert_release_execution,
  find_release_executable,
  run_release_command,
} from '../process.ts';
import { resolved_path } from '../../paths.ts';
import {
  LICENSE_DIRECTORY,
  NATIVE_TOOL_SECONDS,
  PROVENANCE_TOOL_SECONDS,
} from './configuration.ts';
import { hash_native_file } from './elf.ts';

export async function resolve_provider(
  name: string,
  requester: ElfObject,
  ancestors: readonly ElfObject[],
  cache: ReadonlyMap<string, string>,
): Promise<string> {
  if (name.includes('/'))
    throw new RuntimeContractError(`unsafe ELF dependency name: ${requester.path}: ${name}`);
  const searches =
    requester.dynamic.runpath === null
      ? [requester, ...ancestors]
          .filter((owner) => owner.dynamic.runpath === null)
          .map((owner) => [owner, owner.dynamic.rpath] as const)
      : [[requester, requester.dynamic.runpath] as const];
  for (const [owner, entries] of searches)
    for (const entry of entries) {
      const expanded = entry.replace(/\$(?:\{ORIGIN\}|ORIGIN(?![A-Za-z0-9_]))/g, () =>
        dirname(owner.path),
      );
      if (!entry || expanded.includes('$') || !isAbsolute(expanded))
        throw new RuntimeContractError(`unsafe relative ELF search path: ${owner.path}: ${entry}`);
      // Preserve '..' for the filesystem, including its interaction with directory links.
      const candidate = expanded.replace(/\/+$/, '') + '/' + name;
      if (await is_file(candidate)) return realpath(candidate);
    }
  const cached = cache.get(name);
  if (cached !== undefined) return cached;
  throw new RuntimeContractError(
    `unresolved native dependency ${name} required by ${requester.path}`,
  );
}
export function relative_loader_paths(
  paths: readonly string[],
  path: string,
  library_root: string,
): string[] {
  const result = paths.filter(
    (entry) =>
      entry.startsWith('$ORIGIN/') ||
      entry.startsWith('${ORIGIN}/') ||
      entry === '$ORIGIN' ||
      entry === '${ORIGIN}',
  );
  const shared = '$ORIGIN/' + (relative(dirname(path), library_root) || '.');
  return result.includes(shared) ? result : [...result, shared];
}
export async function host_library_cache(
  execution: ReleaseExecution,
): Promise<Map<string, string>> {
  const command = (await find_release_executable('ldconfig')) ?? '/sbin/ldconfig';
  const { stdout } = await run_release_command(
    [command, '-p'],
    {
      timeout_seconds: NATIVE_TOOL_SECONDS,
    },
    execution,
  );
  const cache = new Map<string, string>();
  for (const line of stdout.split('\n')) {
    const match = /^\s*(\S+)\s+\(.*x86-64.*\) => (\S+)/.exec(line);
    if (match && !cache.has(match[1])) cache.set(match[1], await realpath(match[2]));
  }
  return cache;
}
export async function copy_with_metadata(
  source: string,
  destination: string,
  execution: ReleaseExecution,
): Promise<void> {
  const info = await stat(source);
  assert_release_execution(execution);
  await copyFile(source, destination);
  assert_release_execution(execution);
  await chmod(destination, info.mode & 0o7777);
  assert_release_execution(execution);
  await utimes(destination, info.atime, info.mtime);
}
export async function copied_component(
  source: string,
  app_root: string,
  execution: ReleaseExecution,
): Promise<CopiedNativeComponent> {
  source = await realpath(source);
  const query = await find_release_executable('dpkg-query');
  let package_name = '';
  if (query !== null)
    for (const candidate of [source, source.replace('/usr/lib/', '/lib/')]) {
      const result = await run_release_command(
        [query, '-S', candidate],
        {
          timeout_seconds: PROVENANCE_TOOL_SECONDS,
          allow_nonzero: true,
        },
        execution,
      );
      if (result.code === 0) {
        package_name = result.stdout.split(': ', 1)[0].split('\n', 1)[0];
        break;
      }
    }
  if (!package_name)
    throw new RuntimeContractError(`no package provenance for copied library: ${source}`);
  const { stdout: version } = await run_release_command(
    [query!, '-W', '-f=${Version}', package_name],
    {
      timeout_seconds: PROVENANCE_TOOL_SECONDS,
    },
    execution,
  );
  const license_source = join('/usr/share/doc', package_name.split(':', 1)[0], 'copyright');
  if (!(await is_file(license_source)))
    throw new RuntimeContractError(`no license notice for copied library: ${source}`);
  const license = join(LICENSE_DIRECTORY, package_name.replaceAll(':', '_') + '.txt');
  const destination = join(app_root, license);
  if (!path_is_within(app_root, await resolved_path(destination)))
    throw new RuntimeContractError('native license path escapes archive');
  assert_release_execution(execution);
  await mkdir(dirname(destination), { recursive: true });
  await copy_with_metadata(license_source, destination, execution);
  return {
    package: package_name,
    version,
    license,
    source,
    source_sha256: await hash_native_file(source),
  };
}
