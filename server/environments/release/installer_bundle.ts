/** Select sealed installer bytes and publish one exclusive, durable attempt copy. */
import { sha256 } from '../../conversation/record/message_files.ts';
import {
  accessSync,
  chmodSync,
  closeSync,
  constants,
  fchmodSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  readlinkSync,
  readSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeSync,
} from 'node:fs';
import type { Stats } from 'node:fs';
import { dirname, isAbsolute, join, posix } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { inside } from '../../files/logic/paths.ts';
import { is_os_error } from '../../core/system_errors.ts';
import { assert_installer_path } from '../updates/installer/configuration.ts';
import type { InstallerRequest } from '../updates/installer/contracts.ts';
import { replace_existing_json } from '../updates/attempts.ts';
import { is_object } from '../updates/codec.ts';
import { parse_optional_runtime_contract } from '../updates/logic/runtime_contract.ts';
import { manifest_identity, validate_app_sha } from './logic/manifest.ts';
import { assert_release_execution } from './process.ts';
import { AppValidationError } from './contracts.ts';
import type {
  NativeInventory,
  NativeElfFile,
  CopiedNativeComponent,
  ReleaseExecution,
} from './contracts.ts';
import type { InstallerFileManifest, InstallerRecipeEntry } from './installer_bundle_contracts.ts';
import {
  INSTALLER_ENTRY,
  INSTALLER_RECIPE_PATH,
  INSTALLER_SOURCE_FILES,
  INSTALLER_PACKAGE_ROOTS,
  INSTALLER_KOFFI_OPTIONAL_ROOT,
} from './installer_bundle_configuration.ts';
import {
  FILE_CHUNK_BYTES,
  RUNTIME_CONTRACT_PATH,
  RUNTIME_INVENTORY_PATH,
} from './configuration.ts';
import { STAGED_APP_PREFIX, PREVIOUS_APP_PREFIX } from '../updates/configuration.ts';

