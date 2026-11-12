/** Export-only scripted adapters over the production service and database door. */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { createDatabase } from '../../../core/database/index.ts';
import { register_test_root } from '../../../core/test_paths.ts';
import { ChangeSignal } from '../../../core/changes/index.ts';
import { WorkspaceAdmission } from '../../../conversation/workspace_admission.ts';
import type { ConversationSystem } from '../../../conversation/contracts.ts';
import { FileUpdateAttemptStore } from '../attempts.ts';
import { parse_release, attempt_to_dict, parse_attempt_json, canonical_json } from '../codec.ts';
import { instant } from '../logic/experience.ts';
import { UpdateService, staged_app_path, installed_update_root } from '../service.ts';
import { configured_worker_runtime_definitions } from '../../../worker_types/configuration.ts';
import { createApp } from '../../../app.ts';
import { loadConfig } from '../../../core/config.ts';
import { create_update_routes } from '../routes.ts';
import { DeploymentResources } from '../resource_versions.ts';
import { deployment_status } from '../logic/experience.ts';
import type { FastifyRequest } from 'fastify';

export interface ServiceProbeRequest {
  root: string;
  database_path: string;
  now: string;
  release: Record<string, unknown>;
  actions?: { op: string; [key: string]: unknown }[];
  installed?: {
    app_sha: string | null;
    app_root: string;
    database: string;
    service_unit: string | null;
  }[];
}
interface Hooks {
  stage?: () => Promise<void>;
  backup?: () => Promise<void>;
  launch?: () => Promise<void>;
  migrate?: boolean;
  commit_fault?: boolean;
}
function deferred() {
  let release!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((resolve, refuse) => {
    release = resolve;
    reject = refuse;
  });
  return { promise, release, reject };
}
function harness(request: ServiceProbeRequest, hooks: Hooks = {}) {
  mkdirSync(request.root, { recursive: true });
  const unregister = register_test_root(request.root),
    changes = new ChangeSignal();
  const database = createDatabase(request.database_path, {
    onChange: () => changes.emit(),
    commitSteps: hooks.commit_fault
      ? [
          () => {
            throw new Error('scripted commit step failure');
          },
        ]
      : [],
  });
  const counts = { provider: 0, stage: 0, backup: 0, prepare: 0, launch: 0 };
  const trace: string[] = [];
  let activity: 'idle' | 'busy' | 'unknown' = 'idle',
    launch_error: string | null = null,
    owned = true,
    now = Number(instant(request.now).epochMicroseconds) / 1_000_000;
  const barrier = new WorkspaceAdmission();
  const system = {
    workspace_admission: barrier,
    workspace_activity: () => (barrier.closed ? 'unknown' : activity),
    run_with_workspace_admission: async (
      operation: () => void,
      options: { require_idle: boolean },
    ) => barrier.claim(operation, { ...options, idle: () => activity === 'idle' }),
  } as ConversationSystem;
  const release = parse_release(request.release),
    staged = staged_app_path(request.root, release.manifest.app_sha);
  mkdirSync(staged, { recursive: true });
  const attempts = new FileUpdateAttemptStore(
    join(request.root, 'attempt.json'),
    () => changes.emit(),
    () => request.now,
  );
  const service = new UpdateService({
    provider: {
      available: async () => {
        counts.provider++;
        return release;
      },
      download_artifact: async () => {
        throw new Error('unexpected scripted download');
      },
    },
    attempts,
    runtime: {
      database,
      conversation: () => system,
      assert_database_owned: () => {
        if (!owned) throw new Error('ownership ended');
      },
    },
    changes,
    current_root: request.root,
    database: request.database_path,
    backup_directory: join(request.root, 'backups'),
    current_app_sha: 'a'.repeat(40),
    health_url: 'http://127.0.0.1:9/api/health',
    service_unit: 'scripted.service',
    now: () => now,
    record_now: () => request.now,
    staging: {
      stage: async () => {
        counts.stage++;
        trace.push('stage');
        await hooks.stage?.();
        return staged;
      },
    },
    installer: {
      schema_change_pending: () => hooks.migrate ?? false,
      create_backup: async () => {
        counts.backup++;
        trace.push('backup');
        await hooks.backup?.();
        return join(request.root, 'backup');
      },
      prepare: () => {
        counts.prepare++;
        trace.push('prepare');
        return ['scripted-installer'];
      },
      launch: async () => {
        trace.push('launch');
        if (launch_error !== null) throw new Error(launch_error);
        counts.launch++;
        await hooks.launch?.();
      },
    },
    mac: {
      prepare: async () => {
        trace.push('prepare_mac');
      },
      phase: () => null,
      reported_at: () => null,
      configured: () => true,
    },
  });
  return {
    database,
    changes,
    counts,
    trace,
    barrier,
    system,
    release,
    service,
    attempts,
    activity: (value: typeof activity) => {
      activity = value;
    },
    launch_error: (value: string | null) => {
      launch_error = value;
    },
    now: (value: number) => {
      now = value;
    },
    close: async () => {
      if (!owned) return;
      await service.stop();
      owned = false;
      database.close();
      unregister();
    },
  };
}
export async function probe_service(request: ServiceProbeRequest) {
  const host = harness(request),
    answers: unknown[] = [];
  host.service.start();
  await host.service.prefetch();
  try {
    for (const action of request.actions ?? []) {
      try {
        let result: unknown = null;
        const attempt = host.attempts.read();
        switch (action.op) {
          case 'activity':
            host.activity(action.value as 'idle' | 'busy' | 'unknown');
            break;
          case 'launch_error':
            host.launch_error(action.value as string | null);
            break;
          case 'status':
            result = host.service.status();
            break;
          case 'poll':
            result = host.service.poll();
            break;
          case 'install':
            result = attempt_to_dict(
              host.service.install(host.release.manifest.app_sha, action.mode as 'now' | 'idle', {
                confirm_activity: action.confirm_activity === true,
              }),
            );
            break;
          case 'cancel':
            host.service.cancel(attempt!.attempt_id);
            break;
          case 'now':
            result = attempt_to_dict(
              host.service.update_now(attempt!.attempt_id, {
                confirm_activity: action.confirm_activity === true,
              }),
            );
            break;
          case 'retry':
            result = attempt_to_dict(host.service.retry(attempt!.attempt_id));
            break;
          case 'retry_mac':
            result = attempt_to_dict(host.service.retry_mac(attempt!.attempt_id));
            break;
          case 'advance':
            await host.service.advance();
            break;
          case 'change':
            host.attempts.change(attempt!.attempt_id, new Set([attempt!.state]), (current) => ({
              ...current,
              ...(action.patch as object),
            }));
            break;
          default:
            throw new Error('unknown service probe action');
        }
        answers.push({ result, error: null });
      } catch (error) {
        if (!(error instanceof Error)) throw error;
        answers.push({
          result: null,
          error: { type: error.constructor.name, message: error.message },
        });
      }
    }
    return {
      answers,
      closed: host.barrier.closed,
      launches: host.counts.launch,
      attempt: host.attempts.read(),
    };
  } finally {
    await host.close();
  }
}

