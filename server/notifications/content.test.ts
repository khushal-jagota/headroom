import { expect, test, vi } from 'vitest';
import { fixture, create, propose, read, now } from '../tests/ticket_fixture.ts';
import { queue_deliveries, register_subscription, pending_deliveries } from './data.ts';
import { reconcile_attention } from './data.ts';
import * as preview from './logic/preview.ts';
import { notification_payload } from './apns.ts';

// Optional copy failure must preserve a real canonical proposal and imported edge.
test('failed context enrichment commits its source and retains generic copy without private logs', () => {
  const db = fixture(),
    original = create(db);
  db.write((sql) =>
    register_subscription(sql, {
      endpoint: 'https://push.invalid/copy',
      p256dh: 'synthetic',
      auth: 'synthetic',
      now,
    }),
  );
  const diagnostic = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const enrich = vi.spyOn(preview, 'notification_label').mockImplementation(() => {
    throw new Error('private message words');
  });
  try {
    propose(db, original, 'Committed proposal');
    expect(read(db, original.id).pending_proposal?.body).toBe('Committed proposal');
    db.write((sql) => {
      expect(
        sql.get(
          'SELECT context_json FROM notification_attention_edges WHERE subject_id=? ORDER BY generation DESC LIMIT 1',
          [original.id],
        ),
      ).toEqual({ context_json: null });
      sql.run('DELETE FROM notification_attention_edges WHERE subject_id=?', [original.id]);
      sql.run('DELETE FROM notification_attention_state WHERE subject_id=?', [original.id]);
      reconcile_attention(sql);
    });
    expect(diagnostic).toHaveBeenCalled();
    expect(JSON.stringify(diagnostic.mock.calls)).not.toContain('private message words');
  } finally {
    enrich.mockRestore();
    diagnostic.mockRestore();
  }
  db.write((sql) => queue_deliveries(sql, now));
  const delivery = db
    .write((sql) => pending_deliveries(sql, now))
    .find((item) => item.edge.notification_type === 'awaiting_approval')!;
  expect(delivery.intent).toMatchObject({
    title: 'Panels',
    body: 'Test ticket needs your approval.',
    reply: null,
  });
});

// Native JSON assembly is independent of HTTP/2 and Web Push has no text action.
test('APNs payload retains its original reply category and omits both fields without a reply', () => {
  const reply = {
    conversation_id: 'original',
    message_id: 'original:7',
    recipient_kind: 'ticket',
    recipient_id: 't_original',
  };
  const intent = {
    title: 'Wren',
    body: 'Ready',
    route: '/#/workspace/t_original',
    tag: 'panels-ticket-t_original',
    reply,
  };
  const payload = JSON.parse(notification_payload(intent, 'https://panels.invalid').toString());
  expect(payload).toEqual({
    aps: {
      alert: { title: 'Wren', body: 'Ready' },
      sound: 'default',
      'thread-id': intent.tag,
      category: 'PANELS_MESSAGE_REPLY',
    },
    server_url: 'https://panels.invalid',
    route: intent.route,
    reply,
  });
  const generic = JSON.parse(
    notification_payload({ ...intent, reply: null }, 'https://panels.invalid').toString(),
  );
  expect(generic.aps).not.toHaveProperty('category');
  expect(generic).not.toHaveProperty('reply');
});
