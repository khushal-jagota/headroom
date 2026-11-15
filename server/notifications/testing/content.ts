/** Adds content proof to the already-owned direct attention store, without another host. */
import type { Database } from '../../core/database/contracts.ts';
import type { ConversationRecordStore } from '../../conversation/record/contracts.ts';
import { MessageToOwnerEventPayload } from '../../conversation/events.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import { OWNER } from '../../core/contracts.ts';
import { file_current_proposal } from '../../tickets/data/index.ts';
import { queue_deliveries, pending_deliveries } from '../data.ts';
import * as apns from '../apns_data.ts';
import { message_preview, notification_label } from '../logic/preview.ts';

export function notification_content_proof(
  database: Database,
  store: ConversationRecordStore,
  ticket: string,
  now: number,
  cases: string[],
) {
  const began = performance.now();
  const sender = { kind: 'ticket' as const, id: ticket };
  const message = (text: string) =>
    new MessageToOwnerEventPayload({
      content: text_message_content(text),
      sender,
      recipient: OWNER,
      sender_label: 'Wren Manager',
    });
  const first = store.append_event(
    'attention-system',
    message('**Ready** to [review](https://private.invalid).\nPlease check `copy`.'),
  );
  store.append_event('attention-system', message('Later words must not replace the snapshot'));
  database.write((sql) =>
    sql.run("UPDATE tickets SET title='Later name',conversation_id=NULL WHERE id=?", [ticket]),
  );
  database.write((sql) => queue_deliveries(sql, now));
  const web = database
    .read((sql) => pending_deliveries(sql, now))
    .find(
      (item) => item.edge.subject_id === ticket && item.edge.notification_type === 'awaiting_reply',
    )!.intent;
  const native = database
    .write((sql) => apns.pending_deliveries(sql, now))
    .find(
      (item) => item.edge.subject_id === ticket && item.edge.notification_type === 'awaiting_reply',
    )!.intent;
  if (
    web.body !== 'Ready to review. Please check copy.' ||
    web.reply !== null ||
    native.reply?.message_id !== `attention-system:${first.sequence}`
  )
    throw new Error('message snapshot contract failed');
  database.write((sql) => {
    sql.run('UPDATE tickets SET title=? WHERE id=?', ['📋'.repeat(90), ticket]);
    file_current_proposal(sql, ticket, { body: 'A new proposal', principal: sender, now });
    const row = sql.get<{ workflow_json: string }>('SELECT workflow_json FROM tickets WHERE id=?', [
      ticket,
    ])!;
    const workflow = JSON.parse(row.workflow_json);
    for (const stage of workflow.stages) stage.label = 'Later stage label';
    sql.run("UPDATE tickets SET title='Later approval name',workflow_json=? WHERE id=?", [
      JSON.stringify(workflow),
      ticket,
    ]);
    queue_deliveries(sql, now);
  });
  const rows = database.read((sql) =>
    Object.fromEntries(
      [
        'notification_attention_edges',
        'notification_deliveries',
        'notification_apns_deliveries',
      ].map((table) => [
        table,
        sql
          .all<Record<string, unknown>>(
            `SELECT * FROM ${table} WHERE subject_kind='ticket' AND subject_id=? ORDER BY notification_type,generation`,
            [ticket],
          )
          .map((row) =>
            Object.fromEntries(
              Object.entries(row).map(([key, value]) => [
                key,
                ['context_json', 'reply_json'].includes(key) && typeof value === 'string'
                  ? JSON.parse(value)
                  : value,
              ]),
            ),
          ),
      ]),
    ),
  );
  const approval = (
    rows.notification_deliveries as { notification_type: string; title: string; body: string }[]
  ).find((row) => row.notification_type === 'awaiting_approval')!;
  if (
    approval.title !== '📋'.repeat(59) + '…' ||
    approval.body === 'Later stage label waits for your approval.'
  )
    throw new Error('approval snapshot contract failed');
  return {
    proof: {
      rows,
      web,
      native,
      previews: cases.map((text) => ({
        label: notification_label(text),
        preview: message_preview(text),
      })),
    },
    seconds: (performance.now() - began) / 1000,
  };
}
