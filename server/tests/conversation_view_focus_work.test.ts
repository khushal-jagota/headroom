import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { expect, test } from 'vitest';
import type { ConversationEventKind } from '../conversation/events.ts';
import { ConversationAccess, ResolvedConversationStart } from '../conversation/contracts.ts';
import { conversation_event_payload_from_canonical_json } from '../conversation/record/codec.ts';
import { ConversationStorePrimitives } from '../conversation/record/data/store.ts';
import { append_projection } from '../conversation/views/data/writer.ts';
import { read_page } from '../conversation/views/data/storage.ts';
import { conversation_view_changes } from '../conversation/views/routes.ts';
import { in_focus } from '../conversation/views/logic/bodies.ts';
import { event_effect } from '../conversation/views/logic/effects.ts';
import { fixture as database_fixture } from './ticket_fixture.ts';

const owner = { kind: 'owner', id: 'owner' };
const events: [ConversationEventKind, unknown][] = [
  [
    'prompt',
    {
      sender_label: 'owner',
      content: [{ piece: 'text', text: 'Work' }],
      mode: 'queue',
      sender: owner,
    },
  ],
  [
    'tool_call_started',
    { tool_call_id: 't1', title: 'Read', tool_kind: 'read', detail: '{"file_path":"a.ts"}' },
  ],
  ['tool_call_finished', { tool_call_id: 't1', tool_call_status: 'completed', detail: 'contents' }],
  [
    'tool_call_started',
    { tool_call_id: 't2', title: 'Bash', tool_kind: 'execute', detail: '{"command":"ls"}' },
  ],
  ['tool_call_finished', { tool_call_id: 't2', tool_call_status: 'completed', detail: 'a.ts' }],
  [
    'message_to_owner',
    {
      content: [{ piece: 'text', text: 'Done' }],
      sender_label: 'agent',
      recipient: owner,
      sender: { kind: 'ticket', id: 'w' },
    },
  ],
  ['turn_ended', { ending: 'completed', error_summary: null }],
];

function projected() {
  const database = database_fixture();
  const store = new ConversationStorePrimitives(database, { integer_now: () => 100 });
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id: 'c',
      backend_key: parse_backend_instance_id('codex'),
      model: 'm',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/w',
      access: ConversationAccess.full,
    }),
  );
  database.write((tx) => {
    events.forEach(([kind, payload], index) => {
      const sequence = index + 1;
      const encoded = JSON.stringify(payload);
      tx.run(
        'INSERT INTO conversation_events (conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)',
        ['c', sequence, kind, encoded, 100 + sequence],
      );
      append_projection(tx, [
        {
          conversation_id: 'c',
          sequence,
          kind,
          payload: conversation_event_payload_from_canonical_json(kind, encoded),
          created_at: 100 + sequence,
        },
      ]);
    });
    tx.run('UPDATE conversations SET latest_sequence=? WHERE conversation_id=?', [
      events.length,
      'c',
    ]);
  });
  return database;
}

// Focus keeps tool activity visible: its turn head counts the calls and opening that turn
// reaches them, while the Focus timeline itself stays free of tool rows.
test('Focus reaches a settled turn’s tool calls through its head', () => {
  projected().read((sql) => {
    const timeline = read_page(sql, 'c', 'focus');
    const turn = timeline.items.find((item) => item.kind === 'turn');
    expect(turn).toMatchObject({ kind: 'turn', tool_call_count: 2, settled: true });
    expect(timeline.items.some((item) => item.kind === 'work_group')).toBe(false);
    expect(
      timeline.items.some((item) => item.kind === 'row' && item.row_kind === 'tool_call'),
    ).toBe(false);

    const opened = read_page(sql, 'c', 'focus', { scope: { kind: 'turn', key: turn!.key } });
    const group = opened.items.find((item) => item.kind === 'work_group');
    expect(group).toMatchObject({ kind: 'work_group', entry_count: 2 });

    const run = read_page(sql, 'c', 'focus', { scope: { kind: 'work_group', key: group!.key } });
    expect(run.items.map((item) => item.kind === 'row' && item.row_kind)).toEqual([
      'tool_call',
      'tool_call',
    ]);
  });
});

test('Focus change frames carry tool calls only to an opened turn’s scopes', () => {
  projected().read((sql) => {
    const timeline = read_page(sql, 'c', 'focus');
    const turn = timeline.items.find((item) => item.kind === 'turn')!;
    const status = { stopped_without_ending: false } as Parameters<
      typeof conversation_view_changes
    >[5];
    const first = { sequence: 0, slot: 0 };
    const last = { sequence: events.length, slot: 1 };
    const changes = conversation_view_changes(
      sql,
      'c',
      'focus',
      0,
      [
        { scope: { kind: 'timeline', key: null }, first, last },
        { scope: { kind: 'turn', key: turn.key }, first, last },
      ],
      status,
    )!;
    const [timelineKeys, turnKeys] = changes.scope_changes.map((change) => change.item_keys);
    expect(timelineKeys.some((key) => key.startsWith('work:'))).toBe(false);
    expect(timelineKeys).not.toContain('e2');
    expect(turnKeys.some((key) => key.startsWith('work:'))).toBe(true);
  });
});

test('a command reply belongs in Focus by the one shared rule', () => {
  const payload = conversation_event_payload_from_canonical_json(
    'command_reply',
    JSON.stringify({ reply_text: 'Compacted.' }),
  );
  expect(in_focus(payload)).toBe(true);
  expect(event_effect(payload).focus_visible).toBe(true);
});
