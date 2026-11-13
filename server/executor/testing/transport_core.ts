/** Continue the existing transport host through actual core/run consumers. */
import { setTimeout as pause } from 'node:timers/promises';
import type { Database } from '../../core/database/contracts.ts';
import { ConversationStore } from '../../conversation/record/data/store.ts';
import { deployment_participants } from '../../conversation/deploy_interruptions/participants.ts';
import { SqliteProcessConversationSystem } from '../../conversation/system.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import { Deferred } from '../../conversation/runtime/state.ts';
import { executor_remote_recovery } from '../../lifecycle/production.ts';
import type { VpsExecutor } from '../vps_transport.ts';
import type { WireValue } from '../contracts.ts';
import { as_object, decode_json } from '../protocol.ts';
import type { MacRemoteChild } from '../vps_remote_child.ts';
import { model_is_enabled } from '../../conversation/backend_state.ts';
import { ConversationLiveTail } from '../../conversation/live_tail.ts';
import type { EnvelopeKind, BackendEventMethod } from '../wire.generated.ts';
import { StallChecker } from '../../stalls/checker.ts';
import { WAITS } from '../../stalls/registry.ts';
import { run_watchdog } from '../../stalls/watchdog.ts';
import { register_subscription } from '../../notifications/data.ts';
import type { NotificationIntent } from '../../notifications/contracts.ts';

