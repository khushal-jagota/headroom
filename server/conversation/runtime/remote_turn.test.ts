import { expect, test } from 'vitest';
import { JsonNumber } from '../../core/http/numeric_json.ts';
import { admit_remote_turn } from './remote_turn.ts';
// Wire exactness alone cannot prevent rounded lifecycle identity at core adoption.
test('core adoption refuses an exact remote counter above its safe bound', () => {
  const turn = { conversation_id: 'remote', turn_number: new JsonNumber('9007199254740993') };
  expect(() => admit_remote_turn(turn)).toThrow(
    'Remote turn number exceeds the safe integer range',
  );
  expect(admit_remote_turn({ ...turn, turn_number: Number.MAX_SAFE_INTEGER })).toEqual({
    conversation_id: 'remote',
    turn_number: Number.MAX_SAFE_INTEGER,
  });
});
