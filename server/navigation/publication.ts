/** Navigation installation and storage for the injected conversation fact source. */
import type {
  ConversationAttention,
  ConversationSignalPublicationV1,
  SignalBatch,
  SignalDeletion,
} from '../conversation/contracts.ts';
import type { CommitStep, Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { CurrentAuthority } from '../../web/src/lib/navigation/contracts.ts';
import type { NavigationAuthority, NavigationRegistry, PublicationFactory } from './contracts.ts';
import { compact } from './logic/json.ts';
import { NavigationUnavailable } from './logic/errors.ts';
interface StoredPublication {
  conversation_id: string;
  runtime_epoch: string;
  conversation_revision: string;
  attention: string | null;
}
interface Installation {
  installation_id: string;
  scope: string;
  runtime_epoch: string;
  sequence: string;
}
function checkedAttention(value: ConversationAttention): void {
  if (
    Object.keys(value).sort().join(',') !== 'awaiting_answer,last_turn_failed,signal' ||
    Object.keys(value.signal).sort().join(',') !== 'message_for_owner,working' ||
    [
      value.signal.working,
      value.signal.message_for_owner,
      value.awaiting_answer,
      value.last_turn_failed,
    ].some((item) => typeof item !== 'boolean')
  ) {
    throw new Error('Signal publication requires complete attention');
  }
}
/** SQL installation checks run inside the same read snapshot as navigation content. */
export function validateInstallation(sql: ReadSql, value: CurrentAuthority): void {
  const installed = sql.get<Installation>(
    'SELECT installation_id,scope,runtime_epoch,sequence FROM navigation_signal_installation WHERE singleton=1',
  );
  if (
    !installed ||
    installed.installation_id !== value.permit.installation_id ||
    installed.scope !== value.permit.scope ||
    installed.runtime_epoch !== value.permit.runtime_epoch
  )
    throw new NavigationUnavailable('Signal navigation installation changed');
}
/** The source callback supplies facts. Producer identity comes from injected authority. */
export function createNavigationPublication(
  factory: PublicationFactory,
  navigation: NavigationRegistry,
) {
  let installed: { authority: NavigationAuthority; token: CurrentAuthority } | undefined;
  let closed = false;
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
    if (
      result !== null &&
      (typeof result === 'object' || typeof result === 'function') &&
      typeof (result as { then?: unknown }).then === 'function'
    ) {
      if (result instanceof Promise) void result.catch(() => {});
      throw new TypeError('Signal publication callbacks must be synchronous');
    }
    const actual = tx.all<{ conversation_id: string }>(
      'SELECT conversation_id FROM conversations ORDER BY conversation_id',
    );
    if (actual.length !== values.size || actual.some((row) => !values.has(row.conversation_id)))
      throw new Error('Signal snapshot is not the complete conversation set');
    return values;
  }
  const put = (
    tx: TransactionSql,
    runtimeEpoch: string,
    id: string,
    revision: number,
    attention: ConversationAttention | null,
  ) => {
    tx.run(
      'INSERT OR REPLACE INTO navigation_signal_publications(conversation_id,runtime_epoch,conversation_revision,attention) VALUES(?,?,?,?)',
      [id, runtimeEpoch, String(revision), attention === null ? null : compact(attention)],
    );
  };
  const dirty = (tx: TransactionSql, id: string) =>
    tx.run(
      "INSERT OR IGNORE INTO navigation_dirty(kind,id) VALUES('navigation_signal_publications',?)",
      [id],
    );
  const commitStep: CommitStep = (tx) => {
    if (closed) throw new NavigationUnavailable('Signal publication is closed');
    if (!installed) return;
    installed.authority.validate(installed.token);
    const {
      scope,
      runtime_epoch: runtimeEpoch,
      installation_id: installationId,
    } = installed.token.permit;
    const installation = tx.get<Installation>(
      'SELECT installation_id,scope,runtime_epoch,sequence FROM navigation_signal_installation WHERE singleton=1',
    );
    if (
      !installation ||
      installation.installation_id !== installationId ||
      installation.scope !== scope ||
      installation.runtime_epoch !== runtimeEpoch
    )
      throw new NavigationUnavailable('Signal navigation installation changed');
    const values = collect(tx);
    const previous = new Map(
      tx
        .all<StoredPublication>(
          'SELECT conversation_id,runtime_epoch,conversation_revision,attention FROM navigation_signal_publications ORDER BY conversation_id',
        )
        .map((row) => [row.conversation_id, row]),
    );
    const replacements: ConversationSignalPublicationV1[] = [],
      deletions: SignalDeletion[] = [];
    for (const [id, attention] of values) {
      const old = previous.get(id);
      if (old && old.runtime_epoch !== runtimeEpoch)
        throw new NavigationUnavailable('obsolete Signal conversation fence');
      if (old?.attention === compact(attention)) continue;
      const revision = old ? Number(old.conversation_revision) + 1 : 0;
      replacements.push({
        conversation_id: id,
        runtime_epoch: runtimeEpoch,
        conversation_revision: revision,
        attention,
      });
      put(tx, runtimeEpoch, id, revision, attention);
      dirty(tx, id);
    }
    for (const [id, old] of previous)
      if (!values.has(id) && old.attention !== null) {
        const revision = Number(old.conversation_revision) + 1;
        deletions.push({
          conversation_id: id,
          runtime_epoch: runtimeEpoch,
          conversation_revision: revision,
        });
        put(tx, runtimeEpoch, id, revision, null);
        dirty(tx, id);
      }
    if (replacements.length || deletions.length) {
      const before = Number(installation.sequence);
      const batch: SignalBatch = {
        base_cursor: { scope, runtime_epoch: runtimeEpoch, sequence: before },
        cursor: { scope, runtime_epoch: runtimeEpoch, sequence: before + 1 },
        replacements,
        deletions,
        associations: [],
      };
      tx.run(
        'UPDATE navigation_signal_installation SET sequence=?,last_batch=? WHERE singleton=1',
        [String(before + 1), compact(batch)],
      );
    }
  };
  return {
    commitStep,
    initialize(database: Database, authority: NavigationAuthority): void {
      if (closed || installed)
        throw new Error('Signal navigation is already initialized or closed');
      const token = authority.capture();
      const { scope, runtime_epoch: runtimeEpoch, installation_id: installationId } = token.permit;
      database.write(
        (tx) => {
          tx.run(
            'CREATE TABLE IF NOT EXISTS navigation_signal_installation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),installation_id TEXT NOT NULL,scope TEXT NOT NULL,runtime_epoch TEXT NOT NULL,sequence TEXT NOT NULL,last_batch TEXT)',
          );
          tx.run(
            'CREATE TABLE IF NOT EXISTS navigation_signal_publications(conversation_id TEXT PRIMARY KEY,runtime_epoch TEXT NOT NULL,conversation_revision TEXT NOT NULL,attention TEXT)',
          );
          const values = collect(tx);
          tx.run('DELETE FROM navigation_signal_publications');
          for (const [id, attention] of values) put(tx, runtimeEpoch, id, 0, attention);
          tx.run(
            'INSERT OR REPLACE INTO navigation_signal_installation(singleton,installation_id,scope,runtime_epoch,sequence,last_batch) VALUES(1,?,?,?,?,NULL)',
            [installationId, scope, runtimeEpoch, '0'],
          );
          tx.run('UPDATE navigation_meta SET schema_version=10 WHERE singleton=1');
          navigation.rebuild(tx);
        },
        { changeSignal: 'silent' },
      );
      authority.validate(token);
      installed = { authority, token };
    },
    close(): void {
      closed = true;
      installed = undefined;
    },
  };
}
