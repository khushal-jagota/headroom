/** A local child that ends its turn broken is retired before any later prompt reaches a child.
 * Core unit: the real record and the shared scripted process, through the core fixture.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { performance } from 'node:perf_hooks';
import { setup } from './conversation_core_fixture.ts';
import { PromptDeliveryQueued, PromptDeliveryStarted } from '../conversation/contracts.ts';
import { TurnEnded } from '../conversation/backends/contracts.ts';
import { Deferred } from '../core/deferred.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';

afterEach(() => {
  vi.restoreAllMocks();
});

// Risk: a send in the window between the quarantine and the end of the stop is refused, or
// reaches a second child before the first is stopped.
// Gap: nothing ended a child at turn end before rule 4.
test('a send while a broken child stops is held and starts exactly one new child', async () => {
  const f = setup();
  await f.start();
  expect(await f.send('one', 'first')).toBeInstanceOf(PromptDeliveryStarted);
  const broken = f.children[0];
  const stop_entered = new Deferred<void>();
  const stop_release = new Deferred<void>();
  const native_stop = broken.stop.bind(broken);
  const stop = vi.spyOn(broken, 'stop').mockImplementation(async () => {
    stop_entered.resolve();
    await stop_release.promise;
    await native_stop();
  });
  await broken.emit(new TurnEnded({ ending: 'failed', child_disposition: 'broken' }));
  await broken.flush_events();
  await stop_entered.promise;
  const state = f.core.conversations.get('core')!;
  expect(state.child_is_quarantined).toBe(true);
  expect(state.phase).toBe('idle');
  expect(await f.send('two', 'second')).toBeInstanceOf(PromptDeliveryQueued);
  expect(f.children).toHaveLength(1);
  stop_release.resolve();
  await f.core.wait_until_quiescent();
  expect(stop).toHaveBeenCalledOnce();
  expect(f.children).toHaveLength(2);
  expect((await f.children[1].inspect()).delivered_text).toEqual(['two']);
  expect(state.child).toBe(f.children[1]);
  expect(state.child_is_quarantined).toBe(false);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Risk: a failed stop leaves a conversation that can never be used again.
// Gap: the janitor's quarantine recovery was proven only for children it had idled out.
test('a failed stop of a broken child keeps the quarantine until the idle sweep recovers it', async () => {
  const f = setup();
  await f.start();
  await f.send('one', 'first');
  const broken = f.children[0];
  const stop = vi
    .spyOn(broken, 'stop')
    .mockRejectedValueOnce(new Error('the broken child did not stop'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
  await broken.emit(new TurnEnded({ ending: 'failed', child_disposition: 'broken' }));
  await broken.flush_events();
  await f.core.wait_until_quiescent();
  const state = f.core.conversations.get('core')!;
  expect(stop).toHaveBeenCalledOnce();
  expect(state.child).toBe(broken);
  expect(state.child_is_quarantined).toBe(true);
  expect(await f.send('two', 'second')).toBeInstanceOf(PromptDeliveryQueued);
  await f.core.wait_until_quiescent();
  expect(f.children).toHaveLength(1);
  await f.core.sweep_idle_children();
  await f.core.wait_until_quiescent();
  expect(stop).toHaveBeenCalledTimes(2);
  expect(f.children).toHaveLength(2);
  expect((await f.children[1].inspect()).delivered_text).toEqual(['two']);
  expect(state.child_is_quarantined).toBe(false);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});
