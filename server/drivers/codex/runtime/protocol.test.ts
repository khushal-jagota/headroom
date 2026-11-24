import { describe, expect, it } from 'vitest';
import * as P from './protocol.ts';
import { parse_numeric_json } from '../../../core/http/numeric_json.ts';

const identity = { threadId: 'thread', turnId: 'turn' };
describe('consumed Codex contracts', () => {
  it('drops added fields and unknown item kinds without admitting malformed known items', () => {
    expect(
      P.decode_notification('item/completed', {
        ...identity,
        unused: 123,
        item: { type: 'futureItem', id: null, status: 'new' },
      }),
    ).toEqual({ ...identity, item: { type: 'futureItem' } });
    expect(() =>
      P.decode_notification('item/completed', {
        ...identity,
        item: { type: 'agentMessage', id: 'message', text: false },
      }),
    ).toThrow(P.CodexProtocolError);
    expect(P.decode_notification('future/notification', null)).toBeNull();
    expect(P.decode_server_request('toString', {})).toBeNull();
    expect(() => P.ThreadCompactStartResponse.read(parse_numeric_json('1'))).toThrow(
      P.CodexProtocolError,
    );
    expect(() => P.decode_notification('skills/changed', parse_numeric_json('1'))).toThrow(
      P.CodexProtocolError,
    );
  });

  it('keeps usable last-turn input separate and skips malformed last usage', () => {
    const total = { inputTokens: 90000, outputTokens: 10, cachedInputTokens: 3 };
    const last = { inputTokens: 12000, outputTokens: 4, cachedInputTokens: 2 };
    expect(P.ThreadTokenUsage.read({ total, last })).toEqual({ total, last });
    expect(P.ThreadTokenUsage.read({ total, last: { inputTokens: 'bad' } })).toEqual({
      total,
      last: null,
    });
    expect(P.ThreadTokenUsage.read({ total })).toEqual({ total, last: null });
  });

  it('requires identities, exact outcomes and permission decisions', () => {
    expect(() => P.TurnStartResponse.read({ turn: { id: ' ', status: 'completed' } })).toThrow(
      P.CodexProtocolError,
    );
    expect(() =>
      P.TurnCompletedNotification.read({
        threadId: 'thread',
        turn: { id: 'turn', status: 'future' },
      }),
    ).toThrow(P.CodexProtocolError);
    expect(() => P.CommandExecutionRequestApprovalResponse.read({ decision: 'yes' })).toThrow(
      P.CodexProtocolError,
    );
    expect(() =>
      P.decode_notification('item/agentMessage/delta', {
        ...identity,
        itemId: 'message',
      }),
    ).toThrow(P.CodexProtocolError);
    expect(() =>
      P.TokenUsage.read(
        parse_numeric_json('{"inputTokens":25.0,"outputTokens":0,"cachedInputTokens":0}'),
      ),
    ).toThrow(P.CodexProtocolError);
    expect(
      P.TokenUsage.read(
        parse_numeric_json('{"inputTokens":25,"outputTokens":0,"cachedInputTokens":0}'),
      ),
    ).toEqual({ inputTokens: 25, outputTokens: 0, cachedInputTokens: 0 });
    expect(() =>
      P.TokenUsage.read({ inputTokens: true, outputTokens: 0, cachedInputTokens: 0 }),
    ).toThrow(P.CodexProtocolError);
  });
});

// Risk: collaboration items become opaque and lose native agent identities/statuses.
it('decodes sub-agent collaboration identities and rejects malformed states', () => {
  const item = {
    type: 'collabAgentToolCall',
    id: 'spawn',
    tool: 'spawnAgent',
    status: 'completed',
    senderThreadId: 'root',
    receiverThreadIds: ['child'],
    prompt: 'Check the output',
    agentsStates: { child: { status: 'running', message: null } },
  };
  expect(P.decode_item(item)).toEqual({
    type: item.type,
    id: 'spawn',
    tool: 'spawnAgent',
    senderThreadId: 'root',
    receiverThreadIds: ['child'],
    prompt: item.prompt,
    agentsStates: { child: { status: 'running' } },
  });
  expect(() => P.decode_item({ ...item, agentsStates: { child: { status: 'future' } } })).toThrow(
    P.CodexProtocolError,
  );
});

it('reads native child ancestry and leaves old root metadata compatible', () => {
  expect(
    P.decode_notification('thread/started', { thread: { id: 'child', parentThreadId: 'root' } }),
  ).toEqual({ thread: { id: 'child', parentThreadId: 'root' } });
  expect(P.decode_notification('thread/started', { thread: { id: 'root' } })).toEqual({
    thread: { id: 'root', parentThreadId: null },
  });
});

it('reads code-mode activity identities and exact lifecycle kinds', () => {
  const item = { type: 'subAgentActivity', id: 'call', kind: 'started', agentThreadId: 'child' };
  expect(P.decode_item(item)).toEqual(item);
  expect(() => P.decode_item({ ...item, kind: 'future' })).toThrow(P.CodexProtocolError);
});
