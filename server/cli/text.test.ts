import { expect, test } from 'vitest';
import { scalar_text } from './text.ts';
import { MissingResponseValue } from './errors.ts';

test('missing response values fail instead of appearing in human output', () => {
  expect(() => scalar_text(undefined)).toThrow(MissingResponseValue);
});
