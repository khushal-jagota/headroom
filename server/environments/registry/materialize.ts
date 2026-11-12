import { mkdir, mkdtemp, rename, rm, stat } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { build_fake_environment_database } from '../fixture.ts';
import { EnvironmentValidationError } from './contracts.ts';
import type {
  EnvironmentManifest,
  EnvironmentRequest,
  MaterializationOperations,
  PrepareEnvironmentRequest,
  ResetEnvironmentRequest,
  ResolvedEnvironmentInstance,
} from './contracts.ts';
import {
  canonical_path,
  credential_reference,
  parse_environment_file,
  resolve_environment_instance,
  validate_absolute_environment_root,
  validate_instance_id,
  validate_repository_roots,
  validate_resolved_registry,
} from './logic.ts';
import {
  acquire_stopped_environment_lease,
  FAKE_FIXTURE_VERSION,
  instance_from_manifest,
  MANIFEST_FILENAME,
  manifest_from_instance,
  read_manifest,
  with_registry_lock,
  write_manifest,
} from './store.ts';

const operations: MaterializationOperations = {
  build_fixture: (path, now) => {
    build_fake_environment_database(path, now);
  },
  rename,
};
async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '')) return false;
    throw error;
  }
}
async function common_layout(instance: ResolvedEnvironmentInstance): Promise<void> {
  await mkdir(instance.instance_root, { recursive: true, mode: 0o750 });
  for (const path of [
    instance.logs_dir,
    dirname(instance.dispatcher_lock_path),
    dirname(instance.server_control_socket_path),
  ])
    await mkdir(path, { recursive: true });
}
async function replace_fixture(
  path: string,
  now: number,
  effects: MaterializationOperations,
): Promise<void> {
  const data = dirname(path),
    instance = dirname(data);
  await mkdir(instance, { recursive: true });
  const temporary = await mkdtemp(join(instance, `.${basename(data)}-`));
  let backup: string | undefined;
  try {
    const incoming = join(temporary, basename(data)),
      database = join(incoming, basename(path));
    await effects.build_fixture(database, now);
    if (
      !(await stat(database).then(
        (info) => info.isFile(),
        () => false,
      ))
    )
      throw new EnvironmentValidationError('fake fixture did not create a database');
    if (await exists(data)) {
      backup = await mkdtemp(join(instance, `.${basename(data)}-backup-`));
      await effects.rename(data, join(backup, basename(data)));
    }
    try {
      await effects.rename(incoming, data);
    } catch (error) {
      if (
        backup !== undefined &&
        (await exists(join(backup, basename(data)))) &&
        !(await exists(data))
      )
        await effects.rename(join(backup, basename(data)), data);
      throw error;
    }
  } finally {
    // Python also discards the staged predecessor if both replacement AND rollback fail.
    await rm(temporary, { recursive: true, force: true }).catch(() => {});
    if (backup !== undefined) await rm(backup, { recursive: true, force: true }).catch(() => {});
  }
}
async function registry(root: string): Promise<EnvironmentManifest[]> {
  const manifests: EnvironmentManifest[] = [];
  for (const path of [join(root, MANIFEST_FILENAME), join(root, 'staging', MANIFEST_FILENAME)])
    if (await exists(path)) manifests.push(await read_manifest(path, root));
  validate_resolved_registry(manifests.map(instance_from_manifest));
  return manifests;
}
async function validate_credentials(options: EnvironmentRequest): Promise<void> {
  await credential_reference(
    options.kind,
    options.environment_root,
    options.credentials_env_file ?? null,
  );
  if (options.credentials_env_file != null && (await exists(options.credentials_env_file)))
    await parse_environment_file(options.credentials_env_file, options.kind);
}
export async function prepare_environment_instance(
  options: PrepareEnvironmentRequest,
  effects: MaterializationOperations = operations,
): Promise<EnvironmentManifest> {
  const now =
    options.now ?? (options.kind === 'live' ? Math.floor(Date.now() / 1000) : 1_800_000_000);
  const root = await validate_absolute_environment_root(options.environment_root);
  const id = validate_instance_id(options.kind, options.instance_id ?? null);
  await validate_credentials(options);
  return with_registry_lock(root, async () => {
    const manifests = await registry(root),
      existing = manifests.find(
        (manifest) => manifest.kind === options.kind && manifest.instance_id === id,
      );
    if (existing) {
      if (
        options.port != null &&
        options.port !== (existing.port_policy.kind === 'fixed' ? existing.port_policy.port : null)
      )
        throw new EnvironmentValidationError('prepared environment port does not match request');
      if (
        (options.credentials_env_file == null
          ? null
          : await canonical_path(options.credentials_env_file)) !== existing.credentials_env_file
      )
        throw new EnvironmentValidationError(
          'prepared environment credential file does not match request',
        );
      if (
        !isDeepStrictEqual(
          await validate_repository_roots(options.repository_roots, existing.repository_roots),
          existing.repository_roots,
        )
      )
        throw new EnvironmentValidationError(
          'prepared environment repositories do not match request',
        );
      return existing;
    }
    const instance = await resolve_environment_instance({
      ...options,
      environment_root: root,
      instance_id: id,
      allowed_repository_roots: options.repository_roots,
      requested_repository_roots: options.repository_roots,
      fixture_version: options.kind === 'live' ? null : FAKE_FIXTURE_VERSION,
      prepared: true,
    });
    const manifest = manifest_from_instance(instance, now);
    validate_resolved_registry([...manifests.map(instance_from_manifest), instance]);
    if (options.kind !== 'live') {
      await common_layout(instance);
      await replace_fixture(instance.db_path, now, effects);
    }
    await write_manifest(manifest);
    return manifest;
  });
}
export async function inspect_environment_instance(
  options: EnvironmentRequest,
): Promise<EnvironmentManifest> {
  const root = await validate_absolute_environment_root(options.environment_root);
  validate_instance_id(options.kind, options.instance_id ?? null);
  const location = join(options.kind === 'live' ? root : join(root, 'staging'), MANIFEST_FILENAME);
  if (await exists(location)) {
    const manifest = await read_manifest(location, root);
    if (options.repository_roots.length)
      await validate_repository_roots(options.repository_roots, manifest.repository_roots);
    if (
      options.port != null &&
      options.port !== (manifest.port_policy.kind === 'fixed' ? manifest.port_policy.port : null)
    )
      throw new EnvironmentValidationError('prepared environment port does not match request');
    if (
      options.credentials_env_file != null &&
      (await canonical_path(options.credentials_env_file)) !== manifest.credentials_env_file
    )
      throw new EnvironmentValidationError(
        'prepared environment credential file does not match request',
      );
    return manifest;
  }
  return manifest_from_instance(
    await resolve_environment_instance({
      ...options,
      environment_root: root,
      allowed_repository_roots: options.repository_roots,
      requested_repository_roots: options.repository_roots,
    }),
    null,
  );
}
export async function reset_environment_instance(
  options: ResetEnvironmentRequest,
  effects: MaterializationOperations = operations,
): Promise<EnvironmentManifest> {
  if (options.kind === 'live')
    throw new EnvironmentValidationError('live environment cannot be reset');
  const root = await validate_absolute_environment_root(options.environment_root);
  return with_registry_lock(root, async () => {
    const current = await inspect_environment_instance({ ...options, environment_root: root });
    if (current.prepared_at === null)
      throw new EnvironmentValidationError('environment instance is not prepared');
    const instance = await resolve_environment_instance({
      kind: current.kind,
      instance_id: current.instance_id,
      environment_root: current.environment_root,
      port: current.port_policy.kind === 'fixed' ? current.port_policy.port : null,
      credentials_env_file: current.credentials_env_file,
      allowed_repository_roots: current.repository_roots,
      requested_repository_roots: current.repository_roots,
      fixture_version: FAKE_FIXTURE_VERSION,
      prepared: true,
    });
    const lease = acquire_stopped_environment_lease(current);
    try {
      await common_layout(instance);
      const now = options.now ?? 1_800_000_000;
      await replace_fixture(instance.db_path, now, effects);
      const manifest = manifest_from_instance(instance, now);
      await write_manifest(manifest);
      return manifest;
    } finally {
      lease.close();
    }
  });
}
export async function remove_environment_instance(
  options: EnvironmentRequest,
): Promise<EnvironmentManifest> {
  if (options.kind === 'live')
    throw new EnvironmentValidationError('live environment cannot be removed');
  const root = await validate_absolute_environment_root(options.environment_root);
  return with_registry_lock(root, async () => {
    const current = await inspect_environment_instance({ ...options, environment_root: root });
    if (current.prepared_at === null)
      throw new EnvironmentValidationError('environment instance is not prepared');
    const lease = acquire_stopped_environment_lease(current);
    try {
      await rm(current.instance_root, { recursive: true });
    } finally {
      lease.close();
    }
    return current;
  });
}
