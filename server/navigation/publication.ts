/** Navigation storage and delivery permits for the injected conversation fact source. */
import { randomUUID } from 'node:crypto';
import type { ConversationAttention, ConversationSignalPublicationV1, SignalBatch, SignalDeletion } from '../conversation/contracts.ts';
import type { CommitStep, Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { CurrentAuthority } from '../../web/src/lib/navigation/contracts.ts';
import type { NavigationAuthority, NavigationRegistry, PublicationFactory } from './contracts.ts';
import { compact } from './logic/json.ts';
import { NavigationUnavailable } from './logic/errors.ts';
interface StoredPublication { conversation_id: string; runtime_epoch: string; conversation_revision: string; attention: string | null }
interface Installation { installation_id: string; scope: string; runtime_epoch: string; sequence: string }
function checkedAttention(value: ConversationAttention): void {
  if (Object.keys(value).sort().join(',') !== 'awaiting_answer,last_turn_failed,signal'
      || Object.keys(value.signal).sort().join(',') !== 'message_for_owner,working'
      || [value.signal.working, value.signal.message_for_owner, value.awaiting_answer, value.last_turn_failed].some(item => typeof item !== 'boolean')) {
    throw new Error('Signal publication requires complete attention');
  }
}
/** The source callback supplies facts only. All revision and permit state lives here. */
export function createNavigationPublication(factory: PublicationFactory, navigation: NavigationRegistry) {
  const scope = randomUUID(), runtimeEpoch = randomUUID(), installationId = randomUUID();
  const deliverySessionId = randomUUID().replaceAll('-', '');
  let current = false, closed = false;
  const generation = 1;
  const watchers = new Set<() => void>();
  function validate(value: CurrentAuthority): void {
    if (!current || closed || value.delivery_session_id !== deliverySessionId
        || value.availability.state !== 'current' || value.availability.reason !== null
        || value.availability.generation !== String(generation)
        || value.permit.scope !== scope || value.permit.runtime_epoch !== runtimeEpoch
        || value.permit.installation_id !== installationId || value.permit.generation !== String(generation)) {
      throw new NavigationUnavailable('navigation delivery generation changed');
    }
  }
  function validateSnapshot(sql: ReadSql, value: CurrentAuthority): void {
    const installed = sql.get<Installation>('SELECT installation_id,scope,runtime_epoch,sequence FROM navigation_signal_installation WHERE singleton=1');
    if (!installed || installed.installation_id !== value.permit.installation_id || installed.scope !== value.permit.scope || installed.runtime_epoch !== value.permit.runtime_epoch) throw new NavigationUnavailable('Signal navigation installation changed');
  }
  const authority: NavigationAuthority = {
    capture() {
      const result: CurrentAuthority = {
        delivery_session_id: deliverySessionId,
        availability: { state: 'current', generation: String(generation), reason: null },
        permit: { scope, runtime_epoch: runtimeEpoch, installation_id: installationId, generation: String(generation) },
      };
      validate(result);
      return result;
    },
    validate, validateSnapshot,
    watch(value, invalidate) {
      validate(value); watchers.add(invalidate);
      return () => { watchers.delete(invalidate); };
    },
  };
  function collect(tx: TransactionSql): Map<string, ConversationAttention> {
    const values = new Map<string, ConversationAttention>();
    const source = factory((sourceTx, id, attention) => {
      if (sourceTx !== tx) throw new Error('Signal publication must use its source transaction');
      checkedAttention(attention);
      if (!id || values.has(id)) throw new Error('duplicate or invalid Signal conversation');
      values.set(id, { ...attention, signal: { ...attention.signal } });
      return undefined;
    });
    const result: unknown = source(tx);
    if (result !== null && (typeof result === 'object' || typeof result === 'function') && typeof (result as { then?: unknown }).then === 'function') {
      if (result instanceof Promise) void result.catch(() => {});
      throw new TypeError('Signal publication callbacks must be synchronous');
    }
    const actual = tx.all<{ conversation_id: string }>('SELECT conversation_id FROM conversations ORDER BY conversation_id');
    if (actual.length !== values.size || actual.some(row => !values.has(row.conversation_id))) throw new Error('Signal snapshot is not the complete conversation set');
    return values;
  }
  const put = (tx: TransactionSql, id: string, revision: number, attention: ConversationAttention | null) => {
    tx.run('INSERT OR REPLACE INTO navigation_signal_publications(conversation_id,runtime_epoch,conversation_revision,attention) VALUES(?,?,?,?)', [id, runtimeEpoch, String(revision), attention === null ? null : compact(attention)]);
  };
  const dirty = (tx: TransactionSql, id: string) => tx.run("INSERT OR IGNORE INTO navigation_dirty(kind,id) VALUES('navigation_signal_publications',?)", [id]);
  const commitStep: CommitStep = tx => {
    if (closed) throw new NavigationUnavailable('Signal publication is closed');
    if (!current) return;
    const installation = tx.get<Installation>('SELECT installation_id,scope,runtime_epoch,sequence FROM navigation_signal_installation WHERE singleton=1');
    if (!installation || installation.installation_id !== installationId || installation.scope !== scope || installation.runtime_epoch !== runtimeEpoch) throw new NavigationUnavailable('Signal navigation installation changed');
    const values = collect(tx);
    const previous = new Map(tx.all<StoredPublication>('SELECT conversation_id,runtime_epoch,conversation_revision,attention FROM navigation_signal_publications ORDER BY conversation_id').map(row => [row.conversation_id, row]));
    const replacements: ConversationSignalPublicationV1[] = [], deletions: SignalDeletion[] = [];
    for (const [id, attention] of values) {
      const old = previous.get(id);
      if (old && old.runtime_epoch !== runtimeEpoch) throw new NavigationUnavailable('obsolete Signal conversation fence');
      if (old?.attention === compact(attention)) continue;
      const revision = old ? Number(old.conversation_revision) + 1 : 0;
      replacements.push({ conversation_id: id, runtime_epoch: runtimeEpoch, conversation_revision: revision, attention });
      put(tx, id, revision, attention); dirty(tx, id);
    }
    for (const [id, old] of previous) if (!values.has(id) && old.attention !== null) {
      const revision = Number(old.conversation_revision) + 1;
      deletions.push({ conversation_id: id, runtime_epoch: runtimeEpoch, conversation_revision: revision });
      put(tx, id, revision, null); dirty(tx, id);
    }
    if (replacements.length || deletions.length) {
      const before = Number(installation.sequence);
      const batch: SignalBatch = { base_cursor: { scope, runtime_epoch: runtimeEpoch, sequence: before }, cursor: { scope, runtime_epoch: runtimeEpoch, sequence: before + 1 }, replacements, deletions, associations: [] };
      tx.run('UPDATE navigation_signal_installation SET sequence=?,last_batch=? WHERE singleton=1', [String(before + 1), compact(batch)]);
    }
  };
  return {
    authority,
    commitStep,
    initialize(database: Database): void {
      if (closed || current) throw new Error('Signal navigation is already initialized or closed');
      database.write(tx => {
        tx.run('CREATE TABLE IF NOT EXISTS navigation_signal_installation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),installation_id TEXT NOT NULL,scope TEXT NOT NULL,runtime_epoch TEXT NOT NULL,sequence TEXT NOT NULL,last_batch TEXT)');
        tx.run('CREATE TABLE IF NOT EXISTS navigation_signal_publications(conversation_id TEXT PRIMARY KEY,runtime_epoch TEXT NOT NULL,conversation_revision TEXT NOT NULL,attention TEXT)');
        const values = collect(tx);
        tx.run('DELETE FROM navigation_signal_publications');
        for (const [id, attention] of values) put(tx, id, 0, attention);
        tx.run('INSERT OR REPLACE INTO navigation_signal_installation(singleton,installation_id,scope,runtime_epoch,sequence,last_batch) VALUES(1,?,?,?,?,NULL)', [installationId, scope, runtimeEpoch, '0']);
        navigation.rebuild(tx);
      }, { changeSignal: 'silent' });
      current = true;
    },
    close(): void {
      current = false; closed = true;
      for (const watcher of [...watchers]) watcher();
      watchers.clear();
    },
  };
}
