/** Fresh-provider comparison companion. Every process launch is the supplied script. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import childProcess from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createDatabase } from '../../../core/database/index.ts';
import { create_durable_codex_login_rejection } from '../../backend_state.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import type {
  CodexLoginRejectionMemory,
  SavedCodexLoginSource,
} from '../../snapshot/provider_contracts.ts';

export interface ProviderParitySpec {
  executable: string;
  database: string;
  trace: string;
  control: string;
  login: Record<string, unknown>;
}
const instant = '2026-11-12T12:34:56.123456+00:00';
async function wait(condition: () => boolean, description: string): Promise<void> {
  const deadline = performance.now() + 5000;
  while (!condition()) {
    if (performance.now() > deadline) throw new Error(`Timed out: ${description}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}
function read_trace(path: string): Record<string, unknown>[] {
  return existsSync(path)
    ? readFileSync(path, 'utf8')
        .trim()
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line) as Record<string, unknown>)
    : [];
}
export async function run_provider_parity(spec: ProviderParitySpec): Promise<unknown> {
  const child_processes = new ChildProcesses();
  const original = {
    spawn: childProcess.spawn,
    spawnSync: childProcess.spawnSync,
    exec: childProcess.exec,
    execSync: childProcess.execSync,
    execFile: childProcess.execFile,
    execFileSync: childProcess.execFileSync,
    fork: childProcess.fork,
  };
  const children: ChildProcess[] = [];
  const forbidden = (): never => {
    throw new Error('Provider proof permits only its owned fake');
  };
  Object.assign(childProcess, {
    spawn(executable: string, args: string[], options: Parameters<typeof childProcess.spawn>[2]) {
      if (
        executable !== spec.executable ||
        args.length !== 1 ||
        !['app-server', '--version'].includes(args[0]!)
      )
        forbidden();
      const child = original.spawn(executable, args, options);
      children.push(child);
      return child;
    },
    spawnSync: forbidden,
    exec: forbidden,
    execSync: forbidden,
    execFile: forbidden,
    execFileSync: forbidden,
    fork: forbidden,
  });
  syncBuiltinESMExports();
  const { probe_codex_model_catalog } =
    await import('../../backends/codex_app_server/model_catalog.ts');
  const { read_codex_rate_limits } = await import('../../backends/codex_app_server/usage.ts');
  const { saved_codex_login_source, create_codex_providers } =
    await import('../../backends/codex_app_server/providers.ts');
  const { RememberedCodexLoginRejection } =
    await import('../../backends/codex_app_server/login.ts');
  const { CodexSignInService, UnknownSignInSession } =
    await import('../../backends/codex_app_server/login_session.ts');
  const { CodexUsageAdapter } = await import('../../backend_usage/codex.ts');
  const home = process.env.CODEX_HOME!;
  const loginPath = join(home, 'auth.json');
  const memory = new RememberedCodexLoginRejection();
  const joined = () =>
    children.every((child) => child.exitCode !== null || child.signalCode !== null);
  const mode = (name: string) => {
    process.env.UNIT17_PROVIDER_MODE = name;
    process.env.UNIT17_TRACE = spec.trace;
    process.env.UNIT17_PROVIDER_CONTROL = spec.control;
    if (existsSync(spec.control)) unlinkSync(spec.control);
    writeFileSync(loginPath, JSON.stringify(spec.login));
  };
  const saved = (
    source: SavedCodexLoginSource = saved_codex_login_source,
    rejection: CodexLoginRejectionMemory = memory,
  ) => {
    const standing = source.read_codex_login_standing(rejection);
    const stored = standing.stored;
    return {
      present: stored.present,
      readable: stored.readable,
      account_label: stored.account_label,
      access_token_expires_at: stored.access_token_expires_at?.toISOString() ?? null,
      generation: standing.generation,
      rejected: standing.rejected,
      expired: stored.access_token_has_expired({ now: new Date(instant) }),
    };
  };
  const models: Record<string, unknown> = {};
  const signIns: unknown[] = [];
  const usage: unknown[] = [];
  const raw: Record<string, string> = {};
  let standing: unknown[];
  try {
    for (const name of ['models-full', 'models-partial']) {
      mode(name);
      models[name] = {
        catalog: await probe_codex_model_catalog(spec.executable, { child_processes }),
        joined: joined(),
      };
    }
    mode('saved');
    standing = [saved()];
    memory.remember(saved_codex_login_source.read_stored_codex_login().generation);
    standing.push(saved());
    writeFileSync(
      loginPath,
      JSON.stringify({ ...spec.login, last_refresh: 'refreshed-generation' }),
    );
    standing.push(saved());
    for (const name of [
      'signin-cancel',
      'signin-deadline',
      'signin-shutdown',
      'signin-complete',
      'signin-race',
      'signin-unconfirmed',
      'signin-child-success',
    ]) {
      mode(name);
      const database = name === 'signin-shutdown' ? createDatabase(spec.database) : null;
      const one_shot_children = new OneShotChildren(child_processes);
      const clock = () => new Date('2026-11-12T12:34:56Z');
      const durable = database && create_durable_codex_login_rejection(database, clock);
      durable?.remember(saved_codex_login_source.read_stored_codex_login().generation);
      const providers =
        database &&
        create_codex_providers({
          one_shot_children,
          child_processes,
          database,
          now: clock,
          read_codex_snapshot: async () => ({ executable_path: spec.executable }),
        });
      const service =
        providers?.codex_sign_in ??
        new CodexSignInService({
          child_processes,
          resolve_argv: async () => [spec.executable, 'app-server'],
          remembered_login_rejection: memory,
          now: () => instant,
          bounds: {
            child_startup_seconds: 3,
            session_seconds: name === 'signin-deadline' ? 0.06 : 2,
            cancel_seconds: 0.5,
          },
        });
      try {
        let pending: Awaited<ReturnType<typeof service.start>>;
        if (name === 'signin-cancel') {
          const [first, concurrent] = await Promise.all([service.start(), service.start()]);
          pending = first;
          if (concurrent.session_id !== pending.session_id)
            throw new Error('Concurrent starts created different sessions');
          let stale: unknown;
          try {
            await service.cancel('stale-session');
          } catch (error) {
            stale = error;
          }
          if (!(stale instanceof UnknownSignInSession))
            throw new Error('A stale cancellation was not refused');
        } else pending = await service.start();
        let terminal;
        if (name === 'signin-cancel' || name === 'signin-race')
          terminal = await service.cancel(pending.session_id);
        else if (name === 'signin-shutdown') {
          await service.shutdown();
          terminal = service.read();
        } else {
          if (name !== 'signin-deadline') writeFileSync(spec.control, 'complete');
          await wait(() => service.read()?.state !== 'pending', name);
          terminal = service.read();
        }
        signIns.push({
          name,
          pending,
          terminal,
          joined: joined(),
          login: providers && durable ? saved(providers.saved_codex_login!, durable) : saved(),
        });
      } finally {
        try {
          await service.shutdown();
        } finally {
          one_shot_children.seal();
          await one_shot_children.settled();
        }
        database?.close();
      }
    }
    for (const name of ['usage-float', 'usage-rejected', 'usage-timeout', 'usage-cancel']) {
      mode(name);
      const adapter = new CodexUsageAdapter({
        executable_path: () => spec.executable,
        now: () => new Date('2026-11-12T12:34:56Z'),
        read_codex_rate_limits: async (executable, options) => {
          if (options.timeout_seconds !== 30)
            throw new Error('Usage adapter must request its declared 30-second bound');
          const result = await read_codex_rate_limits(
            executable,
            {
              ...options,
              timeout_seconds: name === 'usage-timeout' ? 0.3 : 3,
            },
            { child_processes },
          );
          raw[name] = result;
          return result;
        },
      });
      if (name === 'usage-cancel') {
        const abort = new AbortController();
        const reason = new Error('synthetic usage cancellation');
        const operation = adapter.refresh({ signal: abort.signal });
        const settled = operation.then(
          () => false,
          (error: unknown) => {
            if (error !== reason) throw error;
            return true;
          },
        );
        try {
          await wait(
            () =>
              read_trace(spec.trace).some(
                (frame) => frame.mode === name && frame.method === 'account/rateLimits/read',
              ),
            'usage cancellation reaches native request',
          );
          abort.abort(reason);
          usage.push({ name, canceled: await settled, joined: joined() });
        } finally {
          abort.abort(reason);
          await settled;
        }
      } else usage.push({ name, result: await adapter.refresh(), joined: joined() });
    }
  } finally {
    await child_processes.settled();
    delete process.env.UNIT17_PROVIDER_MODE;
    delete process.env.UNIT17_PROVIDER_CONTROL;
    delete process.env.UNIT17_TRACE;
    Object.assign(childProcess, original);
    syncBuiltinESMExports();
  }
  return {
    models,
    saved: standing!,
    sign_in: signIns,
    usage,
    raw,
    trace: read_trace(spec.trace),
    children: read_trace(`${spec.trace}.children`),
  };
}
