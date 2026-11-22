/** A server without its executor has no native execution route. */
import { expect, test, vi } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { loadConfig } from '../core/config.ts';
import {
  compose_production_backends,
  prepare_machine_backends,
  create_production_backend_bindings,
} from '../lifecycle/production_bindings.ts';
import type { ConversationCompositionContext } from '../core/start.ts';
import { ResolvedConversationStart } from '../conversation/contracts.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import { LocaleBackendChildFactory } from '../executor/routing.ts';

test('missing machine execution refuses every server factory without constructing a native driver', () => {
  const database = fixture();
  const bindings = create_production_backend_bindings();
  const native = bindings.drivers.map((driver) => vi.spyOn(driver, 'create'));
  const context = {
    database,
    config: loadConfig(),
    executor: null,
  } as ConversationCompositionContext;
  const composed = compose_production_backends(
    context,
    prepare_machine_backends(bindings.drivers),
    bindings,
  );
  for (const backend_key of composed.http.backend_ids) {
    expect(() =>
      composed.registry.require_live(backend_key).child_factory({
        resolved_start: new ResolvedConversationStart({
          conversation_id: 'unrecorded',
          backend_key,
          model: 'model',
          reasoning_effort: null,
          role_materials: null,
          access: 'full',
          workspace_folder: '/absent-on-server',
        }),
        event_sink: { async report() {} },
        message_files: {} as never,
      }),
    ).toThrow('Machine execution is unavailable');
  }
  expect(native.every((create) => create.mock.calls.length === 0)).toBe(true);
});

test('a start without a recorded execution machine cannot reach the machine factory', () => {
  const database = fixture();
  const remote = Object.assign(vi.fn(), { recover: () => null, retained_runs: () => [] });
  const factory = new LocaleBackendChildFactory(database, remote);
  expect(() =>
    factory.create({
      resolved_start: new ResolvedConversationStart({
        conversation_id: 'unrecorded',
        backend_key: parse_backend_instance_id('codex'),
        model: 'model',
        reasoning_effort: null,
        role_materials: null,
        access: 'full',
        workspace_folder: '/absent-on-server',
      }),
      event_sink: { async report() {} },
      message_files: {} as never,
    }),
  ).toThrow(BackendSpawnFailed);
  expect(remote).not.toHaveBeenCalled();
});
