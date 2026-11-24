import { describe, expect, it } from 'vitest';
import { PlannerError } from '../core/contracts.ts';
import { revisionFor } from '../resource_versions/logic/index.ts';

describe('resource revision probe', () => {
  it('answers each key by itself when a provider refuses one key', () => {
    const capture = (key: string) => {
      if (key === '/api/tickets/deleted/workspace')
        throw new PlannerError('not_found', 'ticket not found');
      return key === '/api/goals' ? { revision: '7' } : null;
    };
    const keys = ['/api/goals', '/api/tickets/deleted/workspace', '/api/unregistered', 'not-a-key'];
    expect(keys.map((key) => revisionFor(key, capture))).toEqual(['7', null, null, null]);
    expect(() =>
      revisionFor('/api/goals', () => {
        throw new TypeError('provider defect');
      }),
    ).toThrow('provider defect');
  });
});
