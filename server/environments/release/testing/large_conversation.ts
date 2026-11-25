/** Exercise inherited active conversations on the disposable installed home and browser. */
import assert from 'node:assert/strict';
import { join } from 'node:path';
import type { Page } from 'playwright';
import { canonical_principal, issue_launch_token } from '../../../agent_identity/data.ts';
import { randomUUID } from 'node:crypto';
import { DEFAULT_BACKEND_INSTANCE_ID } from '../../../drivers/built_in.ts';
import type { Principal } from '../../../core/contracts.ts';
import { VIEW_PROJECTION_VERSION } from '../../../conversation/views/contracts.ts';
import { append_projection } from '../../../conversation/views/data/writer.ts';
import type { ConversationEventKind } from '../../../conversation/events.ts';
import { conversation_event_payload_from_canonical_json } from '../../../conversation/record/codec.ts';
import { createDatabase, type TransactionSql } from '../../../core/database/index.ts';

export const archive_histories = 448;
const archivePrefix = 'qualification-archive-';
const owner = { kind: 'owner', id: 'owner' };
interface ActiveHistory {
  principal: Principal;
  conversation: string;
  route: string;
  turns: number;
}
type InheritedHistory = ActiveHistory & { through: number };
function historyMarker(principal: Principal) {
  return 'Latest synthetic ' + principal.kind + ' history before the upgrade.';
}

export async function seed_active_histories(
  base: string,
  url: string,
  chiefConversation: string,
  machine_id: string,
  workspace: string,
) {
  const post = async (route: string, data: unknown) => {
    const response = await fetch(url + route, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(data),
      signal: AbortSignal.timeout(25_000),
    });
    const raw = await response.text();
    assert.ok(response.ok, route + ': ' + raw);
    return JSON.parse(raw);
  };
  const project = await post('/api/projects', { name: 'Conversation qualification', summary: '' });
  assert.equal(typeof project.id, 'string');
  const manager = await post('/api/managers', {
    title: 'Active qualification Manager',
    goal: '',
    project_id: project.id,
  });
  assert.equal(typeof manager.id, 'string');
  assert.equal(manager.settled, false);
  const ticket = await post('/api/tickets', {
    title: 'Unfinished qualification Ticket',
    brief: '',
    worker_type: 'coding',
    employee_launch_model: 'TEST',
    project_id: project.id,
    ceiling: 'needs_brief',
  });
  assert.equal(typeof ticket.id, 'string');
  const managerConversation = 'qualification-' + randomUUID();
  const ticketConversation = 'qualification-' + randomUUID();
  for (const id of [managerConversation, ticketConversation])
    await post('/api/conversation/conversations', {
      conversation_id: id,
      backend_key: DEFAULT_BACKEND_INSTANCE_ID,
      model: 'TEST',
      workspace_folder: workspace,
      machine_id,
    });
  const histories: ActiveHistory[] = [
    {
      principal: { kind: 'chief', id: 'chief' },
      conversation: chiefConversation,
      route: 'chief-of-staff',
      turns: 1000,
    },
    {
      principal: { kind: 'manager', id: manager.id },
      conversation: managerConversation,
      route: 'manager/' + manager.id,
      turns: 950,
    },
    {
      principal: { kind: 'ticket', id: ticket.id },
      conversation: ticketConversation,
      route: ticket.id,
      turns: 900,
    },
  ];
  const db = createDatabase(join(base, 'installed/data/planner.db'));
  try {
    // Archived conversations have durable events but no current employee pointer.
    // Their shorter transcripts sort before the active ones in the background rebuild queue.
    db.write((tx) => {
      for (let index = 0; index < archive_histories; index++) {
        const conversation = archivePrefix + index;
        tx.run(
          'INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,created_at,machine_id) SELECT ?,backend_key,model,workspace_folder,access,created_at,machine_id FROM conversations WHERE conversation_id=?',
          [conversation, chiefConversation],
        );
        seed_history(tx, {
          conversation,
          principal: histories[0]!.principal,
          turns: 1 + (index % 3),
        });
      }
    });
    const seeded = histories.map((h) =>
      db.write((tx) => {
        const through = seed_history(tx, h);
        bind_history(tx, h);
        return { ...h, through };
      }),
    );
    // The historical facts and rows are synthetic. Mark the active and archived projections
    // as version 5 in one short transaction immediately before the press.
    return db.write((tx) => {
      const archived = tx.run(
        'UPDATE conversation_view_state SET projection_version=5 WHERE conversation_id GLOB ?',
        [archivePrefix + '*'],
      );
      assert.equal(archived.changes, archive_histories);
      for (const h of histories)
        tx.run('UPDATE conversation_view_state SET projection_version=5 WHERE conversation_id=?', [
          h.conversation,
        ]);
      const largest = tx.all<{ conversation_id: string }>(
        'SELECT conversation_id FROM conversations ORDER BY latest_sequence DESC LIMIT 3',
      );
      assert.deepEqual(
        largest.map((row) => row.conversation_id),
        histories.map((h) => h.conversation),
      );
      return seeded;
    });
  } finally {
    db.close();
  }
}

