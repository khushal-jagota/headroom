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
