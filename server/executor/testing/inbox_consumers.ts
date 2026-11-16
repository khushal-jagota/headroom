/** The canonical consumer pair needs no transport, listener, or provider process. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../core/database/index.ts';
import { TestClock } from '../../core/clock.ts';
import { PlannerError } from '../../core/contracts.ts';
import { parse_identity } from '../../execution_identity.ts';
import type { Principal } from '../../core/contracts.ts';
import type {
  ConversationSystem,
  RecordMessageToOwnerOptions,
} from '../../conversation/contracts.ts';
import type { MessageContent } from '../../conversation/message_content.ts';
import { MessageToOwnerEventPayload } from '../../conversation/events.ts';
import { MacInbox } from '../vps_inbox.ts';
import { InboxStore } from '../data/inbox.ts';
import { FileStaging } from '../vps_files.ts';
import { open_child_assignment } from '../data/child_custody.ts';
import { decode_value } from '../protocol.ts';
import type { WireTypes } from '../wire.generated.ts';

const spec = JSON.parse(readFileSync(0, 'utf8')) as unknown as {
  path: string;
  identity: WireTypes['ExecutorIdentity'];
  child: WireTypes['ChildReference'];
  ticket: string;
  edits: WireTypes['QueuedEnvelope'][];
  bad_batch: WireTypes['ItemBatch'];
  refused: WireTypes['QueuedEnvelope'];
  native: WireTypes['QueuedEnvelope'];
  following: WireTypes['QueuedEnvelope'];
  changed: WireTypes['QueuedEnvelope'];
};
Date.now = () => 1_760_000_000_000;
const database = createDatabase(spec.path);
let close_assignment = false;
const runs = {
  authority(child: WireTypes['ChildReference']): Principal {
    const assignment = database.read((sql) => open_child_assignment(sql, child));
    if (assignment === null)
      throw new PlannerError('agent_forbidden', 'child has no VPS assignment');
    if (close_assignment)
      database.write((tx) =>
        tx.run('UPDATE executor_child_assignments SET closed_at_ms=?', [Date.now()]),
      );
    return { kind: assignment[0] as Principal['kind'], id: assignment[1] };
  },
  validate_stop() {
    throw new Error('no stop in this proof');
  },
  async accept_stop_receipt() {
    throw new Error('no stop in this proof');
  },
};
const inbox = new MacInbox(
  new InboxStore(database, new FileStaging(spec.path)),
  new TestClock('2025-10-09T08:53:20+00:00'),
  runs,
);
let enter!: () => void, release!: () => void;
const entered = new Promise<void>((resolve) => {
  enter = resolve;
});
const gate = new Promise<void>((resolve) => {
  release = resolve;
});
const sent: string[] = [];
inbox.agent_writes.conversations = {
  async record_message_to_owner(
    conversation_id: string,
    content: MessageContent,
    options: RecordMessageToOwnerOptions,
  ) {
    enter();
    await gate;
    database.write((tx) =>
      options.recording_receipt?.in_commit?.(
        tx,
        conversation_id,
        new MessageToOwnerEventPayload({
          content,
          sender: options.sender,
          recipient: options.recipient,
          sender_label: options.sender_label,
        }),
        null,
      ),
    );
    sent.push(options.sender_message_id!);
  },
} as unknown as ConversationSystem;
const rows = () =>
  database.read((sql) => ({
    positions: sql
      .all<{ result_json: string }>(
        'SELECT sender_id,conversation_id,sequence,submission_id,disposition,result_json FROM mac_inbox_positions ORDER BY sender_id,conversation_id',
      )
      .map((row) => ({ ...row, result_json: JSON.parse(row.result_json) })),
    blockers: sql
      .all<Record<string, unknown>>(
        'SELECT * FROM ticket_blocks ORDER BY blocked_ticket_id,blocking_ticket_id',
      )
      .map((row) => Object.values(row)),
  }));
async function outcome(promise: Promise<unknown>): Promise<unknown> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof PlannerError) return error.toPayload();
    throw error;
  }
}
const typed = (value: WireTypes['QueuedEnvelope']) => decode_value(value, 'QueuedEnvelope');
const identity = parse_identity(spec.identity);
try {
  const output: Record<string, unknown> = {};
  for (const [index, value] of spec.edits.entries()) {
    output[`write-${index}`] = await outcome(inbox.accept(typed(value), identity));
    if (index === 0) {
      output.replay = await outcome(inbox.accept(typed(value), identity));
      output.conflict = await outcome(
        inbox.accept(typed({ ...value, submission_id: 'conflicting' }), identity),
      );
    }
  }
  output.batch = await outcome(
    inbox.accept_batch('c', decode_value(spec.bad_batch, 'ItemBatch'), identity),
  );
  output['sender-refusal'] = await outcome(
    inbox.refuse_item(typed(spec.refused), identity, 'capture failed'),
  );
  const pending = outcome(
    inbox.accept(typed(spec.native), identity, { retry_later_at: performance.now() / 1000 + 0.1 }),
  );
  await entered;
  output.reservation = rows();
  output.pending = await pending;
  output.overtake = await outcome(
    inbox.accept(typed(spec.following), identity, { retry_later_at: performance.now() / 1000 }),
  );
  release();
  output.native = await outcome(inbox.accept(typed(spec.native), identity));
  output.sent = sent;
  close_assignment = true;
  output.assignment = await outcome(inbox.accept(typed(spec.changed), identity));
  output['closed-replay'] = await outcome(inbox.accept(typed(spec.changed), identity));
  output.final = rows();
  await inbox.stop({ deadline: performance.now() / 1000 + 2 });
  await inbox.settled();
  process.stdout.write(JSON.stringify(output));
} finally {
  database.close();
}
