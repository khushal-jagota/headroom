import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { expect, test } from 'vitest';
import type { ConversationEventKind } from '../conversation/events.ts';
import { ConversationAccess, ResolvedConversationStart } from '../conversation/contracts.ts';
import {
  conversation_event_payload_from_canonical_json,
  conversation_event_payload_to_canonical_json,
} from '../conversation/record/codec.ts';
import { ConversationStorePrimitives } from '../conversation/record/data/store.ts';
import { append_projection } from '../conversation/views/data/writer.ts';
import { read_status } from '../conversation/views/status.ts';
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

function projected(recorded: [ConversationEventKind, unknown][] = events) {
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
    recorded.forEach(([kind, payload], index) => {
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
      recorded.length,
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

// Full opens a settled turn whole from its first page, with no request per turn or run.
// The live-change door must agree with that page, or a change lands where nothing is drawn.
test('Full’s timeline holds a settled turn whole, and its change frames agree', () => {
  const narration = (text: string): [ConversationEventKind, unknown] => [
    'agent_message',
    { content: [{ piece: 'text', text }] },
  ];
  const database = projected([
    events[0],
    narration('Looking'),
    events[1],
    events[2],
    events[3],
    ['tool_call_finished', { tool_call_id: 't2', tool_call_status: 'failed', detail: 'boom' }],
    narration('One more'),
    [
      'tool_call_started',
      { tool_call_id: 't3', title: 'Grep', tool_kind: 'search', detail: '{"pattern":"x"}' },
    ],
    ['tool_call_finished', { tool_call_id: 't3', tool_call_status: 'completed', detail: 'x' }],
    ...events.slice(5),
  ]);
  database.read((sql) => {
    const timeline = read_page(sql, 'c', 'full');
    expect(timeline.items.map((item) => item.key)).toEqual([
      'e1',
      'turn:e1',
      'e2',
      'work:e3',
      'e7',
      'work:e8',
      'e8',
      'e10',
      'e11',
    ]);
    expect(timeline.items.find((item) => item.key === 'work:e3')).toMatchObject({
      entry_count: 2,
      settled: true,
      call_summary: {
        kinds: [
          { kind: 'read', count: 1 },
          { kind: 'execute', count: 1 },
        ],
        failed_count: 1,
      },
    });
    // A run of one call is drawn as its call, so it needs no summary.
    expect(timeline.items.find((item) => item.key === 'work:e8')).not.toHaveProperty(
      'call_summary',
    );
    expect(read_page(sql, 'c', 'focus').items.map((item) => item.key)).toEqual([
      'e1',
      'turn:e1',
      'e10',
    ]);

    const status = { stopped_without_ending: false } as Parameters<
      typeof conversation_view_changes
    >[5];
    const range = {
      scope: { kind: 'timeline' as const, key: null },
      first: { sequence: 0, slot: 0 },
      last: { sequence: 11, slot: 1 },
    };
    const changes = conversation_view_changes(sql, 'c', 'full', 0, [range], status)!;
    expect(new Set(changes.scope_changes[0].item_keys)).toEqual(
      new Set(timeline.items.map((item) => item.key)),
    );
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

test('a queued message taken back to edit leaves no row; a plain discard keeps its row', () => {
  const discarded = (extra: object) =>
    conversation_event_payload_from_canonical_json(
      'prompt_discarded',
      JSON.stringify({
        sender_label: 'owner',
        content: [{ piece: 'text', text: 'Queued' }],
        sender: owner,
        ...extra,
      }),
    );
  const edited = discarded({ withdrawn_for_edit: true });
  // The mark survives the record, so a reload draws no ghost either.
  const stored = conversation_event_payload_to_canonical_json(edited);
  expect(JSON.parse(stored).withdrawn_for_edit).toBe(true);
  expect(
    event_effect(conversation_event_payload_from_canonical_json('prompt_discarded', stored))
      .row_kind,
  ).toBeNull();
  const removed = discarded({});
  expect(JSON.parse(conversation_event_payload_to_canonical_json(removed))).not.toHaveProperty(
    'withdrawn_for_edit',
  );
  expect(event_effect(removed).row_kind).toBe('prompt_discarded');
});

test('a resend folds the refused message it sends again away, in both lenses', () => {
  const words = {
    sender_label: 'owner',
    content: [{ piece: 'text', text: 'Retry' }],
    sender: owner,
  };
  const database = projected([
    [
      'prompt_delivery_refused',
      {
        ...words,
        mode: 'send_now',
        sender_message_id: 'a',
        refusal_reason: 'write_to_backend_failed',
      },
    ],
    ['prompt', { ...words, mode: 'queue', sender_message_id: 'b', reconciles_sequence: 1 }],
  ]);
  database.read((sql) => {
    for (const lens of ['focus', 'full'] as const) {
      const kinds = read_page(sql, 'c', lens).items.flatMap((item) =>
        item.kind === 'row' ? [item.row_kind] : [],
      );
      expect(kinds).not.toContain('prompt_refused');
    }
  });
});

// A prompted turn no longer supplies every turn boundary. Prove the new start opens
// its own projected turn so status can name it and tools cannot attach to the old turn.
test('an agent-started turn owns its work and keeps background task history', () => {
  const database = projected([
    ['prompt', { sender_label: 'owner', text: 'Delegate the check', mode: 'queue', sender: owner }],
    ['background_task_started', { task_id: 'task-1', task_description: 'Check tests' }],
    ['turn_ended', { ending: 'failed', error_summary: 'Earlier failure' }],
    [
      'background_task_ended',
      { task_id: 'task-1', task_description: 'Check tests', task_status: 'completed' },
    ],
    ['agent_turn_started', { turn_number: 2, reason: "Sub-agent 'Check tests' finished" }],
    [
      'tool_call_started',
      { tool_call_id: 't3', title: 'Read result', tool_kind: 'read', detail: null },
    ],
  ]);
  database.read((sql) => {
    const status = read_status(
      sql,
      'c',
      'focus',
      {
        runtime_epoch: 'test',
        output: () => null,
        status: () => [0, null, 0],
        latest_tool_id: () => null,
        progress: () => null,
        read_chunk: () => {
          throw new Error('No live output');
        },
      },
      { running: true },
    );
    expect(status).toMatchObject({
      turn_key: 'turn:e5',
      turn_running: true,
      rest: { last_boundary_failed: false },
    });
    const focus = read_page(sql, 'c', 'focus');
    expect(
      focus.items.find((item) => item.kind === 'row' && item.row_kind === 'agent_turn_started'),
    ).toMatchObject({
      body: {
        kind: 'inline',
        value: { kind: 'agent_turn_started', reason: "Sub-agent 'Check tests' finished" },
      },
    });
    expect(focus.items.find((item) => item.key === 'turn:e5')).toMatchObject({
      kind: 'turn',
      settled: false,
      started_at_unix_milliseconds: 105000,
      tool_call_count: 1,
    });
    const full = read_page(sql, 'c', 'full');
    expect(
      full.items
        .filter((item) => item.kind === 'row' && item.row_kind.startsWith('background_task_'))
        .map((item) => item.key),
    ).toEqual(['e2', 'e4']);
    expect(
      sql.get<{ current_turn_key: string }>(
        'SELECT current_turn_key FROM conversation_view_state WHERE conversation_id=?',
        ['c'],
      )?.current_turn_key,
    ).toBe('turn:e5');
  });
});

// The same completion must appear once, while an adjacent different task completion
// stays visible. The history remains canonical even when its display row is folded away.
test('an adjacent autonomous header replaces only its matching completion row', () => {
  for (const task of [
    { task_id: 'wake', task_description: 'Check tests', visible: false },
    { task_id: 'other', task_description: 'Build package', visible: true },
  ]) {
    const database = projected([
      [
        'background_task_started',
        { task_id: task.task_id, task_description: task.task_description },
      ],
      ['agent_turn_started', { turn_number: 1, reason: 'Background task "Check tests" completed' }],
      [
        'background_task_ended',
        {
          task_id: task.task_id,
          task_description: task.task_description,
          task_status: 'completed',
        },
      ],
      ['turn_ended', { ending: 'completed' }],
    ]);
    database.read((sql) => {
      const rows = read_page(sql, 'c', 'full').items.filter((item) => item.kind === 'row');
      expect(rows.filter((item) => item.row_kind === 'background_task_started')).toHaveLength(1);
      expect(rows.filter((item) => item.row_kind === 'agent_turn_started')).toHaveLength(1);
      expect(rows.some((item) => item.row_kind === 'background_task_ended')).toBe(task.visible);
      expect(
        sql.get<{ kind: string }>(
          'SELECT kind FROM conversation_events WHERE conversation_id=? AND sequence=?',
          ['c', 3],
        )?.kind,
      ).toBe('background_task_ended');
    });
  }
});