export async function probe_continuations(request: ServiceProbeRequest) {
  const result: Record<string, unknown> = {};
  result.installed_roots = await Promise.all(
    (request.installed ?? []).map((value) =>
      installed_update_root(value.app_sha, value.app_root, value.database, value.service_unit),
    ),
  );
  // Each pending adapter outlives the same absolute shutdown deadline.
  const stage = deferred(),
    restarted_stage = deferred();
  let stages = 0;
  const h = harness(request, {
    stage: () => (++stages === 1 ? stage.promise : restarted_stage.promise),
  });
  try {
    h.service.start();
    const first = h.service.prefetch(),
      second = h.service.prefetch();
    await pause(0);
    const started = performance.now();
    await h.service.stop({ deadline: started / 1000 + 0.03 });
    result.stage_stop_ms = performance.now() - started;
    h.service.start();
    const restarted = h.service.prefetch();
    await pause(0);
    stage.release();
    await Promise.all([first, second]);
    result.old_finally_preserves_new_flight = h.service.prefetch() === restarted;
    await h.service.stop({ deadline: performance.now() / 1000 });
    await h.close();
    restarted_stage.release();
    await restarted;
    result.stage_flight = { ...h.counts, ready: h.service.ready_release(), trace: h.trace };
  } finally {
    stage.release();
    restarted_stage.release();
    await h.close();
  }
  const backup = deferred(),
    b = harness(
      { ...request, root: join(request.root, 'backup-case') },
      { migrate: true, backup: () => backup.promise },
    );
  try {
    b.service.start();
    await b.service.prefetch();
    b.service.install(b.release.manifest.app_sha, 'now', { confirm_activity: false });
    const advance = b.service.advance();
    await pause(0);
    const started = performance.now();
    await b.service.stop({ deadline: started / 1000 + 0.03 });
    result.backup_stop_ms = performance.now() - started;
    const before = readFileSync(b.attempts.path, 'utf8');
    await b.close();
    backup.release();
    await advance;
    result.backup_fence = {
      ...b.counts,
      state: b.attempts.read()?.state,
      closed: b.barrier.closed,
      poll: b.service.poll(),
      unchanged_after_ownership: readFileSync(b.attempts.path, 'utf8') === before,
    };
  } finally {
    backup.release();
    await b.close();
  }
  const launch = deferred(),
    l = harness(
      { ...request, root: join(request.root, 'launch-case') },
      { launch: () => launch.promise },
    );
  try {
    l.service.start();
    await l.service.prefetch();
    l.service.install(l.release.manifest.app_sha, 'now', { confirm_activity: false });
    const advance = l.service.advance();
    await pause(0);
    const started = performance.now();
    await l.service.stop({ deadline: started / 1000 + 0.03 });
    const elapsed_ms = performance.now() - started;
    const before = readFileSync(l.attempts.path, 'utf8');
    await l.close();
    launch.reject(new Error('late installer refusal'));
    await advance;
    result.launch_fence = {
      elapsed_ms,
      launches: l.counts.launch,
      closed: l.barrier.closed,
      state: l.attempts.read()?.state,
      unchanged_after_ownership: readFileSync(l.attempts.path, 'utf8') === before,
    };
  } finally {
    launch.release();
    await l.close();
  }
  const audit = harness({ ...request, root: join(request.root, 'audit-case') });
  const definition = audit.database.read((sql) =>
    sql.get<{ definition_json: string }>(
      "SELECT definition_json FROM worker_types WHERE worker_type='coding'",
    ),
  )!.definition_json;
  try {
    const configured = configured_worker_runtime_definitions();
    audit.database.write((tx) =>
      tx.run("UPDATE worker_types SET definition_json='{}' WHERE worker_type='coding'"),
    );
    audit.service.start();
    await audit.service.prefetch();
    audit.service.install(audit.release.manifest.app_sha, 'now', { confirm_activity: false });
    await audit.service.advance();
    result.audit_refusal = {
      state: audit.attempts.read()?.state,
      detail: audit.attempts.read()?.detail,
      closed: audit.barrier.closed,
      launches: audit.counts.launch,
      global_registry_unchanged: configured === configured_worker_runtime_definitions(),
    };
  } finally {
    audit.database.write((tx) =>
      tx.run("UPDATE worker_types SET definition_json=? WHERE worker_type='coding'", [definition]),
    );
    await audit.close();
  }
  const failedCommit = harness(
    { ...request, root: join(request.root, 'commit-case') },
    { commit_fault: true },
  );
  try {
    failedCommit.service.start();
    await failedCommit.service.prefetch();
    failedCommit.service.install(failedCommit.release.manifest.app_sha, 'now', {
      confirm_activity: false,
    });
    await failedCommit.service.advance();
    result.commit_refusal = {
      state: failedCommit.attempts.read()?.state,
      detail: failedCommit.attempts.read()?.detail,
      closed: failedCommit.barrier.closed,
      launches: failedCommit.counts.launch,
      poll: failedCommit.service.poll(),
    };
  } finally {
    await failedCommit.close();
  }
  const timer = harness({ ...request, root: join(request.root, 'observer-case') });
  try {
    timer.activity('busy');
    timer.service.start();
    await timer.service.prefetch();
    const waiting = timer.service.install(timer.release.manifest.app_sha, 'idle', {
      confirm_activity: false,
    });
    const raw = parse_attempt_json(readFileSync(timer.attempts.path, 'utf8')) as Record<
      string,
      unknown
    >;
    raw.state = 'failed';
    raw.detail = 'external installer';
    writeFileSync(timer.attempts.path, canonical_json(raw));
    let external_seen = false;
    const unsubscribe = timer.changes.subscribe(() => {
      if (timer.service.status().attempt?.detail === 'external installer') external_seen = true;
    });
    await pause(350);
    unsubscribe();
    result.external_observation = {
      seen: external_seen,
      state: timer.service.status().attempt?.state,
    };
    timer.attempts.remove(waiting.attempt_id, new Set(['failed']));
    timer.service.install(timer.release.manifest.app_sha, 'idle', { confirm_activity: false });
    await timer.service.advance();
    timer.activity('idle');
    timer.changes.emit();
    await pause(0);
    const before_poll = { state: timer.attempts.read()?.state, launches: timer.counts.launch };
    const holds_worker_pass = timer.service.poll();
    await timer.service.advance();
    result.readiness_poll = {
      before_poll,
      holds_worker_pass,
      state: timer.attempts.read()?.state,
      launches: timer.counts.launch,
    };
  } finally {
    await timer.close();
  }
  return result;
}

