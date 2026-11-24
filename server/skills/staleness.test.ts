/** Risk: the skill check reads its own copy of the command tree and drifts from the CLI. */
import { expect, test } from 'vitest';
import type { ReadSql } from '../core/database/contracts.ts';
import { stale_references } from './staleness.ts';

test('the skill check reads the declared command tree, hidden commands included', () => {
  const source =
    'Run `panels ticket request-recap <ticket-id>`, `panels ticket show brief`, ' +
    '`panels api GET /api/tickets`, and `panels worker my-ticket`.';
  const sql = {
    all: (query: string) =>
      query.includes('managed_skills') ? [{ skill_name: 'probe', source_text: source }] : [],
  } as unknown as ReadSql;
  expect(stale_references(sql).filter((reference) => reference.skill_name === 'probe')).toEqual([
    { skill_name: 'probe', kind: 'command', reference: 'panels worker my-ticket' },
  ]);
});
