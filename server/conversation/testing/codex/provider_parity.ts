/** Fresh-provider comparison companion. Every process launch is the supplied script. */
import childProcess from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export interface ProviderParitySpec {
  executable: string;
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
export async function run_provider_parity(spec: ProviderParitySpec): Promise<unknown> {
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
  const { saved_codex_login_source } = await import('../../backends/codex_app_server/providers.ts');
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
  const saved = () => {
    const standing = saved_codex_login_source.read_codex_login_standing(memory);
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
        catalog: await probe_codex_model_catalog(spec.executable),
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
      const service = new CodexSignInService({
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
        const pending = await service.start();
        const duplicate = await service.start();
        if (duplicate.session_id !== pending.session_id)
          throw new Error('Duplicate start replaced a pending session');
        let stale = false;
        try {
          await service.cancel('stale-session');
        } catch (error) {
          if (!(error instanceof UnknownSignInSession)) throw error;
          stale = true;
        }
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
          stale_refused: stale,
          joined: joined(),
          login: saved(),
        });
      } finally {
        await service.shutdown();
      }
    }
    for (const name of [
      'usage-valid',
      'usage-float',
      'usage-rejected',
      'usage-timeout',
      'usage-cancel',
    ]) {
      mode(name);
      const adapter = new CodexUsageAdapter({
        executable_path: () => spec.executable,
        now: () => new Date('2026-11-12T12:34:56Z'),
        read_codex_rate_limits: async (executable, options) => {
          if (options.timeout_seconds !== 30)
            throw new Error('Usage adapter must request its declared 30-second bound');
          const result = await read_codex_rate_limits(executable, {
            ...options,
            timeout_seconds: name === 'usage-timeout' ? 0.3 : 3,
          });
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
              readFileSync(spec.trace, 'utf8')
                .trim()
                .split('\n')
                .some((line) => {
                  const frame = JSON.parse(line) as { mode?: string; method?: string };
                  return frame.mode === name && frame.method === 'account/rateLimits/read';
                }),
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
    trace: readFileSync(spec.trace, 'utf8')
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line)),
    children: readFileSync(`${spec.trace}.children`, 'utf8')
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line)),
  };
}
