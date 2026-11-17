import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { AgentMessageDeltaFrame, AgentMessageEventPayload } from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { ConversationViewLiveOutput } from '../conversation/views/live_output.ts';

// Full shows the durable reply row and the live spool together while a turn runs.
// A recorded reply must empty the spool, or its text shows twice until the turn ends.
test('a recorded reply replaces the streamed text it finished', () => {
  const live = new ConversationViewLiveOutput(mkdtempSync(join(tmpdir(), 'live-output-')));
  try {
    const watcher = live.subscribe('c', 'full', { transcript: true, turn_key: 'turn:e1' });
    live.publish_frame('c', new AgentMessageDeltaFrame({ text_delta: 'first reply' }));
    expect(live.output('c')?.byte_count).toBe('first reply'.length);
    live.publish_event(
      'c',
      2,
      new AgentMessageEventPayload({ content: text_message_content('first reply') }),
    );
    expect(live.output('c')?.byte_count).toBe(0);
    live.publish_frame('c', new AgentMessageDeltaFrame({ text_delta: 'next' }));
    expect(live.output('c')?.byte_count).toBe('next'.length);
    watcher.close();
  } finally {
    live.close();
  }
});
