/** Gap: unsupported Home support must refuse before any outbox publication.
 * Exercise the existing gateway without starting its socket or native children.
 */
import { expect, test, vi } from 'vitest';
import { AgentGateway } from './gateway.ts';
import { ExecutorHttpError } from '../transport/errors.ts';
import type { GatewayOptions } from './contracts.ts';
import type { GatewayOutbox } from './outbox.ts';
import type { MacRunningChild } from '../runner/contracts.ts';
import type { ExecutorIdentity } from '../../wire.generated.ts';

test('unavailable, unsupported and forbidden Home capabilities never enter the outbox', async () => {
  const child = {
    reference: { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'conversation' },
    active_turn: null,
    ticket_id: 't_one',
  } as MacRunningChild;
  const add = vi.fn();
  const source_work_called = vi.fn();
  const source_work: GatewayOptions['runner']['source_work'] = async (_reference, work) => {
    source_work_called();
    return work();
  };
  const read = vi.fn();
  const options: GatewayOptions = {
    runner: {
      admission_stopped: false,
      authenticate: () => child,
      source_work,
      seal_admission() {},
      async quiesce() {
        throw new Error('No quiescence request');
      },
    },
    // No outbox method is permitted before support admission.
    outbox: { add } as unknown as GatewayOutbox,
    presence: {
      identity: {} as ExecutorIdentity,
      status: () => {
        throw new Error('No status request');
      },
    },
    client: { read },
    request_exit() {},
    async settle_catalogs() {},
    on_failure: (error) => {
      throw error;
    },
  };
  for (const scenario of [
    { client: null, error: null, support: {}, code: 'connection' },
    {
      client: { read },
      error: new ExecutorHttpError(400, 'validation', 'unsupported executor read'),
      support: {},
      code: 'validation',
    },
    { client: { read }, error: null, support: { ticket_send_back: false }, code: 'validation' },
    {
      client: { read },
      error: new ExecutorHttpError(403, 'agent_forbidden', 'denied'),
      support: {},
      code: 'agent_forbidden',
    },
  ]) {
    read.mockReset();
    if (scenario.error) read.mockRejectedValue(scenario.error);
    else read.mockResolvedValue(scenario.support);
    const gateway = new AgentGateway({ ...options, client: scenario.client });
    const response = await gateway.handle({
      request_id: 'send-back',
      operation: 'write',
      child_id: 'child',
      capability: 'capability',
      payload: {
        operation: 'ticket.send_back',
        arguments: {
          ticket_id: 't_one',
          body: { message: 'Revise', expected_proposal_revision: 7 },
        },
      },
    });
    expect(response.error?.code).toBe(scenario.code);
    expect(add).not.toHaveBeenCalled();
    expect(source_work_called).not.toHaveBeenCalled();
    if (scenario.client)
      expect(read.mock.calls[0]?.[0]).toEqual({
        child: child.reference,
        path: '/api/executor/capabilities',
        params: {},
      });
  }
});
