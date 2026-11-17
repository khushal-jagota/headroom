/** A driver that promises supervised launches is held to it before its child takes a prompt.
 * Core unit: the real record and the shared scripted process, through the core fixture.
 */
import { expect, test, vi } from 'vitest';
import { performance } from 'node:perf_hooks';
import { setup } from './conversation_core_fixture.ts';
import { PromptDeliveryRefused, PromptDeliveryRefusalReason } from '../conversation/contracts.ts';
import { SUPERVISED_CHILD_GENERATION_PREFIX } from '../conversation/child_custody.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';

const STOPPED = 'the agent started without a supervision record, so Panels stopped it';

// Risk: a marked row without a record reads as never spawned after a restart, so a child that
// broke the promise would outlive its custody. Gap: no core check made the marker true.
test('a supervised launch whose child reports no record is stopped and its prompt refused', async () => {
  const f = setup();
  const registry = f.core.options.backend_registry;
  const require_live = registry.require_live.bind(registry);
  vi.spyOn(registry, 'require_live').mockImplementation((id) => ({
    ...require_live(id),
    supervises_local_launches: true,
  }));
  vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    await f.start();
    expect(await f.send()).toEqual(
      new PromptDeliveryRefused({
        refusal_reason: PromptDeliveryRefusalReason.backend_did_not_start,
      }),
    );
    expect(f.children).toHaveLength(1);
    const stopped = f.children[0].last_observation!;
    expect(stopped.calls.map((call) => call.command)).toEqual(['start', 'stop']);
    expect(stopped.delivered_text).toEqual([]);
    const state = f.core.conversations.get('core')!;
    expect(state.child).toBeNull();
    expect(state.child_is_quarantined).toBe(false);
    const custody = f.database.read((sql) =>
      sql.get<{ generation: string; state: string; supervision_record: string | null }>(
        'SELECT generation,state,supervision_record FROM conversation_child_custody',
      ),
    );
    expect(custody?.generation.startsWith(SUPERVISED_CHILD_GENERATION_PREFIX)).toBe(true);
    expect(custody?.state).toBe('retired');
    expect(custody?.supervision_record).toBeNull();
    const refusal = f.store
      .read_events_after('core', 0)
      .find((row) => row.kind === 'prompt_delivery_refused')?.payload as
      { refusal_detail: string } | undefined;
    expect(refusal?.refusal_detail).toBe(STOPPED);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
