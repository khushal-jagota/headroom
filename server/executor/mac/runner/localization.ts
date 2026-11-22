/** Resolve each child's native folder and replace only the VPS transport identity. */
import { BUILT_IN_MANIFESTS } from '../../../drivers/built_in_manifests.ts';
import { parse_backend_instance_id } from '../../../drivers/core/identity.ts';
import { stat } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';
import { platform } from 'node:os';
import { prepare_xcode_compilation_cache } from './xcode_cache.ts';
import { path_is_within } from '../../../core/path_containment.ts';
import { resolve_path_sync } from '../../../core/path_resolution.ts';
import { inspect } from 'node:util';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { BackendSpawnFailed } from '../../../conversation/backends/contracts.ts';
import { RECORD_ROOT_ENV } from '../../../worker_supervision/config.ts';
import { prepare_workspace } from '../../../ticket_workspaces/filesystem.ts';
import {
  ConversationRoleMaterials,
  ResolvedConversationStart,
  WorkspacePathPolicy,
} from '../../../conversation/contracts.ts';
import type { ChildReference, StartRun } from '../../wire.generated.ts';
import { safe_component } from '../files.ts';
import {
  EXECUTOR_ROOT_ENV,
  EXECUTOR_CHILD_ID_ENV,
  EXECUTOR_CAPABILITY_ENV,
  NATIVE_SKILLS_DIR_ENV,
} from '../configuration.ts';
import type { MacNativeChildrenOptions } from './contracts.ts';
import { prepare_agent_home } from '../../../agent_homes/agent_home.ts';
import { child_owner } from './logic/child_owner.ts';
import type { MacChildOwner } from './logic/child_owner.ts';
const workspace_resolution = { strict: true, require_directory_traversal: true };
const removed_environment = [
  'HOME',
  'USERPROFILE',
  // A driver's home is the machine's own, never the start's.
  ...BUILT_IN_MANIFESTS.flatMap(({ manifest: { home } }) =>
    home ? [home.environment_name, ...(home.extra_environment_names ?? [])] : [],
  ),
  EXECUTOR_ROOT_ENV,
  EXECUTOR_CHILD_ID_ENV,
  EXECUTOR_CAPABILITY_ENV,
  'PLAN_TICKET_FILES_DIR',
  NATIVE_SKILLS_DIR_ENV,
  'XCODE_XCCONFIG_FILE',
];
function safe_relative(value: string): string {
  if (
    value.includes('\\') ||
    isAbsolute(value) ||
    (value !== '' && value.split('/').some((part) => part === '' || part === '.' || part === '..'))
  )
    throw new Error('unsafe home-relative workspace');
  return value;
}
function within(root: string, path: string): void {
  if (!path_is_within(root, path))
    throw new Error(`${inspect(path)} is not in the subpath of ${inspect(root)}`);
}
async function create_directory(options: MacNativeChildrenOptions, path: string): Promise<void> {
  const handle = await options.files.open_directory(path, { create: true });
  try {
    await options.files.close_directory(handle);
  } catch (error) {
    const failure = Object.assign(
      new ServerLifecycleError('Mac workspace directory cleanup failed.'),
      { cause: error },
    );
    options.on_failure(failure);
    throw failure;
  }
}
function workspace_failure(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  return error.message;
}
/** A workspace that the start names: a home-relative mapping, an absolute path, or the executor's. */
async function mapped_workspace(
  input: StartRun,
  options: MacNativeChildrenOptions,
): Promise<string> {
  const resolved = input.resolved_start;
  try {
    let home = options.workspace_home;
    let workspace: string;
    const subfolder =
      input.workspace_home_relative == null ? null : safe_relative(input.workspace_home_relative);
    if (resolved.workspace_path_policy === WorkspacePathPolicy.executor_default) {
      home = options.paths.workspace;
      await create_directory(options, join(home, subfolder ?? ''));
    }
    if (subfolder !== null) {
      home = resolve_path_sync(home, workspace_resolution);
      workspace = resolve_path_sync(join(home, subfolder), workspace_resolution);
      within(home, workspace);
    } else {
      const path =
        resolved.workspace_path_policy === WorkspacePathPolicy.executor_default
          ? options.paths.workspace
          : resolved.workspace_folder;
      if (!isAbsolute(path))
        throw new Error('workspace needs absolute native path or home mapping');
      workspace = resolve_path_sync(path, workspace_resolution);
    }
    if (!(await stat(workspace)).isDirectory())
      throw new Error('mapped workspace is not a directory');
    if (resolved.workspace_path_policy === WorkspacePathPolicy.executor_default)
      within(resolve_path_sync(options.paths.root, { strict: true }), workspace);
    return workspace;
  } catch (error) {
    if (error instanceof ServerLifecycleError) throw error;
    throw new BackendSpawnFailed(`Mac workspace unavailable: ${workspace_failure(error)}`);
  }
}
interface LocalizedFolder {
  readonly workspace_folder: string;
  readonly role_text: string;
  /** A Ticket's folder, named to its Worker as PLAN_TICKET_FILES_DIR. */
  readonly ticket_files: string | null;
  readonly managed_ticket_folder: string | null;
}
async function ticket_folder(
  input: StartRun,
  ticket_id: string,
  identity: ReadonlyMap<string, string>,
  options: MacNativeChildrenOptions,
): Promise<LocalizedFolder> {
  const artifacts = join(options.paths.artifacts, safe_component(ticket_id));
  const managed = identity.get('PLAN_TICKET_WORKSPACE_MANAGED') === '1';
  let workspace = managed ? artifacts : await mapped_workspace(input, options);
  try {
    await create_directory(options, artifacts);
    if (managed) {
      let repository = input.workspace_repository ?? null;
      if (repository === null) {
        if (identity.get('PLAN_TICKET_PROJECT_ID'))
          throw new Error(
            'Home must supply the Project repository configuration before starting a managed Project workspace',
          );
        repository = { repository_url: null, default_branch: null };
      }
      workspace = await prepare_workspace(artifacts, ticket_id, repository, {
        git_execution: options.workspace_git,
        mirror_root: join(options.paths.root, 'cache', 'git-mirrors'),
      });
    }
  } catch (error) {
    if (error instanceof ServerLifecycleError) throw error;
    throw new BackendSpawnFailed(`Mac Ticket worktree unavailable: ${workspace_failure(error)}`);
  }
  const role_text = managed
    ? `\n\nPanels starts you in the Ticket folder at ${artifacts}. ` +
      'Panels owns this Ticket workspace. ' +
      `Use the supplied worktree at ${workspace}. ` +
      'Do not create or remove worktrees. Keep scratch and dependencies inside it. ' +
      `Write reviewable artifacts and evidence under ${join(artifacts, 'artifacts')}. ` +
      'Follow repository setup instructions. Workers commit, push, open PRs, and merge. ' +
      'Panels removes the complete Ticket folder seven days after the Ticket ends, ' +
      'unless unpublished or uncommitted work prevents safe removal.'
    : `\n\nThis existing conversation keeps its original workspace at ${workspace}. ` +
      'Continue the work there, including any unpublished changes. ' +
      `Write reviewable output under ${join(artifacts, 'artifacts')}.`;
  return {
    workspace_folder: managed ? artifacts : workspace,
    role_text,
    ticket_files: artifacts,
    managed_ticket_folder: managed ? artifacts : null,
  };
}
/**
 * The Chief's or a Manager's own home on this machine. In the server's folder the server has
 * already written its files there. Elsewhere, a Manager's home gets the same files here.
 */
