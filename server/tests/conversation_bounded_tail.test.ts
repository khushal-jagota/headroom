import { readSync } from 'node:fs';
import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ResolvedConversationStart } from '../conversation/contracts.ts';
import {
  AgentMessageDeltaFrame,
  CommandReplyEventPayload,
  ModelThinkingFrame,
  PromptEventPayload,
  TurnEndedEventPayload,
} from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { ConversationLiveTail, MAXIMUM_HELD_TAIL_ITEMS } from '../conversation/live_tail.ts';
import { conversation_view_tail } from '../conversation/views/routes.ts';
import type { ConversationViewRuntime } from '../conversation/views/routes.ts';
import { ViewBodyChanged } from '../conversation/views/data/storage.ts';
// The fixture projection cannot prove a subscription across replay, bounded live queues,
// or the immutable spool descriptor after a turn removes its pathname. Reuse the real
// SQLite fixture and one in-process tail; no browser, provider, or HTTP producer host.
test('replay joins live publication without a hole and bounded tails reset safely', async () => {
  const database = fixture();
  const store = new ConversationStore(database, { integer_now: () => 100 });
  const tail = new ConversationLiveTail();
  const id = 'bounded';
  store.create_conversation(
    new ResolvedConversationStart({
      conversation_id: id,
      backend_key: 'codex',
      model: 'model',
      reasoning_effort: null,
      role_materials: null,
      workspace_folder: '/fixture',
      access: 'full',
    }),
  );
  store.append_events(id, [
    new PromptEventPayload({
      content: text_message_content('Begin'),
      sender_label: 'owner',
      mode: 'queue',
    }),
  ]);
  let during_replay = true;
  const runtime: ConversationViewRuntime = {
    database,
    store,
    live_tail: tail,
    sse_heartbeat_ms: 100,
    async runtime_facts() {
      if (during_replay) {
        during_replay = false;
        // The tail subscribed before this snapshot read. This commit is both in the
        // snapshot and queued as a wake, so the wake must not repeat its rows.
        for (const row of store.append_events(id, [
          new CommandReplyEventPayload({ reply_text: 'During replay' }),
        ])) {
          tail.publish_event(row);
        }
      }
      return { running: true, permissions: [], inputs: [] };
    },
  };
  const frames = conversation_view_tail(runtime, id, 'full', true, null, []);
  try {
    const first = await frames.next();
    expect(first.value).toContain('event: snapshot');
    expect(first.value).toContain('"through_sequence":2');
    const repeated = await frames.next();
    expect(repeated.value).toContain('"from_sequence":2');
    expect(repeated.value).toContain('"upserts":[]');
    for (const row of store.append_events(id, [
      new CommandReplyEventPayload({ reply_text: 'After replay' }),
    ])) {
      tail.publish_event(row);
    }
    const live = await frames.next();
    expect(live.value).toContain('"from_sequence":2');
    expect(live.value).toContain('"through_sequence":3');
    expect(live.value).toContain('After replay');
    tail.publish_frame(id, new AgentMessageDeltaFrame({ text_delta: 'Live 雪😀' }));
    const output = tail.views.output(id)!;
    expect(tail.views.read_chunk(id, output.reference, 0).text).toBe('Live 雪😀');
    const source = tail.views.open_content(id, output.reference);
    try {
      for (const row of store.append_events(id, [
        new TurnEndedEventPayload({ ending: 'completed' }),
      ])) {
        tail.publish_event(row);
      }
      expect(() => tail.views.read_chunk(id, output.reference, 0)).toThrow(ViewBodyChanged);
      const bytes = Buffer.alloc(output.byte_count);
      expect(readSync(source.fd, bytes, 0, bytes.length, null)).toBe(output.byte_count);
      expect(bytes.toString()).toBe('Live 雪😀');
    } finally {
      source.close();
    }
    // More than 128 durable notifications reset the coalesced view subscription.
    for (const row of store.append_events(
      id,
      Array.from(
        { length: 129 },
        (_, index) => new CommandReplyEventPayload({ reply_text: String(index) }),
      ),
    )) {
      tail.publish_event(row);
    }
    expect((await frames.next()).value).toContain('"reason":"overflow"');
    expect((await frames.next()).done).toBe(true);
    const raw = tail.subscribe(id);
    for (let index = 0; index < MAXIMUM_HELD_TAIL_ITEMS + 1; index++) {
      tail.publish_frame(id, new ModelThinkingFrame());
    }
    let held = 0;
    while ((await raw.next_item()) !== null) {
      held++;
    }
    expect(held).toBe(MAXIMUM_HELD_TAIL_ITEMS);
    expect(tail.open_subscription_count()).toBe(0);
  } finally {
    await frames.return(undefined);
    tail.close_all_subscriptions();
  }
});
