import { expect, test } from 'vitest';
import { JsonNumber, clone_numeric_json, parse_numeric_json } from '../core/http/numeric_json.ts';
import { stringify_numeric_response } from '../core/http/numeric_intake.ts';
import { document_edit } from '../goals/logic/revisions.ts';

// HTTP output keeps exact integer tokens and preserves unsafe computed float tokens.
// Safe whole floats use native spelling without exposing internal numeric markers.
test('HTTP JSON uses native float spelling and retains exact integer tokens', () => {
  const value = parse_numeric_json('{"integer":900719925474099312345,"float":50.0,"zero":-0.0}');
  expect(stringify_numeric_response({ ...(value as object), reading: 9.007199254740993e20 })).toBe(
    '{"integer":900719925474099312345,"float":50,"zero":0,"reading":9.007199254740993e+20}',
  );
  const cloned = clone_numeric_json(value) as Record<string, JsonNumber>;
  expect(cloned.float!.kind).toBe('float');
  expect(cloned.integer!.integer).toBe(900719925474099312345n);
  expect(() => JSON.stringify(new JsonNumber('1e999'))).toThrow('JSON numbers must be finite');
});

// Metadata must distinguish integer tokens from whole floats before either writer runs.
test('document revisions preserve exact integer identity and reject other numeric kinds', () => {
  for (const expected_revision of [null, true, '1', -1, new JsonNumber('1.0')])
    expect(() => document_edit({ expected_revision }, 1)).toThrow('nonnegative integer');
  const revision = new JsonNumber('900719925474099312345');
  expect(() => document_edit({ expected_revision: revision }, 1)).toThrowError(
    expect.objectContaining({
      statusCode: 409,
      detail: { expected_revision: revision, current_revision: 1 },
    }),
  );
});
