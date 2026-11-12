import { expect, test } from 'vitest';
import { isAbsolute, relative, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fixture, fixture_path, create } from '../tests/ticket_fixture.ts';
import { collection_script, DAILY_METRICS } from './collection.ts';
import { Runtime } from '../measure_sources/runtime/supervisor.ts';
import { DEFAULT_LIMITS } from '../measure_sources/configuration.ts';
import type { RunAdmission } from '../measure_sources/contracts.ts';
import { numeric_value } from '../core/http/numeric_json.ts';
import { createReadAccess } from '../core/database/index.ts';

// Existing summary tests do not cross the generated module/child/private SQLite boundary.
// Reuse the Ticket schema fixture and ordinary source Runtime, without another host.
test('generated collector runs in a real child with an explicit temporary read-only database path', async () => {
  const database = fixture(),
    path = fixture_path(database),
    now = 1793793600,
    ticket = create(database);
  const inside = relative(resolve(tmpdir()), resolve(path));
  expect(inside.startsWith('..') || isAbsolute(inside)).toBe(false);
  database.write((sql) => {
    sql.run('UPDATE attention_history_metadata SET started_at=?', [now - 86400 * 20]);
    sql.run(
      `INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at)
 VALUES ('attention-child','codex',?,'full',?)`,
      [resolve(tmpdir()), now - 86400 * 20],
    );
    sql.run(
      `INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at)
 VALUES ('attention-child',1,'prompt',?,?)`,
      [
        JSON.stringify({
          content: [],
          sender: { kind: 'owner', id: 'owner' },
          recipient: { kind: 'ticket', id: ticket.id },
          sender_message_id: 'attention-child-owner',
          mode: 'steer',
        }),
        now - 60,
      ],
    );
    sql.run(
      `INSERT INTO attention_approval_events(ticket_id,field,actor_kind,actor_id,method,
 occurred_at,destination_json) VALUES (?,'brief','owner','owner','explicit_accept',?,?)`,
      [
        ticket.id,
        now - 120,
        JSON.stringify({
          kind: 'ticket',
          id: ticket.id,
          title: ticket.title,
          url: '/tickets/' + ticket.id,
          project_id: 'project_vylo',
          manager_id: null,
          goal_id: null,
          attribution_basis: 'recorded',
        }),
      ],
    );
  });
  const dataset_ids = Object.fromEntries(
      DAILY_METRICS.map((metric) => [metric, 'dataset_' + metric]),
    ),
    measure_ids = Object.fromEntries(DAILY_METRICS.map((metric) => [metric, 'measure_' + metric])),
    script = collection_script(path, dataset_ids, measure_ids);
  expect(script).toContain(JSON.stringify(path));
  expect(script).toContain("from 'panels:attention'");
  expect(script).not.toContain(new URL('./collection.ts', import.meta.url).href);
  expect(script).not.toContain('file:');
  const admission: RunAdmission = {
    script_text: script,
    previous: [],
    limits: DEFAULT_LIMITS,
    deadline_monotonic: performance.now() / 1000 + 30,
    run: {
      id: 'attention-run',
      source_id: 'attention-source',
      mode: 'test',
      trigger: 'test',
      status: 'running',
      source_revision: 1,
      script_revision: 1,
      script_sha256: 'synthetic',
      frozen_at: now,
      required_outputs: DAILY_METRICS.map((metric) => ({
        measure_id: measure_ids[metric]!,
        definition_revision: 1,
        kind: 'period_count',
        timezone: 'Europe/Berlin',
        period: 'day',
        required_date: '2026-11-04',
        period_interval: null,
      })),
      dataset_outputs: DAILY_METRICS.map((metric) => ({
        dataset_id: dataset_ids[metric]!,
        definition_revision: 1,
        timezone: 'Europe/Berlin',
        required_date: '2026-11-04',
        dimensions: [
          ['destination_kind', 'Destination kind'],
          ['destination_id', 'Destination'],
          ['project_id', 'Project'],
          ['manager_id', 'Manager'],
          ['attribution_basis', 'Attribution basis'],
          ['coverage_status', 'Coverage'],
        ].map(([name, label]) => ({ name, label })),
        fields: [
          {
            name: 'value',
            label: metric,
            unit: metric.endsWith('seconds') ? 'seconds' : 'events',
            kind: metric.endsWith('seconds') ? 'amount' : 'count',
          },
        ],
      })),
      history: { first_date: '2026-11-04', last_date: '2026-11-04' },
      schedule_date: null,
      schedule_time: null,
      created_at: now,
      started_at: now,
      finished_at: null,
      failure: null,
      metrics: null,
      observation_count: 0,
      dataset_result_count: 0,
      dataset_row_count: 0,
    },
  };
  const result = await new Runtime().execute(admission, {
    resolve() {
      throw new Error('collector needs no credentials');
    },
  });
  expect(result.failure).toBeNull();
  expect(result.observations).toHaveLength(3);
  expect(
    numeric_value(
      result.observations.find((x) => x.measure_id === measure_ids.owner_messages)!.value,
    ),
  ).toBe(1);
  expect(
    numeric_value(
      result.observations.find((x) => x.measure_id === measure_ids.owner_approvals)!.value,
    ),
  ).toBe(1);
  expect(result.dataset_results).toHaveLength(3);
  expect(
    result.dataset_results
      .find((x) => x.dataset_id === dataset_ids.owner_messages)!
      .rows.some((row) => numeric_value(row.values.value) === 1),
  ).toBe(true);
  // The exact reader used by the imported child collector opens query_only/readonly.
  const reader = createReadAccess(path);
  try {
    reader.read((sql) => {
      expect('run' in sql).toBe(false);
      expect(() =>
        sql.get('UPDATE attention_history_metadata SET started_at=0 RETURNING started_at'),
      ).toThrow();
    });
  } finally {
    reader.close();
  }
  expect(
    database.read(
      (sql) =>
        sql.get<{ started_at: number }>('SELECT started_at FROM attention_history_metadata')!
          .started_at,
    ),
  ).toBe(now - 86400 * 20);
});