interface RouteRequest extends ServiceProbeRequest {
  requests: {
    method: 'GET' | 'POST';
    path: string;
    payload?: unknown;
    headers?: Record<string, string>;
    seed?: Record<string, unknown>;
  }[];
  mac_status: unknown;
}
export async function probe_update_routes(request: RouteRequest) {
  const host = harness(request),
    responses: unknown[] = [];
  let health_reads = 0,
    mac_reads = 0;
  host.activity('busy');
  // Python's HTTP/status Harness has no event loop. Both pause background advancement;
  // the service and continuation proofs exercise it separately.
  host.service.schedule_advance = () => {};
  host.service.start();
  await host.service.prefetch();
  const config = { ...loadConfig(), app_sha: 'a'.repeat(40) };
  const app = await createApp(config, {
    database: host.database,
    changes: host.changes,
    workspaceAdmission: host.barrier,
    areas: [
      {
        prefix: '/api',
        routes: create_update_routes(host.service, {
          health_payload: () => {
            health_reads++;
            return { presence: 'present', platform: 'darwin' };
          },
          status: async () => {
            mac_reads++;
            return request.mac_status;
          },
        }),
      },
    ],
  });
  try {
    let nonowner_unchanged = true;
    for (const operation of request.requests) {
      let attempt = host.attempts.read();
      if (operation.seed !== undefined && attempt !== null) {
        host.attempts.change(attempt.attempt_id, new Set([attempt.state]), (current) => ({
          ...current,
          ...operation.seed,
        }));
        attempt = host.attempts.read();
      }
      const before = operation.headers ? readFileSync(host.attempts.path, 'utf8') : null;
      const response = await app.inject({
        method: operation.method,
        url: operation.path.replace('@attempt', attempt?.attempt_id ?? 'missing'),
        payload: operation.payload as Record<string, unknown> | undefined,
        headers: operation.headers,
      });
      if (before !== null && readFileSync(host.attempts.path, 'utf8') !== before)
        nonowner_unchanged = false;
      responses.push({ status: response.statusCode, body: response.json() });
    }
    let captures = 0;
    const resources = new DeploymentResources(() => {
      captures++;
      return deployment_status(host.attempts.read(), 'a'.repeat(40), {
        success_seconds: 300,
        now: request.now,
      });
    });
    const snapshot = host.database.read((sql) =>
      resources.capture(sql, {} as FastifyRequest, '/api/deployment-status'),
    )!;
    const captured = snapshot.read();
    const attempt = host.attempts.read();
    if (attempt !== null) host.attempts.remove(attempt.attempt_id, new Set([attempt.state]));
    return {
      responses,
      health_reads,
      mac_reads,
      nonowner_unchanged,
      resource: { captures, revision: snapshot.revision, body: captured, after: snapshot.read() },
    };
  } finally {
    await app.close();
    await host.close();
  }
}
