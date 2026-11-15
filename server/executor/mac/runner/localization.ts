/** Resolve existing native workspaces and replace only the VPS transport identity. */
import { stat } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';
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
const workspace_resolution = { strict: true, require_directory_traversal: true };
const removed_environment = [
  'HOME',
  'USERPROFILE',
  'CODEX_HOME',
  'HERMES_HOME',
  'CLAUDE_CONFIG_DIR',
  EXECUTOR_ROOT_ENV,
  EXECUTOR_CHILD_ID_ENV,
  EXECUTOR_CAPABILITY_ENV,
  'PLAN_TICKET_FILES_DIR',
  NATIVE_SKILLS_DIR_ENV,
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
export async function localize_start(
  input: StartRun,
  reference: ChildReference,
  capability: string,
  generation: string,
  options: MacNativeChildrenOptions,
): Promise<ResolvedConversationStart> {
  const resolved = input.resolved_start;
  let workspace: string;
  try {
    let home = options.workspace_home;
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
  } catch (error) {
    if (error instanceof ServerLifecycleError) throw error;
    throw new BackendSpawnFailed(`Mac workspace unavailable: ${workspace_failure(error)}`);
  }
  const identity = new Map(resolved.role_materials?.identity_environment_variables ?? []);
  const ticket_id = safe_component(identity.get('PLAN_TICKET_ID')!);
  const artifacts = join(options.paths.artifacts, ticket_id);
  const managed = identity.get('PLAN_TICKET_WORKSPACE_MANAGED') === '1';
  try {
    await create_directory(options, artifacts);
    if (managed) {
      const repository = resolved.workspace_path_policy === WorkspacePathPolicy.executor_default &&
        !input.workspace_home_relative ? null : workspace;
      workspace = await prepare_workspace(artifacts, repository, ticket_id, {
        git_execution: options.workspace_git,
      });
    }
  } catch (error) {
    if (error instanceof ServerLifecycleError) throw error;
    throw new BackendSpawnFailed(`Mac Ticket worktree unavailable: ${workspace_failure(error)}`);
  }
  for (const name of removed_environment) identity.delete(name);
  identity.set(EXECUTOR_CHILD_ID_ENV, reference.child_id);
  identity.set(EXECUTOR_CAPABILITY_ENV, capability);
  identity.set(EXECUTOR_ROOT_ENV, options.paths.root);
  identity.set('PLAN_TICKET_FILES_DIR', artifacts);
  identity.set(RECORD_ROOT_ENV, join(options.paths.run, 'worker-processes'));
  identity.set('PLAN_EXECUTOR_SOURCE_INCARNATION', reference.executor_incarnation);
  identity.set(NATIVE_SKILLS_DIR_ENV, join(generation, 'skills'));
  let role_text = resolved.role_materials?.role_text ?? '';
  if (managed) {
    role_text +=
      '\n\nPanels owns this Ticket workspace. ' +
      `Use the supplied worktree at ${workspace}. ` +
      'Do not create or remove worktrees. Keep scratch and dependencies inside it. ' +
      `Write reviewable artifacts and evidence under ${join(artifacts, 'artifacts')}. ` +
      'Follow repository setup instructions. Workers commit, push, open PRs, and merge. ' +
      'Panels removes the complete Ticket folder seven days after the Ticket ends, ' +
      'unless unpublished or uncommitted work prevents safe removal.';
  } else {
    role_text +=
      `\n\nThis existing conversation keeps its original workspace at ${workspace}. ` +
      'Continue the work there, including any unpublished changes. ' +
      `Write reviewable output under ${join(artifacts, 'artifacts')}.`;
  }
  return new ResolvedConversationStart({
    ...resolved,
    workspace_folder: workspace,
    workspace_path_policy: WorkspacePathPolicy.absolute,
    role_materials: new ConversationRoleMaterials({
      role_text,
      identity_environment_variables: [...identity],
    }),
  });
}
