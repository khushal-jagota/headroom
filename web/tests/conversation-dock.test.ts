import { describe, expect, it } from 'vitest';
import { dockFeedbackFor, dockPreviewText, latestUnreadReply } from '../src/lib/conversation/dockPresentation';
import { happened } from '../src/lib/conversation/view/presentation';
import type { TranscriptRow } from '../src/lib/conversation/transcript';

describe('dock unread reply', () => {
  it('separates Markdown blocks and preserves link text without markup', () => {
    expect(dockPreviewText('\n# First **answer**\n\nA [document](/files/report.html).\n\n- One\n- Two\n\n```js\nconst x = 1\n```'))
      .toBe('First answer\nA document.\nOne\nTwo\nconst x = 1');
    expect(dockPreviewText('  ')).toBeNull();
  });
  it('uses only the newest unread owner reply despite later activity and attachment-only replies', () => {
    const base = { createdAt: 0, key: 'reply', sequence: 4 };
    const reply: TranscriptRow = { ...base, kind: 'agent_message', toOwner: true, content: [{ piece: 'text', text: 'Owner answer' }] };
    const rows: TranscriptRow[] = [reply, { ...base, key: 'later', sequence: 5, kind: 'turn_stopped' }];
    expect(latestUnreadReply(rows, 3)).toBe('Owner answer');
    expect(latestUnreadReply(rows, 4)).toBeNull();
    expect(latestUnreadReply([...rows, { ...reply, sequence: 6, content: [] }], 3)).toBe('');
    expect(dockPreviewText(latestUnreadReply([...rows, { ...reply, sequence: 6, content: [] }], 3))).toBeNull();
  });
});

describe('dock feedback', () => {
  it('shows no feedback for an earlier interrupted ending, so a running turn adds no second Working', () => {
    const ending = { kind: 'turn_ended' as const, ending: 'interrupted' as const, error_summary: null, automatic_compaction_result: null };
    expect(dockFeedbackFor(ending, happened(ending, 'owner'))).toBeNull();
  });
  it('keeps failures and delivery problems, and says nothing for completed endings or messages', () => {
    expect(dockFeedbackFor({ kind: 'turn_ended', ending: 'failed' }, { text: 'backend exited' })).toBe('backend exited');
    expect(dockFeedbackFor({ kind: 'prompt_uncertain' }, { text: 'delivery uncertain · do not resend' })).toBe('delivery uncertain · do not resend');
    expect(dockFeedbackFor({ kind: 'turn_ended', ending: 'completed' }, { text: 'done' })).toBeNull();
    expect(dockFeedbackFor({ kind: 'agent_message' }, { text: 'hello' })).toBeNull();
  });
});
