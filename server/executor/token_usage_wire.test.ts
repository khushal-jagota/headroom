import { expect, test } from 'vitest';
import { TokenUsageReported } from '../conversation/backends/contracts.ts';
import { encode_backend_event, decode_event_arguments } from './backend_events.ts';

test('executor event carries context size and old events leave it unavailable', () => {
  const [method, args] = encode_backend_event(
    new TokenUsageReported({ input_tokens: 100000, context_input_tokens: 12000 }),
  );
  expect(decode_event_arguments({ method, arguments: args })).toMatchObject({
    input_tokens: 100000,
    context_input_tokens: 12000,
  });
  expect(decode_event_arguments({ method, arguments: { input_tokens: 100000 } })).toMatchObject({
    input_tokens: 100000,
    context_input_tokens: null,
  });
});
