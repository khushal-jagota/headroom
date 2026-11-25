/** Shared admission and recovery risks that isolated receipt and claim tests do not cover. */
import { expect, test, vi } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { ConversationLinkLock } from '../runtime/conversation_start.ts';
import { ConversationStartRequest, PromptDeliveryQueued } from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { resume_recipient_deliveries } from '../message_delivery/recovery.ts';
import {
  decode_recipient_message,
  mark_recipient_attempt,
  finish_recipient_delivery,
  link_recipient_command,
  with_recipient_attempts,
  recipient_command_is_current,
  prune_finished_recipient_deliveries,
  hold_recipient_delivery,
} from '../message_delivery/data/recipient_deliveries.ts';
import { fixture } from './ticket_fixture.ts';

// Existing native-receipt tests do not interleave two producers through the admission lock.
// Reuse their scripted process. The second conversation proves the absence of a global wait.
test('durable admission releases two senders and another recipient before a native receipt', async () => {
  const f = setup({ behavior: { barrier: 'native' } });
  await f.start();
  const lock = new ConversationLinkLock();
  const first = lock.run(() => f.send('first', 'first'));
  const child = await f.child;
  await child.wait_for_barrier('native');
  try {
    const duplicate = lock.run(() => f.send('first', 'first'));
    expect(await lock.run(() => f.send('second', 'second'))).toBeInstanceOf(PromptDeliveryQueued);
    expect(
      f.database
        .read((sql) =>
          sql.all<{ submission_id: string }>(
            'SELECT submission_id FROM recipient_deliveries ORDER BY delivery_order',
          ),
        )
        .map((row) => row.submission_id),
    ).toEqual(['core:first', 'core:second']);
    await f.core.start_conversation(
      new ConversationStartRequest({
        conversation_id: 'other',
        model: 'model',
        workspace_folder: '/workspace',
      }),
    );
    const independent = f.core.send('other', text_message_content('independent'), {
      sender_label: 'Owner',
      sender_message_id: 'independent',
    });
    await vi.waitFor(() => expect(f.children).toHaveLength(2));
    await f.children[1].wait_for_barrier('native');
    await f.children[1].release('native');
    await independent;
    expect((await f.children[1].inspect()).delivered_text).toEqual(['independent']);
    await child.release('native');
    await Promise.all([first, duplicate]);
    expect((await child.inspect()).delivered_text).toEqual(['Role\n\nfirst']);
  } finally {
    await child.release('native');
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// Previous restart tests prove uncertainty, but do not restore the held line for every sender.
test('restart restores held prompts in order and delivers their real content once', async () => {
  const f = setup();
  await f.start();
  await f.send('running');
  await f.send('pending earlier', 'earlier');
  await f.send('held one', 'one');
  await f.send('held two', 'two');
  f.database.write((tx) => {
    tx.run(
      "DELETE FROM conversation_held_prompts WHERE json_extract(payload,'$.sender_message_id')='earlier'",
    );
    tx.run("UPDATE recipient_deliveries SET state='pending' WHERE submission_id='core:earlier'");
    tx.run(
      "UPDATE recipient_deliveries SET state='dispatching',native_transport='mailbox' WHERE submission_id='core:one'",
    );
    tx.run(
      "UPDATE conversation_held_prompts SET dispatching=1,dispatch_turn=2 WHERE json_extract(payload,'$.sender_message_id')='one'",
    );
  });
  await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  const restarted = setup({ restart_of: f.database });
  try {
    expect((await restarted.core.held_prompts('core')).map((row) => row.sender_message_id)).toEqual(
      ['one', 'two'],
    );
    resume_recipient_deliveries(restarted.core);
    const child = await restarted.child;
    await vi.waitFor(async () => {
      const texts = (await child.inspect()).delivered_text;
      expect(texts).toHaveLength(1);
      expect(texts[0]).toMatch(/pending earlier[\s\S]*held one[\s\S]*held two/);
    });
    await vi.waitFor(() =>
      expect(
        restarted.database.read((sql) => sql.all('SELECT * FROM conversation_held_prompts')),
      ).toEqual([]),
    );
    resume_recipient_deliveries(restarted.core);
    expect((await child.inspect()).delivered_text).toHaveLength(1);
  } finally {
    await restarted.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// A replacement after local admission must never receive the old recipient's input.
test.each(['conversation', 'generation'] as const)(
  'a replaced recipient %s is refused before its prompt reaches a backend',
  async (replacement) => {
    const f = setup();
    await f.start();
    f.database.write((tx) =>
      tx.run('INSERT INTO agents(agent_key,conversation_id) VALUES(?,?)', [
        'chief_of_staff',
        'core',
      ]),
    );
    try {
      const fate = await f.core.send('core', text_message_content('old target'), {
        sender_label: 'Owner',
        sender_message_id: 'stale',
        recipient: { kind: 'chief', id: 'chief' },
        recording_receipt: {
          sender_id: '',
          submission_id: 'stale',
          admitted: () => {
            f.database.write((tx) => {
              if (replacement === 'conversation')
                tx.run('UPDATE agents SET conversation_id=? WHERE agent_key=?', [
                  'replacement',
                  'chief_of_staff',
                ]);
              else tx.run("INSERT INTO recipient_lifecycles VALUES('chief','chief',1,0)");
            });
          },
        },
      });
      expect(fate).toMatchObject({ refusal_reason: 'no_such_conversation' });
      expect(
        f.database.read((sql) =>
          sql.get<{ state: string }>(
            'SELECT state FROM recipient_deliveries WHERE submission_id=?',
            ['core:stale'],
          ),
        )?.state,
      ).toBe('refused');
      for (const child of f.children) expect((await child.inspect()).delivered_text).toEqual([]);
    } finally {
      await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
    }
  },
);

// Crash snapshots distinguish resumable work, known non-arrival, and a possible native effect.
// They reuse the same record and process fixture rather than create another host.
test.each([
  'pending',
  'dispatching',
  'steer_refused',
  'local_failure',
  'mailbox_unattempted',
] as const)('restart reconciles the %s dispatch boundary without guessing', async (state) => {
  const f = setup();
  await f.start();
  await expect(
    f.core.send('core', text_message_content('surviving input'), {
      sender_label: 'Owner',
      sender_message_id: 'crash-boundary',
      mode: state === 'steer_refused' ? 'steer' : 'queue',
      recording_receipt: {
        sender_id: '',
        submission_id: 'crash-boundary',
        admitted: () => {
          if (state === 'local_failure') throw new Error('crash after commit');
          throw new DOMException('crash after commit', 'AbortError');
        },
      },
    }),
  ).rejects.toThrow('crash after commit');
  if (state === 'dispatching' || state === 'steer_refused' || state === 'mailbox_unattempted')
    f.database.write((tx) =>
      tx.run(
        "UPDATE recipient_deliveries SET state='dispatching' WHERE submission_id='core:crash-boundary'",
      ),
    );
  if (state === 'mailbox_unattempted')
    f.database.write((tx) =>
      tx.run(
        "UPDATE recipient_deliveries SET native_transport='mailbox' WHERE submission_id='core:crash-boundary'",
      ),
    );
  if (state === 'steer_refused')
    f.database.write((tx) => {
      tx.run(
        "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms,outcome_json) VALUES('refused-steer','old','steer','core','child',1,'{}','finished',1,?)",
        [
          JSON.stringify({
            child_outcome: { disposition: 'succeeded', result: { kind: 'steer_refused' } },
          }),
        ],
      );
      tx.run(
        "INSERT INTO recipient_delivery_commands SELECT 'refused-steer',submission_id,attempt_id FROM recipient_deliveries WHERE submission_id='core:crash-boundary'",
      );
    });
  await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  const restarted = setup({ restart_of: f.database });
  try {
    resume_recipient_deliveries(restarted.core);
    if (state === 'pending' || state === 'steer_refused' || state === 'mailbox_unattempted') {
      const child = await restarted.child;
      await vi.waitFor(async () =>
        expect((await child.inspect()).delivered_text).toEqual(['Role\n\nsurviving input']),
      );
    } else {
      expect(restarted.children).toHaveLength(0);
      await vi.waitFor(() =>
        expect(restarted.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
          state === 'local_failure' ? 'prompt_delivery_refused' : 'prompt_delivery_uncertain',
        ]),
      );
    }
    resume_recipient_deliveries(restarted.core);
    expect(restarted.children).toHaveLength(
      state === 'pending' || state === 'steer_refused' || state === 'mailbox_unattempted' ? 1 : 0,
    );
  } finally {
    await restarted.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// A failed delivery commit must not leave a cleared recap without its obligation.
test('recap clearing and recipient custody commit or roll back together', async () => {
  const { request_recap } = await import('../message_delivery/recap_request.ts');
  const { update_chief_recap, read_chief_settings } = await import('../worker_settings/service.ts');
  const { OWNER } = await import('../core/contracts.ts');
  const { buildClock } = await import('../core/clock.ts');
  const { loadConfig } = await import('../core/config.ts');
  const f = setup();
  await f.start();
  f.database.write((tx) =>
    tx.run('INSERT INTO agents(agent_key,conversation_id) VALUES(?,?)', ['chief_of_staff', 'core']),
  );
  update_chief_recap(f.database, { recap: 'Keep this recap' });
  const clock = buildClock(loadConfig(undefined, { PLAN_TEST_MODE: '1' }));
  const target = { kind: 'chief' as const, id: 'chief' };
  f.database.write((tx) =>
    tx.run(
      "CREATE TRIGGER reject_recap AFTER UPDATE OF recap ON chief_settings WHEN NEW.recap='' BEGIN SELECT RAISE(ABORT,'commit failed'); END",
    ),
  );
  try {
    await expect(
      request_recap(f.core, f.database, clock, { principal: OWNER, conversationId: null }, target),
    ).rejects.toThrow('commit failed');
    f.database.write((tx) => tx.run('DROP TRIGGER IF EXISTS reject_recap'));
    expect(f.database.read((sql) => read_chief_settings(sql)).recap).toBe('Keep this recap');
    expect(f.database.read((sql) => sql.all('SELECT * FROM recipient_deliveries'))).toEqual([]);
    const admitted = await request_recap(
      f.core,
      f.database,
      clock,
      { principal: OWNER, conversationId: null },
      target,
    );
    expect(admitted.conversation_id).toBe('core');
    expect(f.database.read((sql) => read_chief_settings(sql)).recap).toBe('');
    expect(f.database.read((sql) => sql.all('SELECT * FROM recipient_deliveries'))).toHaveLength(1);
  } finally {
    f.database.write((tx) => tx.run('DROP TRIGGER IF EXISTS reject_recap'));
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// The source can finish at acceptance. Shutdown must still own the service's local tail.
test('shutdown retains the admitted delivery and its original conversation link', async () => {
  const { send_message } = await import('../message_delivery/service.ts');
  const { configure_conversation_start, read_agent_conversation } =
    await import('../runtime/conversation_start.ts');
  const { OWNER } = await import('../core/contracts.ts');
  const { buildClock } = await import('../core/clock.ts');
  const { loadConfig } = await import('../core/config.ts');
  const { Deferred } = await import('../core/deferred.ts');
  const f = setup({ behavior: { barrier: 'shutdown-receipt' } });
  configure_conversation_start(f.database, {
    db_path: f.database_path,
    workspace_root: f.database_path,
  });
  const accepted = new Deferred<void>();
  const delivery = send_message(
    f.core,
    f.database,
    buildClock(loadConfig(undefined, { PLAN_TEST_MODE: '1' })),
    { principal: OWNER, conversationId: null },
    { kind: 'chief', id: 'chief' },
    'retained input',
    'queue',
    {
      created_conversation_id: 'new-chief',
      sender_message_id: 'shutdown-message',
      recording_receipt: {
        sender_id: '',
        submission_id: 'shutdown-message',
        admitted: () => accepted.resolve(),
      },
    },
  ).catch((error) => error);
  await accepted.promise;
  const child = await f.child;
  await child.wait_for_barrier('shutdown-receipt');
  await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  expect((await delivery).name).toBe('AbortError');
  expect(read_agent_conversation(f.database, 'chief_of_staff')).toBe('new-chief');
  await f.core.settled();
});

// A queued result can return after the drain starts. It cannot replace that native attempt.
test('a delayed queued result and an obsolete command cannot replace a newer attempt', async () => {
  const f = setup();
  await f.start();
  await f.send('running');
  await f.send('held', 'held');
  try {
    const held = f.database.read((sql) =>
      sql.get<{ submission_id: string; attempt_id: string; payload: string }>(
        "SELECT submission_id,attempt_id,payload FROM recipient_deliveries WHERE submission_id='core:held'",
      ),
    )!;
    const message = decode_recipient_message(held.payload);
    const [dispatch] = mark_recipient_attempt(f.database, 'core', [message], 2, 'mailbox');
    finish_recipient_delivery(f.database, held, new PromptDeliveryQueued({ queue_position: 1 }));
    expect(
      f.database.read((sql) =>
        sql.get<{ state: string }>(
          "SELECT state FROM recipient_deliveries WHERE submission_id='core:held'",
        ),
      )?.state,
    ).toBe('dispatching');
    await with_recipient_attempts([dispatch], async () =>
      f.database.write((tx) => {
        tx.run(
          "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms) VALUES('old-attempt','old','write_prompt','core','child',2,'{}','waiting',1)",
        );
        link_recipient_command(tx, 'old-attempt', 'write_prompt');
      }),
    );
    expect(f.database.read((sql) => recipient_command_is_current(sql, 'old-attempt'))).toBe(true);
    f.database.write((tx) => hold_recipient_delivery(tx, 'core', message.sender_message_id));
    expect(f.database.read((sql) => recipient_command_is_current(sql, 'old-attempt'))).toBe(false);
    const [next] = mark_recipient_attempt(f.database, 'core', [message], 3, 'mailbox');
    expect(next.attempt_id).not.toBe(dispatch.attempt_id);
    expect(f.database.read((sql) => recipient_command_is_current(sql, 'old-attempt'))).toBe(false);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + 10 });
  }
});

// Retention must release old payloads without dropping the fence for an unresolved native input.
test('terminal retention removes settled payloads but retains unresolved command fences', () => {
  const database = fixture();
  database.write((tx) => {
    for (const [id, state] of [
      ['settled', 'delivered'],
      ['waiting', 'refused'],
      ['unknown', 'uncertain'],
    ])
      tx.run(
        'INSERT INTO recipient_deliveries(submission_id,recipient_kind,recipient_id,conversation_id,generation,payload,state,attempt_id,settled_at) VALUES(?,?,?,?,?,?,?,?,?)',
        [
          id,
          'conversation',
          'core',
          'core',
          '{}',
          '{"text":"retained content"}',
          state,
          id,
          Date.now() - 8 * 24 * 60 * 60 * 1000,
        ],
      );
    for (const [id, state] of [
      ['settled', 'finished'],
      ['waiting', 'waiting'],
    ]) {
      tx.run(
        "INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms) VALUES(?,'old','write_prompt','core','child',1,'{}',?,1)",
        [id, state],
      );
      tx.run('INSERT INTO recipient_delivery_commands VALUES(?,?,?)', [id, id, id]);
    }
    prune_finished_recipient_deliveries(tx);
  });
  expect(
    database
      .read((sql) =>
        sql.all<{ submission_id: string }>(
          'SELECT submission_id FROM recipient_deliveries ORDER BY submission_id',
        ),
      )
      .map((row) => row.submission_id),
  ).toEqual(['unknown', 'waiting']);
  expect(
    database
      .read((sql) =>
        sql.all<{ command_id: string }>('SELECT command_id FROM recipient_delivery_commands'),
      )
      .map((row) => row.command_id),
  ).toEqual(['waiting']);
});
