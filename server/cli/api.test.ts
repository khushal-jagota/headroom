import { expect, test, vi } from 'vitest';
import { run } from './main.ts';
import * as http from './http.ts';

// Existing route comparisons prove effects, but not a missing credential's refusal
// marker. Keep that transport distinction at the environment serialization seam.
test('CLI authentication never substitutes role claims for a missing launch token', () => {
  expect(http.authentication_headers({})).toEqual({});
  expect(http.authentication_headers({ PLAN_ACTOR: 'chief' })).toEqual({
    Authorization: 'Bearer unidentified',
  });
  expect(http.authentication_headers({ PLAN_LAUNCH_TOKEN: ' ' })).toEqual({
    Authorization: 'Bearer unidentified',
  });
  expect(
    http.authentication_headers({
      PLAN_LAUNCH_TOKEN: ' private-token ',
      PLAN_ACTOR: 'worker',
      PLAN_TICKET_ID: 't_fake',
    }),
  ).toEqual({ Authorization: 'Bearer private-token' });
});

// No dedicated command previously exercised arbitrary JSON values through the
// authenticated shared transport. Reuse the CLI runner and mock its existing seam.
test('api sends parsed stdin JSON through shared transport and rejects malformed input', async () => {
  const request = vi.spyOn(http, 'send').mockResolvedValue({ recap: 'Ready' });
  const environment = { PLAN_LAUNCH_TOKEN: 'synthetic-token' };
  try {
    const reply = await run(
      ['api', 'put', '/api/workers/chief-of-staff/recap', '--input-json', '-', '--json'],
      environment,
      async () => Buffer.from('{"recap":"Ready"}'),
    );
    expect(reply).toEqual({ exit_code: 0, stdout: '{"recap":"Ready"}\n', stderr: '' });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ environment }),
      'PUT',
      '/api/workers/chief-of-staff/recap',
      { json_body: { recap: 'Ready' } },
    );
    request.mockClear();
    const invalid = await run(
      ['api', 'PATCH', '/api/no-write', '--input-json', '-', '--json'],
      environment,
      async () => Buffer.from('{invalid'),
    );
    expect(invalid.exit_code).toBe(1);
    expect(JSON.parse(invalid.stderr).error.code).toBe('validation');
    expect(request).not.toHaveBeenCalled();
  } finally {
    request.mockRestore();
  }
});