function seed_history(
  tx: TransactionSql,
  { conversation, principal, turns }: Pick<ActiveHistory, 'conversation' | 'principal' | 'turns'>,
) {
  const last = tx.get<{ latest_sequence: number }>(
    'SELECT latest_sequence FROM conversations WHERE conversation_id=?',
    [conversation],
  );
  assert.ok(last);
  let sequence = last.latest_sequence;
  const time = Math.floor(Date.now() / 1000);
  const insert = (kind: ConversationEventKind, payload: unknown) => {
    const encoded = JSON.stringify(payload);
    tx.run('INSERT INTO conversation_events VALUES (?,?,?,?,?)', [
      conversation,
      ++sequence,
      kind,
      encoded,
      time,
    ]);
    append_projection(tx, [
      {
        conversation_id: conversation,
        sequence,
        kind,
        payload: conversation_event_payload_from_canonical_json(kind, encoded),
        created_at: time,
      },
    ]);
  };
  for (let i = 0; i < turns; i++) {
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
      content: [
        {
          piece: 'text',
          text: i === turns - 1 ? historyMarker(principal) : 'Checked work ' + i,
        },
      ],
      sender_label: principal.kind,
      sender: principal,
      recipient: owner,
    });
    insert('turn_ended', { ending: 'completed', error_summary: null });
  }
  tx.run('UPDATE conversations SET latest_sequence=? WHERE conversation_id=?', [
    sequence,
    conversation,
  ]);
  return sequence;
}
function bind_history(tx: TransactionSql, { conversation, principal }: ActiveHistory) {
  if (principal.kind === 'ticket') {
    tx.run("UPDATE tickets SET conversation_id=? WHERE id=? AND stage!='done'", [
      conversation,
      principal.id,
    ]);
    tx.run('INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)', [
      conversation,
      principal.id,
    ]);
  } else {
    tx.run(
      'INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id',
      [principal.kind === 'chief' ? 'chief_of_staff' : 'manager_' + principal.id, conversation],
    );
  }
  assert.deepEqual(canonical_principal(tx, conversation), principal);
}

/** One parallel read per active view immediately after the install journal commits.
 * A 409 or 503 must fail this attempt even if a retry would succeed a moment later. */
export async function check_committed_views(url: string, histories: InheritedHistory[]) {
  return Promise.all(
    histories.map(async ({ conversation, through, principal }) => {
      const response = await fetch(
        url +
          '/api/conversation/conversations/' +
          encodeURIComponent(conversation) +
          '/view?lens=focus',
        { signal: AbortSignal.timeout(25_000) },
      );
      assert.equal(response.status, 200, principal.kind + ' initial view: ' + response.status);
      const snapshot = await response.json();
      assert.equal(snapshot.page.projection_version, VIEW_PROJECTION_VERSION);
      assert.equal(snapshot.page.conversation_id, conversation);
      assert.ok(snapshot.coverage.through_sequence >= through);
      assert.ok(JSON.stringify(snapshot.page.items).includes(historyMarker(principal)));
      return {
        role: principal.kind,
        status: response.status,
        projection: snapshot.page.projection_version,
        sequence: snapshot.coverage.through_sequence,
      };
    }),
  );
}

export async function check_large_conversation(
  page: Page,
  url: string,
  base: string,
  { conversation: id, through: seeded, principal, route, turns }: InheritedHistory,
) {
  const liveMarker = 'New ' + principal.kind + ' message from the upgraded home.';
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
    await page.goto(url + '/#/workspace/' + route, { waitUntil: 'domcontentloaded' });
    const pane = page.locator('[data-conversation-pane]');
    if ((await pane.getAttribute('data-conversation-state')) !== 'opened')
      await page.locator('[data-conversation-expand]').click();
    const rows = pane.locator('[data-conversation-view-key]');
    await rows
      .filter({ hasText: historyMarker(principal) })
      .first()
      .waitFor({ timeout: 25_000 });
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
      assert.ok(
        projected_rows >= turns * 7,
        'Expected the candidate to project a large transcript',
      );
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
