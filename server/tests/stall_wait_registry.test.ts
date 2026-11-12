/** Port of test_stall_wait_registry.py: overlapping writers cannot hide an older wait. */
import { expect, test } from 'vitest';
import { ProcessWaitRegistry } from '../stalls/registry.ts';
import { finding_key } from '../stalls/contracts.ts';

test('snapshot keeps one wait per finding key from its earliest start', () => {
  const times = [100, 200, 300][Symbol.iterator]();
  const registry = new ProcessWaitRegistry(() => times.next().value!);
  const first = registry.track('vps_save_retrying', 'c1', {
    conversation_id: 'c1',
    detail: 'stuck at write 1',
  });
  try {
    const second = registry.track('vps_save_retrying', 'c1', {
      conversation_id: 'c1',
      detail: 'second writer',
    });
    try {
      const other = registry.track('vps_save_retrying', 'c2', {
        conversation_id: 'c2',
        detail: 'other',
      });
      try {
        first.set_detail('stuck at write 2');
        const waits = new Map(registry.snapshot().map((wait) => [finding_key(wait), wait]));
        expect(registry.snapshot()).toHaveLength(2);
        expect(waits.get('vps_save_retrying:c1')?.started_at_ms).toBe(100);
        expect(waits.get('vps_save_retrying:c1')?.detail).toBe('stuck at write 2');
        expect(waits.get('vps_save_retrying:c2')?.started_at_ms).toBe(300);
      } finally {
        other.close();
      }
    } finally {
      second.close();
    }
    expect(registry.snapshot().map((wait) => wait.started_at_ms)).toEqual([100]);
  } finally {
    first.close();
  }
  expect(registry.snapshot()).toEqual([]);
});
