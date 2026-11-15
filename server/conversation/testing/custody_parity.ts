/** One direct SQLite proof of custody transitions, with synthetic inspection evidence. */
import { createDatabase } from '../../core/database/index.ts';
import { ConversationStore } from '../record/data/store.ts';
import { deployment_participants } from '../deploy_interruptions/participants.ts';
import type { LocalChildCustodyInspection } from '../local_child_custody_contracts.ts';

export async function run(path: string, journal: string): Promise<Record<string, unknown>> {
  const database = createDatabase(path);
  let absent = false;
  let change_during_inspection = false;
  let startup = false;
  const inspection: LocalChildCustodyInspection = {
    async host_boot_identity() {
      return { host: 'host', boot: 'boot' };
    },
    async retirement_row_proven(row) {
      if (startup && row.conversation_id === 'startup-blocked') return false;
      if (change_during_inspection || (startup && row.conversation_id === 'startup-changed'))
        database.write((tx) =>
          tx.run('UPDATE conversation_child_custody SET generation=? WHERE conversation_id=?', [
            'replacement',
            row.conversation_id,
          ]),
        );
      return row.state === 'retired' || absent;
    },
    async stop_recorded_child() {
      return absent;
    },
    async inspect_supervision() {
      return absent;
    },
    async stop_supervision() {
      return absent;
    },
  };
  const store = new ConversationStore(database, {
    integer_now: () => 100,
    local_child_custody: inspection,
  });
  const identity = { pid: 42, birth: 'birth', boot: 'boot', unit: null, supervision_record: null };
  const points: unknown[] = [];
  const queries = {
    custody: 'SELECT * FROM conversation_child_custody ORDER BY conversation_id',
    facts: 'SELECT * FROM conversation_lifecycle_facts ORDER BY conversation_id,kind,identity',
    bindings: 'SELECT * FROM automatic_start_bindings ORDER BY conversation_id,identity',
    accounts: 'SELECT * FROM deploy_interruption_accounts ORDER BY capture_id',
    interruptions: 'SELECT * FROM deploy_interruptions ORDER BY interruption_id',
  };
  const snapshot = (names: readonly (keyof typeof queries)[]) =>
    database.read((sql) => Object.fromEntries(names.map((name) => [name, sql.all(queries[name])])));
  function capture(label: string, result: unknown): void {
    points.push({
      label,
      result,
      ...snapshot(['custody', 'facts', 'bindings']),
      settled: store.settled_recorded_turn('pending'),
    });
  }

  try {
    await store.record_child_custody('started', 'old');
    await store.record_child_custody('started', 'new');
    let refused = false;
    try {
      await store.record_child_custody('started', 'old', identity);
    } catch {
      refused = true;
    }
    capture('stale-start', refused);
    await store.record_child_custody('started', 'new', identity);
    capture('started', await store.check_local_child_started('started', 'new', 1));
    database.write((tx) =>
      tx.run(
        "UPDATE conversation_child_custody SET occurrence_id='wrong' WHERE conversation_id='started'",
      ),
    );
    capture('wrong-occurrence', await store.check_local_child_started('started', 'new', 1));
    database.write((tx) =>
      tx.run(
        "UPDATE conversation_child_custody SET occurrence_id='started-occurrence' WHERE conversation_id='started'",
      ),
    );
    await store.record_child_custody('pending', 'pending-generation');
    await store.record_child_custody('quiet', 'quiet-generation');
    let wrong_owner = false;
    try {
      await store.record_child_supervision('quiet', 'quiet-generation', journal);
    } catch {
      wrong_owner = true;
    }
    capture('wrong-journal-owner', wrong_owner);
    await store.record_child_supervision('pending', 'pending-generation', journal);
    capture(
      'pending-unknown',
      await store.settle_recorded_source('pending', { inspect_only: true }),
    );
    absent = true;
    capture(
      'pending-undelivered',
      await store.settle_recorded_source('pending', { inspect_only: true }),
    );
    await store.record_child_custody('quiet', 'quiet-generation', identity);
    change_during_inspection = true;
    capture(
      'changed-during-inspection',
      await store.retire_idle_recorded_source('quiet', { inspect_only: true }),
    );
    change_during_inspection = false;
    capture(
      'quiet-retired',
      await store.retire_idle_recorded_source('quiet', { inspect_only: true }),
    );
    capture(
      'started-retired',
      await store.settle_recorded_source('started', { inspect_only: true }),
    );
    capture('retirement-proof', await store.recorded_source_retirement_proven('started'));
    for (const id of [
      'startup-blocked',
      'startup-proven',
      'startup-changed',
      'startup-mismatch',
      'startup-manifest',
      'startup-unavailable',
    ]) {
      await store.record_child_custody(id, id);
      await store.record_child_custody(id, id, identity);
    }
    database.write((tx) => {
      tx.run(
        "UPDATE conversation_child_custody SET occurrence_id='wrong' WHERE conversation_id='startup-mismatch'",
      );
      tx.run("DELETE FROM conversation_child_custody WHERE conversation_id='startup-legacy'");
      tx.run(
        "UPDATE conversation_lifecycle_facts SET status='unavailable' WHERE conversation_id='startup-unavailable' AND kind='turn'",
      );
      tx.run(
        "UPDATE conversation_lifecycle_facts SET status='unknown' WHERE conversation_id IN ('startup-unavailable','pending') AND kind='attempt' AND operation='prompt'",
      );
    });
    startup = true;
    const restored = new ConversationStore(database, {
      integer_now: () => 100,
      local_child_custody: inspection,
      ...deployment_participants(path),
    });
    await restored.prepare_reconciliation(new Map());
    database.write((tx) => restored.reconcile_in_transaction(tx, new Map()), {
      changeSignal: 'silent',
    });
    const restart = {
      blocked: [...restored.blocked_local_conversations].sort(),
      ...snapshot(['facts', 'custody', 'accounts', 'interruptions']),
      epoch_changed: database.read(
        (sql) =>
          sql.get<{ epoch: string }>(
            'SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1',
          )!.epoch !== 'epoch',
      ),
    };
    return { points, restart };
  } finally {
    await store.finish_writes();
    database.close();
  }
}
