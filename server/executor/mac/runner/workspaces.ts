/** Physical workspace custody shares the native-start lock and exact-stop publication owner. */
import { dirname, join } from 'node:path';
import type { FifoLock } from '../../../conversation/runtime/state.ts';
import type { WorkspaceIdentity, WorkspaceState } from '../../../ticket_workspaces/contracts.ts';
import {
  cleanup_local_workspaces,
  retire_workspace,
  workspace_identity,
} from '../../../ticket_workspaces/filesystem.ts';
import type { ExecutorPaths } from '../configuration.ts';
import type { MacCommandWork } from './command_contracts.ts';
import type { MacNativeChildrenContract, MacRunningChild } from './contracts.ts';
import type { MacRunnerContract, MacRunnerOptions } from './lifecycle_contracts.ts';
import type { MacSourceWorkContract } from './retirement_contracts.ts';
import { RetainedWorkspaceReporter } from '../../../ticket_workspaces/reporting.ts';

export function same_workspace(
  left: WorkspaceIdentity | null,
  right: WorkspaceIdentity | null,
): boolean {
  return (
    left !== null &&
    right !== null &&
    left.machine_id === right.machine_id &&
    left.workspace_id === right.workspace_id
  );
}

export interface MacWorkspaceCleanupDependencies extends Pick<
  MacRunnerOptions,
  'workspace_git' | 'retire_workspace' | 'retirement_is_committed'
> {
  readonly paths: ExecutorPaths;
  readonly workspace_home: string;
  readonly workspace_lock: FifoLock;
  readonly native_children: Pick<MacNativeChildrenContract, 'retained_children'>;
  readonly sources: Pick<MacSourceWorkContract, 'count' | 'failed'>;
  readonly commands: Pick<MacCommandWork, 'records'>;
  readonly stop_and_publish: MacRunnerContract['stop_and_publish'];
  readonly cleanup_refused: (ticket_id: string) => boolean;
}

export class MacWorkspaceCleanup {
  private readonly dependencies: MacWorkspaceCleanupDependencies;
  private readonly reporter = new RetainedWorkspaceReporter();
  constructor(dependencies: MacWorkspaceCleanupDependencies) {
    this.dependencies = dependencies;
  }

  private busy(child: MacRunningChild): boolean {
    const { sources, commands } = this.dependencies;
    return (
      child.active_turn !== null ||
      child.background_task_ids.size !== 0 ||
      child.open_tool_calls.size !== 0 ||
      sources.count(child.reference) !== 0 ||
      sources.failed(child.reference) ||
      commands
        .records(child.reference)
        .some(
          (record) => record.outcome === null || (record.has_outputs && !record.outputs_published),
        )
    );
  }

  private async authorize(
    state: WorkspaceState,
    workspace: WorkspaceIdentity,
    replay: boolean,
  ): Promise<boolean> {
    if (this.dependencies.cleanup_refused(state.ticket_id)) return false;
    const children = this.dependencies.native_children
      .retained_children()
      .filter(
        (child) =>
          child.ticket_id === state.ticket_id && same_workspace(child.workspace, workspace),
      );
    if (children.some((child) => this.busy(child))) return false;
    const grant = replay
      ? this.dependencies.retirement_is_committed
      : this.dependencies.retire_workspace;
    let granted = await grant(state, workspace);
    if (replay && !granted) granted = await this.dependencies.retire_workspace(state, workspace);
    if (!granted) return false;
    if (children.some((child) => this.busy(child))) return false;
    for (const child of children) {
      const result = await this.dependencies.stop_and_publish({
        command_id: 'workspace_stop_' + child.reference.child_id,
        child: child.reference,
        operation: 'stop',
        turn: null,
        arguments: { kind: 'stop' },
      });
      if (result.disposition !== 'succeeded') return false;
    }
    return true;
  }

  async cleanup(states: readonly WorkspaceState[], now: number): Promise<void> {
    const { paths, workspace_home, workspace_lock, workspace_git, native_children } =
      this.dependencies;
    const result = await cleanup_local_workspaces(
      paths.artifacts,
      new Map(states.map((state) => [state.ticket_id, state])),
      now,
      {
        git_execution: workspace_git,
        mirror_root: join(paths.root, 'cache', 'git-mirrors'),
        extra_roots: [join(workspace_home, 'panels-session-evidence')],
        active_ticket_ids: new Set(
          native_children
            .retained_children()
            .filter((child) => this.busy(child))
            .map((child) => child.ticket_id),
        ),
        retirement_is_committed: (state, workspace) =>
          workspace_lock.run(() => this.authorize(state, workspace, true)),
        retire: (path, state, workspace, checkout_only) =>
          workspace_lock.run(async () => {
            if (!same_workspace(workspace_identity(path), workspace))
              return { reason: 'workspace ownership changed' };
            if (!(await this.authorize(state, workspace, false)))
              return { reason: 'retirement authorization refused' };
            return retire_workspace(path, state, {
              git_execution: workspace_git,
              identity: workspace,
              retired_root: join(dirname(paths.artifacts), '.ticket-retired'),
              checkout_only,
            });
          }),
      },
    );
    this.reporter.report(result.skipped);
  }
}