async function supervisor_home(
  owner: Extract<MacChildOwner, { kind: 'chief' | 'manager' }>,
  options: MacNativeChildrenOptions,
): Promise<string> {
  try {
    const home =
      owner.kind === 'chief' ? options.paths.chief_home : options.paths.manager_home(owner.id);
    await create_directory(options, home);
    if (owner.kind === 'manager') {
      // The file sync sends this folder, and the owner sees it.
      await create_directory(options, join(home, 'artifacts'));
      if (options.paths.server_folders === null) await prepare_agent_home(home, 'manager');
    }
    return home;
  } catch (error) {
    if (error instanceof ServerLifecycleError) throw error;
    throw new BackendSpawnFailed(
      `Mac ${owner.kind === 'chief' ? 'Chief' : 'Manager'} home unavailable: ${workspace_failure(error)}`,
    );
  }
}
async function child_folder(
  input: StartRun,
  owner: MacChildOwner,
  identity: ReadonlyMap<string, string>,
  options: MacNativeChildrenOptions,
): Promise<LocalizedFolder> {
  const role_text = '';
  switch (owner.kind) {
    case 'ticket':
      return ticket_folder(input, owner.id, identity, options);
    case 'chief':
    case 'manager':
      return {
        workspace_folder: await supervisor_home(owner, options),
        role_text,
        ticket_files: null,
        managed_ticket_folder: null,
      };
    case 'conversation':
      return {
        workspace_folder: await mapped_workspace(input, options),
        role_text,
        ticket_files: null,
        managed_ticket_folder: null,
      };
  }
}
export async function localize_start(
  input: StartRun,
  reference: ChildReference,
  capability: string,
  generation: string,
  options: MacNativeChildrenOptions,
): Promise<ResolvedConversationStart> {
  const resolved = input.resolved_start;
  const identity = new Map(resolved.role_materials?.identity_environment_variables ?? []);
  const folder = await child_folder(input, child_owner(resolved), identity, options);
  for (const name of removed_environment) identity.delete(name);
  if (folder.managed_ticket_folder !== null && platform() === 'darwin') {
    identity.set(
      'XCODE_XCCONFIG_FILE',
      await prepare_xcode_compilation_cache(
        options.paths.root,
        folder.managed_ticket_folder,
        process.env.XCODE_XCCONFIG_FILE,
      ),
    );
  }
  identity.set(EXECUTOR_CHILD_ID_ENV, reference.child_id);
  identity.set(EXECUTOR_CAPABILITY_ENV, capability);
  identity.set(EXECUTOR_ROOT_ENV, options.paths.root);
  if (folder.ticket_files !== null) identity.set('PLAN_TICKET_FILES_DIR', folder.ticket_files);
  identity.set(RECORD_ROOT_ENV, join(options.paths.run, 'worker-processes'));
  identity.set('PLAN_EXECUTOR_SOURCE_INCARNATION', reference.executor_incarnation);
  identity.set(NATIVE_SKILLS_DIR_ENV, join(generation, 'skills'));
  return new ResolvedConversationStart({
    ...resolved,
    backend_key: parse_backend_instance_id(resolved.backend_key),
    workspace_folder: folder.workspace_folder,
    workspace_path_policy: WorkspacePathPolicy.absolute,
    role_materials: new ConversationRoleMaterials({
      role_text: (resolved.role_materials?.role_text ?? '') + folder.role_text,
      identity_environment_variables: [...identity],
    }),
  });
}
