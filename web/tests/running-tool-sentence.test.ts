import { describe, expect, it } from 'vitest';
import { runningToolSentence } from '../src/lib/conversation/view/presentation';
import type { ConversationViewStatus } from '../src/lib/conversation/view/contracts';

const tool = (status: 'running' | 'completed') => ({
  kind: 'tool_call' as const, tool_call_id: 't1', title: 'Bash', tool_kind: 'execute',
  body: { kind: 'inline' as const, value: { kind: 'tool_call' as const, tool_call_id: 't1', title: 'Bash', tool_kind: 'execute', detail: null, started_detail: '{"command":"go test -race ./internal/push"}', capped_detail_sequence: null, status } }
});
const status = (over: Partial<ConversationViewStatus> = {}, latest = tool('running')) => ({
  authority: 'current', turn_key: 'turn1', turn_running: true, live_tool_key: 't1',
  rest: { latest_tool: latest }, ...over
}) as unknown as ConversationViewStatus;

describe('what a running turn is doing now', () => {
  it('reads the running call in its tool row sentence', () => {
    const sentence = runningToolSentence(status(), 'turn1');
    expect(sentence).toContain('go test -race ./internal/push');
  });
  it('says nothing for a finished call, another turn, a stopped turn or unknown activity', () => {
    expect(runningToolSentence(status({}, tool('completed')), 'turn1')).toBeNull();
    expect(runningToolSentence(status(), 'turn0')).toBeNull();
    expect(runningToolSentence(status({ turn_running: false }), 'turn1')).toBeNull();
    expect(runningToolSentence(status({ authority: 'unknown' }), 'turn1')).toBeNull();
    expect(runningToolSentence(status({}, null as never), 'turn1')).toBeNull();
    expect(runningToolSentence(null, 'turn1')).toBeNull();
  });
});
