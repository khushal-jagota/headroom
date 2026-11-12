/** One bounded usage probe, with the rate-limit result's original JSON text. */
import { homedir } from 'node:os';
import { CodexAppServerClient, CodexLoginIsDuplicated, CodexRequestRejected } from './client.ts';
import {
  CodexUsageTimeout,
  CodexUsageRequestRejected,
  CodexUsageLoginIsDuplicated,
} from '../../backend_usage/contracts.ts';
import type { CodexUsageSource } from '../../backend_usage/contracts.ts';
import { child_environment } from './environment.ts';
import {
  CLIENT_NAME,
  CLIENT_TITLE,
  CLIENT_VERSION,
  INITIALIZE_CAPABILITIES,
} from './adapter_configuration.ts';
type UsageClient = Pick<
  CodexAppServerClient,
  'start' | 'stop' | 'request' | 'request_raw' | 'notify'
>;
export interface UsageSourceOptions {
  client_factory?: (options: ConstructorParameters<typeof CodexAppServerClient>[0]) => UsageClient;
}
export async function read_codex_rate_limits(
  executable: string,
  options: Parameters<CodexUsageSource>[1],
  dependencies: UsageSourceOptions = {},
): Promise<string> {
  options.signal?.throwIfAborted();
  const client = (dependencies.client_factory ?? ((config) => new CodexAppServerClient(config)))({
    description: 'usage-read',
    handler: {
      async on_notification() {},
      async on_server_request() {},
      async on_child_ended() {},
    },
  });
  const cancel = new AbortController();
  let timed_out = false;
  const abort = () => cancel.abort(options.signal!.reason);
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  const timeout = setTimeout(() => {
    timed_out = true;
    cancel.abort(new CodexUsageTimeout());
  }, options.timeout_seconds * 1000);
  const canceled = new Promise<never>((_, reject) => {
    if (cancel.signal.aborted) reject(cancel.signal.reason);
    else
      cancel.signal.addEventListener('abort', () => reject(cancel.signal.reason), { once: true });
  });
  const operation = (async () => {
    cancel.signal.throwIfAborted();
    await client.start({
      argv: [executable, 'app-server'],
      environment: child_environment(),
      working_directory: homedir(),
    });
    cancel.signal.throwIfAborted();
    await client.request(
      'initialize',
      {
        clientInfo: { name: CLIENT_NAME, title: CLIENT_TITLE, version: CLIENT_VERSION },
        capabilities: INITIALIZE_CAPABILITIES,
      },
      { signal: cancel.signal },
    );
    cancel.signal.throwIfAborted();
    await client.notify('initialized');
    cancel.signal.throwIfAborted();
    return client.request_raw('account/rateLimits/read', {}, { signal: cancel.signal });
  })();
  try {
    return await Promise.race([operation, canceled]);
  } catch (error) {
    options.signal?.throwIfAborted();
    if (timed_out) throw new CodexUsageTimeout();
    if (error instanceof CodexRequestRejected)
      throw new CodexUsageRequestRejected(error.code ?? 0, error.rejection_message);
    if (error instanceof CodexLoginIsDuplicated)
      throw new CodexUsageLoginIsDuplicated(error.message);
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
    try {
      await client.stop();
    } finally {
      await operation.catch(() => {});
    }
  }
}
