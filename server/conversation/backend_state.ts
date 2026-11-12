/** Durable decorations of backend cards share the existing database write door. */
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ConversationBackendKey } from './contracts.ts';
import type { BackendSnapshot } from './snapshot/contracts.ts';
import type {
  BackendUsageResult,
  BackendUsageWindow,
  BackendUsageWindowKind,
  CachedBackendUsage,
} from './backend_usage/contracts.ts';
export class BackendStateStore {
  private readonly database: Database;
  constructor(database: Database) {
    this.database = database;
  }
  read_usage(backend_key: ConversationBackendKey): CachedBackendUsage | null {
    return this.database.read((sql) => read_cached_backend_usage(sql, backend_key));
  }
  keep_successful_usage(result: BackendUsageResult): void {
    if (result.outcome !== 'succeeded' || result.observed_at === null)
      throw new Error('only a successful observed usage result can be kept');
    this.database.write((tx) => {
      tx.run(
        'INSERT INTO backend_usage_snapshots(backend_key,observed_at) VALUES (?,?) ' +
          'ON CONFLICT(backend_key) DO UPDATE SET observed_at=excluded.observed_at',
        [result.backend_key, Math.trunc(result.observed_at!.getTime() / 1000)],
      );
      tx.run('DELETE FROM backend_usage_windows WHERE backend_key=?', [result.backend_key]);
      for (const window of result.windows) {
        tx.run(
          'INSERT INTO backend_usage_windows(backend_key,window_kind,model_id,used_percent,resets_at) ' +
            'VALUES (?,?,?,?,?)',
          [
            result.backend_key,
            window.kind,
            window.model_scope ?? '',
            window.used_percent,
            Math.trunc(window.resets_at.getTime() / 1000),
          ],
        );
      }
    });
  }
  read_disabled_models(backend_key: ConversationBackendKey): ReadonlySet<string> {
    return this.database.read((sql) => disabled_models(sql, backend_key));
  }
  model_is_enabled(backend_key: ConversationBackendKey, model_id: string): boolean {
    return this.database.read((sql) => model_is_enabled(sql, backend_key, model_id));
  }
  write_model_enablement(
    backend_key: ConversationBackendKey,
    model_id: string,
    enabled: boolean,
  ): void {
    this.database.write((tx) => write_model_enablement(tx, backend_key, model_id, enabled));
  }
}
export function read_cached_backend_usage(
  sql: ReadSql,
  backend_key: ConversationBackendKey,
): CachedBackendUsage | null {
  const snapshot = sql.get<{ observed_at: number }>(
    'SELECT observed_at FROM backend_usage_snapshots WHERE backend_key=?',
    [backend_key],
  );
  if (!snapshot) return null;
  const windows = sql.all<{
    window_kind: BackendUsageWindowKind;
    model_id: string;
    used_percent: number;
    resets_at: number;
  }>(
    'SELECT window_kind,model_id,used_percent,resets_at FROM backend_usage_windows ' +
      'WHERE backend_key=? ORDER BY window_kind,model_id',
    [backend_key],
  );
  return {
    observed_at: new Date(snapshot.observed_at * 1000),
    windows: windows.map((row) => ({
      kind: row.window_kind,
      model_scope: row.model_id || null,
      used_percent: row.used_percent,
      resets_at: new Date(row.resets_at * 1000),
    })),
  };
}
export function model_is_enabled(
  sql: ReadSql,
  backend_key: ConversationBackendKey,
  model_id: string,
): boolean {
  const row = sql.get<{ enabled: number }>(
    'SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?',
    [backend_key, model_id],
  );
  return row === undefined || Boolean(row.enabled);
}
export function disabled_models(
  sql: ReadSql,
  backend_key: ConversationBackendKey,
): ReadonlySet<string> {
  return new Set(
    sql
      .all<{ model_id: string }>(
        'SELECT model_id FROM backend_model_enablement WHERE backend_key=? AND enabled=0',
        [backend_key],
      )
      .map((row) => row.model_id),
  );
}
export function require_enabled_model(
  sql: ReadSql,
  backend_key: ConversationBackendKey,
  model_id: string,
): void {
  if (!model_is_enabled(sql, backend_key, model_id))
    throw new PlannerError(
      'validation',
      `Model "${model_id}" is disabled. Choose an enabled model.`,
      { employee_backend: backend_key, employee_launch_model: model_id },
    );
}
export function write_model_enablement(
  tx: TransactionSql,
  backend_key: ConversationBackendKey,
  model_id: string,
  enabled: boolean,
): void {
  tx.run(
    'INSERT INTO backend_model_enablement(backend_key,model_id,enabled) VALUES (?,?,?) ' +
      'ON CONFLICT(backend_key,model_id) DO UPDATE SET enabled=excluded.enabled',
    [backend_key, model_id, Number(enabled)],
  );
}
export function resolve_usage_model_scopes(
  result: BackendUsageResult,
  snapshot: BackendSnapshot,
): BackendUsageResult {
  const windows: BackendUsageWindow[] = [];
  for (const window of result.windows) {
    if (window.model_scope === null) {
      windows.push(window);
      continue;
    }
    const model_id = catalogue_model_id(snapshot, window.model_scope);
    if (model_id !== null) windows.push({ ...window, model_scope: model_id });
  }
  return { ...result, windows };
}
function catalogue_model_id(snapshot: BackendSnapshot, scope: string): string | null {
  const wanted = scope.toLowerCase().trim();
  const exact = snapshot.available_models.filter((model) =>
    [model.model_id.toLowerCase(), (model.display_name ?? '').toLowerCase()].includes(wanted),
  );
  if (exact.length === 1) return exact[0].model_id;
  const family = snapshot.available_models.filter(
    (model) =>
      (model.display_name ?? '').toLowerCase().trimStart().split(/\s+/, 1)[0] === wanted ||
      model.model_id.toLowerCase().split('[', 1)[0] === wanted,
  );
  return family.length === 1 ? family[0].model_id : null;
}

/** Remember only the nonsecret generation refused by a real Codex turn. */
export class DurableCodexLoginRejection {
  private readonly database: Database;
  private readonly now: () => Date;
  constructor(database: Database, now: () => Date = () => new Date()) {
    this.database = database;
    this.now = now;
  }
  remember(
    generation: import('./snapshot/provider_contracts.ts').CodexLoginGeneration | null,
  ): void {
    if (generation === null) return;
    this.database.write((tx) =>
      tx.run(
        'INSERT INTO codex_login_rejection(id,account_id,last_refresh,rejected_at) ' +
          'VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,' +
          'last_refresh=excluded.last_refresh,rejected_at=excluded.rejected_at',
        [generation.account_id, generation.last_refresh, Math.trunc(this.now().getTime() / 1000)],
      ),
    );
  }
  forget(generation: import('./snapshot/provider_contracts.ts').CodexLoginGeneration | null): void {
    if (generation === null) return;
    this.database.write((tx) =>
      tx.run('DELETE FROM codex_login_rejection WHERE account_id=? AND last_refresh=?', [
        generation.account_id,
        generation.last_refresh,
      ]),
    );
  }
  applies_to(
    generation: import('./snapshot/provider_contracts.ts').CodexLoginGeneration | null,
  ): boolean {
    if (generation === null) return false;
    return this.database.read(
      (sql) =>
        sql.get('SELECT 1 FROM codex_login_rejection WHERE account_id=? AND last_refresh=?', [
          generation.account_id,
          generation.last_refresh,
        ]) !== undefined,
    );
  }
}
