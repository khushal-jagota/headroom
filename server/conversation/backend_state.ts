/** Durable decorations of backend cards share the existing database write door. */
import type { BackendInstanceId } from '../drivers/core/identity.ts';
import { built_in_driver_of } from '../drivers/built_in_manifests.ts';
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { HOME_MACHINE_ID, legacy_machine_id } from '../execution_identity.ts';
import { read_presence } from '../executor/vps_presence_store.ts';
import { snapshot_for_wire } from '../executor/backend_snapshot.ts';
import { conversation_machine_id } from '../machines/conversation_machine.ts';
import type { BackendSnapshot } from './snapshot/contracts.ts';
import type { BackendUsageResult, CachedBackendUsage } from './backend_usage/contracts.ts';
import { decode_usage_windows } from './backend_usage/machine.ts';
export class BackendStateStore {
  private readonly database: Database;
  private readonly capability_sources = new Map<string, string>();
  constructor(database: Database) {
    this.database = database;
    database.read((sql) => {
      for (const row of sql.all<{ machine_id: string; backend_key: string }>(
        'SELECT machine_id,backend_key FROM machine_backend_state WHERE capabilities_json IS NOT NULL',
      ))
        this.capability_sources.set(
          JSON.stringify([row.machine_id, row.backend_key]),
          capability_source(sql, row.machine_id, row.backend_key),
        );
    });
  }
  read_usage(
    backend_key: BackendInstanceId,
    machine_id = HOME_MACHINE_ID,
  ): CachedBackendUsage | null {
    return this.database.read((sql) => backend_card_state(sql).read_usage(backend_key, machine_id));
  }

  /** The account the machine's saved card names for the backend. Null before any card is saved. */
  read_saved_identity(
    machine_id: string,
    backend_key: BackendInstanceId,
  ): BackendSnapshot['identity'] {
    const row = this.database.read((sql) =>
      sql.get<{ snapshot_json: string }>(
        'SELECT snapshot_json FROM machine_backend_snapshots WHERE machine_id=? AND backend_key=?',
        [snapshot_machine_id(machine_id), backend_key],
      ),
    );
    return row ? ((JSON.parse(row.snapshot_json) as BackendSnapshot).identity ?? null) : null;
  }

