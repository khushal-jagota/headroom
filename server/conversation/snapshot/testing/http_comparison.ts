/** Backend-only route proof reuses the ordinary app through injection, without a listener. */
import { createApp } from '../../../app.ts';
import { loadConfig } from '../../../core/config.ts';
import { TestClock } from '../../../core/clock.ts';
import { createDatabase } from '../../../core/database/index.ts';
import { BackendStateStore } from '../../backend_state.ts';
import { BackendUsageService } from '../../backend_usage/service.ts';
import type { BackendUsageResult } from '../../backend_usage/contracts.ts';
import type { ConversationBackendKey } from '../../contracts.ts';
import { BackendSnapshotService } from '../service.ts';
import type { BackendSnapshot, BackendHttpRuntime } from '../contracts.ts';
import type { CodexSignIn, CodexSignInService } from '../provider_contracts.ts';
import { UnknownSignInSession } from '../provider_contracts.ts';
import { createBackendRoutes } from '../routes.ts';
import { RecordingBackendProbeEnvironment } from './environment.ts';
interface Spec {
  readonly db_path: string;
  readonly now: string;
  readonly cards: readonly BackendSnapshot[];
  readonly usage: readonly (Omit<BackendUsageResult, 'observed_at' | 'windows'> & {
    observed_at: string | null;
    windows: readonly {
      kind: 'five_hour' | 'seven_day';
      used_percent: number;
      resets_at: string;
      model_scope: string | null;
    }[];
  })[];
  readonly sign_in: Omit<CodexSignIn, 'started_at' | 'deadline_at'> & {
    started_at: string;
    deadline_at: string;
  };
  readonly requests: readonly {
    method: 'GET' | 'POST' | 'PUT' | 'DELETE';
    path: string;
    json?: unknown;
    headers?: Record<string, string>;
  }[];
}
export async function run(spec: Spec) {
  const database = createDatabase(spec.db_path);
  const environment = new RecordingBackendProbeEnvironment();
  let version = '1.0.0';
  environment.run = async (argv) => {
    environment.commands.push([...argv]);
    if (JSON.stringify(argv) !== JSON.stringify(['synthetic-cli', 'update']))
      throw new Error('Unrecorded external action');
    version = '2.0.0';
    return { exit_code: 0, standard_output: 'Recorded update', standard_error: '' };
  };
  let current: CodexSignIn | null = null;
  const calls: string[] = [];
  const pending: CodexSignIn = {
    ...spec.sign_in,
    started_at: new Date(spec.sign_in.started_at),
    deadline_at: new Date(spec.sign_in.deadline_at),
  };
  const sign_in: CodexSignInService = {
    read: () => current,
    async start() {
      calls.push('start');
      current = pending;
      return current;
    },
    async cancel(id) {
      calls.push('cancel:' + id);
      if (!current || current.session_id !== id) throw new UnknownSignInSession();
      current = {
        ...current,
        state: 'ended',
        verification_url: null,
        user_code: null,
        detail: 'Recorded cancellation',
      };
      return current;
    },
    async shutdown() {
      calls.push('shutdown');
    },
  };
  const snapshots = new BackendSnapshotService({
    environment,
    now: () => new Date(spec.now),
    read_codex_login_standing: () => null,
    probe: async (key) => {
      const card = spec.cards.find((value) => value.backend_key === key)!;
      return { ...card, version: key === 'codex' ? version : card.version };
    },
  });
  const usage = new BackendUsageService(
    Object.fromEntries(
      spec.usage.map((value) => [
        value.backend_key,
        {
          async refresh() {
            return {
              ...value,
              observed_at: value.observed_at === null ? null : new Date(value.observed_at),
              windows: value.windows.map((window) => ({
                ...window,
                resets_at: new Date(window.resets_at),
              })),
            };
          },
        },
      ]),
    ) as Partial<Record<ConversationBackendKey, { refresh(): Promise<BackendUsageResult> }>>,
  );
  const runtime: BackendHttpRuntime = {
    database,
    backend_snapshots: snapshots,
    backend_state: new BackendStateStore(database),
    backend_usage: usage,
    codex_sign_in: sign_in,
    machine_catalog: () => null,
  };
  const config = loadConfig();
  config.db_path = spec.db_path;
  config.test_mode = true;
  const app = await createApp(config, {
    database,
    clock: new TestClock(spec.now),
    areas: [{ prefix: '/api/conversation', routes: createBackendRoutes(runtime) }],
  });
  try {
    const answers = [];
    for (const request of spec.requests) {
      const response = await app.inject({
        method: request.method,
        url: request.path,
        ...(request.json === undefined ? {} : { payload: request.json as Record<string, unknown> }),
        headers: request.headers,
      });
      answers.push({ status: response.statusCode, body: response.json() as unknown });
    }
    const tables = database.read((sql) =>
      Object.fromEntries(
        ['backend_model_enablement', 'backend_usage_snapshots', 'backend_usage_windows'].map(
          (table) => [table, sql.all(`SELECT * FROM ${table} ORDER BY rowid`)],
        ),
      ),
    );
    return { answers, tables, commands: environment.commands, sign_in_calls: calls };
  } finally {
    await app.close();
    database.close();
  }
}
