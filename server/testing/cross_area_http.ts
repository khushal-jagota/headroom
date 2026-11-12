/** Synthetic loopback HTTP proof. No provider executable or database is used. */
import { readFileSync } from 'node:fs';
import { Runtime } from '../measure_sources/runtime/supervisor.ts';
import { DEFAULT_LIMITS } from '../measure_sources/configuration.ts';
import type { RunAdmission } from '../measure_sources/contracts.ts';
import type { CredentialResolver } from '../credentials/contracts.ts';
import { GitHubUpdateProvider } from '../environments/updates/provider.ts';
import { message_chief } from '../stalls/alarms.ts';
import { numeric_value } from '../core/http/numeric_json.ts';

const spec = JSON.parse(readFileSync(process.argv[2]!, 'utf8')) as {
  first: string;
  second: string;
  token: string;
};
const credentials: CredentialResolver = {
  resolve() {
    return {
      metadata: {
        id: 'synthetic',
        revision: 1,
        reference: { scheme: 'local', name: 'synthetic' },
        auth_kind: 'header',
        created_at: 0,
        updated_at: 0,
      },
      secret: { kind: 'header', header_name: 'X-Synthetic-Key', value: 'synthetic-secret' },
    };
  },
};
const program = `export default async function ({request,record}) {
  const first = ${JSON.stringify(spec.first)}, second = ${JSON.stringify(spec.second)};
  const same = await request(first + '/same', {credential_ref:'synthetic'});
  if (same.status !== 200) throw new Error('same-origin');
  let refused = false;
  try { await request(first + '/cross', {credential_ref:'synthetic'}); }
  catch (error) { refused = error.detail?.code === 'transport_failure' && error.message.includes(new URL(second).origin) && !error.message.includes('synthetic-secret'); }
  if (!refused) throw new Error('credential refusal');
  refused = false;
  try { await request(first + '/cross', {headers:{'x-TeSt-SeCrEt':'synthetic-secret'}}); }
  catch(error) { refused = error.detail?.code === 'transport_failure' && error.message.includes(new URL(second).origin) && !error.message.includes('synthetic-secret'); }
  if (!refused) throw new Error('custom header refusal');
  const cross = await request(first + '/cross', {headers:{AcCePt:'application/json','CoNtEnT-TyPe':'application/json'}});
  if (cross.status !== 200) throw new Error('uncredentialed redirect');
  const rewritten = await request(first + '/rewrite', {method:'POST',json_body:{value:1}});
  if (await rewritten.text() !== 'GET') throw new Error('redirect method');
  const twenty = await request(first + '/chain/20');
  if (twenty.status !== 200) throw new Error('twenty redirects');
  let limited = false;
  try { await request(first + '/chain/21'); }
  catch(error) { limited = error.detail?.code === 'transport_failure'; }
  if (!limited) throw new Error('redirect limit');
  await record('proof','2026-11-12',1);
}`;
const admission: RunAdmission = {
  script_text: program,
  previous: [],
  limits: DEFAULT_LIMITS,
  deadline_monotonic: performance.now() / 1000 + 30,
  run: {
    id: 'http-proof',
    source_id: 'source-proof',
    mode: 'test',
    trigger: 'test',
    status: 'running',
    source_revision: 1,
    script_revision: 1,
    script_sha256: 'synthetic',
    frozen_at: 1800000000,
    required_outputs: [
      {
        measure_id: 'proof',
        definition_revision: 1,
        kind: 'reading',
        timezone: 'UTC',
        period: null,
        required_date: '2026-11-12',
        period_interval: null,
      },
    ],
    dataset_outputs: [],
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
const source = await new Runtime().execute(admission, credentials);
const stall = await message_chief('synthetic', spec.first);
const provider = new GitHubUpdateProvider('synthetic/repo', spec.token, {
  fetch: (_url, options) => fetch(spec.first + '/updates', options),
});
const update = await provider.available();

// Shorten the product timers; preserve their per-read reset behavior.
const nativeTimeout = globalThis.setTimeout;
globalThis.setTimeout = ((
  handler: (...args: unknown[]) => void,
  timeout?: number,
  ...args: unknown[]
) =>
  nativeTimeout(
    handler,
    timeout === 30_000 || timeout === 10_000 ? 200 : timeout,
    ...args,
  )) as typeof setTimeout;
let slow: boolean, idle: boolean, stallSlow: boolean, stallIdle: boolean;
try {
  stallSlow = await message_chief('synthetic', spec.first + '/slow-chief');
  stallIdle = await message_chief('synthetic', spec.first + '/idle-chief');
  const slowProvider = new GitHubUpdateProvider('synthetic/repo', spec.token, {
    fetch: (_url, options) => fetch(spec.first + '/slow', options),
  });
  slow = (await slowProvider.available()) === null;
  const idleProvider = new GitHubUpdateProvider('synthetic/repo', spec.token, {
    fetch: (_url, options) => fetch(spec.first + '/idle', options),
  });
  idle = false;
  try {
    await idleProvider.available();
  } catch {
    idle = true;
  }
} finally {
  globalThis.setTimeout = nativeTimeout;
}
console.log(
  JSON.stringify({
    source_failure: source.failure,
    source_values: source.observations.map((item) => numeric_value(item.value)),
    stall,
    update,
    slow,
    idle,
    stallSlow,
    stallIdle,
  }),
);
