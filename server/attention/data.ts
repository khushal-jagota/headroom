import { is_record as object } from '../core/records.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { stringify_python_json } from '../core/http/numeric_json.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { destination } from './contracts.ts';
import type { AttentionEvent, Destination, Evidence, GateInterval } from './contracts.ts';
const key = (kind: string, id: string): string => JSON.stringify([kind, id]);
function snapshot(raw: string): Destination {
  const value = JSON.parse(raw) as Destination;
  return destination(value.kind, value.id, value.title, value.url, {
    project_id: value.project_id ?? null,
    manager_id: value.manager_id ?? null,
    goal_id: value.goal_id ?? null,
    attribution_basis: value.attribution_basis ?? 'recorded',
  });
}
function destinations(sql: ReadSql): Map<string, Destination> {
  const values = new Map<string, Destination>();
  const add = (item: Destination) => values.set(key(item.kind, item.id), item);
  for (const row of sql.all<{ id: string; name: string }>('SELECT id,name FROM projects'))
    add(destination('project', row.id, row.name, null, { project_id: row.id }));
  for (const row of sql.all<{ id: string; title: string; project_id: string | null }>(
    'SELECT id,title,project_id FROM managers',
  ))
    add(
      destination('manager', row.id, row.title, `/#/workspace/manager/${row.id}`, {
        project_id: row.project_id,
        manager_id: row.id,
        attribution_basis: 'current_membership',
      }),
    );
  for (const row of sql.all<{
    id: string;
    title: string;
    project_id: string | null;
    manager_id: string | null;
  }>(
    `SELECT t.id,t.title,CASE WHEN t.manager_kind='manager' THEN m.project_id ELSE t.project_id END project_id,t.manager_id
    FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id`,
  ))
    add(
      destination('ticket', row.id, row.title, `/#/workspace/${row.id}`, {
        project_id: row.project_id,
        manager_id: row.manager_id,
        attribution_basis: 'current_membership',
      }),
    );
  for (const row of sql.all<{ id: string; title: string; project_id: string | null }>(
    'SELECT id,title,project_id FROM goals',
  ))
    add(
      destination('goal', row.id, row.title, `/#/workspace/goal/${row.id}`, {
        project_id: row.project_id,
        goal_id: row.id,
        attribution_basis: 'current_membership',
      }),
    );
  add(destination('chief', 'chief', 'Chief', '/#/workspace/chief-of-staff'));
  return values;
}
interface ApprovalRow {
  id: number;
  actor_kind: string;
  actor_id: string;
  method: string;
  occurred_at: number;
  destination_json: string;
}
interface GateRow {
  id: number;
  opened_at: number | null;
  observed_since: number;
  closed_at: number | null;
  destination_json: string;
}
export function load_evidence(sql: ReadSql, first_at: number, last_at: number): Evidence {
  const installed = sql.get(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attention_history_metadata'",
  );
  const metadata = installed
    ? sql.get<{ started_at: number }>(
        'SELECT started_at FROM attention_history_metadata WHERE singleton=1',
      )
    : undefined;
  const started_at = metadata?.started_at ?? last_at;
  const all_destinations = destinations(sql);
  const approval_rows = installed
    ? sql.all<ApprovalRow>(
        'SELECT * FROM attention_approval_events WHERE occurred_at < ? ORDER BY occurred_at,id',
        [last_at],
      )
    : [];
  const gate_rows = installed
    ? sql.all<GateRow>(
        `SELECT * FROM attention_gate_intervals WHERE observed_since < ?
    AND (closed_at IS NULL OR closed_at > ?)`,
        [last_at, first_at],
      )
    : [];
  const message_destinations = new Map(all_destinations);
  const snapshots = new Map<string, Destination>();
  for (const row of [...approval_rows, ...gate_rows]) {
    const item = snapshot(row.destination_json),
      id = key(item.kind, item.id);
    if (!snapshots.has(id)) snapshots.set(id, item);
  }
  for (const [id, item] of snapshots) if (!all_destinations.has(id)) all_destinations.set(id, item);
  const by_conversation = new Map<string, [string, string]>();
  for (const row of sql.all<{
    owner_kind: string;
    owner_id: string | null;
    conversation_id: string;
  }>('SELECT owner_kind,owner_id,conversation_id FROM conversation_history_catalog')) {
    const kind = row.owner_kind === 'worker' ? 'ticket' : row.owner_kind;
    if (
      ['ticket', 'manager', 'chief'].includes(kind) &&
      row.owner_id !== null &&
      !by_conversation.has(row.conversation_id)
    )
      by_conversation.set(row.conversation_id, [kind, row.owner_id]);
  }
  for (const row of sql.all<{ conversation_id: string; ticket_id: string }>(
    `SELECT conversation_id,ticket_id FROM ticket_conversations
    UNION SELECT conversation_id,id FROM tickets WHERE conversation_id IS NOT NULL`,
  ))
    by_conversation.set(row.conversation_id, ['ticket', row.ticket_id]);
  for (const row of sql.all<{ agent_key: string; conversation_id: string }>(
    'SELECT agent_key,conversation_id FROM agents WHERE conversation_id IS NOT NULL',
  )) {
    let identity: [string, string] | null = null;
    if (row.agent_key === CHIEF_SETTINGS_KEY) identity = ['chief', 'chief'];
    if (row.agent_key.startsWith('manager_'))
      identity = ['manager', row.agent_key.slice('manager_'.length)];
    if (identity !== null && !by_conversation.has(row.conversation_id))
      by_conversation.set(row.conversation_id, identity);
  }
  const events: AttentionEvent[] = [],
    missing_senders: number[] = [];
  for (const row of sql.all<{
    conversation_id: string;
    sequence: number;
    kind: string;
    payload: string;
    created_at: number;
  }>(
    `SELECT conversation_id,sequence,kind,payload,created_at FROM conversation_events
    WHERE kind IN ('prompt','agent_message','turn_ended') AND created_at < ?
    ORDER BY created_at,rowid`,
    [last_at],
  )) {
    const payload = JSON.parse(row.payload) as Record<string, unknown>;
    const recipient = payload.recipient;
    let identity = by_conversation.get(row.conversation_id);
    if (
      object(recipient) &&
      typeof recipient.kind === 'string' &&
      ['ticket', 'manager', 'chief'].includes(recipient.kind)
    )
      identity = [String(recipient.kind), String(recipient.id ?? '')];
    identity ??= ['unattributed', 'unattributed'];
    const id = key(...identity);
    let item = message_destinations.get(id);
    if (!item) {
      const retained = snapshots.get(id);
      item = retained
        ? {
            ...retained,
            project_id: null,
            manager_id: null,
            goal_id: null,
            attribution_basis: 'identity_only',
          }
        : destination(...identity, 'Deleted or unattributed work', null, {
            attribution_basis: 'identity_only',
          });
    }
    let kind = 'activity';
    const sender = payload.sender;
    if (row.kind === 'prompt') {
      if (
        object(sender) &&
        Object.keys(sender).length === 2 &&
        sender.kind === 'owner' &&
        sender.id === 'owner'
      )
        kind = 'owner_message';
      else if (
        (!object(sender) || !('kind' in sender) || !('id' in sender)) &&
        payload.system_generated !== true
      )
        missing_senders.push(row.created_at);
    }
    const message_id = payload.sender_message_id;
    const event_id =
      row.kind === 'prompt' && object(sender) && typeof message_id === 'string' && message_id
        ? 'message:' +
          stringify_python_json([sender.kind ?? null, sender.id ?? null, message_id], {
            compact: true,
            ensure_ascii: true,
          })
        : `conversation:${row.conversation_id}:${row.sequence}`;
    events.push({ identity: event_id, occurred_at: row.created_at, kind, destination: item });
  }
  for (const row of approval_rows) {
    const owner =
      row.actor_kind === 'owner' && row.actor_id === 'owner' && row.method === 'explicit_accept';
    events.push({
      identity: `approval:${row.id}`,
      occurred_at: row.occurred_at,
      kind: owner ? 'owner_approval' : 'activity',
      destination: snapshot(row.destination_json),
    });
  }
  const gates: GateInterval[] = gate_rows.map((row) => ({
    identity: `gate:${row.id}`,
    destination: snapshot(row.destination_json),
    opened_at: row.opened_at,
    observed_since: row.observed_since,
    closed_at: row.closed_at,
  }));
  const earliest = sql.get<{ at: number | null }>(
    'SELECT MIN(created_at) AS at FROM conversations',
  )!.at;
  return {
    events,
    gates,
    history_started_at: started_at,
    limitations: [
      'Gate waits are elapsed delays, not owner work hours. Reading time and Mac work are unobserved.',
      'Message attribution uses recorded recipients and labeled current relationship metadata.',
    ],
    destinations: [...all_destinations.values()],
    message_history_started_at: earliest,
    unattributed_message_times: missing_senders,
  };
}
