import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import {
  AgentMessageDeltaFrame,
  AgentMessageEventPayload,
  AgentTurnStartedEventPayload,
  ConversationTurnEnding,
  TurnEndedEventPayload,
} from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { ConversationViewLiveOutput } from '../conversation/views/live_output.ts';

// Full shows the durable reply row and the live spool together while a turn runs.
// A recorded reply must empty the spool, or its text shows twice until the turn ends.
test('recorded replies replace streamed text across prompted and agent-started turns', () => {
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
    live.publish_event(
      'c',
      3,
      new TurnEndedEventPayload({ ending: ConversationTurnEnding.completed }),
    );
    expect(live.output('c')).toBe(null);
    // Distinct regression: an agent-started turn follows a completed prompt while the same
    // viewer stays open. It needs a new spool keyed exactly as the durable projection.
    live.publish_event(
      'c',
      4,
      new AgentTurnStartedEventPayload({ turn_number: 2, reason: 'Background command completed' }),
    );
    live.publish_frame('c', new AgentMessageDeltaFrame({ text_delta: 'FINISHED' }));
    const autonomous = live.output('c');
    expect(autonomous?.turn_key).toBe('turn:e4');
    expect(autonomous?.byte_count).toBe('FINISHED'.length);
    expect(live.read_chunk('c', autonomous!.reference, 0).text).toBe('FINISHED');
    live.publish_event(
      'c',
      5,
      new AgentMessageEventPayload({ content: text_message_content('FINISHED') }),
    );
    expect(live.output('c')?.byte_count).toBe(0);
    live.publish_event(
      'c',
      6,
      new TurnEndedEventPayload({ ending: ConversationTurnEnding.interrupted }),
    );
    expect(live.output('c')).toBe(null);
    watcher.close();
  } finally {
    live.close();
  }
});
