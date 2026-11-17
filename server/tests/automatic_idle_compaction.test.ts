import { expect, test } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { create } from './ticket_fixture.ts';
import { TokenUsageReported } from '../conversation/backends/contracts.ts';

// The sweep must not spend a provider turn for a small, unknown, finished, or stopped conversation.
test('idle compaction requires a large recorded count, an unfinished ticket, and a live child', async () => {
  let now = 100;
  const f = setup({
    record: { integer_now: () => now },
    core: {
      unix_time_now: () => now,
      monotonic_now: () => now,
      idle_child_stop_after_seconds: 10000,
    },
  });
  const ticket = create(f.database);
  await f.start();
  f.database.write((tx) =>
    tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', ['core', ticket.id]),
  );
  await f.send();
  const child = f.children[0];
  await child.emit(new TokenUsageReported({ input_tokens: 29999 }));
  await child.finish();
  await f.core.wait_until_quiescent();
  now += 50 * 60;
  const due = () =>
    f.store.conversations_due_for_automatic_compaction({
      due_at_or_before: now - 3000,
      activity_after: now - 3600,
    });
  expect(due()).toEqual([]);
  const setCount = (count: number | null) =>
    f.database.write((tx) =>
      tx.run(
        "UPDATE conversation_events SET payload=json_set(payload,'$.input_tokens',?) WHERE conversation_id='core' AND kind='token_usage'",
        [count],
      ),
    );
  setCount(null);
  expect(due()).toEqual([]);
  setCount(30000);
  expect(due()).toEqual(['core']);
  f.database.write((tx) => tx.run("UPDATE tickets SET stage='done' WHERE id=?", [ticket.id]));
  expect(due()).toEqual([]);
  const selection = f.store.conversations_due_for_automatic_compaction.bind(f.store);
  f.store.conversations_due_for_automatic_compaction = () => ['core'];
  await f.core.sweep_idle_children();
  await f.core.wait_until_quiescent();
  expect((await child.inspect()).delivered_text).toEqual(['Role\n\none']);
  f.store.conversations_due_for_automatic_compaction = selection;
  f.database.write((tx) =>
    tx.run("UPDATE tickets SET stage='needs_implementation' WHERE id=?", [ticket.id]),
  );
  await f.core.sweep_idle_children();
  await f.core.wait_until_quiescent();
  expect((await child.inspect()).delivered_text).toEqual(['Role\n\none', '/compact']);
});

test('idle sweep does not restart a stopped child for compaction', async () => {
  let now = 100;
  const f = setup({
    record: { integer_now: () => now },
    core: { unix_time_now: () => now, monotonic_now: () => now },
  });
  await f.start();
  await f.send();
  const child = f.children[0];
  await child.emit(new TokenUsageReported({ input_tokens: 30000 }));
  await child.finish();
  await f.core.wait_until_quiescent();
  now += 31 * 60;
  await f.core.sweep_idle_children();
  expect(f.core.conversation_state('core')?.child).toBeNull();
  now += 19 * 60;
  await f.core.sweep_idle_children();
  await f.core.wait_until_quiescent();
  expect(f.children).toHaveLength(1);
  expect(
    f.store.read_events_after('core', 0).filter((event) => event.payload.kind === 'prompt'),
  ).toHaveLength(1);
});
