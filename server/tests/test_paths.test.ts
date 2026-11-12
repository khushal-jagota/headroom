import { expect, test, vi } from 'vitest';
import { randomUUID } from 'node:crypto';
import { createDatabase, createReadAccess } from '../core/database/index.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import {
  agent_resolve_from_sql,
  configure_conversation_start,
  chief_home_folder,
  manager_home_folder,
} from '../runtime/conversation_start.ts';

test('ambient non-temp database paths cannot open a database or resolve a home', () => {
  const path = `/panels-test-missing-${randomUUID()}/planner.db`;
  vi.stubEnv('PLAN_DB_PATH', path);
  const database = {} as Database;
  const refusal = /Tests can only use databases/;
  try {
    expect(() =>
      agent_resolve_from_sql({} as ReadSql, undefined, { db_path: path, workspace_root: null }),
    ).toThrow(refusal);
    expect(() => createDatabase(process.env.PLAN_DB_PATH!)).toThrow(refusal);
    expect(() => createReadAccess(process.env.PLAN_DB_PATH!)).toThrow(refusal);
    expect(() => chief_home_folder(database)).toThrow('not registered');
    configure_conversation_start(database, { db_path: path, workspace_root: null });
    expect(() => chief_home_folder(database)).toThrow(refusal);
    expect(() => manager_home_folder(database, 'synthetic-manager')).toThrow(refusal);
  } finally {
    vi.unstubAllEnvs();
  }
});
