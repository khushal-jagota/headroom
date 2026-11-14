/** One filtered-environment child per trusted script. Only the parent accepts provisional output. */
import { ChildProcesses, child_lifetime, signal_child } from '../../lifecycle/owned_processes.ts';
import type { ChildProcess } from 'node:child_process';
import type { Duplex } from 'node:stream';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { numeric_value } from '../../core/http/numeric_json.ts';
import type { NumericJSON } from '../../core/http/numeric_json.ts';
import { stringify_runtime_json } from './json.ts';
import { calendar_date } from '../../measures/logic/dates.ts';
import { validate_coverage } from '../../measures/logic/coverage.ts';
import { float_number } from '../../measures/logic/formulas.ts';
import { parse_result, validate_result } from '../../datasets/logic/validation.ts';
import type { CandidateDatasetResult } from '../../datasets/contracts.ts';
import type { CredentialResolver } from '../../credentials/contracts.ts';
import type { CandidateObservation, RunAdmission, RunResult, RunFailure } from '../contracts.ts';
import { failure } from '../contracts.ts';
function plain(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error('protocol');
  return value as Record<string, unknown>;
}
function scalar(raw: Record<string, unknown>): NumericJSON<CandidateObservation> {
  if (typeof raw.measure_id !== 'string' || typeof raw.date !== 'string')
    throw new Error('candidate');
  calendar_date(raw.date);
  const value = raw.value === null ? null : float_number(raw.value);
  const reason = raw.unavailable_reason ?? null;
  if (
    (value === null) !== (reason !== null) ||
    (reason !== null &&
      (typeof reason !== 'string' ||
        !['provider_has_no_history', 'provider_has_no_value'].includes(reason)))
  )
    throw new Error('candidate');
  const start = raw.query_start_at ?? null,
    end = raw.query_end_at ?? null,
    limitation = raw.limitation ?? null;
  validate_coverage(start, end, limitation);
  return {
    measure_id: raw.measure_id,
    date: raw.date,
    value,
    unavailable_reason: reason as CandidateObservation['unavailable_reason'],
    query_start_at: start === null ? null : float_number(start),
    query_end_at: end === null ? null : float_number(end),
    limitation: limitation as string | null,
  };
}
const CHILD_ENVIRONMENT_KEYS = [
  'TZ',
  'PATH',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'NO_PROXY',
  'http_proxy',
  'https_proxy',
  'no_proxy',
  'SSL_CERT_FILE',
  'SSL_CERT_DIR',
  'NODE_EXTRA_CA_CERTS',
] as const;
export class Runtime {
  private active = new Map<string, () => void>();
  private canceled = new Set<string>();
  cancel(id: string): void {
    const stop = this.active.get(id);
    if (stop) stop();
    else this.canceled.add(id);
  }
  async execute(admission: RunAdmission, credentials: CredentialResolver): Promise<RunResult> {
    const started = performance.now() / 1000;
    const metrics = {
      request_count: 0,
      response_bytes: 0,
      child_output_bytes: 0,
      elapsed_milliseconds: 0,
      script_cpu_milliseconds: 0,
    };
    // The accepted budget covers control frames too; the legacy metric does not.
    let totalOutputBytes = 0;
    let resultFailure: RunFailure | null = null;
    const observations: NumericJSON<CandidateObservation[]> = [];
    const datasets: NumericJSON<CandidateDatasetResult[]> = [];
    let process: ChildProcess | undefined;
    let directory: string | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const children = new ChildProcesses();
    let childExited: Promise<void> | undefined;
    const kill = () => {
      if (process) signal_child(process, 'SIGKILL');
    };
    let finish: (value: RunFailure | null) => void = () => {};
    let stopped = false;
    const stop = () => {
      stopped = true;
      finish(failure('interrupted'));
    };
    this.active.set(admission.run.id, stop);
    if (this.canceled.delete(admission.run.id)) stop();
    try {
      if (stopped) {
        resultFailure = failure('interrupted');
        return { observations: [], dataset_results: [], failure: resultFailure, metrics };
      }
      directory = await mkdtemp(join(tmpdir(), 'panels-source-'));
      const script = join(directory, 'source.mjs');
      await writeFile(script, admission.script_text, { mode: 0o600 });
      if (stopped) {
        resultFailure = failure('interrupted');
        return { observations: [], dataset_results: [], failure: resultFailure, metrics };
      }
      const remaining = Math.min(
        admission.deadline_monotonic - started,
        admission.limits.wall_seconds,
      );
      if (remaining <= 0) {
        resultFailure = failure('budget_exhausted', { budget: 'wall_seconds' });
        return { observations: [], dataset_results: [], failure: resultFailure, metrics };
      }
      process = children.spawn(
        globalThis.process.execPath,
        [
          `--max-old-space-size=${admission.limits.heap_megabytes}`,
          fileURLToPath(new URL('./bootstrap.ts', import.meta.url)),
        ],
        {
          cwd: directory,
          env: {
            HOME: directory,
            TMPDIR: directory,
            LANG: 'C.UTF-8',
            ...Object.fromEntries(
              CHILD_ENVIRONMENT_KEYS.flatMap((key) =>
                globalThis.process.env[key] === undefined
                  ? []
                  : [[key, globalThis.process.env[key]]],
              ),
            ),
          },
          detached: true,
          stdio: ['ignore', 'pipe', 'pipe', 'pipe'],
        },
      );
      childExited = child_lifetime(process).exited;
      const channel = process.stdio[3] as Duplex;
      const complete = new Promise<RunFailure | null>((resolve) => {
        let settled = false;
        finish = (value) => {
          if (settled) return;
          settled = true;
          resolve(value);
        };
        const count = (bytes: number, ordinaryOutput: boolean) => {
          totalOutputBytes += bytes;
          if (ordinaryOutput) metrics.child_output_bytes += bytes;
          if (totalOutputBytes > admission.limits.output_bytes)
            finish(failure('budget_exhausted', { budget: 'output_bytes' }));
        };
        let pending = Buffer.alloc(0);
        const reply = (id: unknown, value: unknown, problem: RunFailure | null = null) =>
          channel.write(stringify_runtime_json({ id, value, failure: problem }) + '\n');
        channel.on('data', (chunk: Buffer) => {
          count(chunk.length, false);
          if (settled) return;
          pending = Buffer.concat([pending, chunk]);
          let boundary: number;
          while (!settled && (boundary = pending.indexOf(10)) >= 0) {
            const line = pending.subarray(0, boundary).toString('utf8');
            pending = pending.subarray(boundary + 1);
            try {
              // Candidate validation below projects readings and datasets to finite doubles.
              const raw = plain(JSON.parse(line));
              if (raw.op === 'done') {
                for (const [key, wire] of [
                  ['request_count', 'request_count'],
                  ['response_bytes', 'response_bytes'],
                  ['script_cpu_milliseconds', 'cpu_milliseconds'],
                ] as const) {
                  const n = numeric_value(raw[wire]);
                  if (n === undefined || !Number.isInteger(n) || n < 0) throw new Error('metrics');
                  metrics[key] = n;
                }
                if (metrics.response_bytes > admission.limits.response_bytes) {
                  finish(failure('budget_exhausted', { budget: 'response_bytes' }));
                  continue;
                }
                // Child diagnostics are allowlisted, never arbitrary exception messages.
                let problem: RunFailure | null = null;
                if (raw.failure !== null) {
                  const f = plain(raw.failure);
                  const codes = [
                    'runtime_failure',
                    'malformed_protocol',
                    'credential_unavailable',
                    'transport_failure',
                    'http_error',
                    'invalid_json',
                    'invalid_output',
                    'budget_exhausted',
                  ];
                  if (!codes.includes(String(f.code))) throw new Error('failure');
                  const status = numeric_value(f.http_status);
                  const sourceLine = numeric_value(f.source_line);
                  const budget = f.budget === 'response_bytes' ? 'response_bytes' : null;
                  problem = failure(f.code as RunFailure['code'], {
                    budget,
                    source_line:
                      sourceLine !== undefined &&
                      Number.isInteger(sourceLine) &&
                      sourceLine >= 1 &&
                      sourceLine <= admission.script_text.split('\n').length
                        ? sourceLine
                        : null,
                    http_status:
                      status !== undefined &&
                      Number.isInteger(status) &&
                      status >= 100 &&
                      status <= 599
                        ? status
                        : null,
                    exception_type: [
                      'SyntaxError',
                      'TypeError',
                      'ReferenceError',
                      'RangeError',
                    ].includes(String(f.exception_type))
                      ? (f.exception_type as string)
                      : null,
                  });
                }
                finish(problem);
                continue;
              }
              const id = raw.id;
              if (!Number.isInteger(numeric_value(id))) throw new Error('request');
              if (raw.op === 'credential') {
                try {
                  const reference = plain(raw.reference);
                  if (reference.scheme !== 'local' || typeof reference.name !== 'string')
                    throw new Error('credential');
                  reply(id, credentials.resolve({ scheme: 'local', name: reference.name }).secret);
                } catch {
                  reply(id, null, failure('credential_unavailable'));
                }
              } else if (raw.op === 'latest') {
                if (!admission.run.required_outputs.some((o) => o.measure_id === raw.measure_id)) {
                  reply(id, null, failure('invalid_output'));
                  continue;
                }
                const prior = admission.previous
                  .filter((o) => o.measure_id === raw.measure_id)
                  .sort((a, b) => b.date.localeCompare(a.date));
                reply(id, prior[0] ?? null);
              } else if (raw.op === 'record') {
                try {
                  if (!admission.run.required_outputs.some((o) => o.measure_id === raw.measure_id))
                    throw new Error('output');
                  observations.push(scalar(raw));
                  reply(id, null);
                } catch {
                  reply(id, null, failure('invalid_output'));
                }
              } else if (raw.op === 'record_dataset') {
                try {
                  const { op: _op, id: _id, ...body } = raw;
                  const parsed = parse_result(body);
                  const output = admission.run.dataset_outputs.find(
                    (o) => o.dataset_id === parsed.dataset_id,
                  );
                  if (!output) throw new Error('output');
                  validate_result(output, parsed);
                  datasets.push({
                    ...parsed,
                    query_end_at: float_number(parsed.query_end_at),
                    query_start_at:
                      parsed.query_start_at === null ? null : float_number(parsed.query_start_at),
                    rows: parsed.rows.map((row) => ({
                      ...row,
                      values: Object.fromEntries(
                        Object.entries(row.values).map(([key, value]) => [
                          key,
                          float_number(value),
                        ]),
                      ),
                    })),
                  });
                  reply(id, null);
                } catch {
                  reply(id, null, failure('invalid_output'));
                }
              } else throw new Error('operation');
            } catch {
              finish(failure('malformed_protocol'));
            }
          }
        });
        process!.stdout!.on('data', (chunk: Buffer) => count(chunk.length, true));
        let fatalHeap = false;
        let stderrSuffix = '';
        process!.stderr!.on('data', (chunk: Buffer) => {
          count(chunk.length, true);
          const diagnostic = stderrSuffix + chunk.toString('utf8');
          if (
            diagnostic.includes('JavaScript heap out of memory') ||
            diagnostic.includes('Reached heap limit')
          )
            fatalHeap = true;
          stderrSuffix = diagnostic.slice(-128);
        });
        process!.on('error', () => finish(failure('runtime_failure')));
        process!.on('exit', () => {
          // Allow already-readable diagnostics one poll turn, without waiting
          // for EOF from a detached descendant that inherited the pipes.
          setImmediate(() =>
            finish(
              fatalHeap
                ? failure('budget_exhausted', { budget: 'heap_bytes' })
                : failure('runtime_failure'),
            ),
          );
        });
        channel.on('error', () => finish(failure('runtime_failure')));
        timer = setTimeout(
          () => finish(failure('budget_exhausted', { budget: 'wall_seconds' })),
          Math.max(0, (started + remaining - performance.now() / 1000) * 1000),
        );
      });
      channel.write(
        stringify_runtime_json({
          script_url: pathToFileURL(script).href,
          context: {
            run_id: admission.run.id,
            frozen_at: admission.run.frozen_at,
            required_outputs: admission.run.required_outputs,
            dataset_outputs: admission.run.dataset_outputs,
            history: admission.run.history,
          },
          limits: admission.limits,
        }) + '\n',
      );
      if (stopped) finish(failure('interrupted'));
      resultFailure = await complete;
    } catch {
      resultFailure = failure('runtime_failure');
    } finally {
      if (timer) clearTimeout(timer);
      kill();
      await childExited;
      // Drain already-readable bytes once; descendant-held EOF is not a gate.
      await new Promise<void>((resolve) => setImmediate(resolve));
      process?.stdio.forEach((stream) => stream?.destroy());
      await children.settled();
      if (directory) await rm(directory, { recursive: true, force: true });
      this.active.delete(admission.run.id);
      metrics.elapsed_milliseconds = Math.trunc((performance.now() / 1000 - started) * 1000);
    }
    if (totalOutputBytes > admission.limits.output_bytes)
      resultFailure = failure('budget_exhausted', { budget: 'output_bytes' });
    if (metrics.response_bytes > admission.limits.response_bytes)
      resultFailure = failure('budget_exhausted', { budget: 'response_bytes' });
    return {
      observations: resultFailure === null ? observations : [],
      dataset_results: resultFailure === null ? datasets : [],
      failure: resultFailure,
      metrics,
    };
  }
}
