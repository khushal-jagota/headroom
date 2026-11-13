/** Export-only direct proof companion. It starts only the supplied scripted executable. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { readFileSync } from 'node:fs';
import {
  CodexAppServerClient,
  CodexRequestRejected,
  CodexWireFailed,
} from '../../backends/codex_app_server/client.ts';

export interface WireParitySpec {
  executable: string;
  environment: Record<string, string>;
  working_directory: string;
  trace: string;
}
export async function run_wire_parity(spec: WireParitySpec): Promise<unknown> {
  const notifications: unknown[] = [];
  const ordering: string[] = [];
  const requests: unknown[] = [];
  const warnings: string[] = [];
  let child_ended = 0;
  let ended!: () => void;
  const ended_promise = new Promise<void>((resolve) => {
    ended = resolve;
  });
  const client = new CodexAppServerClient({
    child_processes: new ChildProcesses(),
    description: 'unit17-wire',
    warning(message) {
      warnings.push(
        message.includes('item/agentMessage/delta')
          ? 'malformed notification'
          : message.includes('item/fileChange/requestApproval')
            ? 'malformed request'
            : message.includes('JSON')
              ? 'invalid JSON'
              : message,
      );
    },
    handler: {
      async on_notification(method, notification) {
        notifications.push({ method, params: notification });
        if ((notification as { delta?: string }).delta === 'slow') {
          ordering.push('handler-start');
          await new Promise((resolve) => setTimeout(resolve, 20));
          ordering.push('handler-end');
        }
      },
      async on_server_request(method, request_id, params) {
        requests.push({ method, id: request_id, params });
        await client.respond(request_id, { decision: 'decline' });
      },
      async on_child_ended() {
        child_ended += 1;
        ended();
      },
    },
  });
  let result: unknown;
  try {
    await client.start({
      argv: [spec.executable, 'app-server'],
      environment: spec.environment,
      working_directory: spec.working_directory,
    });
    const initialize = await client.request('initialize', {
      clientInfo: { name: 'unit17-proof', version: '1' },
      capabilities: { experimentalApi: true },
    });
    await client.notify('initialized');
    const first = await client.begin_request('wire/first', { input: 'first' });
    const second = await client.begin_request('wire/second', { input: 'second' });
    const replies = await Promise.all([
      client.finish_request('wire/first', first),
      client.finish_request('wire/second', second),
    ]);
    const exchange = await client.request('wire/exchange', {});
    await client.request('wire/slow-handler', {});
    ordering.push('reply');
    const raw_usage = await client.request_raw('account/rateLimits/read', {});
    let rejection: unknown = null;
    try {
      await client.request('wire/reject', {});
    } catch (error) {
      if (!(error instanceof CodexRequestRejected)) throw error;
      rejection = {
        method: error.method,
        code: error.code,
        message: error.message,
        data: error.data,
        request_id: error.request_id,
      };
    }
    let exit_failure: unknown = null;
    try {
      await client.request('wire/exit', {});
    } catch (error) {
      if (!(error instanceof CodexWireFailed)) throw error;
      exit_failure = {
        message: error.message,
        request_may_have_been_written: error.request_may_have_been_written,
      };
    }
    await ended_promise;
    result = {
      initialize,
      replies,
      exchange,
      usage: JSON.parse(raw_usage),
      raw_usage,
      rejection,
      exit_failure,
      notifications,
      ordering,
      requests,
      warnings,
      child_ended,
      stderr: client.standard_error_tail(),
      is_alive: client.is_alive,
      trace: readFileSync(spec.trace, 'utf8')
        .trim()
        .split('\n')
        .map((line) => JSON.parse(line)),
    };
  } finally {
    await client.stop();
  }
  return result;
}
