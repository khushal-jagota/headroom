import { expect, it, vi } from 'vitest';

const posted = vi.hoisted(() => [] as Record<string, unknown>[]);
vi.mock('../src/lib/conversation/ownerSend', async (original) => ({
  ...(await original<typeof import('../src/lib/conversation/ownerSend')>()),
  postOwnerSend: vi.fn(async (_path: string, body: Record<string, unknown>) => { posted.push(body); throw new Error('stop after the post'); })
}));
vi.mock('../src/lib/review/sendAttempts', async (original) => ({
  ...(await original<typeof import('../src/lib/review/sendAttempts')>()),
  rememberReviewAttempts: vi.fn(async () => true)
}));

import { ReviewReplyComposer } from '../src/lib/review/replyComposer.svelte';

// A review reply used to go as `queue`, so a busy Chief held it until its turn ended.
it('posts a review reply as a steer that never waits on the running turn', async () => {
  const composer = new ReviewReplyComposer({
    draftKey: 'review-mode-test', attachmentDraftId: 'review-mode-test', owner: { kind: 'chief', id: 'chief' },
    destination: () => 'conversation-1', attemptKey: 'review-mode-attempts', attemptMetadataKey: 'review-mode-meta'
  });
  composer.ready = true;
  composer.text = 'Use Postgres, please.';
  await composer.send(3);
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({ mode: 'steer' });
  expect(composer.attempts[0].message.queueWhilePending).toBe(false);
});
