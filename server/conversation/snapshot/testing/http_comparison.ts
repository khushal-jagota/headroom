/** Backend-only route proof reuses the ordinary app through injection, without a listener. */
import { parse_backend_instance_id } from '../../../drivers/core/identity.ts';
import type { BackendInstanceId } from '../../../drivers/core/identity.ts';
import { createApp } from '../../../app.ts';
import { loadConfig } from '../../../core/config.ts';
import { TestClock } from '../../../core/clock.ts';
import { createDatabase } from '../../../core/database/index.ts';
import {
  BackendStateStore,
  model_refusal,
  offered_model_ids,
  save_machine_model_list,
} from '../../backend_state.ts';
import type { Database } from '../../../core/database/contracts.ts';
import { BackendUsageService } from '../../backend_usage/service.ts';
import type { BackendUsageAdapter, BackendUsageResult } from '../../backend_usage/contracts.ts';
import { BUILT_IN_MANIFESTS, built_in_driver_of } from '../../../drivers/built_in_manifests.ts';
import { default_instance_id_for_driver } from '../../../drivers/core/identity.ts';
import { BackendSnapshotService } from '../service.ts';
import type { BackendSnapshot, BackendHttpRuntime } from '../contracts.ts';
import type { DriverSignInController, SignInSession } from '../../../drivers/core/sign_in.ts';
import { UnknownSignInSession } from '../../../drivers/core/sign_in.ts';
import { createBackendRoutes } from '../routes.ts';
import { ConversationHttpWriteScope } from '../../http/writes.ts';
import { RecordingBackendProbeEnvironment } from '../recording_environment.ts';
import { ticketModelCatalogControl } from './ticket_models.ts';
import type { RemoteCatalogSource } from '../../model_resolution/contracts.ts';
interface Spec {
  readonly db_path: string;
  readonly now: string;
  readonly cards: readonly BackendSnapshot[];
  readonly named_machine: string;
  readonly usage: readonly (Omit<BackendUsageResult, 'observed_at' | 'windows'> & {
    observed_at: string | null;
    windows: readonly {
      kind: 'five_hour' | 'seven_day';
      used_percent: number;
      resets_at: string;
      model_scope: string | null;
    }[];
  })[];
  readonly sign_in: Omit<SignInSession, 'started_at' | 'deadline_at'> & {
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
function model_admission_evidence(database: Database, cards: readonly BackendSnapshot[]) {
  return database.write((tx) => {
    const initial = Object.fromEntries(
      ['hermes', 'codex', 'claude'].map((key) => [
        key,
        offered_model_ids(tx, 'vps', key as BackendInstanceId),
      ]),
    );
    for (const model of ['retired-model', 'mac-only'])
      tx.run("INSERT INTO backend_model_enablement VALUES ('codex',?,0)", [model]);
    const codex = cards.find((value) => value.backend_key === 'codex')!;
    const mac = {
      ...codex,
      available_models: [{ ...codex.available_models[0], model_id: 'mac-only' }],
      models_complete: false,
    };
    save_machine_model_list(tx, 'macos', mac);
    save_machine_model_list(tx, 'vps', codex);
    const rows = () =>
      tx
        .all<{ model_id: string }>(
          "SELECT model_id FROM backend_model_enablement WHERE model_id IN ('retired-model','mac-only') ORDER BY model_id",
        )
        .map((row) => row.model_id);
    const before = rows();
    save_machine_model_list(tx, 'macos', { ...mac, models_complete: true });
    const after = rows();
    const aliases = Object.fromEntries(
      ['opus', 'opus[1m]', 'claude-opus-5[1m]', 'sonnet'].map((model) => [
        model,
        model_refusal(tx, parse_backend_instance_id('claude'), model, 'vps'),
      ]),
    );
    return {
      initial,
      before,
      after,
      aliases,
      mac_refusal: model_refusal(tx, parse_backend_instance_id('codex'), 'gpt-6.1-sol', 'macos'),
    };
  });
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
  let current: SignInSession | null = null;
  const calls: string[] = [];
  const pending: SignInSession = {
    ...spec.sign_in,
    started_at: spec.sign_in.started_at,
    deadline_at: new Date(spec.sign_in.deadline_at),
  };
  const sign_in: DriverSignInController = {
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
  };
  const ids = BUILT_IN_MANIFESTS.map((driver) =>
    default_instance_id_for_driver(driver.driver_kind),
  );
  const snapshots = new BackendSnapshotService({
    environment,
    keep_complete_model_list: (snapshot) =>
      new BackendStateStore(database).keep_vps_model_list(snapshot),
    now: () => new Date(spec.now),
    sources: BUILT_IN_MANIFESTS.map((driver) => {
      const key = default_instance_id_for_driver(driver.driver_kind);
      return {
        instance_id: key,
        executable_name: driver.manifest.executable.name,
        status: {
          version_detects_upgrade: true,
          async probe() {
            const { backend_key: _key, ...card } = spec.cards.find(
              (value) => value.backend_key === key,
            )!;
            return { ...card, version: key === 'codex' ? version : card.version };
          },
        },
      };
    }),
  });
  const usage = new BackendUsageService(
    new Map(
      BUILT_IN_MANIFESTS.map((driver) => {
        const key = default_instance_id_for_driver(driver.driver_kind);
        const value = spec.usage.find((result) => result.backend_key === key);
        const adapter: BackendUsageAdapter | undefined =
          value === undefined
            ? undefined
            : {
                async refresh() {
                  return {
                    ...value,
                    observed_at: value.observed_at === null ? null : new Date(value.observed_at),
                    windows: value.windows.map((window) => ({
                      ...window,
                      scope_label: null,
                      resets_at: new Date(window.resets_at),
                    })),
                  };
                },
              };
        return [
          key,
          adapter === undefined
            ? { label: driver.client.label }
            : { label: driver.client.label, adapter },
        ] as const;
      }),
    ),
  );
  const writes = new ConversationHttpWriteScope();
  const named_catalog: RemoteCatalogSource = {
    identity: null,
    ready: true,
    owns_conversation: () => false,
    retained: () => null,
    async snapshot(key) {
      return {
        ...spec.cards.find((value) => value.backend_key === key)!,
        version: 'named-3.0.0',
      };
    },
  };
  const runtime: BackendHttpRuntime = {
    settled: () => writes.drain(),
    database,
    backend_snapshots: snapshots,
    backend_state: new BackendStateStore(database),
    backend_usage: usage,
    backend_ids: ids,
    sign_in: (key) => (key === 'codex' ? sign_in : null),
    machine_catalog: (machine_id) => (machine_id === spec.named_machine ? named_catalog : null),
    drivers: BUILT_IN_MANIFESTS,
    driver_of: (key) => built_in_driver_of(key) ?? null,
    reports_allowance_usage: (key) => spec.usage.some((result) => result.backend_key === key),
  };
  const config = loadConfig();
  config.db_path = spec.db_path;
  config.test_mode = true;
  const app = await createApp(config, {
    database,
    clock: new TestClock(spec.now),
    areas: [
      {
        prefix: '/api/conversation',
        routes: createBackendRoutes(runtime, {
          available: true,
          writes,
        }),
      },
      ticketModelCatalogControl(runtime),
    ],
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
    const admission = model_admission_evidence(database, spec.cards);
    const tables = database.read((sql) =>
      Object.fromEntries(
        ['backend_model_enablement', 'backend_usage_snapshots', 'backend_usage_windows'].map(
          (table) => [table, sql.all(`SELECT * FROM ${table} ORDER BY rowid`)],
        ),
      ),
    );
    return { admission, answers, tables, commands: environment.commands, sign_in_calls: calls };
  } finally {
    writes.seal();
    await writes.drain();
    await app.close();
    database.close();
  }
}
