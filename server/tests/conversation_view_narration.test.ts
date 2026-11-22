import { describe, expect, it } from 'vitest';
import { narration_sentence } from '../conversation/views/logic/rest.ts';

describe('v6: the live narration sentence', () => {
  it('is the last sentence of the agent prose, as plain words', () => {
    expect(
      narration_sentence(
        "The CI log's stack pair matches: `flush()` swaps `b.pending` at `batcher.go:97` while `Add()` reads it at `batcher.go:61` without the lock. Confirming it reproduces under `-race` before I write it up.",
      ),
    ).toBe('Confirming it reproduces under -race before I write it up.');
  });

  it('keeps an unfinished sentence that is still streaming, and drops code blocks and emphasis', () => {
    expect(narration_sentence('Reading the route.\n\n```go\nfunc x() {}\n```\n\nNow **checking** the bucket')).toBe(
      'Now checking the bucket',
    );
    expect(narration_sentence('   ')).toBeNull();
  });

  it('caps a very long sentence', () => {
    expect(Array.from(narration_sentence('a'.repeat(400)) ?? '').length).toBe(240);
  });
});