  read_machine_capabilities(
    machine_id: string,
    backend_key: BackendInstanceId,
  ): { can_sign_in: boolean; reports_allowance_usage: boolean } | null {
    const row = this.database.read((sql) =>
      sql.get<{ capabilities_json: string | null }>(
        'SELECT capabilities_json FROM machine_backend_state WHERE machine_id=? AND backend_key=?',
        [machine_id, backend_key],
      ),
    );
    return row?.capabilities_json ? JSON.parse(row.capabilities_json) : null;
  }
  capability_source(machine_id: string, backend_key: BackendInstanceId): string {
    return this.database.read((sql) => capability_source(sql, machine_id, backend_key));
  }
  capabilities_current(
    machine_id: string,
    backend_key: BackendInstanceId,
    source: string,
  ): boolean {
    return this.capability_sources.get(JSON.stringify([machine_id, backend_key])) === source;
  }
  keep_machine_capabilities(
    machine_id: string,
    backend_key: BackendInstanceId,
    value: { can_sign_in: boolean; reports_allowance_usage: boolean },
    source = this.capability_source(machine_id, backend_key),
  ): boolean {
    const existing = this.read_machine_capabilities(machine_id, backend_key);
    if (
      existing !== null &&
      existing.can_sign_in === value.can_sign_in &&
      existing.reports_allowance_usage === value.reports_allowance_usage
    ) {
      if (this.capability_source(machine_id, backend_key) !== source) return false;
      this.capability_sources.set(JSON.stringify([machine_id, backend_key]), source);
      return true;
    }
    const kept = this.database.write((tx) => {
      if (capability_source(tx, machine_id, backend_key) !== source) return false;
      tx.run(
        'INSERT INTO machine_backend_state(machine_id,backend_key,capabilities_json) VALUES (?,?,?) ON CONFLICT(machine_id,backend_key) DO UPDATE SET capabilities_json=excluded.capabilities_json WHERE capabilities_json IS NOT excluded.capabilities_json',
        [machine_id, backend_key, JSON.stringify(value)],
      );
      return true;
    });
    if (kept) this.capability_sources.set(JSON.stringify([machine_id, backend_key]), source);
    return kept;
  }
  /** Each machine keeps its own last observation, the home machine included. */
  keep_successful_usage(result: BackendUsageResult, machine_id = HOME_MACHINE_ID): void {
    if (result.outcome !== 'succeeded' || result.observed_at === null)
      throw new Error('only a successful observed usage result can be kept');
    this.database.write((tx) => {
      tx.run(
        'INSERT INTO machine_backend_state(machine_id,backend_key,usage_json) VALUES (?,?,?) ON CONFLICT(machine_id,backend_key) DO UPDATE SET usage_json=excluded.usage_json',
        [
          machine_id,
          result.backend_key,
          JSON.stringify({ observed_at: result.observed_at, windows: result.windows }),
        ],
      );
    });
  }
  read_disabled_models(backend_key: BackendInstanceId): ReadonlySet<string> {
    return this.database.read((sql) => disabled_models(sql, backend_key));
  }
  model_is_enabled(backend_key: BackendInstanceId, model_id: string): boolean {
    return this.database.read((sql) => model_is_enabled(sql, backend_key, model_id));
  }
  /** The model is checked against the list of the machine that runs the conversation. */
  delivery_model_refusal(
    backend_key: BackendInstanceId,
    model_id: string,
    conversation_id: string,
  ): string | null {
    return this.database.read((sql) =>
      model_refusal(
        sql,
        backend_key,
        model_id,
        conversation_machine_id(sql, conversation_id) ?? HOME_MACHINE_ID,
      ),
    );
  }
  keep_vps_model_list(snapshot: BackendSnapshot): void {
    this.database.write((tx) => save_machine_model_list(tx, HOME_MACHINE_ID, snapshot));
  }
  write_model_enablement(backend_key: BackendInstanceId, model_id: string, enabled: boolean): void {
    this.database.write((tx) => write_model_enablement(tx, backend_key, model_id, enabled));
  }
}
export function model_is_enabled(
  sql: ReadSql,
  backend_key: BackendInstanceId,
  model_id: string,
): boolean {
  const row = sql.get<{ enabled: number }>(
    'SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?',
    [backend_key, model_id],
  );
  return row === undefined || Boolean(row.enabled);
}
export function disabled_models(sql: ReadSql, backend_key: BackendInstanceId): ReadonlySet<string> {
  return new Set(
    sql
      .all<{ model_id: string }>(
        'SELECT model_id FROM backend_model_enablement WHERE backend_key=? AND enabled=0',
        [backend_key],
      )
      .map((row) => row.model_id),
  );
}
/** Persist one machine's list and retire switch-offs only with complete lists everywhere. */
export function save_machine_model_list(
  tx: TransactionSql,
  machine: string,
  snapshot: BackendSnapshot,
): void {
  tx.run(
    'INSERT INTO machine_backend_snapshots VALUES (?,?,?) ' +
      'ON CONFLICT(machine_id,backend_key) DO UPDATE SET snapshot_json=excluded.snapshot_json ' +
      'WHERE snapshot_json != excluded.snapshot_json',
    [
      snapshot_machine_id(machine),
      snapshot.backend_key,
      JSON.stringify(snapshot_for_wire(snapshot)),
    ],
  );
  if (!snapshot.models_complete) return;
  const offered = new Set<string>();
  for (const { machine_id } of tx.all<{ machine_id: string }>('SELECT machine_id FROM machines')) {
    const listed = offered_model_ids(tx, machine_id, snapshot.backend_key);
    if (listed === null) return;
    for (const model of listed) offered.add(model);
  }
  for (const row of tx.all<{ model_id: string }>(
    'SELECT model_id FROM backend_model_enablement WHERE backend_key=?',
    [snapshot.backend_key],
  )) {
    if (!offered.has(row.model_id))
      tx.run('DELETE FROM backend_model_enablement WHERE backend_key=? AND model_id=?', [
        snapshot.backend_key,
        row.model_id,
      ]);
  }
}
export function offered_model_ids(
  sql: ReadSql,
  machine: string,
  backend_key: BackendInstanceId,
): readonly string[] | null {
  const row = sql.get<{ snapshot_json: string }>(
    'SELECT snapshot_json FROM machine_backend_snapshots WHERE machine_id=? AND backend_key=?',
    [snapshot_machine_id(machine), backend_key],
  );
  if (!row) return null;
  const snapshot = JSON.parse(row.snapshot_json) as BackendSnapshot;
  if (!snapshot.models_complete || !snapshot.available_models?.length) return null;
  return snapshot.available_models.map((model) => String(model.model_id));
}
function is_offered(
  backend_key: BackendInstanceId,
  model_id: string,
  offered: readonly string[],
): boolean {
  const rule = built_in_driver_of(backend_key)?.models?.is_offered;
  return rule === undefined ? offered.includes(model_id) : rule(model_id, offered);
}
export function model_refusal(
  sql: ReadSql,
  backend_key: BackendInstanceId,
  model_id: string,
  machine: string,
): string | null {
  if (!model_is_enabled(sql, backend_key, model_id))
    return `Model "${model_id}" is disabled. Choose an enabled model.`;
  const offered = offered_model_ids(sql, machine, backend_key);
  if (offered === null || is_offered(backend_key, model_id, offered)) return null;
  const disabled = disabled_models(sql, backend_key);
  const choices = offered.filter((model) => !disabled.has(model)).join(', ');
  return `Model "${model_id}" is not offered by ${backend_key} on ${machine_name(sql, machine)}. Choose one of: ${choices}.`;
}
function snapshot_machine_id(machine: string): string {
  return machine === 'vps' || machine === 'macos' ? legacy_machine_id(machine) : machine;
}
function machine_name(sql: ReadSql, machine: string): string {
  const machine_id = snapshot_machine_id(machine);
  const row = sql.get<{ name: string }>('SELECT name FROM machines WHERE machine_id=?', [
    machine_id,
  ]);
  if (!row) throw new PlannerError('validation', 'Unknown machine destination', { machine_id });
  return row.name;
}
export function require_usable_model(
  sql: ReadSql,
  backend_key: BackendInstanceId,
  model_id: string,
  machine: string,
): void {
  const refusal = model_refusal(sql, backend_key, model_id, machine);
  if (refusal !== null)
    throw new PlannerError('validation', refusal, {
      employee_backend: backend_key,
      employee_launch_model: model_id,
    });
}
export function write_model_enablement(
  tx: TransactionSql,
  backend_key: BackendInstanceId,
  model_id: string,
  enabled: boolean,
): void {
  tx.run(
    'INSERT INTO backend_model_enablement(backend_key,model_id,enabled) VALUES (?,?,?) ' +
      'ON CONFLICT(backend_key,model_id) DO UPDATE SET enabled=excluded.enabled',
    [backend_key, model_id, Number(enabled)],
  );
}