export async function continue_core(database: Database, endpoint: VpsExecutor) {
  const store = new ConversationStore(database, {
    ...deployment_participants(endpoint.db_path),
    integer_now: () => 1760000000,
  });
  database.write((tx) => store.reconcile_in_transaction(tx, endpoint.open_runs()));
  const remote = endpoint.runs.as_factory();
  const tail = new ConversationLiveTail();
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
  const identity = endpoint.presence.current_identity()!;
  const address = endpoint.app.server.address();
  if (address === null || typeof address === 'string' || address.address !== '127.0.0.1')
    throw new Error('Core peer requires this fixture-owned listener');
  const origin = `http://127.0.0.1:${address.port}`;
  async function peer(method: string, path: string, current: typeof identity, value?: unknown) {
    const target = new URL(path, origin);
    if (target.origin !== origin || !path.startsWith('/api/executor/'))
      throw new Error('Core peer request left its owned listener');
    const response = await fetch(target, {
      method,
      body: value === undefined ? undefined : JSON.stringify(value),
      headers: {
        'tailscale-user-login': 'owner@example.com',
        'x-panels-executor-id': 'test-executor',
        'x-panels-executor-incarnation': current.executor_incarnation,
        'x-panels-executor-protocol': '22',
      },
      redirect: 'error',
      signal: AbortSignal.timeout(5000),
    });
    const raw = Buffer.from(await response.arrayBuffer());
    if (response.status !== (path.endsWith('/live') ? 204 : 200))
      throw new Error(`Core peer failed: ${response.status} ${raw}`);
    return raw;
  }
  const poll = async (current: typeof identity) =>
    decode_json(
      await peer('GET', '/api/executor/commands?after=0&wait=0', current),
      'MailboxPollReply',
    );
  const rows = (query: string) =>
    database.read((sql) => sql.all<Record<string, unknown>>(query).map(Object.values));
  async function until(predicate: () => boolean) {
    const deadline = performance.now() + 5000;
    while (!predicate()) {
      if (performance.now() >= deadline) throw new Error('core continuation did not settle');
      await pause(1);
    }
  }
  async function finish(commands: readonly WireValue<'MailboxCommand'>[]) {
    await peer('POST', '/api/executor/commands/outcomes', identity, {
      outcomes: commands.flatMap((row) =>
        row.child_command == null
          ? []
          : [
              {
                command_id: row.command_id,
                child_outcome: {
                  command_id: row.command_id,
                  child: row.child_command!.child,
                  disposition: 'succeeded' as const,
                  result: {},
                },
                model_list: null,
              },
            ],
      ),
    });
    for (const row of commands) {
      const command = row.child_command;
      if (command?.operation !== 'stop') continue;
      const outcome: WireValue<'CommandOutcome'> = {
        command_id: command.command_id,
        child: command.child,
        disposition: 'succeeded',
        result: {},
      };
      const reply = decode_json(
        await peer(
          'POST',
          `/api/executor/conversations/${command.child.conversation_id}/items`,
          identity,
          {
            sender_id: 'fixture-stops',
            items: [
              {
                sender_refusal: null,
                envelope: {
                  sender_id: 'fixture-stops',
                  conversation_id: command.child.conversation_id,
                  sequence: 1,
                  submission_id: 'stopped',
                  kind: 'child_stopped',
                  payload: as_object(outcome, 'CommandOutcome'),
                  file_dependencies: [],
                },
              },
            ],
          },
        ),
        'ItemBatchReply',
      );
      if (reply.receipts[0]?.disposition !== 'accepted')
        throw new Error('stop proof was not accepted');
    }
  }
  const sequences = new Map<string, number>();
  const envelope = (id: string, kind: EnvelopeKind, payload: WireValue<'JsonObject'>) => {
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
  };
  async function submit(items: WireValue<'QueuedEnvelope'>[], current = identity) {
    return decode_json(
      await peer(
        'POST',
        `/api/executor/conversations/${items[0]!.conversation_id}/items`,
        current,
        {
          sender_id: 'fixture-peer',
          items: items.map((envelope) => ({ envelope, sender_refusal: null })),
        },
      ),
      'ItemBatchReply',
    );
  }
  function event(
    command: WireValue<'ChildCommand'>,
    method: BackendEventMethod,
    args: WireValue<'JsonObject'>,
    turn = command.turn,
  ) {
    return { child: command.child, turn, method, arguments: args };
  }
  async function report(commands: readonly WireValue<'MailboxCommand'>[]) {
    await peer('POST', '/api/executor/commands/outcomes', identity, {
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
  }
  async function launched(id: string) {
    await core.send(id, text_message_content(`work ${id}`), { sender_label: 'Owner' });
    const commands = (await poll(identity)).commands;
    const command = commands.find(
      (row) =>
        row.child_command?.child.conversation_id === id &&
        row.child_command.operation === 'write_prompt',
    )!.child_command!;
    await report(commands);
    return command;
  }
  async function stop_proof(command: WireValue<'ChildCommand'>) {
    return submit([
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
      ),
    ]);
  }
  async function join_boundaries() {
    const live = await launched('live');
    const watcher = tail.subscribe('live');
    const before = rows('SELECT count(*) FROM conversation_events')[0]![0];
    const positions = rows('SELECT count(*) FROM mac_inbox_positions')[0]![0];
    const live_events = [
      event(live, 'agent_message_delta', { text_delta: 'Live text' }),
      event(live, 'model_thinking_happened', {}),
      event(live, 'tool_call_progress', { tool_call_id: 'tool', detail: 'Working' }),
    ];
    await peer('POST', '/api/executor/conversations/live/live', identity, {
      events: [
        event(
          live,
          'agent_message_delta',
          { text_delta: 'Wrong turn' },
          { conversation_id: 'live', turn_number: 99 },
        ),
        ...live_events,
      ],
    });
    const frames = [];
    for (let i = 0; i < 3; i++) frames.push((await watcher.next_item())!.constructor.name);
    watcher.close();
    if (
      before !== rows('SELECT count(*) FROM conversation_events')[0]![0] ||
      positions !== rows('SELECT count(*) FROM mac_inbox_positions')[0]![0]
    )
      throw new Error('live frames reached durable storage');
    const completed = envelope(
      'live',
      'backend_event',
      as_object(
        event(live, 'agent_message_completed', { content: [{ text: 'Full reply' }] }),
        'BackendEvent',
      ),
    );
    const accepted = await submit([completed]);
    const replay = await submit([completed]);
    if (JSON.stringify(accepted) !== JSON.stringify(replay))
      throw new Error('completed message replay changed its receipt');
    // Backend prose is runtime output. Its durable receipt adds no notebook row.
    if (
      accepted.receipts[0]?.result.applied !== true ||
      rows(
        "SELECT count(*) FROM conversation_events WHERE conversation_id='live' AND kind='agent_message'",
      )[0]![0] !== 0
    )
      throw new Error('completed prose changed the notebook');
    let kill_done = false;
    const killing_live = core.kill('live').then(() => {
      kill_done = true;
    });
    await until(
      () =>
        rows("SELECT 1 FROM executor_commands WHERE conversation_id='live' AND operation='stop'")
          .length > 0,
    );
    const commands = (await poll(identity)).commands;
    const stop = commands.find(
      (row) =>
        row.child_command?.child.conversation_id === 'live' &&
        row.child_command.operation === 'stop',
    )!.child_command!;
    await report(commands);
    if (kill_done || !core.conversations.get('live')!.child)
      throw new Error('a stop result released a child before its proof');
    await stop_proof(stop);
    await killing_live;
    const current = await launched('live');
    const current_watcher = tail.subscribe('live');
    await peer('POST', '/api/executor/conversations/live/live', identity, {
      events: [
        event(live, 'agent_message_delta', { text_delta: 'Retired child' }, current.turn),
        event(current, 'agent_message_delta', { text_delta: 'New child' }),
      ],
    });
    const current_frame = await current_watcher.next_item();
    current_watcher.close();
    if (
      !current_frame ||
      !('text_delta' in current_frame) ||
      current_frame.text_delta !== 'New child'
    )
      throw new Error('retired live child reached the subscriber');

    const ordinary = await launched('ordinary');
    await submit([
      envelope(
        'ordinary',
        'backend_event',
        as_object(
          event(ordinary, 'permission_ask_raised', {
            ask: {
              ask_id: 'ask',
              title: 'Fixture',
              detail: null,
              options: [{ option_id: 'allow', label: 'Allow', option_kind: 'allow_once' }],
            },
          }),
          'BackendEvent',
        ),
      ),
    ]);
    const ask_id = [...(await core.pending_permission_ask_ids('ordinary'))][0]!;
    if ((await core.answer_permission_ask('ordinary', ask_id, 'allow')) !== 'saved')
      throw new Error('answer was not saved');
    const answer = (await poll(identity)).commands.find(
      (row) =>
        row.child_command?.child.conversation_id === 'ordinary' &&
        row.child_command.operation === 'answer_permission_ask',
    )!.child_command!;
    const application = (answer.arguments as WireValue<'AnswerPermission'>).answer!;
    const entered_application = new Deferred<void>(),
      release_application = new Deferred<void>();
    const take_application = core.take_answer_application.bind(core);
    core.take_answer_application = async (...args) => {
      entered_application.resolve();
      await release_application.promise;
      return take_application(...args);
    };
    let ordinary_done = false;
    const killing_ordinary = core.kill('ordinary').then(() => {
      ordinary_done = true;
    });
    await until(
      () =>
        rows(
          "SELECT 1 FROM executor_commands WHERE conversation_id='ordinary' AND operation='stop'",
        ).length > 0,
    );
    const pending = (await poll(identity)).commands;
    const ordinary_stop = pending.find(
      (row) =>
        row.child_command?.child.conversation_id === 'ordinary' &&
        row.child_command.operation === 'stop',
    )!.child_command!;
    await report(pending);
    const batch = [
      envelope(
        'ordinary',
        'answer_application',
        as_object(
          { answer_id: application.answer_id, ask: application.ask, applied_at_mac_ms: Date.now() },
          'AnswerApplication',
        ),
      ),
      envelope(
        'ordinary',
        'backend_event',
        as_object(
          event(
            ordinary,
            'vendor_session_cursor_rebound',
            { vendor_session_cursor: 'cleanup-cursor' },
            null,
          ),
          'BackendEvent',
        ),
      ),
      envelope(
        'ordinary',
        'child_stopped',
        as_object(
          {
            command_id: ordinary_stop.command_id,
            child: ordinary_stop.child,
            disposition: 'succeeded',
            result: {},
          },
          'CommandOutcome',
        ),
      ),
    ];
    const saving = submit(batch.slice(0, 1));
    let final_saving: ReturnType<typeof submit> | undefined;
    let gate_timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        entered_application.promise,
        saving.then((reply) => {
          throw new Error(`application did not enter its core: ${JSON.stringify(reply)}`);
        }),
        new Promise<never>((_resolve, reject) => {
          gate_timer = setTimeout(
            () => reject(new Error('application did not reach its core within five seconds')),
            5000,
          );
        }),
      ]);
      final_saving = submit(batch.slice(1));
      if (ordinary_done || !core.conversations.get('ordinary')!.child)
        throw new Error('kill passed an admitted application');
      if (
        rows("SELECT 1 FROM mac_inbox_positions WHERE conversation_id='ordinary' AND sequence>1")
          .length
      )
        throw new Error('later event passed the held application');
    } finally {
      clearTimeout(gate_timer);
      release_application.resolve();
    }
    const application_reply = await saving;
    const final_reply = await final_saving!;
    if (
      application_reply.receipts.length !== 1 ||
      application_reply.stop !== null ||
      final_reply.receipts.length !== 2 ||
      final_reply.stop !== null
    )
      throw new Error('application and final output did not retain their exact prefixes');
    const ordered = { receipts: [...application_reply.receipts, ...final_reply.receipts] };
    core.take_answer_application = take_application;
    await killing_ordinary;
    if (
      ordered.receipts.length !== 3 ||
      ordered.receipts.some((row) => row.disposition !== 'accepted')
    )
      throw new Error('final application/event/stop did not commit in order');
    if (
      ordered.receipts[2]!.result.stop_command_id !== ordinary_stop.command_id ||
      JSON.stringify(ordered.receipts[2]!.result.stopped_child) !==
        JSON.stringify(ordinary_stop.child)
    )
      throw new Error('stop proof named another child');

    const rebind = await launched('rebind');
    await submit([
      envelope(
        'rebind',
        'backend_event',
        as_object(event(rebind, 'turn_ended', { ending: 'completed' }), 'BackendEvent'),
      ),
    ]);
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
    await until(
      () =>
        rows("SELECT 1 FROM executor_commands WHERE conversation_id='rebind' AND operation='stop'")
          .length > 0,
    );
    const changing_commands = (await poll(identity)).commands;
    const rebind_stop = changing_commands.find(
      (row) =>
        row.child_command?.child.conversation_id === 'rebind' &&
        row.child_command.operation === 'stop',
    )!.child_command!;
    await report(changing_commands);
    if (
      rebound ||
      rows(
        "SELECT 1 FROM executor_commands WHERE conversation_id='rebind' AND operation='write_prompt'",
      ).length !== 1
    )
      throw new Error('rebind escaped its stop proof');
    await stop_proof(rebind_stop);
    await changing;
    await until(
      () =>
        rows(
          "SELECT 1 FROM executor_commands WHERE conversation_id='rebind' AND operation='write_prompt'",
        ).length === 2,
    );
    const joined = rows(
      "SELECT operation,command_json FROM executor_commands WHERE conversation_id='rebind' ORDER BY command_sequence",
    )
      .filter(([op]) => op === 'write_prompt' || op === 'start')
      .map(([op, raw]) => {
        const command = JSON.parse(String(raw)).child_command;
        return [
          op,
          command.turn,
          command.arguments.resolved_start?.model ?? null,
          command.arguments.sender_content ?? null,
        ];
      });
    return {
      frames,
      completed_receipt: accepted.receipts[0]!.result,
      completed_count: rows(
        "SELECT count(*) FROM conversation_events WHERE conversation_id='live' AND kind='agent_message'",
      )[0]![0],
      stop_waited_for_proof: true,
      ordered: ordered.receipts.map((row) => row.result.applied ?? row.result.recorded),
      rebind: joined,
      reservation: rows(
        "SELECT count(*) FROM conversation_events WHERE conversation_id='rebind' AND payload LIKE '%same-reservation%'",
      )[0]![0],
    };
  }
  async function isolation_alarm() {
    const a = await launched('isolate-a'),
      b = await launched('isolate-b');
    const entered_save = new Deferred<void>(),
      release_save = new Deferred<void>();
    const settle = store.settle_recording.bind(store);
    store.settle_recording = async (id, ...args) => {
      if (id === 'isolate-a') {
        entered_save.resolve();
        await release_save.promise;
      }
      return settle(id, ...args);
    };
    const old_now = Date.now;
    const waits_clock = Reflect.get(WAITS, 'clock');
    let at = old_now();
    Date.now = () => at;
    Reflect.set(WAITS, 'clock', () => at);
    const checker = new StallChecker(
      database,
      WAITS,
      () => endpoint.stall_sources(),
      () => at,
    );
    const pushes: NotificationIntent[] = [],
      chief: string[] = [];
    database.write((tx) =>
      register_subscription(tx, {
        endpoint: 'https://push.example/1',
        p256dh: 'key',
        auth: 'auth',
        now: 1,
      }),
    );
    const watch = () =>
      run_watchdog(endpoint.db_path, endpoint.db_path + '.watchdog', {
        now_ms: at,
        web_push: {
          async send(_subscription, intent) {
            pushes.push(intent);
            return { delivered: true, expired: false, error: null };
          },
        },
        apns: null,
        push_origin: null,
        message_chief(text) {
          chief.push(text);
          return true;
        },
      });
    const stuck = envelope(
      'isolate-a',
      'backend_event',
      as_object(
        event(a, 'tool_call_started', {
          tool_call_id: 'stuck',
          title: 'Stuck',
          tool_kind: 'other',
          detail: null,
        }),
        'BackendEvent',
      ),
    );
    const saving = submit([stuck]);
    try {
      await entered_save.promise;
      database.write((tx) => tx.get('SELECT 1'), { changeSignal: 'silent' });
      const lease = () =>
        peer('POST', '/api/executor/lease', identity, {
          identity,
          process_id: 4242,
          mac_wall_clock_ms: at,
          queue_depth: 1,
          oldest_item_age_ms: 0,
          waiting_conversations: [{ conversation_id: 'isolate-a', oldest_sequence: 1 }],
          waiting_conversations_truncated: false,
          working_turns: [],
          admission_stopped: false,
          going_to_sleep: false,
        });
      await submit([
        envelope(
          'isolate-b',
          'backend_event',
          as_object(
            event(b, 'agent_message_completed', { content: [{ text: 'Other reply' }] }),
            'BackendEvent',
          ),
        ),
      ]);
      await lease();
      await core.send('isolate-b', text_message_content('other next'), { sender_label: 'Owner' });
      await submit([
        envelope(
          'isolate-b',
          'backend_event',
          as_object(event(b, 'turn_ended', { ending: 'completed' }), 'BackendEvent'),
        ),
      ]);
      await until(
        () =>
          rows(
            "SELECT 1 FROM executor_commands WHERE conversation_id='isolate-b' AND operation='write_prompt'",
          ).length === 2,
      );
      await report((await poll(identity)).commands);
      if (
        rows(
          "SELECT 1 FROM conversation_events WHERE conversation_id='isolate-a' AND kind='tool_call_started'",
        ).length
      )
        throw new Error('held save committed before its release');
      checker.check_once();
      for (let n = 0; n < 19; n++) {
        at += 10000;
        await lease();
        checker.check_once();
      }
      const findings = checker
        .check_once()
        .filter((finding) => finding.conversation_id === 'isolate-a');
      if (
        findings
          .map((finding) => finding.kind)
          .sort()
          .join(',') !== 'mac_item_still_saving,mac_items_not_moving' ||
        findings.some((finding) => finding.ticket_title !== 'isolate-a' || !finding.ticket_id)
      )
        throw new Error('bound executor did not name both stalled-save and backlog findings');
      const first = await watch(),
        second = await watch();
      if (
        first.raised.length !== 2 ||
        first.pushes_delivered !== 1 ||
        second.raised.length ||
        pushes.length !== 1 ||
        chief.length !== 1
      )
        throw new Error('watchdog did not alarm once');
      const backlog = findings.find((finding) => finding.kind === 'mac_items_not_moving')!;
      release_save.resolve();
      const saved = await saving;
      if (saved.receipts[0]?.result.applied !== true) throw new Error('held save did not finish');
      await submit([stuck]);
      if (
        rows(
          "SELECT count(*) FROM conversation_events WHERE conversation_id='isolate-a' AND kind='tool_call_started'",
        )[0]![0] !== 1
      )
        throw new Error('released save did not apply once');
      at += 61000;
      const away = checker
        .check_once()
        .filter((finding) => finding.conversation_id === 'isolate-a');
      const final = await watch();
      if (
        away.length !== 1 ||
        away[0]!.finding_key !== backlog.finding_key ||
        away[0]!.stalled_since_ms !== backlog.stalled_since_ms ||
        final.raised.length ||
        pushes.length !== 1 ||
        chief.length !== 1
      )
        throw new Error('away backlog lost its alarm identity');
      return {
        independent_prompts: 2,
        a_events: rows(
          "SELECT count(*) FROM conversation_events WHERE conversation_id='isolate-a' AND kind='tool_call_started'",
        )[0]![0],
        kinds: findings.map((finding) => finding.kind).sort(),
        first_alarm: first.raised.length,
        pushes: pushes.map((intent) => ({ body: intent.body, route: intent.route })),
        chief,
        away: away.map((finding) => [
          finding.kind,
          finding.stalled_since_ms === backlog.stalled_since_ms,
        ]),
        raised_again: final.raised.length,
      };
    } finally {
      release_save.resolve();
      store.settle_recording = settle;
      await saving.catch(() => {});
      await checker.close();
      Date.now = old_now;
      Reflect.set(WAITS, 'clock', waits_clock);
    }
  }
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  const original = endpoint.mailbox.put_child_command.bind(endpoint.mailbox);
  let sending: ReturnType<SqliteProcessConversationSystem['send']> | undefined;
  let killing: Promise<void> | undefined;
  try {
    endpoint.mailbox.put_child_command = async (command, options) => {
      if (command.operation === 'write_prompt') {
        entered.resolve();
        await release.promise;
      }
      return original(command, options);
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
    if (fate?.constructor.name !== 'PromptDeliverySuppressed')
      throw new Error('initial delivery was not suppressed');
    endpoint.mailbox.put_child_command = original;
    await until(
      () =>
        rows(
          "SELECT 1 FROM executor_commands WHERE conversation_id='withdraw' AND operation='stop'",
        ).length > 0,
    );
    await finish((await poll(identity)).commands);
    await core.wait_until_quiescent();
    const joins = await join_boundaries();
    const isolation = await isolation_alarm();
    await core.send('recover', text_message_content('already ran'), { sender_label: 'Owner' });
    await finish((await poll(identity)).commands);
    const fenced = await launched('fenced');
    await core.send('fenced', text_message_content('waited'), {
      sender_label: 'Owner',
      sender_message_id: 'fenced-reservation',
    });
    await core.send('rewrite', text_message_content('never ran'), { sender_label: 'Owner' });
    const fresh = { ...identity, executor_incarnation: '22222222-2222-4222-8222-222222222222' };
    await peer('POST', '/api/executor/lease', fresh, {
      identity: fresh,
      process_id: 4242,
      mac_wall_clock_ms: Date.now(),
      queue_depth: 0,
      oldest_item_age_ms: null,
      waiting_conversations: [{ conversation_id: 'fenced', oldest_sequence: 1 }],
      waiting_conversations_truncated: false,
      working_turns: [],
      admission_stopped: false,
      going_to_sleep: false,
    });
    await poll(fresh);
    const ended = await submit(
      [
        envelope(
          'fenced',
          'backend_event',
          as_object(event(fenced, 'turn_ended', { ending: 'completed' }), 'BackendEvent'),
        ),
      ],
      fresh,
    );
    if (
      ended.receipts[0]?.result.applied !== true ||
      rows(
        "SELECT 1 FROM executor_commands WHERE conversation_id='fenced' AND operation='write_prompt'",
      ).length !== 1
    )
      throw new Error('replayed ending escaped its replacement fence');
    await peer('POST', '/api/executor/lease', fresh, {
      identity: fresh,
      process_id: 4242,
      mac_wall_clock_ms: Date.now(),
      queue_depth: 0,
      oldest_item_age_ms: null,
      waiting_conversations: [],
      waiting_conversations_truncated: false,
      working_turns: [],
      admission_stopped: false,
      going_to_sleep: false,
    });
    await until(
      () =>
        rows(
          "SELECT 1 FROM executor_commands WHERE executor_incarnation='" +
            fresh.executor_incarnation +
            "' AND operation='write_prompt' AND conversation_id IN ('recover','rewrite','fenced')",
        ).length === 3,
    );
    await core.wait_until_quiescent();
    await poll(fresh);
    return {
      kill: fate.constructor.name,
      joins,
      isolation,
      fenced: {
        prompts: rows(
          "SELECT executor_incarnation,command_json FROM executor_commands WHERE conversation_id='fenced' AND operation='write_prompt' ORDER BY command_sequence",
        ).map(([incarnation, raw]) => [
          incarnation === fresh.executor_incarnation ? 'fresh' : 'old',
          JSON.parse(String(raw)).child_command.arguments.sender_content,
        ]),
        endings: rows(
          "SELECT json_extract(payload,'$.ending') FROM conversation_events WHERE conversation_id='fenced' AND kind='turn_ended' ORDER BY sequence",
        ),
        refused: rows(
          "SELECT count(*) FROM conversation_events WHERE conversation_id='fenced' AND kind='prompt_delivery_refused'",
        )[0]![0],
      },
      commands: rows(
        "SELECT conversation_id,operation,state,command_json FROM executor_commands WHERE conversation_id IN ('withdraw','recover','rewrite') ORDER BY conversation_id,command_sequence",
      ).map(([conversation, operation, state, command]) => {
        const value = JSON.parse(String(command)).child_command;
        return [
          conversation,
          operation,
          state,
          value.turn,
          value.arguments.content ?? null,
          value.arguments.mode ?? null,
        ];
      }),
      interruptions: rows(
        'SELECT source_conversation_id,turn_number,recovery_state,detail FROM deploy_interruptions ORDER BY source_conversation_id',
      ),
      claims: rows(
        "SELECT conversation_id,worker_step_claim FROM tickets WHERE conversation_id IN ('withdraw','recover','rewrite') ORDER BY conversation_id",
      ),
      turns: rows(
        "SELECT conversation_id,turn_number,status FROM conversation_lifecycle_facts WHERE conversation_id IN ('withdraw','recover','rewrite') AND kind='turn' ORDER BY conversation_id,turn_number",
      ),
    };
  } finally {
    release.resolve();
    endpoint.mailbox.put_child_command = original;
    if (sending) await sending.catch(() => {});
    if (killing) await killing.catch(() => {});
    await core.shutdown({ deadline: performance.now() / 1000 + 5 });
    await core.settled();
    tail.close();
  }
}