const ordered = (a: string, b: string): number => Buffer.compare(Buffer.from(a), Buffer.from(b));
function fail(message: string): never {
  throw new AppValidationError(message);
}
function path_name(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !value ||
    isAbsolute(value) ||
    value.includes('\0') ||
    value.split('/').some((part) => !part || part === '.' || part === '..')
  )
    fail('invalid installer recipe path');
  return value;
}
function fields(value: Record<string, unknown>, names: readonly string[]): boolean {
  return (
    Object.keys(value).length === names.length && names.every((name) => Object.hasOwn(value, name))
  );
}
function digest(value: unknown): value is string {
  return typeof value === 'string' && value.length === 64 && /^[0-9a-f]{64}$/.test(value);
}
export function installer_attempt_id(value: string): string {
  if (
    /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.exec(value)?.[0] !== value ||
    value === '.' ||
    value === '..'
  )
    fail('invalid installer attempt identity');
  return value;
}
function ordinary_mode(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 0o777;
}
/** Check ancestors without following a link before its recipe target is admitted. */
function source_path(root: string, name: string, missing_final = false): string {
  const parts = path_name(name).split('/');
  let path = root;
  for (let index = 0; index < parts.length; index++) {
    path = join(path, parts[index]);
    assert_installer_path(path);
    try {
      const info = lstatSync(path);
      if (index < parts.length - 1 && (!info.isDirectory() || info.isSymbolicLink()))
        fail('installer recipe ancestor must be a real directory');
    } catch (error) {
      if (
        missing_final &&
        index === parts.length - 1 &&
        is_os_error(error) &&
        error instanceof Error &&
        'code' in error &&
        error.code === 'ENOENT'
      )
        return path;
      throw error;
    }
  }
  return path;
}
function root_path(input: string): string {
  assert_installer_path(input);
  const root = realpathSync(input);
  assert_installer_path(root);
  if (!lstatSync(root).isDirectory()) fail('installer application root must be a directory');
  return root;
}
function regular_source(path: string): { descriptor: number; info: Stats } {
  const descriptor = openSync(
    path,
    constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
  );
  try {
    const info = fstatSync(descriptor);
    if (!info.isFile()) fail('installer source must be a regular file');
    return { descriptor, info };
  } catch (error) {
    closeSync(descriptor);
    throw error;
  }
}
function read_regular(root: string, name: string): Buffer {
  const { descriptor } = regular_source(source_path(root, name));
  try {
    return readFileSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
}
function read_json(root: string, name: string): unknown {
  return JSON.parse(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(read_regular(root, name)),
  );
}
/** Resolve recipe links using only the selected names, never a host filesystem target. */
function validate_links(files: readonly InstallerRecipeEntry[]): void {
  const entries = new Map(files.map((entry) => [entry.path, entry]));
  function target(name: string, seen = new Set<string>()): string {
    if (seen.has(name)) fail('installer recipe contains a link cycle');
    const entry = entries.get(name);
    if (!entry) fail('installer recipe link target is not selected');
    if (entry.kind !== 'symlink') return name;
    seen.add(name);
    if (isAbsolute(entry.target) || entry.target.includes('\0'))
      fail('invalid installer recipe link');
    const selected = path_name(posix.normalize(posix.join(posix.dirname(name), entry.target)));
    return target(selected, seen);
  }
  for (const entry of files) {
    if (entry.kind === 'symlink') target(entry.path);
    let parent = posix.dirname(entry.path);
    while (parent !== '.') {
      if (entries.get(parent)?.kind !== 'directory')
        fail('installer recipe parent is not selected');
      parent = posix.dirname(parent);
    }
  }
}
export function parse_installer_file_manifest(value: unknown): InstallerFileManifest {
  if (
    !is_object(value) ||
    !fields(value, ['version', 'source_sha', 'entry', 'runtime_inventory_sha256', 'files']) ||
    value.version !== 1 ||
    value.entry !== INSTALLER_ENTRY ||
    !digest(value.runtime_inventory_sha256) ||
    !Array.isArray(value.files)
  )
    fail('invalid installer file manifest');
  if (typeof value.source_sha !== 'string') fail('invalid installer source SHA');
  validate_app_sha(value.source_sha);
  const files: InstallerRecipeEntry[] = [],
    seen = new Set<string>();
  for (const raw of value.files) {
    if (!is_object(raw)) fail('invalid installer recipe entry');
    const path = path_name(raw.path);
    if (seen.has(path) || path === INSTALLER_RECIPE_PATH)
      fail('duplicate or self-referential installer recipe entry');
    seen.add(path);
    if (
      raw.kind === 'file' &&
      fields(raw, ['kind', 'path', 'mode', 'sha256']) &&
      ordinary_mode(raw.mode) &&
      digest(raw.sha256)
    )
      files.push({ kind: 'file', path, mode: raw.mode, sha256: raw.sha256 });
    else if (
      raw.kind === 'directory' &&
      fields(raw, ['kind', 'path', 'mode']) &&
      ordinary_mode(raw.mode)
    )
      files.push({ kind: 'directory', path, mode: raw.mode });
    else if (
      raw.kind === 'symlink' &&
      fields(raw, ['kind', 'path', 'target']) &&
      typeof raw.target === 'string' &&
      raw.target
    )
      files.push({ kind: 'symlink', path, target: raw.target });
    else fail('invalid installer recipe entry');
  }
  if (
    !INSTALLER_SOURCE_FILES.every((path) =>
      files.some((entry) => entry.path === path && entry.kind === 'file'),
    )
  )
    fail('installer recipe omits its frozen source closure');
  for (const root of INSTALLER_PACKAGE_ROOTS)
    if (!files.some((entry) => entry.path === root + '/package.json' && entry.kind === 'file'))
      fail('installer recipe omits a required package');
  validate_links(files);
  return {
    version: 1,
    source_sha: value.source_sha,
    entry: INSTALLER_ENTRY,
    runtime_inventory_sha256: value.runtime_inventory_sha256,
    files: files.sort((a, b) => ordered(a.path, b.path)),
  };
}
function native_metadata(root: string): {
  inventory: NativeInventory;
  hash: string;
  external: Set<string>;
} {
  const contract = parse_optional_runtime_contract(read_json(root, RUNTIME_CONTRACT_PATH));
  if (contract?.version !== 2) fail('installer recipe requires a Node runtime contract');
  const bytes = read_regular(root, RUNTIME_INVENTORY_PATH),
    hash = sha256(bytes);
  if (hash !== contract.inventory_sha256) fail('installer native inventory hash mismatch');
  const inventory: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  if (
    !is_object(inventory) ||
    inventory.version !== 2 ||
    inventory.node_major !== 22 ||
    !Array.isArray(inventory.native_files) ||
    !is_object(inventory.copied_components)
  )
    fail('invalid native inventory for installer');
  return {
    inventory: inventory as unknown as NativeInventory,
    hash,
    external: new Set(contract.external_libraries),
  };
}
function entry_for(root: string, name: string): InstallerRecipeEntry {
  const path = source_path(root, name),
    info = lstatSync(path);
  if (info.isSymbolicLink()) return { kind: 'symlink', path: name, target: readlinkSync(path) };
  if (info.isDirectory()) return { kind: 'directory', path: name, mode: info.mode & 0o777 };
  if (info.isFile()) {
    const source = regular_source(path);
    try {
      return {
        kind: 'file',
        path: name,
        mode: source.info.mode & 0o777,
        sha256: sha256(readFileSync(source.descriptor)),
      };
    } finally {
      closeSync(source.descriptor);
    }
  }
  return fail('installer recipe contains an unsupported filesystem entry');
}
function selected_recipe(
  root: string,
  source_sha: string,
  fence: () => void,
): InstallerFileManifest {
  const metadata = native_metadata(root),
    selected = new Map<string, InstallerRecipeEntry>();
  function add(name: string): void {
    fence();
    name = path_name(name);
    if (selected.has(name)) return;
    const parent = posix.dirname(name);
    if (parent !== '.') add(parent);
    selected.set(name, entry_for(root, name));
  }
  function tree(name: string): void {
    add(name);
    if (selected.get(name)!.kind === 'directory')
      for (const child of readdirSync(source_path(root, name)).sort(ordered))
        tree(name + '/' + child);
  }
  for (const name of INSTALLER_SOURCE_FILES) add(name);
  const packages: string[] = [...INSTALLER_PACKAGE_ROOTS];
  try {
    source_path(root, INSTALLER_KOFFI_OPTIONAL_ROOT);
    packages.push(INSTALLER_KOFFI_OPTIONAL_ROOT);
  } catch (error) {
    if (!(
      is_os_error(error) &&
      error instanceof Error &&
      'code' in error &&
      error.code === 'ENOENT'
    ))
      throw error;
  }
  for (const name of packages) tree(name);
  const records = new Map(
    metadata.inventory.native_files.map((entry) => [path_name(entry.path), entry]),
  );
  if (records.size !== metadata.inventory.native_files.length)
    fail('duplicate native inventory path');
  const visited = new Set<string>();
  function native(name: string): void {
    if (visited.has(name)) return;
    visited.add(name);
    const record = records.get(name);
    if (!record || !digest(record.sha256)) fail('installer native provider is not inventoried');
    add(name);
    const entry = selected.get(name)!;
    if (entry.kind !== 'file' || entry.sha256 !== record.sha256)
      fail('installer native bytes differ from inventory');
    if (record.format !== 'ELF' || !record.active) return;
    const elf: NativeElfFile = record;
    for (const [needed, provider] of Object.entries(elf.providers)) {
      if (provider.startsWith('host:')) {
        if (provider !== 'host:' + needed || !metadata.external.has(needed))
          fail('installer native provider is not admitted');
      } else native(path_name(provider));
    }
    const provenance: CopiedNativeComponent | undefined =
      metadata.inventory.copied_components[name];
    if (provenance) {
      if (
        !digest(provenance.source_sha256) ||
        typeof provenance.package !== 'string' ||
        typeof provenance.version !== 'string' ||
        typeof provenance.source !== 'string'
      )
        fail('invalid copied native provenance');
      add(path_name(provenance.license));
      if (selected.get(provenance.license)?.kind !== 'file')
        fail('copied native license is not regular');
    } else if (name.startsWith('runtime/lib/')) fail('copied native provider lacks provenance');
  }
  for (const entry of records.values())
    if (packages.some((root) => entry.path.startsWith(root + '/'))) native(entry.path);
  return parse_installer_file_manifest({
    version: 1,
    source_sha,
    entry: INSTALLER_ENTRY,
    runtime_inventory_sha256: metadata.hash,
    files: [...selected.values()],
  });
}
export async function write_installer_file_manifest(
  root: string,
  source_sha: string,
  execution: ReleaseExecution,
): Promise<void> {
  validate_app_sha(source_sha);
  const fence = () => assert_release_execution(execution);
  fence();
  root = root_path(root);
  const recipe = selected_recipe(root, source_sha, fence);
  const path = source_path(root, INSTALLER_RECIPE_PATH, true);
  try {
    if (!lstatSync(path).isFile()) fail('installer recipe target must be regular');
  } catch (error) {
    if (!(
      is_os_error(error) &&
      error instanceof Error &&
      'code' in error &&
      error.code === 'ENOENT'
    ))
      throw error;
  }
  fence();
  replace_existing_json(path, recipe as unknown as Record<string, unknown>);
  fence();
}
function verify(root: string, files: readonly InstallerRecipeEntry[], fence: () => void): void {
  for (const expected of files) {
    fence();
    const actual = entry_for(root, expected.path);
    if (!isDeepStrictEqual(actual, expected))
      fail('installer recipe bytes, mode or link changed: ' + expected.path);
  }
}
function sync_directory(path: string): void {
  const fd = openSync(path, constants.O_RDONLY | constants.O_DIRECTORY);
  try {
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}
function copy_regular(source: string, destination: string, mode: number): void {
  const incoming = regular_source(source);
  try {
    if ((incoming.info.mode & 0o777) !== mode) fail('installer source mode changed');
    const outgoing = openSync(
      destination,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
      0o600,
    );
    try {
      const buffer = Buffer.allocUnsafe(FILE_CHUNK_BYTES);
      let position = 0;
      for (;;) {
        const count = readSync(incoming.descriptor, buffer, 0, buffer.length, position);
        if (count === 0) break;
        for (let written = 0; written < count;) {
          const accepted = writeSync(outgoing, buffer, written, count - written, null);
          if (accepted === 0) fail('installer destination did not accept bytes');
          written += accepted;
        }
        position += count;
      }
      fchmodSync(outgoing, mode);
      fsyncSync(outgoing);
    } finally {
      closeSync(outgoing);
    }
  } finally {
    closeSync(incoming.descriptor);
  }
}
function remove_copy(path: string): void {
  const info = lstatSync(path);
  if (info.isDirectory()) {
    chmodSync(path, info.mode | 0o700);
    for (const name of readdirSync(path)) remove_copy(join(path, name));
  }
  rmSync(path, { recursive: true, force: true });
}
function replaceable_applications(current_root: string): readonly string[] {
  const current = root_path(current_root),
    roots: string[] = [];
  for (const name of readdirSync(current)) {
    if (
      name !== 'app' &&
      name !== 'app-previous' &&
      !name.startsWith(STAGED_APP_PREFIX) &&
      !name.startsWith(PREVIOUS_APP_PREFIX)
    )
      continue;
    const path = join(current, name);
    assert_installer_path(path);
    roots.push(realpathSync(path));
  }
  return roots;
}
function external_node(request: InstallerRequest): string {
  if (!isAbsolute(request.node_executable)) fail('installer Node must be absolute');
  const node = realpathSync(request.node_executable);
  if (!lstatSync(node).isFile()) fail('installer Node must be a file');
  accessSync(node, constants.X_OK);
  for (const app of replaceable_applications(request.current_root)) {
    try {
      inside(app, node);
    } catch {
      continue;
    }
    fail('installer Node resolves inside a replaceable application');
  }
  return node;
}
export function copy_installer_runtime(
  application_root: string,
  request: InstallerRequest,
  signal: AbortSignal,
): readonly [string, string, string] {
  const fence = () => signal.throwIfAborted();
  fence();
  for (const path of [request.current_root, request.database, request.data_directory])
    assert_installer_path(path);
  installer_attempt_id(request.attempt_id);
  const root = root_path(application_root),
    node = external_node(request);
  const recipe = parse_installer_file_manifest(read_json(root, INSTALLER_RECIPE_PATH));
  const manifest = read_json(root, 'manifest.json');
  manifest_identity(manifest, recipe.source_sha);
  const metadata = native_metadata(root);
  if (
    !is_object(manifest) ||
    !isDeepStrictEqual(manifest.runtime_contract, read_json(root, RUNTIME_CONTRACT_PATH)) ||
    metadata.hash !== recipe.runtime_inventory_sha256
  )
    fail('installer recipe identity or inventory changed');
  if (!isDeepStrictEqual(recipe, selected_recipe(root, recipe.source_sha, fence)))
    fail('installer recipe does not match the complete selected closure');
  const data = root_path(request.data_directory),
    attempt = join(data, 'update-installer-' + request.attempt_id);
  assert_installer_path(attempt);
  for (const app of replaceable_applications(request.current_root)) {
    try {
      inside(app, attempt);
    } catch {
      continue;
    }
    fail('installer attempt is inside a replaceable application');
  }
  fence();
  mkdirSync(attempt, { mode: 0o700 });
  const work = join(attempt, 'work'),
    runtime = join(attempt, 'runtime'),
    request_path = join(attempt, 'request.json');
  try {
    mkdirSync(work, { mode: 0o700 });
    for (const entry of recipe.files) {
      fence();
      const destination = join(work, entry.path),
        source = source_path(root, entry.path);
      if (entry.kind === 'directory') mkdirSync(destination, { mode: 0o700 });
      else if (entry.kind === 'symlink') symlinkSync(entry.target, destination);
      else {
        copy_regular(source, destination, entry.mode);
      }
    }
    for (const entry of [...recipe.files].reverse())
      if (entry.kind === 'directory') {
        chmodSync(join(work, entry.path), entry.mode);
        sync_directory(join(work, entry.path));
      }
    verify(work, recipe.files, fence);
    fence();
    replace_existing_json(request_path, { ...request, node_executable: node });
    sync_directory(work);
    renameSync(work, runtime);
    sync_directory(attempt);
    fence();
    return [node, join(runtime, INSTALLER_ENTRY), request_path];
  } catch (error) {
    try {
      remove_copy(attempt);
    } catch (cleanup) {
      throw new AggregateError([error, cleanup], 'installer copy and cleanup failed');
    }
    throw error;
  }
}
