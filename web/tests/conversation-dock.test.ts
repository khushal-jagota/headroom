import { describe, expect, it } from 'vitest';
import { dockPreviewText, latestUnreadReply } from '../src/lib/conversation/dockPresentation';
import type { TranscriptRow } from '../src/lib/conversation/transcript';
import { dockEdgeOpacity, DOCK_EDGE_FADE_MS } from '../src/lib/conversation/layout/dock';
import { CONVERSATION_CARD_MS } from '../src/lib/conversation/surfaceMotion';

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

describe('the dock edge', () => {
  it('fades over 180ms: out at the start of opening, in over the end of the return', () => {
    expect(DOCK_EDGE_FADE_MS).toBe(180);
    const at = (ms: number) => ms / CONVERSATION_CARD_MS;
    // Opening: gone 180ms in, half gone at 90ms.
    expect(dockEdgeOpacity(1, 0, 0)).toBe(1);
    expect(dockEdgeOpacity(1, 0, at(90))).toBeCloseTo(0.5);
    expect(dockEdgeOpacity(1, 0, at(180))).toBe(0);
    expect(dockEdgeOpacity(1, 0, 1)).toBe(0);
    // Returning: nothing while the layer is still tall, fully there as the dock lands.
    const start = CONVERSATION_CARD_MS - 180;
    expect(dockEdgeOpacity(0, 1, at(start))).toBe(0);
    expect(dockEdgeOpacity(0, 1, at(start + 90))).toBeCloseTo(0.5);
    expect(dockEdgeOpacity(0, 1, 1)).toBe(1);
  });
});
