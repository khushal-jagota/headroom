import { afterEach, expect, test } from 'vitest';
import { createServer } from 'node:http';
import type { RequestListener } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Runtime } from './supervisor.ts';
import { DEFAULT_LIMITS } from '../configuration.ts';
import type { RunAdmission } from '../contracts.ts';
import type { CredentialResolver } from '../../credentials/contracts.ts';
import { numeric_value, stringify_python_json } from '../../core/http/numeric_json.ts';
import { fixture } from '../../tests/ticket_fixture.ts';
import { create_credential_in_transaction } from '../../credentials/actions.ts';
import { DatabaseCredentialResolver, FileCredentialCipher } from '../../credentials/store.ts';
import { OWNER } from '../../core/contracts.ts';
import { create_source, submit_script } from '../actions.ts';
import { read_source, read_failure_episode } from '../data.ts';
import { request_run, claim_run, complete_run } from '../run_actions.ts';
import { prepare_admission } from '../admission.ts';
import { create_measure } from '../../measures/actions.ts';

const cleanup: Array<() => void | Promise<void>> = [];
afterEach(async () => {
  for (const close of cleanup.splice(0).reverse()) await close();
});
const noCredentials: CredentialResolver = {
  resolve() {
    throw new Error('not available');
  },
};
function admission(script: string, limits: Partial<RunAdmission['limits']> = {}): RunAdmission {
  return {
    script_text: script,
    previous: [],
    limits: { ...DEFAULT_LIMITS, ...limits },
    deadline_monotonic: performance.now() / 1000 + 10,
    run: {
      id: 'run-test',
      source_id: 'source-test',
      mode: 'test',
      trigger: 'test',
      status: 'running',
      source_revision: 1,
      script_revision: 1,
      script_sha256: 'synthetic',
      frozen_at: 1800000000,
      required_outputs: [
        {
          measure_id: 'measure-test',
          definition_revision: 1,
          kind: 'reading',
          timezone: 'UTC',
          period: null,
          required_date: '2026-11-12',
          period_interval: null,
        },
      ],
      dataset_outputs: [
        {
          dataset_id: 'dataset-test',
          definition_revision: 1,
          timezone: 'UTC',
          dimensions: [],
          fields: [{ name: 'value', label: 'Value', unit: 'events', kind: 'count' }],
          required_date: '2026-11-12',
        },
      ],
      history: null,
      schedule_date: null,
      schedule_time: null,
      created_at: 1800000000,
      started_at: 1800000000,
      finished_at: null,
      failure: null,
      metrics: null,
      observation_count: 0,
      dataset_result_count: 0,
      dataset_row_count: 0,
    },
  };
}
function module(body: string): string {
  return `export default async function ({context,request,latest,record,record_dataset}) {\n${body}\n}`;
}
async function provider(handler: RequestListener): Promise<string> {
  const server = createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  cleanup.push(
    () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
        server.closeAllConnections();
      }),
  );
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('address');
  return `http://127.0.0.1:${address.port}`;
}
// The runner was absent from existing coverage: this proves its actual process/IPC boundary.
test('good child sees only frozen data and a clean temporary home; parent retains numeric candidates', async () => {
  const result = await new Runtime().execute(
    admission(
      module(`
    if (!Object.isFrozen(context) || !Object.isFrozen(context.required_outputs)) throw new Error();
    if (Object.keys(process.env).some(key=>key.startsWith('PLAN_') || ['CODEX_HOME','HERMES_HOME','CLAUDE_CONFIG_DIR','NODE_OPTIONS'].includes(key))) throw new Error();
    if (process.cwd() !== process.env.HOME || process.env.HOME !== process.env.TMPDIR) throw new Error();
    if (!process.execArgv.includes('--max-old-space-size=256')) throw new Error();
    if (await latest('measure-test') !== null) throw new Error();
    await record('measure-test','2026-11-12',9007199254740993n);
    await record('measure-test','2026-11-11',-0);
    await record_dataset('dataset-test','2026-11-12',[{dimensions:{},values:{value:1}}],{query_end_at:1800000000});
  `),
    ),
    noCredentials,
  );
  expect(result.failure).toBeNull();
  expect(result.observations).toHaveLength(2);
  expect(stringify_python_json(result.dataset_results[0]!.rows[0]!.values)).toBe('{"value": 1.0}');
  expect(numeric_value(result.observations[0]!.value)).toBe(9007199254740992);
  expect(Object.is(numeric_value(result.observations[1]!.value), -0)).toBe(true);
});
// Failed child output cannot escape into atomic retention or persist an exception secret.
test('failed script discards provisional outputs and retains only safe diagnostics', async () => {
  const result = await new Runtime().execute(
    admission(
      module(`await record('measure-test','2026-11-12',7); throw new Error('synthetic-secret');`),
    ),
    noCredentials,
  );
  expect(result.failure?.code).toBe('runtime_failure');
  expect(result.observations).toEqual([]);
  expect(JSON.stringify(result)).not.toContain('synthetic-secret');
});
test('parent kills a non-cooperative script at its wall deadline', async () => {
  const result = await new Runtime().execute(
    admission(module('while(true) {}'), { wall_seconds: 0.1 }),
    noCredentials,
  );
  expect(result.failure?.code).toBe('budget_exhausted');
  expect(result.failure?.budget).toBe('wall_seconds');
  expect(result.metrics.elapsed_milliseconds).toBeLessThan(3000);
});
test('server interruption cancels an active child and prevents candidate retention', async () => {
  const runtime = new Runtime();
  const result = runtime.execute(admission(module('await new Promise(()=>{});')), noCredentials);
  const timer = setTimeout(() => runtime.cancel('run-test'), 100);
  try {
    expect((await result).failure?.code).toBe('interrupted');
  } finally {
    clearTimeout(timer);
  }
});
test('credential use crosses private IPC from an encrypted temporary store to a local provider', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-source-credential-'));
  cleanup.push(() => rmSync(root, { recursive: true, force: true }));
  const key = join(root, 'key');
  const db = fixture();
  const settings = { principal: OWNER, now: 1800000000, key_path: key };
  db.write((sql) =>
    create_credential_in_transaction(
      sql,
      {
        reference: { scheme: 'local', name: 'local-provider' },
        auth_kind: 'bearer',
        secret: { kind: 'bearer', token: 'synthetic-only-token' },
      },
      settings,
    ),
  );
  let authorization: string | undefined;
  const url = await provider((request, response) => {
    authorization = request.headers.authorization;
    response.setHeader('Content-Type', 'application/json');
    if (request.url === '/fail') {
      response.writeHead(401);
      response.end('{"error":"synthetic-provider-secret"}');
    } else response.end('{"value":12.5}');
  });
  const credentials: CredentialResolver = {
    resolve(reference) {
      return db.read((sql) =>
        new DatabaseCredentialResolver(sql, new FileCredentialCipher(key, true)).resolve(reference),
      );
    },
  };
  const result = await new Runtime().execute(
    admission(
      module(
        `const response=await request(${JSON.stringify(url)},{credential_ref:'local-provider'}); response.raise_for_status(); await record('measure-test','2026-11-12',(await response.json()).value);`,
      ),
    ),
    credentials,
  );
  expect(result.failure).toBeNull();
  expect(authorization).toBe('Bearer synthetic-only-token');
  expect(numeric_value(result.observations[0]!.value)).toBe(12.5);
  expect(result.metrics.request_count).toBe(1);
  expect(JSON.stringify(result)).not.toContain('synthetic-only-token');
  // Gap: standalone runner results do not prove child output reaches retained rows
  // or that a real HTTP failure reaches the canonical typed notice producer.
  // Reuse this same database, key, provider and Runtime for both collection outcomes.
  const source = db.write((sql) =>
    create_source(
      sql,
      {
        name: 'Credential-backed collection',
        scope: { project_id: 'project_vylo', account: 'local-provider' },
        responsible: { kind: 'chief', id: 'chief' },
      },
      settings,
    ),
  );
  const measure = db.write((sql) =>
    create_measure(
      sql,
      {
        project_id: 'project_vylo',
        source_id: source.id,
        name: 'Provider value',
        unit: 'items',
        origin: 'taken',
        kind: 'reading',
        timezone: 'UTC',
        period: null,
        formula: null,
      },
      settings.now,
    ),
  );
  const collect = async (providerPath: string) => {
    const selected = db.write((sql) =>
      submit_script(
        sql,
        source.id,
        {
          expected_revision: read_source(sql, source.id).source_revision,
          text: module(
            `const response=await request(${JSON.stringify(url + providerPath)},{credential_ref:'local-provider'}); response.raise_for_status(); const output=context.required_outputs[0]; await record(output.measure_id,output.required_date,(await response.json()).value);`,
          ),
        },
        settings,
      ),
    );
    const queued = db.write((sql) =>
      request_run(sql, source.id, {
        mode: 'collection',
        history: null,
        expected_script_revision: selected.active_script!.revision,
        principal: OWNER,
        now: settings.now,
      }),
    );
    const claimed = db.write((sql) => claim_run(sql, queued.id, { now: settings.now }));
    expect(claimed).not.toBeNull();
    const [prepared, resolver] = prepare_admission(db, claimed!, { key_path: key });
    const executed = await new Runtime().execute(prepared, resolver);
    const completed = db.write((sql) =>
      complete_run(sql, queued.id, executed, { now: settings.now }),
    );
    return { executed, completed };
  };
  const collected = await collect('');
  expect(collected.executed.failure).toBeNull();
  expect(collected.completed.run.status).toBe('succeeded');
  expect(collected.completed.run.observation_count).toBe(1);
  expect(
    db.read(
      (sql) =>
        sql.get<{ value: number }>('SELECT value FROM measure_observations WHERE measure_id=?', [
          measure.id,
        ])!.value,
    ),
  ).toBe(12.5);
  const failed = await collect('/fail');
  expect(failed.executed.failure).toMatchObject({ code: 'http_error', http_status: 401 });
  expect(failed.executed.observations).toEqual([]);
  expect(failed.completed.run.status).toBe('failed');
  expect(authorization).toBe('Bearer synthetic-only-token');
  expect(JSON.stringify(failed)).not.toContain('synthetic-provider-secret');
  expect(JSON.stringify(failed)).not.toContain('synthetic-only-token');
  const episode = db.read((sql) => read_failure_episode(sql, source.id));
  expect(episode).toMatchObject({
    source_id: source.id,
    episode: 1,
    latest_run_id: failed.completed.run.id,
    closed_at: null,
  });
  const notice = db.read((sql) =>
    sql.get<Record<string, unknown>>('SELECT * FROM manager_wakes WHERE measure_source_id=?', [
      source.id,
    ]),
  );
  expect(notice).toMatchObject({
    source_kind: 'source_failure',
    measure_source_id: source.id,
    source_episode: 1,
    source_responsibility_revision: 1,
    target_kind: 'chief',
    target_id: 'chief',
  });
  expect(
    db.read((sql) =>
      sql.get<{ value: number; run_id: string }>(
        'SELECT value,run_id FROM measure_observations WHERE measure_id=?',
        [measure.id],
      ),
    ),
  ).toEqual({ value: 12.5, run_id: collected.completed.run.id });
  db.close();
});
// Smaller configured limits exercise real enforcement without consuming production budgets on the VPS.
test('stdout and private control frames share the output byte budget', async () => {
  for (const [body, ordinaryBytes] of [
    ["process.stdout.write('x'.repeat(4096));", 4096],
    ["console.log('x'.repeat(4096));", 4097],
    ["await record('measure-test','2026-11-12',1,{limitation:'x'.repeat(4096)});", 0],
  ] as const) {
    const result = await new Runtime().execute(
      admission(module(body), { output_bytes: 1024 }),
      noCredentials,
    );
    expect(result.failure?.budget).toBe('output_bytes');
    expect(result.metrics.child_output_bytes).toBe(ordinaryBytes);
    expect(result.observations).toEqual([]);
  }
});
test('decoded HTTP bytes accumulate across responses and exhaust the run budget', async () => {
  const url = await provider((_request, response) => response.end('1234567890'));
  const result = await new Runtime().execute(
    admission(
      module(
        `await request(${JSON.stringify(url)}); try { await request(${JSON.stringify(url)}); } catch {}`,
      ),
      { response_bytes: 15 },
    ),
    noCredentials,
  );
  expect(result.failure?.budget).toBe('response_bytes');
  expect(result.metrics.response_bytes).toBe(20);
});
test('V8 old-space exhaustion is a safe budget failure and the child is reaped', async () => {
  const result = await new Runtime().execute(
    admission(module('const held=[]; while(true) held.push(new Array(100000).fill(42));'), {
      heap_megabytes: 32,
    }),
    noCredentials,
  );
  expect(result.failure?.budget).toBe('heap_bytes');
  expect(result.observations).toEqual([]);
  // Pipe chunks have no message boundary. Exercise both diagnostic markers with
  // a real child and a delay between writes, without another test host.
  for (const marker of ['JavaScript heap out of memory', 'Reached heap limit']) {
    const middle = Math.floor(marker.length / 2);
    const split = await new Runtime().execute(
      admission(
        module(`
        await new Promise(resolve => process.stderr.write(${JSON.stringify(marker.slice(0, middle))}, resolve));
        await new Promise(resolve => setTimeout(resolve, 25));
        await new Promise(resolve => process.stderr.write(${JSON.stringify(marker.slice(middle))}, resolve));
        process.exit(1);
      `),
        { wall_seconds: 1 },
      ),
      noCredentials,
    );
    expect(split.failure?.budget).toBe('heap_bytes');
    expect(split.observations).toEqual([]);
    expect(JSON.stringify(split)).not.toContain(marker);
  }
});
