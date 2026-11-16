/** Workspace detach admission joins durable grants with physical custody. */
import { existsSync, renameSync, unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createDatabase, createReadAccess } from '../core/database/index.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { filesRoot } from '../files/logic/paths.ts';
import type {
  CleanupResult,
  RetirementTarget,
  WorkspaceCleanupOptions,
  WorkspaceIdentity,
  WorkspaceRetirementOperation,
  WorkspaceState,
} from './contracts.ts';
import { authorize_retirement, has_committed_retirement, read_workspace_states } from './data.ts';
import {
  cleanup_local_workspaces,
  prepare_workspace_retirement,
  read_workspace_identity,
} from './filesystem.ts';

export interface DatabaseCleanupOptions extends WorkspaceCleanupOptions {
  retire_local_children?: WorkspaceRetirementOperation;
}
function custody_retired(sql: ReadSql, identity: WorkspaceIdentity): boolean {
  return (
    sql.get(
      "SELECT 1 FROM ticket_workspace_conversations w JOIN conversation_child_custody c USING(conversation_id) WHERE w.machine_id=? AND w.workspace_id=? AND c.state!='retired' LIMIT 1",
      [identity.machine_id, identity.workspace_id],
    ) === undefined
  );
}
export async function clean_for_database(
  db_path: string,
  now: number,
  options: DatabaseCleanupOptions = {},
): Promise<CleanupResult> {
  const reader = createReadAccess(db_path);
  let snapshot: { states: Map<string, WorkspaceState>; active: Set<string> };
  try {
    snapshot = reader.read((sql) => ({
      states: read_workspace_states(sql),
      active: new Set(
        sql
          .all<{ id: string }>("SELECT id FROM tickets WHERE worker_step_claim='out'")
          .map((row) => row.id),
      ),
    }));
  } finally {
    reader.close();
  }
  async function granted(expected: WorkspaceState, identity: WorkspaceIdentity): Promise<boolean> {
    const check = (): RetirementTarget => {
      const fresh = createReadAccess(db_path);
      try {
        return fresh.read((sql) =>
          custody_retired(sql, identity) && has_committed_retirement(sql, expected, identity)
            ? { path: '.' }
            : { reason: 'Local child retirement is not proven' },
        );
      } finally {
        fresh.close();
      }
    };
    const result = await (options.retire_local_children
      ? options.retire_local_children(expected.ticket_id, identity, check)
      : check());
    return 'path' in result;
  }
  async function detach(
    path: string,
    expected: WorkspaceState,
    identity: WorkspaceIdentity,
  ): Promise<RetirementTarget> {
    const target = await prepare_workspace_retirement(path, expected, {
      ...options,
      identity,
      retired_root: join(dirname(filesRoot(db_path, 'ticket')), '.ticket-retired'),
    });
    if ('reason' in target) return target;
    let detached = false;
    try {
      const fresh = createDatabase(db_path);
      try {
        return fresh.write((sql) => {
          if (!custody_retired(sql, identity))
            return { reason: 'Local child retirement is not proven' };
          if (!authorize_retirement(sql, expected, now, identity))
            return { reason: 'Ticket is active or no longer eligible' };
          const current = read_workspace_identity(path);
          if (
            current.machine_id !== identity.machine_id ||
            current.workspace_id !== identity.workspace_id
          )
            throw new Error('Physical workspace identity changed');
          renameSync(path, target.path);
          detached = true;
          return target;
        });
      } catch (error) {
        if (detached && existsSync(target.path) && !existsSync(path)) renameSync(target.path, path);
        throw error;
      } finally {
        fresh.close();
      }
    } finally {
      if (!detached) {
        const token = target.path.slice(target.path.lastIndexOf('-') + 1);
        try {
          unlinkSync(join(dirname(target.path), `${token}.json`));
        } catch (error) {
          if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
        }
      }
    }
  }
  return cleanup_local_workspaces(filesRoot(db_path, 'ticket'), snapshot.states, now, {
    ...options,
    active_ticket_ids: snapshot.active,
    retire: (path, state, identity) =>
      options.retire_local_children
        ? options.retire_local_children(state.ticket_id, identity, () =>
            detach(path, state, identity),
          )
        : detach(path, state, identity),
    retirement_is_committed: granted,
  });
}
