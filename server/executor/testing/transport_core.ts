/** Joined executor consumers on the existing fixture-owned listener. */
import assert from 'node:assert/strict';
import { setTimeout as pause } from 'node:timers/promises';
import type { Database } from '../../core/database/contracts.ts';
import { ConversationStore } from '../../conversation/record/data/store.ts';
import { deployment_participants } from '../../conversation/deploy_interruptions/participants.ts';
import { SqliteProcessConversationSystem } from '../../conversation/system.ts';
import { message_content_text, text_message_content } from '../../conversation/message_content.ts';
import { ConversationLiveTail } from '../../conversation/live_tail.ts';
import { Deferred } from '../../conversation/runtime/state.ts';
import { executor_remote_recovery } from '../../lifecycle/production.ts';
import { model_is_enabled } from '../../conversation/backend_state.ts';
import type { VpsExecutor } from '../vps_transport.ts';
import type { WireValue } from '../contracts.ts';
import type { BackendEventMethod, EnvelopeKind } from '../wire.generated.ts';
import type { MacRemoteChild } from '../vps_remote_child.ts';
import { native_message_content } from '../backend_events.ts';
import { as_object, decode_json } from '../protocol.ts';

export async function continue_core(database: Database, endpoint: VpsExecutor) {
  const store = new ConversationStore(database, {
    ...deployment_participants(endpoint.db_path),
    integer_now: () => 1760000000,
  });
  database.write((tx) => store.reconcile_in_transaction(tx, endpoint.open_runs()));
  const remote = endpoint.runs.as_factory(),
    tail = new ConversationLiveTail();
  let core!: SqliteProcessConversationSystem;
  core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: { codex: remote, claude: remote, hermes: remote },
    message_files: endpoint.message_files,
    model_is_enabled: (backend, model) =>
      database.read((sql) => model_is_enabled(sql, backend, model)),
    execution_refusal: () => null,
    remote_recovery: executor_remote_recovery(database, () => core),
    unix_time_now: () => 1760000000,
    publish_event: (event) => tail.publish_event(event),
    publish_frame: (id, frame) => tail.publish_frame(id, frame),
  });
  endpoint.bind_conversations(core);
  const identity = endpoint.presence.current_identity()!,
    address = endpoint.app.server.address();
  assert(address !== null && typeof address !== 'string' && address.address === '127.0.0.1');
  const origin = `http://127.0.0.1:${address.port}`;
  async function peer(path: string, value: unknown, current = identity) {
    assert(new URL(path, origin).origin === origin && path.startsWith('/api/executor/'));
    const response = await fetch(new URL(path, origin), {
      method: 'POST',
      body: JSON.stringify(value),
      redirect: 'error',
      signal: AbortSignal.timeout(5000),
      headers: {
        'tailscale-user-login': 'owner@example.com',
        'x-panels-executor-id': 'test-executor',
        'x-panels-executor-incarnation': current.executor_incarnation,
        'x-panels-executor-protocol': '22',
      },
    });
    const raw = Buffer.from(await response.arrayBuffer());
    assert.equal(response.status, path.endsWith('/live') ? 204 : 200, raw.toString());
    return raw;
  }
  const rows = (sql: string) =>
    database.read((tx) => tx.all<Record<string, unknown>>(sql).map(Object.values));
  const commands = (id: string, operation: string) =>
    database
      .read((sql) =>
        sql.all<{ command_json: string }>(
          'SELECT command_json FROM executor_commands WHERE conversation_id=? AND operation=? ORDER BY command_sequence',
          [id, operation],
        ),
      )
      .map((row) => decode_json(row.command_json, 'MailboxCommand').child_command!);
  const text = (command: WireValue<'ChildCommand'>) =>
    message_content_text(
      native_message_content((command.arguments as WireValue<'WritePrompt'>).sender_content),
    );
  async function until(ready: () => boolean) {
    const deadline = performance.now() + 5000;
    while (!ready()) {
      assert(performance.now() < deadline, 'core continuation did not settle');
      await pause(1);
    }
  }
  const poll = (current = identity) => endpoint.mailbox.poll(current, 0, 0);
  const report = (commands: readonly WireValue<'MailboxCommand'>[]) =>
    endpoint.mailbox.record_results(identity, {
      outcomes: commands
        .filter((row) => row.child_command)
        .map((row) => ({
          command_id: row.command_id,
          model_list: null,
          child_outcome: {
            command_id: row.command_id,
            child: row.child_command!.child,
            disposition: 'succeeded',
            result: {},
          },
        })),
    });
  const sequences = new Map<string, number>();
  function envelope(id: string, kind: EnvelopeKind, payload: WireValue<'JsonObject'>) {
    const sequence = (sequences.get(id) ?? 0) + 1;
    sequences.set(id, sequence);
    return {
      sender_id: 'fixture-peer',
      conversation_id: id,
      sequence,
      submission_id: `peer-${sequence}`,
      kind,
      payload,
      file_dependencies: [],
    };
  }
  const event = (
    command: WireValue<'ChildCommand'>,
    method: BackendEventMethod,
    args: WireValue<'JsonObject'>,
    turn = command.turn,
  ) => ({ child: command.child, turn, method, arguments: args });
  const stored = (
    command: WireValue<'ChildCommand'>,
    method: BackendEventMethod,
    args: WireValue<'JsonObject'>,
    turn = command.turn,
  ) =>
    envelope(
      command.child.conversation_id,
      'backend_event',
      as_object(event(command, method, args, turn), 'BackendEvent'),
    );
  const submit = async (items: WireValue<'QueuedEnvelope'>[], current = identity) =>
    decode_json(
      await peer(
        `/api/executor/conversations/${items[0]!.conversation_id}/items`,
        {
          sender_id: 'fixture-peer',
          items: items.map((envelope) => ({ envelope, sender_refusal: null })),
        },
        current,
      ),
      'ItemBatchReply',
    );
  const stopped = (command: WireValue<'ChildCommand'>) =>
    envelope(
      command.child.conversation_id,
      'child_stopped',
      as_object(
        {
          command_id: command.command_id,
          child: command.child,
          disposition: 'succeeded',
          result: {},
        },
        'CommandOutcome',
      ),
    );
  async function launched(id: string) {
    await core.send(id, text_message_content(`work ${id}`), { sender_label: 'Owner' });
    await report((await poll()).commands);
    return commands(id, 'write_prompt').at(-1)!;
  }
  const lease = (current: typeof identity, waiting: string[]) =>
    peer(
      '/api/executor/lease',
      {
        identity: current,
        process_id: 4242,
        mac_wall_clock_ms: Date.now(),
        queue_depth: waiting.length,
        oldest_item_age_ms: 0,
        waiting_conversations: waiting.map((conversation_id) => ({
          conversation_id,
          oldest_sequence: 1,
        })),
        waiting_conversations_truncated: false,
        working_turns: [],
        admission_stopped: false,
        going_to_sleep: false,
      },
      current,
    );
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  const original_put = endpoint.mailbox.put_child_command.bind(endpoint.mailbox);
  let sending: ReturnType<typeof core.send> | undefined, killing: Promise<void> | undefined;
  try {
    endpoint.mailbox.put_child_command = async (command, options) => {
      if (command.operation === 'write_prompt') {
        entered.resolve();
        await release.promise;
      }
      return original_put(command, options);
    };
    sending = core.send('withdraw', text_message_content('withdraw me'), { sender_label: 'Owner' });
    await entered.promise;
    killing = core.kill('withdraw');
    await until(
      () =>
        (core.conversations.get('withdraw')!.child as MacRemoteChild).initial_delivery_cancelled,
    );
    release.resolve();
    const fate = await sending;
    await killing;
    assert.equal(fate.constructor.name, 'PromptDeliverySuppressed');
    endpoint.mailbox.put_child_command = original_put;
    await until(() => commands('withdraw', 'stop').length === 1);
    await report((await poll()).commands);
    await submit([stopped(commands('withdraw', 'stop')[0]!)]);

    const ordinary = await launched('ordinary'),
      watcher = tail.subscribe('ordinary');
    const before = rows('SELECT count(*) FROM conversation_events')[0]![0];
    await peer('/api/executor/conversations/ordinary/live', {
      events: [
        event(
          ordinary,
          'agent_message_delta',
          { text_delta: 'Wrong turn' },
          { conversation_id: 'ordinary', turn_number: 99 },
        ),
        event(ordinary, 'agent_message_delta', { text_delta: 'Live text' }),
        event(ordinary, 'model_thinking_happened', {}),
        event(ordinary, 'tool_call_progress', { tool_call_id: 'tool', detail: 'Working' }),
      ],
    });
    const frames = [];
    for (let n = 0; n < 3; n++) frames.push((await watcher.next_item())!.constructor.name);
    watcher.close();
    assert.equal(rows('SELECT count(*) FROM conversation_events')[0]![0], before);
    const completed = stored(ordinary, 'agent_message_completed', {
      content: [{ text: 'Full reply' }],
    });
    const accepted = await submit([completed]);
    assert.deepEqual(await submit([completed]), accepted);
    assert.equal(accepted.receipts[0]!.result.applied, true);
    assert.equal(
      rows(
        "SELECT count(*) FROM conversation_events WHERE conversation_id='ordinary' AND kind='agent_message'",
      )[0]![0],
      0,
    );
    await submit([
      stored(ordinary, 'permission_ask_raised', {
        ask: {
          ask_id: 'ask',
          title: 'Fixture',
          detail: null,
          options: [{ option_id: 'allow', label: 'Allow', option_kind: 'allow_once' }],
        },
      }),
    ]);
    assert.equal(
      await core.answer_permission_ask(
        'ordinary',
        [...(await core.pending_permission_ask_ids('ordinary'))][0]!,
        'allow',
      ),
      'saved',
    );
    await poll();
    const answer = commands('ordinary', 'answer_permission_ask')[0]!
      .arguments as WireValue<'AnswerPermission'>;
    const gate = new Deferred<void>(),
      settle = store.settle_recording.bind(store);
    const b = await launched('other');
    let application_entered = false,
      kill_done = false;
    const ordinary_kill = core.kill('ordinary').then(() => {
      kill_done = true;
    });
    await until(() => commands('ordinary', 'stop').length === 1);
    const stop = commands('ordinary', 'stop')[0]!;
    await report((await poll()).commands);
    assert(!kill_done && core.conversations.get('ordinary')!.child);
    store.settle_recording = async (id, ...args) => {
      if (id === 'ordinary') {
        application_entered = true;
        await gate.promise;
      }
      return settle(id, ...args);
    };
    const application = envelope(
      'ordinary',
      'answer_application',
      as_object(
        {
          answer_id: answer.answer!.answer_id,
          ask: answer.answer!.ask,
          applied_at_mac_ms: Date.now(),
        },
        'AnswerApplication',
      ),
    );
    const saving = submit([application]);
    let final_saving: ReturnType<typeof submit> | undefined;
    try {
      await until(() => application_entered);
      final_saving = submit([
        stored(
          ordinary,
          'vendor_session_cursor_rebound',
          { vendor_session_cursor: 'cleanup-cursor' },
          null,
        ),
        stopped(stop),
      ]);
      assert(!kill_done && core.conversations.get('ordinary')!.child);
      await submit([stored(b, 'agent_message_completed', { content: [{ text: 'Other reply' }] })]);
      await lease(identity, ['ordinary']);
      await core.send('other', text_message_content('other next'), { sender_label: 'Owner' });
      await submit([stored(b, 'turn_ended', { ending: 'completed' })]);
      await until(() => commands('other', 'write_prompt').length === 2);
      assert.equal(
        rows("SELECT 1 FROM mac_inbox_positions WHERE conversation_id='ordinary' AND sequence>2")
          .length,
        0,
      );
    } finally {
      gate.resolve();
    }
    const applied = await saving,
      final = await final_saving!;
    store.settle_recording = settle;
    await ordinary_kill;
    assert(applied.stop === null && final.stop === null);
    assert.equal(applied.receipts.length, 1);
    assert.equal(final.receipts.length, 2);
    const receipts = [...applied.receipts, ...final.receipts];
    assert.equal(receipts[0]!.result.recorded, true);
    assert.equal(receipts[1]!.result.applied, true);
    assert.deepEqual(
      receipts.map((row) => row.disposition),
      ['accepted', 'accepted', 'accepted'],
    );
    assert.equal(receipts[2]!.result.stop_command_id, stop.command_id);
    assert.deepEqual(receipts[2]!.result.stopped_child, stop.child);
    const a_events = rows(
      "SELECT count(*) FROM conversation_events WHERE conversation_id='ordinary' AND kind='answer_applied'",
    )[0]![0];
    assert.equal(a_events, 1);
    const isolation = { independent_prompts: commands('other', 'write_prompt').length, a_events };
    const current = await launched('ordinary'),
      current_watcher = tail.subscribe('ordinary');
    await peer('/api/executor/conversations/ordinary/live', {
      events: [
        event(ordinary, 'agent_message_delta', { text_delta: 'Retired' }, current.turn),
        event(current, 'agent_message_delta', { text_delta: 'New child' }),
      ],
    });
    const frame = await current_watcher.next_item();
    current_watcher.close();
    assert(frame && 'text_delta' in frame && frame.text_delta === 'New child');

    const rebind = await launched('rebind');
    await submit([stored(rebind, 'turn_ended', { ending: 'completed' })]);
    let rebound = false;
    const changing = core
      .send('rebind', text_message_content('changed model'), {
        sender_label: 'Owner',
        sender_message_id: 'same-reservation',
        model_change: 'fixture-new',
      })
      .then(() => {
        rebound = true;
      });
    await until(() => commands('rebind', 'stop').length === 1);
    await report((await poll()).commands);
    assert(!rebound && commands('rebind', 'write_prompt').length === 1);
    await submit([stopped(commands('rebind', 'stop')[0]!)]);
    await changing;
    const rebound_prompt = commands('rebind', 'write_prompt')[1]!;
    const rebound_start = commands('rebind', 'start')[1]!.arguments as WireValue<'StartRun'>;
    const reservations = rows(
      "SELECT count(*) FROM conversation_events WHERE conversation_id='rebind' AND payload LIKE '%same-reservation%'",
    )[0]![0];
    assert.equal(reservations, 1);

    const fenced = await launched('fenced');
    await core.send('fenced', text_message_content('waited'), {
      sender_label: 'Owner',
      sender_message_id: 'fenced-reservation',
    });
    const fresh = { ...identity, executor_incarnation: '22222222-2222-4222-8222-222222222222' };
    await lease(fresh, ['fenced']);
    await poll(fresh);
    assert.equal(
      (await submit([stored(fenced, 'turn_ended', { ending: 'completed' })], fresh)).receipts[0]!
        .result.applied,
      true,
    );
    assert.equal(commands('fenced', 'write_prompt').length, 1);
    await lease(fresh, []);
    await until(() => commands('fenced', 'write_prompt').length === 2);
    await core.wait_until_quiescent();
    const waiting = commands('fenced', 'write_prompt')[1]!;
    assert.equal(waiting.child.executor_incarnation, fresh.executor_incarnation);
    assert.equal(text(waiting), 'waited');
    assert.deepEqual(
      rows(
        "SELECT json_extract(payload,'$.ending') FROM conversation_events WHERE conversation_id='fenced' AND kind='turn_ended'",
      ),
      [['completed']],
    );
    return {
      kill: fate.constructor.name,
      frames,
      ordered: receipts.map((row) => row.disposition === 'accepted'),
      rebind: {
        model: rebound_start.resolved_start.model,
        text: text(rebound_prompt),
        turn: rebound_prompt.turn!.turn_number,
        reservations,
      },
      isolation,
      replacement: { fenced_text: text(waiting), fenced_ending: 'completed' },
    };
  } finally {
    release.resolve();
    endpoint.mailbox.put_child_command = original_put;
    if (sending) await sending.catch(() => {});
    if (killing) await killing.catch(() => {});
    await core.shutdown({ deadline: performance.now() / 1000 + 5 });
    await core.settled();
    tail.close();
  }
}
