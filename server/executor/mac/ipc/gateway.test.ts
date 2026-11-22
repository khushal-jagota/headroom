/** Gap: unsupported Home support must refuse before any outbox publication, for send-back
 * and for the generic agent request alike.
 * Exercise the existing gateway without starting its socket or native children.
 */
import { expect, test, vi } from 'vitest';
import { executor_response } from '../../../cli/http.ts';
import { CliFailure } from '../../../cli/errors.ts';
import { CliCommand } from '../../../cli/declarations.ts';
import { AgentGateway } from './gateway.ts';
import { ExecutorHttpError } from '../transport/errors.ts';
import type { GatewayOptions } from './contracts.ts';
import type { GatewayOutbox } from './outbox.ts';
import type { MacRunningChild } from '../runner/contracts.ts';
import type {
  ExecutorIdentity,
  JsonObject,
  IpcRequest,
  AcceptanceReceipt,
  IpcResponse,
} from '../../wire.generated.ts';

test('unavailable, unsupported and forbidden Home capabilities never enter the outbox', async () => {
  const child = {
    reference: { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'conversation' },
    active_turn: null,
    owner: { kind: 'ticket', id: 't_one' },
  } as unknown as MacRunningChild;
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
  const writes: { operation: string; arguments: JsonObject; capability: string }[] = [
    {
      operation: 'ticket.send_back',
      arguments: { ticket_id: 't_one', body: { message: 'Revise', expected_proposal_revision: 7 } },
      capability: 'ticket_send_back',
    },
    {
      operation: 'cli.request',
      arguments: { method: 'POST', path: '/api/managers/m_one/settle', body: {} },
      capability: 'cli_request',
    },
  ];
  for (const write of writes)
    for (const scenario of [
      { client: null, error: null, support: {}, code: 'connection' },
      {
        client: { read },
        error: new ExecutorHttpError(400, 'validation', 'unsupported executor read'),
        support: {},
        code: 'validation',
      },
      { client: { read }, error: null, support: { [write.capability]: false }, code: 'validation' },
      // An older home answers its capabilities without the newer one.
      { client: { read }, error: null, support: { ticket_send_back: true }, code: 'validation' },
      {
        client: { read },
        error: new ExecutorHttpError(403, 'agent_forbidden', 'denied'),
        support: {},
        code: 'agent_forbidden',
      },
    ]) {
      if (write.capability === 'ticket_send_back' && 'ticket_send_back' in scenario.support)
        continue;
      read.mockReset();
      if (scenario.error) read.mockRejectedValue(scenario.error);
      else read.mockResolvedValue(scenario.support);
      const gateway = new AgentGateway({ ...options, client: scenario.client });
      const response = await gateway.handle({
        request_id: 'gated-write',
        operation: 'write',
        child_id: 'child',
        capability: 'capability',
        payload: { operation: write.operation, arguments: write.arguments },
      });
      expect(response.error?.code).toBe(scenario.code);
      if (scenario.code === 'validation')
        expect(response.error?.message).toContain('update Home before retrying');
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

// Risk: reads and capability probes fail immediately during a server pause. Existing
// admission tests prove permanent refusals only. Reuse the gateway without a socket.
test('reads and write capability probes wait through a temporary server failure', async () => {
  const child = {
    reference: { executor_incarnation: 'inc', child_id: 'child', conversation_id: 'conversation' },
    active_turn: null,
    owner: { kind: 'ticket', id: 't_one' },
  } as unknown as MacRunningChild;
  const receipt = {
    submission_id: 'write',
    sender_id: 'sender',
    disposition: 'accepted',
    accepted_at_vps_ms: 1,
    result: { applied: true },
  };
  const add = vi.fn(async (..._arguments: unknown[]) => ({}));
  const wait_confirmed = vi.fn<GatewayOutbox['wait_confirmed']>(
    async () => receipt as AcceptanceReceipt,
  );
  const read = vi.fn();
  const lookup = vi.fn();
  const gateway = new AgentGateway({
    runner: {
      authenticate: () => child,
      admission_stopped: false,
      source_work: (async (_reference, work) => work()) as GatewayOptions['runner']['source_work'],
    } as unknown as GatewayOptions['runner'],
    outbox: { add, wait_confirmed, lookup } as unknown as GatewayOutbox,
    presence: {} as GatewayOptions['presence'],
    client: { read },
    request_exit() {},
    async settle_catalogs() {},
    on_failure: (error) => {
      throw error;
    },
  });
  const request = (operation: 'read' | 'write', payload: JsonObject): IpcRequest => ({
    request_id: operation,
    operation,
    child_id: 'child',
    capability: 'capability',
    payload,
  });
  read
    .mockRejectedValueOnce(Object.assign(new Error('server down'), { code: 'ECONNREFUSED' }))
    .mockResolvedValueOnce({ answer: true });
  expect(await gateway.handle(request('read', { path: '/api/probe' }))).toMatchObject({
    result: { answer: true },
    error: null,
  });
  expect(read).toHaveBeenCalledTimes(2);
  read.mockReset();
  read
    .mockRejectedValueOnce(new ExecutorHttpError(503, 'retry_later'))
    .mockResolvedValueOnce({ cli_request: true });
  expect(
    await gateway.handle(
      request('write', {
        operation: 'cli.request',
        arguments: { method: 'POST', path: '/api/probe', body: {} },
      }),
    ),
  ).toMatchObject({ result: { applied: true }, error: null });
  expect(read).toHaveBeenCalledTimes(2);
  expect(add).toHaveBeenCalledTimes(1);
  expect(add.mock.calls[0]?.[1]).toBe('write');
  // The five-minute pause has two extra minutes for backoff and a healthy response.
  expect(wait_confirmed.mock.calls[0]?.[1]).toBeGreaterThan(417);
  expect(wait_confirmed.mock.calls[0]?.[1]).toBeLessThanOrEqual(420);
  // A caller's earlier deadline is a controlled connection answer, with no extra retry.
  read.mockReset();
  read.mockRejectedValue(
    Object.assign(new Error('closed mid-response'), { code: 'ERR_STREAM_PREMATURE_CLOSE' }),
  );
  const [seconds, nanoseconds] = process.hrtime();
  const expires = seconds + nanoseconds / 1_000_000_000 + 0.02;
  expect(
    await gateway.handle({
      ...request('read', { path: '/api/probe' }),
      response_deadline: expires,
    }),
  ).toMatchObject({ result: null, error: { code: 'connection' } });
  expect(read).toHaveBeenCalled();
  // The observer returns the same submission as unconfirmed at its total budget.
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    read.mockReset();
    read.mockResolvedValue({ cli_request: true });
    wait_confirmed.mockImplementationOnce(
      (_submission, seconds) =>
        new Promise((resolve) => setTimeout(() => resolve(null), seconds * 1000)),
    );
    let boundary_response: IpcResponse | undefined;
    const expiring = gateway
      .handle({
        ...request('write', {
          operation: 'cli.request',
          arguments: { method: 'POST', path: '/api/probe', body: {} },
        }),
        request_id: 'boundary',
      })
      .then((response) => {
        boundary_response = response;
      });
    await vi.advanceTimersByTimeAsync(419_000);
    expect(boundary_response).toBeUndefined();
    await vi.advanceTimersByTimeAsync(1_000);
    await expiring;
    expect(boundary_response).toMatchObject({
      unconfirmed: { submission_id: 'boundary', publication: 'queued' },
    });
  } finally {
    vi.useRealTimers();
  }
  // A forced stop can leave an uncertain durable receipt. Neither the initial waiter
  // nor a later inspection can translate it into a refusal or repeat its effect.
  const uncertain_results: JsonObject[] = [
    { error: { code: 'uncertain', message: 'Outcome unknown' } },
    { fate: 'uncertain' },
  ];
  for (const result of uncertain_results) {
    const uncertain: AcceptanceReceipt = {
      ...receipt,
      submission_id: 'original-write',
      disposition: 'uncertain',
      result,
    };
    wait_confirmed.mockResolvedValueOnce(uncertain);
    const response = await gateway.handle({
      ...request('write', {
        operation: 'cli.request',
        arguments: { method: 'POST', path: '/api/probe', body: {} },
      }),
      request_id: 'original-write',
    });
    expect(response).toMatchObject({
      result: null,
      error: null,
      unconfirmed: { submission_id: 'original-write', acceptance: 'unconfirmed' },
    });
    lookup.mockReturnValue({
      envelope: {
        kind: 'cli_write',
        payload: {
          child: child.reference,
          operation: 'cli.request',
          arguments: { method: 'POST', path: '/api/probe', body: {} },
          turn: null,
        },
      },
      receipt: uncertain,
    });
    const published = add.mock.calls.length;
    const inspected = await gateway.handle({
      ...request('read', { submission_id: 'original-write' }),
      operation: 'retry',
      request_id: 'inspection',
    });
    expect(inspected).toMatchObject({
      request_id: 'inspection',
      unconfirmed: { submission_id: 'original-write', acceptance: 'unconfirmed' },
    });
    expect(add).toHaveBeenCalledTimes(published);
    for (const as_json of [false, true]) {
      let failure: unknown;
      try {
        executor_response(
          {
            command: new CliCommand(['executor', 'retry'], 'Inspect a write'),
            values: { as_json },
            environment: {},
            stdin: async () => Buffer.alloc(0),
          },
          inspected,
        );
      } catch (error) {
        failure = error;
      }
      expect(failure).toBeInstanceOf(CliFailure);
      const output = (failure as CliFailure).result;
      expect(output.exit_code).toBe(3);
      expect(output.stderr).toBe('');
      if (as_json)
        expect(JSON.parse(output.stdout)).toMatchObject({
          submission_id: 'original-write',
          acceptance: 'unconfirmed',
        });
      else expect(output.stdout).toContain('Do not repeat the original write.');
    }
  }
});
