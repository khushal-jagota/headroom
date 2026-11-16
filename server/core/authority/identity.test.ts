import { expect, test } from 'vitest';
import type { FastifyRequest } from 'fastify';
import { OWNER } from '../contracts.ts';
import {
  requestContext,
  TRUSTED_INGRESS_CONFIGURED,
  IN_PROCESS_REQUEST_CONTEXT,
} from './identity.ts';

// Credential failures must never inherit owner ingress or the identity-free local default.
// Direct request contexts cover this precedence without another HTTP server fixture.
test('failed and duplicate credentials stay unidentified before owner ingress', async () => {
  const request = (rawHeaders: string[], headers: Record<string, string>, hosted = false) =>
    ({
      raw: { rawHeaders },
      headers,
      authenticatedOwner: true,
      [TRUSTED_INGRESS_CONFIGURED]: hosted,
    }) as unknown as FastifyRequest;
  for (const [raw, headers] of [
    [['Authorization', 'Bearer bad'], { authorization: 'Bearer bad' }],
    [['Authorization', 'Basic bad'], { authorization: 'Basic bad' }],
    [
      ['Authorization', 'Bearer one', 'Authorization', 'Bearer two'],
      { authorization: 'Bearer one' },
    ],
    [['Authorization', ''], { authorization: '' }],
  ] as Array<[string[], Record<string, string>]>) {
    for (const hosted of [false, true])
      expect(requestContext(request(raw, headers, hosted)).principal.kind).toBe('unidentified');
  }
  expect(requestContext(request([], {})).principal).toEqual(OWNER);
  const context = {
    principal: { kind: 'ticket' as const, id: 'verified' },
    conversationId: 'conversation',
  };
  const internalRequest = request(['Authorization', 'Bearer bad'], { authorization: 'Bearer bad' });
  Object.assign(internalRequest.raw, { [IN_PROCESS_REQUEST_CONTEXT]: context });
  const verified = requestContext(internalRequest);
  expect(verified).toEqual(context);
  expect(requestContext(request([], {})).principal).toEqual(OWNER);
});
