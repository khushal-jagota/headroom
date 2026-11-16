/** Workspace detach admission joins durable grants with physical custody. */
import { existsSync, readFileSync, readdirSync, renameSync, rmdirSync, unlinkSync } from 'node:fs';
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
  WorkspaceMarker,
  RetirementRecord,
} from './contracts.ts';
import { authorize_retirement, has_committed_retirement, read_workspace_states } from './data.ts';
import {
  cleanup_local_workspaces,
  prepare_workspace_retirement,
  read_workspace_identity,
  detach_workspace,
} from './filesystem.ts';
import { stat_sync } from './filesystem/shared.ts';

export interface DatabaseCleanupOptions extends WorkspaceCleanupOptions {
  retire_local_children?: WorkspaceRetirementOperation;
}
function custody_retired(
  sql: ReadSql,
  identity: WorkspaceIdentity,
  require_registration: boolean,
): boolean {
  if (
    require_registration &&
    !sql.get(
      'SELECT 1 FROM ticket_workspace_conversations WHERE machine_id=? AND workspace_id=? LIMIT 1',
      [identity.machine_id, identity.workspace_id],
    )
  )
    return false;
  return (
    sql.get(
      "SELECT 1 FROM ticket_workspace_conversations w LEFT JOIN conversation_child_custody c USING(conversation_id) WHERE w.machine_id=? AND w.workspace_id=? AND (c.state IS NULL OR c.state!='retired') LIMIT 1",
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
    function requires_registration(): boolean {
      const retired = join(dirname(filesRoot(db_path, 'ticket')), '.ticket-retired');
      try {
        for (const name of readdirSync(retired).filter((name) => name.endsWith('.json'))) {
          const record = JSON.parse(readFileSync(join(retired, name), 'utf8')) as RetirementRecord;
          if (
            record.ticket_id !== expected.ticket_id ||
            record.machine_id !== identity.machine_id ||
            record.workspace_id !== identity.workspace_id
          )
            continue;
          if (dirname(record.target) !== retired || stat_sync(record.target)?.isSymbolicLink())
            return true;
          const marker = JSON.parse(
            readFileSync(join(record.target, '.panels-workspace.json'), 'utf8'),
          ) as WorkspaceMarker;
          return marker.checkout_kind === 'clone' || marker.repository != null;
        }
      } catch {
        return true;
      }
      return true;
    }
    const check = (): RetirementTarget => {
      const fresh = createDatabase(db_path);
      try {
        return fresh.write((sql) =>
          custody_retired(sql, identity, requires_registration()) &&
          (has_committed_retirement(sql, expected, identity) ||
            authorize_retirement(sql, expected, now, identity))
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
    checkout_only: boolean,
  ): Promise<RetirementTarget> {
    const target = await prepare_workspace_retirement(path, expected, {
      ...options,
      identity,
      retired_root: join(dirname(filesRoot(db_path, 'ticket')), '.ticket-retired'),
      checkout_only,
    });
    if ('reason' in target) return target;
    let detached = false;
    try {
      const fresh = createDatabase(db_path);
      try {
        return fresh.write((sql) => {
          if (!custody_retired(sql, identity, existsSync(join(path, 'worktree', '.git'))))
            return { reason: 'Local child retirement is not proven' };
          if (!authorize_retirement(sql, expected, now, identity))
            return { reason: 'Ticket is active or no longer eligible' };
          const current = read_workspace_identity(path);
          if (
            current.machine_id !== identity.machine_id ||
            current.workspace_id !== identity.workspace_id
          )
            throw new Error('Physical workspace identity changed');
          detach_workspace(path, target.path, checkout_only);
          detached = true;
          return target;
        });
      } catch (error) {
        if (detached && existsSync(target.path)) {
          if (checkout_only) {
            renameSync(join(target.path, 'worktree'), join(path, 'worktree'));
            renameSync(
              join(target.path, '.panels-workspace.json'),
              join(path, '.panels-workspace.json'),
            );
          } else if (!existsSync(path)) renameSync(target.path, path);
        }
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
        if (
          checkout_only &&
          stat_sync(target.path)?.isDirectory() &&
          !existsSync(join(target.path, 'worktree'))
        ) {
          unlinkSync(join(target.path, '.panels-workspace.json'));
          rmdirSync(target.path);
        }
      }
    }
  }
  return cleanup_local_workspaces(filesRoot(db_path, 'ticket'), snapshot.states, now, {
    ...options,
    active_ticket_ids: snapshot.active,
    retire: (path, state, identity, checkout_only) =>
      options.retire_local_children
        ? options.retire_local_children(state.ticket_id, identity, () =>
            detach(path, state, identity, checkout_only),
          )
        : detach(path, state, identity, checkout_only),
    retirement_is_committed: granted,
  });
}
