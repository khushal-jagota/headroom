/** Read-only derivation of every navigation root from canonical source rows. */
import type { ReadSql } from '../../core/database/contracts.ts';
import type { NavigationKey, NavigationRecord } from '../contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { compact } from '../logic/json.ts';
import {
  currentAttention,
  effectiveCompletion,
  hasColumn,
  lifecycleSummary,
  newDerivationContext,
  proposal,
  recapPreview,
  safeReason,
  storedWorkflow,
} from './derivation_helpers.ts';
import type { Row, DerivationContext } from './derivation_helpers.ts';
export interface DerivedRoot {
  records: NavigationRecord[];
  dependencies: NavigationKey[];
}
export const parent = (row: Row): NavigationKey =>
  row.manager_id ? ['manager', String(row.manager_id)] : ['chief', 'chief'];
const record = (kind: string, id: string, value: Row): NavigationRecord =>
  ({ kind, id, value }) as NavigationRecord;
export function newRoots(sql: ReadSql, [table, id]: NavigationKey): NavigationKey[] {
  const roots = new Map<string, NavigationKey>();
  const add = (kind: string, identity: string) =>
    roots.set(compact([kind, identity]), [kind, identity]);
  if (table === '@totals') add('totals', id);
  else if (table === 'tickets') {
    add('ticket', id);
    for (const row of sql.all<{ conversation_id: string }>(
      'SELECT conversation_id FROM ticket_conversations WHERE ticket_id=? UNION SELECT conversation_id FROM tickets WHERE id=? AND conversation_id IS NOT NULL ORDER BY conversation_id',
      [id, id],
    ))
      add('conversation', row.conversation_id);
    const ticket = sql.get<Row>('SELECT manager_id FROM tickets WHERE id=?', [id]);
    if (ticket) add('totals', parent(ticket).join(':'));
    for (const row of sql.all<{ blocked_ticket_id: string }>(
      'SELECT blocked_ticket_id FROM ticket_blocks WHERE blocking_ticket_id=? ORDER BY blocked_ticket_id',
      [id],
    ))
      add('ticket', row.blocked_ticket_id);
    add('totals', 'workspace');
  } else if (table === 'managers') {
    add('manager', id);
    add('totals', 'manager:' + id);
    for (const ticket of sql.all<{ id: string }>(
      'SELECT id FROM tickets WHERE manager_id=? ORDER BY id',
      [id],
    ))
      for (const root of newRoots(sql, ['tickets', ticket.id])) add(...root);
    const agent = sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=? AND conversation_id IS NOT NULL',
      ['manager_' + id],
    );
    if (agent?.conversation_id) add('conversation', agent.conversation_id);
  } else if (table === 'goals') add('goal', id);
  else if (table === 'projects') add('project', id);
  else if (table === 'worker_types')
    for (const ticket of sql.all<{ id: string }>(
      'SELECT id FROM tickets WHERE worker_type=? ORDER BY id',
      [id],
    ))
      add('ticket', ticket.id);
  else if (table === 'chief_settings') add('chief', 'chief');
  else if (table === 'agents') {
    if (id === 'chief_of_staff') add('chief', 'chief');
    else if (id.startsWith('manager_')) add('manager', id.slice('manager_'.length));
    const agent = sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=? AND conversation_id IS NOT NULL',
      [id],
    );
    if (agent?.conversation_id) add('conversation', agent.conversation_id);
  } else if (table === 'ticket_blocks') add('ticket', id);
  else if (
    ['ticket_conversations', 'navigation_signal_publications'].includes(table) ||
    table.startsWith('conversation')
  )
    add('conversation', id);
  return [...roots.values()];
}
export function ticketOwners(sql: ReadSql): Map<string, Map<string, Row>> {
  const tickets = new Map(
    sql
      .all<Row>('SELECT id,manager_id,conversation_id FROM tickets ORDER BY id')
      .map((row) => [row.id as string, row]),
  );
  const owners = new Map<string, Map<string, Row>>();
  const add = (conversation: string, row: Row) => {
    if (!owners.has(conversation)) owners.set(conversation, new Map());
    owners.get(conversation)!.set(row.id, row);
  };
  for (const row of tickets.values())
    if (row.conversation_id !== null) add(row.conversation_id, row);
  for (const association of sql.all<Row>(
    'SELECT conversation_id,ticket_id FROM ticket_conversations ORDER BY conversation_id,ticket_id',
  )) {
    const ticket = tickets.get(association.ticket_id);
    if (ticket) add(association.conversation_id, ticket);
  }
  return owners;
}
export function derive(
  sql: ReadSql,
  [kind, id]: NavigationKey,
  context: DerivationContext = newDerivationContext(),
): DerivedRoot {
  const records: NavigationRecord[] = [],
    dependencies = new Map<string, NavigationKey>();
  const dep = (table: string, identity: string) =>
    dependencies.set(compact([table, identity]), [table, identity]);
  const result = () => ({ records, dependencies: [...dependencies.values()] });
  const version = sql.get<{ schema_version: number }>(
    'SELECT schema_version FROM navigation_meta WHERE singleton=1',
  )!.schema_version;
  if (kind === 'ticket') {
    dep('tickets', id);
    const snapshots = hasColumn(sql, 'tickets', 'workflow_json');
    const integrity = version >= 7 && hasColumn(sql, 'tickets', 'integrity_error_reason');
    const columns =
      (snapshots ? 't.workflow_json,' : '') +
      (integrity ? 't.integrity_error_reason,' : '') +
      (version < 7 ? 't.priority,' : '') +
      't.title,t.worker_type,t.stage,t.deadline,t.manager_kind,t.manager_id,t.conversation_id,t.ceiling,t.locale,t.worker_step_claim,t.pending_proposal,t.is_backlogged,t.ceiling_holder,t.pending_proposal_revision,t.updated_at,t.created_at,t.recap';
    const t = sql.get<Row>(
      'SELECT ' +
        columns +
        ',COALESCE(m.project_id,t.project_id) AS effective_project,CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END AS diagnostic_project FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id WHERE t.id=?',
      [id],
    );
    if (!t) return result();
    if (!snapshots) dep('worker_types', t.worker_type);
    if (t.manager_id) dep('managers', t.manager_id);
    const blockers = sql.all<Row>(
      'SELECT blocker.id,blocker.stage FROM ticket_blocks b JOIN tickets blocker ON blocker.id=b.blocking_ticket_id WHERE b.blocked_ticket_id=? ORDER BY blocker.id',
      [id],
    );
    dep('ticket_blocks', id);
    for (const blocker of blockers) dep('tickets', blocker.id);
    if (
      version >= 7 &&
      t.integrity_error_reason !== null &&
      t.integrity_error_reason !== undefined
    ) {
      const value: Row = {};
      for (const key of [
        'title',
        'worker_type',
        'stage',
        'recap',
        'conversation_id',
        'manager_id',
        'manager_kind',
        'created_at',
        'updated_at',
        'deadline',
        'locale',
      ])
        value[key] = t[key];
      Object.assign(value, {
        ticket_status: 'errored',
        integrity_error: { reason: safeReason(t.integrity_error_reason) },
        project_id: t.diagnostic_project,
        is_backlogged: Boolean(t.is_backlogged),
        recap_preview: recapPreview(String(t.recap)),
      });
      records.push(record(kind, id, value));
      return result();
    }
    const live = blockers.some((blocker) =>
      version <= 2 ? !['done', 'cancelled'].includes(blocker.stage) : blocker.stage !== 'done',
    );
    if (!['none', 'out', 'errored'].includes(t.worker_step_claim))
      throw new Error('invalid worker-step claim');
    let status =
      t.worker_step_claim === 'errored'
        ? 'errored'
        : t.pending_proposal !== null
          ? 'awaiting_approval'
          : t.worker_step_claim === 'out'
            ? 'agent'
            : live
              ? 'blocked'
              : 'empty';
    if (version <= 2 && t.stage === 'cancelled') status = 'empty';
    let workflowRaw = t.workflow_json;
    if (!snapshots) {
      const definition = sql.get<{ definition_json: string }>(
        'SELECT definition_json FROM worker_types WHERE worker_type=?',
        [t.worker_type],
      );
      if (!definition) throw new Error('navigation Ticket names a missing Worker type');
      workflowRaw = definition.definition_json;
    }
    const workflow = storedWorkflow(workflowRaw, context),
      stage = workflow.stages.find((stage) => stage.id === t.stage);
    if (!stage)
      throw new PlannerError('validation', 'stage outside the linear order', { stage: t.stage });
    const rawProposal = t.pending_proposal === null ? null : JSON.parse(t.pending_proposal);
    const value: Row = {};
    for (const key of [
      'title',
      'worker_type',
      'stage',
      'deadline',
      'manager_kind',
      'manager_id',
      'conversation_id',
      'ceiling',
      'locale',
    ])
      value[key] = t[key];
    if (version < 7) value.priority = t.priority;
    Object.assign(value, {
      ticket_status: status,
      project_id: t.effective_project,
      is_backlogged: Boolean(t.is_backlogged),
      ceiling_holder: JSON.parse(t.ceiling_holder),
      proposal_field: rawProposal?.field ?? null,
      proposal_revision: t.pending_proposal_revision,
      terminal: stage.is_terminal,
      stage_ownership: stage.ownership_mode,
    });
    if (snapshots)
      Object.assign(value, { stage_label: stage.label, workflow_label: workflow.label });
    if (version >= 3)
      Object.assign(value, {
        updated_at: t.updated_at,
        proposal_created_at: proposal(t.pending_proposal)?.created_at ?? null,
        worker_step_claim_out: t.worker_step_claim === 'out',
        blocked: live,
      });
    if (version >= 4)
      Object.assign(value, {
        gating_field: stage.gating_field,
        created_at: t.created_at,
        recap_preview: recapPreview(String(t.recap)),
        ...(version >= 7 ? { recap: String(t.recap) } : {}),
      });
    records.push(record(kind, id, value));
  } else if (kind === 'chief' || kind === 'manager') {
    let key: string, value: Row;
    if (kind === 'manager') {
      dep('managers', id);
      const t = sql.get<Row>('SELECT * FROM managers WHERE id=?', [id]);
      if (!t) return result();
      key = 'manager_' + id;
      value = { title: t.title, project_id: t.project_id, settled: Boolean(t.settled) };
      if (version >= 3)
        Object.assign(value, {
          created_at: t.created_at,
          ...(version >= 7 ? { updated_at: t.updated_at } : { priority: t.priority }),
        });
      if (version >= 4) {
        const recap = String(t.recap ?? t.body);
        value.recap_preview = recapPreview(recap);
        if (version >= 7) value.recap = recap;
      }
    } else {
      key = 'chief_of_staff';
      dep('chief_settings', key);
      value = {
        label:
          sql.get<{ label: string }>('SELECT label FROM chief_settings WHERE employee_id=?', [key])
            ?.label ?? 'Chief of Staff',
      };
    }
    dep('agents', key);
    value.conversation_id =
      sql.get<{ conversation_id: string | null }>(
        'SELECT conversation_id FROM agents WHERE agent_key=?',
        [key],
      )?.conversation_id ?? null;
    records.push(record(kind, id, value));
  } else if (kind === 'goal') {
    dep('goals', id);
    const goal = sql.get<Row>('SELECT * FROM goals WHERE id=?', [id]);
    if (goal && 'holder_kind' in goal)
      records.push(
        record(kind, id, {
          holder_kind: goal.holder_kind,
          holder_id: goal.holder_id,
          title: goal.title,
          active: Boolean(goal.active),
          completed: effectiveCompletion(goal),
          created_at: goal.created_at,
          updated_at: goal.updated_at,
        }),
      );
  } else if (kind === 'project') {
    dep('projects', id);
    const project = sql.get<Row>('SELECT * FROM projects WHERE id=?', [id]);
    if (project)
      records.push(
        record(kind, id, {
          name: project.name,
          ...(version < 7 ? { priority: project.priority } : {}),
        }),
      );
  } else if (kind === 'conversation') {
    for (const table of [
      'conversations',
      'conversation_events',
      'conversation_lifecycle_facts',
      'conversation_error_acknowledgements',
      'ticket_conversations',
    ])
      dep(table, id);
    const c = sql.get<Row>(
      'SELECT latest_sequence,owner_read_through_sequence,latest_agent_activity_sequence,latest_agent_activity_at,identity_environment_variables FROM conversations WHERE conversation_id=?',
      [id],
    );
    if (!c) return result();
    const value: Row = {};
    for (const key of [
      'latest_sequence',
      'owner_read_through_sequence',
      'latest_agent_activity_sequence',
      'latest_agent_activity_at',
    ])
      value[key] = c[key];
    value.first_unread_owner_message_at =
      sql.get<{ created_at: number }>(
        "SELECT created_at FROM conversation_events WHERE conversation_id=? AND kind='message_to_owner' AND sequence>? ORDER BY sequence LIMIT 1",
        [id, c.owner_read_through_sequence],
      )?.created_at ?? null;
    value.error_acknowledged_through_sequence =
      sql.get<{ through_sequence: number }>(
        'SELECT through_sequence FROM conversation_error_acknowledgements WHERE conversation_id=?',
        [id],
      )?.through_sequence ?? 0;
    value.lifecycle_summary = lifecycleSummary(sql, id);
    if (version === 4 || version === 10) {
      dep('navigation_signal_publications', id);
      value.attention = currentAttention(sql, id);
    }
    records.push(record(kind, id, value));
    const addresses = new Map<string, { key: NavigationKey; value: Row }>();
    const owners = context.ticketOwners
      ? [...(context.ticketOwners.get(id)?.values() ?? [])]
      : sql.all<Row>(
          'SELECT t.id,t.manager_id,t.conversation_id FROM tickets t LEFT JOIN ticket_conversations tc ON tc.ticket_id=t.id WHERE t.conversation_id=? OR tc.conversation_id=? GROUP BY t.id ORDER BY t.id',
          [id, id],
        );
    for (const t of owners) {
      dep('tickets', t.id);
      const p = parent(t);
      if (p[0] === 'manager') dep('managers', p[1]);
      const key: NavigationKey = ['ticket', t.id];
      addresses.set(compact(key), {
        key,
        value: {
          owner_kind: 'ticket',
          owner_id: t.id,
          conversation_id: id,
          current: t.conversation_id === id,
          parent_kind: p[0],
          parent_id: p[1],
        },
      });
    }
    for (const a of sql.all<{ agent_key: string }>(
      'SELECT agent_key FROM agents WHERE conversation_id=? ORDER BY agent_key',
      [id],
    )) {
      const agentKey = a.agent_key;
      dep('agents', agentKey);
      let owner: string | undefined;
      if (agentKey === 'chief_of_staff') owner = 'chief';
      else if (agentKey.startsWith('manager_')) {
        owner = agentKey.slice('manager_'.length);
        dep('managers', owner);
        if (!sql.get('SELECT 1 FROM managers WHERE id=?', [owner])) continue;
      }
      if (owner !== undefined) {
        const key: NavigationKey = [agentKey === 'chief_of_staff' ? 'chief' : 'manager', owner];
        addresses.set(compact(key), {
          key,
          value: {
            owner_kind: key[0],
            owner_id: key[1],
            conversation_id: id,
            current: true,
            parent_kind: null,
            parent_id: null,
          },
        });
      }
    }
    let environment: Record<string, string> = {};
    try {
      const pairs = JSON.parse(c.identity_environment_variables);
      if (Array.isArray(pairs))
        environment = Object.fromEntries(
          pairs
            .filter((pair) => Array.isArray(pair) && pair.length === 2)
            .map((pair) => [String(pair[0]), String(pair[1])]),
        );
    } catch {
      /* Invalid historical identities supply no supervisor alias. */
    }
    if (environment.PLAN_ACTOR === 'manager') {
      const owner = environment.PLAN_MANAGER_ID ?? '';
      dep('managers', owner);
      const key: NavigationKey = ['manager', owner];
      if (sql.get('SELECT 1 FROM managers WHERE id=?', [owner]) && !addresses.has(compact(key)))
        addresses.set(compact(key), {
          key,
          value: {
            owner_kind: 'manager',
            owner_id: owner,
            conversation_id: id,
            current: false,
            parent_kind: null,
            parent_id: null,
          },
        });
    }
    for (const [, address] of [...addresses].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      records.push(record('address', compact([...address.key, id]), address.value));
  } else if (kind === 'totals') {
    let children: number, conversations: number;
    if (id === 'workspace') {
      children = sql.get<{ value: number }>('SELECT COUNT(*) AS value FROM tickets')!.value;
      conversations = sql.get<{ value: number }>(
        "SELECT COUNT(DISTINCT json_extract(value,'$.conversation_id')) AS value FROM navigation_records WHERE kind='address'",
      )!.value;
    } else {
      const split = id.indexOf(':'),
        parentKind = id.slice(0, split),
        parentId = id.slice(split + 1);
      if (parentKind === 'manager') {
        dep('managers', parentId);
        if (!sql.get('SELECT 1 FROM managers WHERE id=?', [parentId])) return result();
        children = sql.get<{ value: number }>(
          'SELECT COUNT(*) AS value FROM tickets WHERE manager_id=?',
          [parentId],
        )!.value;
      } else
        children = sql.get<{ value: number }>(
          "SELECT COUNT(*) AS value FROM tickets WHERE manager_kind='chief'",
        )!.value;
      conversations = sql.get<{ value: number }>(
        "SELECT COUNT(DISTINCT conversation_id) AS value FROM (SELECT json_extract(value,'$.conversation_id') AS conversation_id FROM navigation_records WHERE kind='address' AND json_extract(value,'$.parent_kind')=? AND json_extract(value,'$.parent_id')=? UNION ALL SELECT json_extract(value,'$.conversation_id') AS conversation_id FROM navigation_records WHERE kind='address' AND json_extract(value,'$.owner_kind')=? AND json_extract(value,'$.owner_id')=?)",
        [parentKind, parentId, parentKind, parentId],
      )!.value;
    }
    records.push(
      record(kind, id, { child_count: children, distinct_conversation_count: conversations }),
    );
  }
  return result();
}
