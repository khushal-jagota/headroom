/** Gaps: autonomous orchestration and delivery fates are absent from Ticket route proofs.
 * Reuses Ticket SQLite fixtures and the existing in-memory contract. No provider adapter.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { fixture, create, edit, read, now, claim } from './ticket_fixture.ts';
import * as data from '../tickets/data/index.ts';
import * as feedback from '../tickets/revision_feedback.ts';
import { OWNER } from '../core/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import { PromptDeliveryRefused, PromptDeliverySuppressed, PromptDeliveryUncertain } from '../conversation/contracts.ts';
import { start_ready_worker_step, WorkerStepReadinessLoop } from '../runtime/worker_step_readiness_loop.ts';
import { prepare_background_loops } from '../core/loops.ts';
import { ChangeSignal } from '../core/changes/index.ts';
afterEach(() => vi.restoreAllMocks());
function setup() {
  const db = fixture();
  const system = new InMemoryConversationSystem();
  const options = {connect_database: () => db, conversation_system: system,
    worker_type_registry: configured_worker_type_registry(), now: () => now};
  return {db, system, options};
}
test.each([
  ['refused', () => new PromptDeliveryRefused({refusal_reason:'session_did_not_load'}), 'none'],
  ['suppressed', () => new PromptDeliverySuppressed({reason:'test suppression'}), 'none'],
  ['uncertain', () => new PromptDeliveryUncertain(), 'errored'],
  ['backend_did_not_start', () => new PromptDeliveryRefused({refusal_reason:'backend_did_not_start'}), 'errored'],
] as const)('delivery %s preserves its claim outcome', async (_name, fate, expected_claim) => {
  const {db, system, options} = setup();
  const ticket = create(db);
  vi.spyOn(system, 'send').mockResolvedValue(fate());
  const result = await start_ready_worker_step(ticket.id, options);
  expect(result.started).toBe(false);
  expect(read(db, ticket.id).worker_step_claim).toBe(expected_claim);
});
test('start refusal releases a new link and the claim', async () => {
  const {db, system, options} = setup();
  const ticket = create(db);
  vi.spyOn(system, 'start_conversation').mockRejectedValue(new Error('start refused'));
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id)).toMatchObject({worker_step_claim: 'none', conversation_id: null});
});
test('late uncertainty cannot overwrite a newer claim or acknowledge newer revision feedback', async () => {
  const {db, system, options} = setup();
  const ticket = create(db);
  db.write((tx) => feedback.set_feedback(tx, ticket.id, {stage: ticket.stage, sender: OWNER, message:'first', now}));
  vi.spyOn(system, 'send').mockImplementation(async () => {
    const taken = read(db, ticket.id);
    db.write((tx) => data.release_worker_step_claim(tx, ticket.id, {expected_claim: taken.worker_step_claim, expected_claim_revision: taken.worker_step_claim_revision, now}));
    claim(db, ticket.id);
    db.write((tx) => feedback.set_feedback(tx, ticket.id, {stage: ticket.stage, sender: OWNER, message:'new feedback', now}));
    return new PromptDeliveryUncertain();
  });
  await start_ready_worker_step(ticket.id, options);
  expect(read(db, ticket.id).worker_step_claim).toBe('out');
  expect(db.read((sql) => feedback.snapshot(sql, ticket.id))?.text).toContain('new feedback');
});
test('successful user-stage opener releases its claim and cannot run twice', async () => {
  const {db, options} = setup();
  const ticket = create(db, {worker_type:'new_worker'});
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(true);
  expect(read(db, ticket.id).worker_step_claim).toBe('none');
  expect((await start_ready_worker_step(ticket.id, options)).started).toBe(false);
});
test('poll hook, in-flight deduplication, commit wake, and shutdown share one loop owner', async () => {
  const {db, options} = setup();
  const ticket = create(db);
  let held = true;
  const loop = new WorkerStepReadinessLoop({...options, before_poll: () => held});
  expect(loop.poll_once()).toEqual([]);
  held = false;
  expect(loop.poll_once()).toEqual([ticket.id]);
  expect(loop.poll_once()).toEqual([]);
  await loop.drain();
  expect(loop.poll_once()).toEqual([]);
  await loop.stop();
  expect(loop.poll_once()).toEqual([]);
});
test('production composition requires opt-in and dispatcher ownership; test mode never polls automatically', async () => {
  const {db, options} = setup();
  create(db);
  const base = {enable_worker_readiness:true, test_mode:false, dispatch_enabled:true, dispatcher_owned:true, worker:options};
  for (const refusal of [{enable_worker_readiness:false}, {test_mode:true}, {dispatch_enabled:false}, {dispatcher_owned:false}]) {
    expect(prepare_background_loops({...base, ...refusal}).worker_step_readiness_loop).toBeNull();
  }
  const loops = prepare_background_loops(base);
  const changes = new ChangeSignal();
  loops.start(60, changes);
  await loops.worker_step_readiness_loop!.drain();
  const next = create(db);
  changes.emit();
  await new Promise(resolve => setImmediate(resolve));
  await loops.worker_step_readiness_loop!.drain();
  expect(read(db, next.id).worker_step_claim).toBe('out');
  await loops.stop();
  const last = create(db);
  changes.emit();
  await new Promise(resolve => setImmediate(resolve));
  expect(read(db, last.id).worker_step_claim).toBe('none');
});