/** A reconnect or a new provider snapshot invalidates capability observations, not unrelated writes. */
export function capability_source(sql: ReadSql, machine_id: string, backend_key: string): string {
  const presence = read_presence(sql, machine_id);
  return JSON.stringify([
    presence?.executor_incarnation ?? null,
    presence?.continuous_since_ms ?? null,
    presence?.identity?.connection_generation ?? null,
    presence?.lease?.driver_kinds ?? [],
    sql.get<{ snapshot_json: string }>(
      'SELECT snapshot_json FROM machine_backend_snapshots WHERE machine_id=? AND backend_key=?',
      [machine_id, backend_key],
    )?.snapshot_json ?? null,
  ]);
}
export function backend_card_state(sql: ReadSql) {
  return {
    read_usage(
      backend_key: BackendInstanceId,
      machine_id = HOME_MACHINE_ID,
    ): CachedBackendUsage | null {
      const row = sql.get<{ usage_json: string | null }>(
        'SELECT usage_json FROM machine_backend_state WHERE machine_id=? AND backend_key=?',
        [machine_id, backend_key],
      );
      if (!row?.usage_json) return null;
      const value = JSON.parse(row.usage_json);
      const windows = decode_usage_windows(value.windows);
      // An observation saved in an older shape counts as none until the next refresh.
      if (windows === null) return null;
      return { observed_at: new Date(value.observed_at), windows };
    },
    read_disabled_models: (backend_key: BackendInstanceId) => disabled_models(sql, backend_key),
  };
}
