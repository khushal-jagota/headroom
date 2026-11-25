/** Exercise an inherited Chief transcript on the same disposable installed home and browser. */
import assert from 'node:assert/strict';
import { join } from 'node:path';
import type { Page } from 'playwright';
import { issue_launch_token } from '../../../agent_identity/data.ts';
import { createDatabase } from '../../../core/database/index.ts';

const historyMarker = 'Latest synthetic Chief history before the upgrade.';
const liveMarker = 'New Chief message from the upgraded home.';
const owner = { kind: 'owner', id: 'owner' };
const chief = { kind: 'chief', id: 'chief' };

export function seed_large_conversation(base: string, conversation: string) {
  const db = createDatabase(join(base, 'installed/data/planner.db'));
  try {
    return db.write((tx) => {
      const last = tx.get<{ latest_sequence: number }>(
        'SELECT latest_sequence FROM conversations WHERE conversation_id=?',
        [conversation],
      );
      assert.ok(last);
      let sequence = last.latest_sequence;
      const time = Math.floor(Date.now() / 1000);
      const insert = (kind: string, payload: unknown) =>
        tx.run('INSERT INTO conversation_events VALUES (?,?,?,?,?)', [
          conversation,
          ++sequence,
          kind,
          JSON.stringify(payload),
          time,
        ]);
      for (let i = 0; i < 1_000; i++) {
        insert('prompt', {
          content: [{ piece: 'text', text: 'Check work ' + i }],
          mode: 'queue',
          sender_label: 'Owner',
          sender: owner,
        });
        for (const status of ['pending', 'in_progress', 'completed'])
          insert('plan_updated', { entries: [{ text: 'Check work ' + i, status }] });
        for (const tool of ['Read', 'Bash', 'Edit', 'Verify']) {
          const tool_call_id = tool + i;
          insert('tool_call_started', {
            tool_call_id,
            title: tool,
            tool_kind: 'execute',
            detail: 'Synthetic input ' + i,
          });
          insert('tool_call_finished', {
            tool_call_id,
            tool_call_status: 'completed',
            detail: 'Synthetic result ' + i,
          });
        }
        insert('message_to_owner', {
          content: [{ piece: 'text', text: i === 999 ? historyMarker : 'Checked work ' + i }],
          sender_label: 'Chief',
          sender: chief,
          recipient: owner,
        });
        insert('turn_ended', { ending: 'completed', error_summary: null });
      }
      tx.run('UPDATE conversations SET latest_sequence=? WHERE conversation_id=?', [
        sequence,
        conversation,
      ]);
      // An older projection forces the installed candidate to rebuild from the inherited events.
      tx.run('DELETE FROM conversation_view_state WHERE conversation_id=?', [conversation]);
      tx.run(
        "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff',?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id",
        [conversation],
      );
      const largest = tx.get<{ conversation_id: string; count: number }>(
        'SELECT conversation_id, count(*) AS count FROM conversation_events GROUP BY conversation_id ORDER BY count DESC LIMIT 1',
      );
      assert.equal(largest?.conversation_id, conversation);
      assert.equal(largest.count, sequence);
      return sequence;
    });
  } finally {
    db.close();
  }
}

export async function check_large_conversation(
  page: Page,
  url: string,
  base: string,
  id: string,
  seeded: number,
) {
  const session = await page.context().newCDPSession(page);
  const snapshots = new Set<string>();
  let streamed: number | undefined;
  try {
    await session.send('Network.enable');
    session.on('Network.eventSourceMessageReceived', (event) => {
      if (event.eventName !== 'snapshot' && event.eventName !== 'changes') return;
      const value = JSON.parse(event.data);
      if (
        value.page?.conversation_id === id &&
        value.coverage.through_sequence >= seeded &&
        value.page.items.length
      )
        snapshots.add(event.requestId);
      if (
        snapshots.has(event.requestId) &&
        value.conversation_id === id &&
        JSON.stringify(value.upserts).includes(liveMarker)
      )
        streamed = value.coverage.through_sequence;
    });
    await page.goto('about:blank');
    await page.goto(url + '/#/workspace/chief-of-staff', { waitUntil: 'domcontentloaded' });
    const pane = page.locator('[data-conversation-pane]');
    if ((await pane.getAttribute('data-conversation-state')) !== 'opened')
      await page.locator('[data-conversation-expand]').click();
    const rows = pane.locator('[data-conversation-view-key]');
    await rows.filter({ hasText: historyMarker }).first().waitFor({ timeout: 25_000 });
    assert.ok(snapshots.size, 'Browser never received the large conversation snapshot');
    const db = createDatabase(join(base, 'installed/data/planner.db'));
    let token: string;
    let projected_rows: number;
    try {
      projected_rows = db.read(
        (sql) =>
          sql.get<{ count: number }>(
            'SELECT count(*) AS count FROM conversation_view_rows WHERE conversation_id=?',
            [id],
          )!.count,
      );
      assert.ok(projected_rows >= 7_000, 'Expected the candidate to project a large transcript');
      token = issue_launch_token(db, id);
    } finally {
      db.close();
    }
    const sent = await page.request.post(url + '/api/messages/send', {
      headers: { authorization: 'Bearer ' + token },
      data: { target: owner, message: liveMarker, mode: 'queue' },
    });
    assert.ok(sent.ok(), await sent.text());
    await rows.filter({ hasText: liveMarker }).first().waitFor({ timeout: 25_000 });
    assert.ok(
      streamed && streamed > seeded,
      'New message did not arrive on the open browser event stream',
    );
    return {
      conversation_id: id,
      inherited_events: seeded,
      projected_rows,
      live_sequence: streamed,
    };
  } finally {
    await session.detach();
  }
}
