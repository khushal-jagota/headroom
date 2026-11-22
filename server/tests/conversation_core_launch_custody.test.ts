/** A driver that promises supervised launches is held to it before its child takes a prompt.
 * Core unit: the real record and the shared scripted process, through the core fixture.
 */
import { expect, test, vi } from 'vitest';
import { performance } from 'node:perf_hooks';
import { setup } from './conversation_core_fixture.ts';
import {
  PromptDeliveryRefused,
  PromptDeliveryRefusalReason,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import { LocalChildCustody } from '../conversation/child_custody.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';

// Risk: core changes recreate server-owned process records behind machine dispatch.
// Existing coverage checked native launch writes, not their absence at the record boundary.
test('a new conversation creates no server process record or native custody hooks', async () => {
  const f = setup();
  try {
    await f.start();
    expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
    expect(f.database.read((sql) => sql.all('SELECT * FROM conversation_child_custody'))).toEqual(
      [],
    );
    const state = f.core.conversations.get('core')!;
    expect(state.child_event_sink?.record_local_child_custody).toBeUndefined();
    expect(state.child_event_sink?.record_local_child_supervision).toBeUndefined();
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

// Risk: an old server's idle local process still holds a non-Ticket conversation's vendor
// session when a new turn starts a second child (through machine code) on it. Gap: only a kill
// or a restart judged the recorded process; a new turn outside a Ticket did not.
test('outside a Ticket, a new turn starts no child until the recorded process is retired', async () => {
  const f = setup({ role_actor: 'chief' });
  await f.start();
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  // The old server died with its agent idle and alive, so the record holds a started process.
  f.database.write((tx) =>
    tx.run(
      "INSERT INTO conversation_child_custody(conversation_id,generation,state,pid,birth,boot,host) VALUES('core','old','started',4321,'1','boot','linux:machine')",
    ),
  );
  let stopped = false;
  vi.spyOn(LocalChildCustody.prototype, 'retirement_row_proven').mockImplementation(
    async (row) => row.state === 'retired',
  );
  const stop = vi
    .spyOn(LocalChildCustody.prototype, 'stop_recorded_child')
    .mockImplementation(async () => stopped);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const restarted = setup({ restart_of: f.database, role_actor: 'chief' });
  await restarted.store.prepare_reconciliation(new Map());
  f.database.write((tx) => restarted.store.reconcile_in_transaction(tx, new Map()));
  try {
    expect(await restarted.send('one', 'first')).toEqual(
      new PromptDeliveryRefused({
        refusal_reason: PromptDeliveryRefusalReason.backend_did_not_start,
      }),
    );
    expect(restarted.children).toHaveLength(0);
    // The next turn stops the old process through its record, as a kill does, then starts.
    stopped = true;
    expect(await restarted.send('two', 'second')).toBeInstanceOf(PromptDeliveryStarted);
    expect(stop).toHaveBeenLastCalledWith(
      expect.objectContaining({ pid: 4321, birth: '1' }),
      'linux:machine',
      undefined,
      false,
    );
    expect(restarted.children).toHaveLength(1);
  } finally {
    await restarted.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
