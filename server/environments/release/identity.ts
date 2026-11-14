/** Manifest and tree identity. Runtime structure is separate from offline admission. */
import { createHash } from 'node:crypto';
import { constants, createReadStream } from 'node:fs';
import { access, lstat, readdir, readFile, readlink, stat } from 'node:fs/promises';
import { dirname, extname, join, relative, sep } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { path_is_within } from '../../core/path_containment.ts';
import type { AppManifest, AppValidationOptions, RuntimeContract } from './contracts.ts';
import { AppValidationError } from './contracts.ts';
import {
  FILE_CHUNK_BYTES,
  NODE_RUNTIME_EXECUTABLES,
  NODE_RUNTIME_FILES,
  RUNTIME_CONTRACT_PATH,
  RUNTIME_INVENTORY_PATH,
  SHARED_RUNTIME_FILES,
} from './configuration.ts';
import { expand_path, resolved_path } from '../paths.ts';
import { parse_optional_runtime_contract } from '../updates/logic/runtime_contract.ts';
import { manifest_identity } from './logic/manifest.ts';
export { validate_app_sha } from './logic/manifest.ts';

function missing(error: unknown): boolean {
  return (
    error instanceof Error &&
    'code' in error &&
    (error.code === 'ENOENT' || error.code === 'ENOTDIR')
  );
}
async function file(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isFile();
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
function compare_names(a: string, b: string): number {
  const left = Array.from(a, (value) => value.codePointAt(0)!);
  const right = Array.from(b, (value) => value.codePointAt(0)!);
  for (let i = 0; i < Math.min(left.length, right.length); i++)
    if (left[i] !== right[i]) return left[i] - right[i];
  return left.length - right.length;
}
/** sorted(Path.rglob): component order, with no recursion through directory links. */
export async function app_tree_paths(root: string): Promise<string[]> {
  const paths: string[] = [];
  async function visit(directory: string): Promise<void> {
    const names = (await readdir(directory)).sort(compare_names);
    for (const name of names) {
      const path = join(directory, name);
      paths.push(path);
      if ((await lstat(path)).isDirectory()) await visit(path);
    }
  }
  await visit(root);
  return paths;
}
async function assert_link(root: string, path: string): Promise<void> {
  const actual = await resolved_path(path);
  if (actual === root || !path_is_within(root, actual))
    throw new AppValidationError('app contains a symlink outside its root');
}
async function digest_tree(
  app: string,
  exclude_pytest_cache = false,
  include_python_cache_before_ns?: bigint,
): Promise<string> {
  const root = await resolved_path(app),
    digest = createHash('sha256');
  for (const path of await app_tree_paths(root)) {
    if (path === join(root, 'manifest.json')) continue;
    const parts = relative(root, path).split(sep);
    if (exclude_pytest_cache && parts.includes('.pytest_cache')) continue;
    if (parts.includes('__pycache__') || ['.pyc', '.pyo'].includes(extname(path))) {
      if (include_python_cache_before_ns === undefined) continue;
      try {
        if ((await lstat(path, { bigint: true })).mtimeNs > include_python_cache_before_ns)
          continue;
      } catch (error) {
        if (missing(error)) continue;
        throw error;
      }
    }
    const info = await lstat(path);
    const name = parts.join('/');
    if (info.isSymbolicLink()) {
      await assert_link(root, path);
      digest.update(name + '\0SYMLINK\0' + (await readlink(path)) + '\0');
    } else if (info.isFile() && !path.split(sep).includes('.git')) {
      digest.update(name + '\0');
      for await (const chunk of createReadStream(path, { highWaterMark: FILE_CHUNK_BYTES }))
        digest.update(chunk);
      digest.update('\0');
    }
  }
  return digest.digest('hex');
}
export function digest_app_source(root: string): Promise<string> {
  return digest_tree(root);
}
export function digest_app_artifact(root: string): Promise<string> {
  return digest_tree(root, true);
}
export async function validate_app_tree(root: string): Promise<void> {
  for (const path of await app_tree_paths(root)) {
    if (relative(root, path).split(sep).includes('.git'))
      throw new AppValidationError('app must not contain Git metadata');
    if ((await lstat(path)).isSymbolicLink()) await assert_link(root, path);
  }
}
async function validate_runtime_tree(root: string, node: boolean): Promise<void> {
  const executables = node
    ? NODE_RUNTIME_EXECUTABLES
    : ['.venv/bin/python', 'bin/panels', 'bin/panels-launcher'];
  for (const name of executables) {
    const path = join(root, name);
    if (!(await file(path))) throw new AppValidationError(`runtime file is missing: ${name}`);
    try {
      await access(path, constants.X_OK);
    } catch {
      throw new AppValidationError(`runtime file is not executable: ${name}`);
    }
  }
  for (const name of [...SHARED_RUNTIME_FILES, ...(node ? NODE_RUNTIME_FILES : [])])
    if (!(await file(join(root, name))))
      throw new AppValidationError(`runtime file is missing: ${name}`);
}
function decode(bytes: Uint8Array): unknown {
  return JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes));
}
export async function validate_app_manifest(
  path: string,
  options: AppValidationOptions = {},
): Promise<AppManifest> {
  const input = expand_path(path);
  try {
    if ((await lstat(input)).isSymbolicLink())
      throw new AppValidationError(`app manifest must be a regular file: ${input}`);
  } catch (error) {
    if (!missing(error)) throw error;
  }
  const manifest_path = await resolved_path(input),
    root = dirname(manifest_path);
  if (!(await file(manifest_path)))
    throw new AppValidationError(`app manifest is missing: ${manifest_path}`);
  try {
    await stat(join(root, '.git'));
    throw new AppValidationError('app root must not contain Git metadata');
  } catch (error) {
    if (!missing(error)) throw error;
  }
  let raw: unknown;
  try {
    raw = decode(await readFile(manifest_path));
  } catch (error) {
    if (
      error instanceof SyntaxError ||
      error instanceof TypeError ||
      (error instanceof Error && 'code' in error)
    )
      throw new AppValidationError('app manifest is not valid JSON');
    throw error;
  }
  const identity = manifest_identity(raw, options.expected_sha);
  const value = raw as Record<string, unknown>;
  let artifact_matches =
    options.verify_artifact_digest === false ||
    identity.artifact_digest === (await digest_app_artifact(root));
  if (!artifact_matches)
    artifact_matches =
      identity.artifact_digest ===
      (await digest_tree(root, true, (await stat(manifest_path, { bigint: true })).mtimeNs));
  if (!artifact_matches)
    throw new AppValidationError('app artifact digest does not match its manifest');
  await validate_app_tree(root);
  let contract: RuntimeContract | null;
  try {
    contract = parse_optional_runtime_contract(value.runtime_contract);
    if (contract !== null) {
      if (!isDeepStrictEqual(decode(await readFile(join(root, RUNTIME_CONTRACT_PATH))), contract))
        throw new Error('manifest runtime contract differs from payload');
      const inventory = await readFile(join(root, RUNTIME_INVENTORY_PATH));
      if (createHash('sha256').update(inventory).digest('hex') !== contract.inventory_sha256)
        throw new Error('runtime inventory differs from contract');
    }
  } catch (error) {
    if (error instanceof Error)
      throw new AppValidationError(`invalid runtime contract: ${error.message}`);
    throw error;
  }
  if (options.require_runtime) await validate_runtime_tree(root, contract?.version === 2);
  return { ...identity, runtime_contract: contract };
}
