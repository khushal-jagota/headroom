/** Navigation installation and storage for the injected conversation fact source. */
import type {
  ConversationAttention,
  ConversationSignalPublicationV1,
  SignalBatch,
  SignalDeletion,
  SignalSnapshot,
  SignalCursor,
} from '../conversation/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { CurrentAuthority } from '../../web/src/lib/navigation/contracts.ts';
import type { NavigationRegistry } from './contracts.ts';
import { compact } from './logic/json.ts';
import { NavigationUnavailable } from './logic/errors.ts';
import { SCHEMA_VERSION } from './contracts.ts';

export class SignalInstallationError extends Error {}

function checkedCursor(value: SignalCursor): void {
  if (
    !value.scope ||
    !value.runtime_epoch ||
    !Number.isInteger(value.sequence) ||
    value.sequence < 0
  )
    throw new SignalInstallationError('invalid Signal cursor');
}

function checkedPublication(value: ConversationSignalPublicationV1, epoch: string): void {
  if (
    !value.conversation_id ||
    value.runtime_epoch !== epoch ||
    !Number.isInteger(value.conversation_revision) ||
    value.conversation_revision < 0
  )
    throw new SignalInstallationError('invalid Signal publication fence');
  checkedAttention(value.attention);
}

function putPublication(tx: TransactionSql, value: ConversationSignalPublicationV1): void {
  tx.run(
    `INSERT OR REPLACE INTO navigation_signal_publications
    (conversation_id,runtime_epoch,conversation_revision,attention) VALUES(?,?,?,?)`,
    [
      value.conversation_id,
      value.runtime_epoch,
      String(value.conversation_revision),
      compact(value.attention),
    ],
  );
}

function dirtyPublication(tx: TransactionSql, id: string): void {
  tx.run(
    `INSERT OR IGNORE INTO navigation_dirty(kind,id)
    VALUES('navigation_signal_publications',?)`,
    [id],
  );
}

/** Accept a complete producer snapshot. The caller owns this source transaction. */
export function installSignal(
  tx: TransactionSql,
  snapshot: SignalSnapshot,
  navigation: NavigationRegistry,
): void {
  const version = tx.get<{ schema_version: number }>(
    'SELECT schema_version FROM navigation_meta',
  )?.schema_version;
  if (version !== 7 && version !== SCHEMA_VERSION)
    throw new SignalInstallationError(
      'Signal installation requires prepared or current navigation',
    );
  for (const table of ['projects', 'managers', 'tickets']) {
    if (tx.get("SELECT 1 FROM pragma_table_info(?) WHERE name='priority'", [table]))
      throw new SignalInstallationError('current navigation requires priority removal');
  }
  checkedCursor(snapshot.cursor);
  if (!snapshot.installation_id)
    throw new SignalInstallationError('Signal installation requires an identity');
  const ids = new Set<string>();
  for (const publication of snapshot.values) {
    checkedPublication(publication, snapshot.cursor.runtime_epoch);
    if (ids.has(publication.conversation_id))
      throw new SignalInstallationError('duplicate Signal snapshot conversation');
    ids.add(publication.conversation_id);
  }
  const actual = tx.all<{ conversation_id: string }>(
    'SELECT conversation_id FROM conversations ORDER BY conversation_id',
  );
  if (actual.length !== ids.size || actual.some((row) => !ids.has(row.conversation_id)))
    throw new SignalInstallationError('Signal snapshot is not the complete conversation set');
  tx.run('DELETE FROM navigation_signal_publications');
  for (const publication of snapshot.values) putPublication(tx, publication);
  tx.run(
    `INSERT OR REPLACE INTO navigation_signal_installation
    VALUES(1,?,?,?,?,NULL)`,
    [
      snapshot.installation_id,
      snapshot.cursor.scope,
      snapshot.cursor.runtime_epoch,
      String(snapshot.cursor.sequence),
    ],
  );
  tx.run('UPDATE navigation_meta SET schema_version=? WHERE singleton=1', [SCHEMA_VERSION]);
  navigation.rebuild(tx);
}

/** Store the exact producer boundary before the retained navigation finalizer. */
export function participateSignal(tx: TransactionSql, batch: SignalBatch): void {
  checkedCursor(batch.base_cursor);
  checkedCursor(batch.cursor);
  if (
    batch.cursor.scope !== batch.base_cursor.scope ||
    batch.cursor.runtime_epoch !== batch.base_cursor.runtime_epoch ||
    batch.cursor.sequence !== batch.base_cursor.sequence + 1
  )
    throw new SignalInstallationError('invalid Signal batch boundary');
  const installed = tx.get<Installation & { last_batch: string | null }>(`
    SELECT scope,runtime_epoch,sequence,last_batch
    FROM navigation_signal_installation WHERE singleton=1
  `);
  if (!installed) throw new SignalInstallationError('Signal publication is not installed');
  const body = compact(batch);
  const sameCursor = (cursor: SignalCursor) =>
    installed.scope === cursor.scope &&
    installed.runtime_epoch === cursor.runtime_epoch &&
    Number(installed.sequence) === cursor.sequence;
  if (sameCursor(batch.cursor)) {
    if (installed.last_batch === body) return;
    throw new SignalInstallationError('conflicting Signal batch at installed cursor');
  }
  if (!sameCursor(batch.base_cursor))
    throw new SignalInstallationError('Signal cursor gap or obsolete batch');
  const ids = new Set<string>();
  for (const publication of batch.replacements) {
    checkedPublication(publication, batch.cursor.runtime_epoch);
    if (ids.has(publication.conversation_id))
      throw new SignalInstallationError('duplicate Signal batch conversation');
    ids.add(publication.conversation_id);
  }
  for (const deletion of batch.deletions) {
    if (
      !deletion.conversation_id ||
      deletion.runtime_epoch !== batch.cursor.runtime_epoch ||
      !Number.isInteger(deletion.conversation_revision) ||
      deletion.conversation_revision < 0 ||
      ids.has(deletion.conversation_id)
    )
      throw new SignalInstallationError('invalid or duplicate Signal deletion');
    ids.add(deletion.conversation_id);
  }
  for (const candidate of [...batch.replacements, ...batch.deletions]) {
    const previous = tx.get<{ runtime_epoch: string; conversation_revision: string }>(
      `
      SELECT runtime_epoch,conversation_revision FROM navigation_signal_publications
      WHERE conversation_id=?
    `,
      [candidate.conversation_id],
    );
    if (
      previous &&
      (previous.runtime_epoch !== candidate.runtime_epoch ||
        Number(previous.conversation_revision) >= candidate.conversation_revision)
    )
      throw new SignalInstallationError('obsolete Signal conversation fence');
  }
  for (const publication of batch.replacements) {
    putPublication(tx, publication);
    dirtyPublication(tx, publication.conversation_id);
  }
  for (const deletion of batch.deletions) {
    tx.run(
      `INSERT OR REPLACE INTO navigation_signal_publications
      VALUES(?,?,?,NULL)`,
      [deletion.conversation_id, deletion.runtime_epoch, String(deletion.conversation_revision)],
    );
    dirtyPublication(tx, deletion.conversation_id);
  }
  for (const association of batch.associations) {
    for (const id of [association.before_conversation_id, association.after_conversation_id]) {
      if (id !== null)
        tx.run("INSERT OR IGNORE INTO navigation_dirty VALUES('conversations',?)", [id]);
    }
  }
  tx.run(
    `UPDATE navigation_signal_installation
    SET sequence=?,last_batch=? WHERE singleton=1`,
    [String(batch.cursor.sequence), body],
  );
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
