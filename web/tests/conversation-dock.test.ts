import { describe, expect, it } from 'vitest';
import { dockPreviewText, latestUnreadReply } from '../src/lib/conversation/dockPresentation';
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
