/** Fixed external installer entry. Copy independence is a later packaging gate. */
import { readFileSync, writeSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { is_object } from '../codec.ts';
import { create_installer_effects, error_detail } from './commands.ts';
import { install } from './install.ts';
import { InstallationRecord } from './record.ts';
import { assert_installer_health_url, assert_installer_path } from './configuration.ts';
import type { InstallerRequest } from './contracts.ts';

function read_request(path: string): InstallerRequest {
  assert_installer_path(path);
  const value: unknown = JSON.parse(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(path)),
  );
  if (!is_object(value)) throw new Error('Installer request must be an object');
  const text = (key: string): string => {
    const item = value[key];
    if (typeof item !== 'string' || item.includes('\0') || item === '')
      throw new Error(`Invalid installer field: ${key}`);
    return item;
  };
  const absolute = (key: string): string => {
    const item = text(key);
    if (!isAbsolute(item)) throw new Error(`Installer field must be absolute: ${key}`);
    if (key !== 'node_executable') assert_installer_path(item);
    return item;
  };
  const timeout = value.health_timeout_seconds,
    legacy = value.admitted_legacy_null_sha;
  if (typeof timeout !== 'number' || !Number.isFinite(timeout))
    throw new Error('Invalid installer health timeout');
  if (
    legacy !== null &&
    (typeof legacy !== 'string' || legacy.length !== 40 || !/^[0-9a-f]{40}$/.test(legacy))
  )
    throw new Error('Invalid admitted legacy SHA');
  const health_url = text('health_url');
  assert_installer_health_url(health_url);
  return {
    current_root: absolute('current_root'),
    database: absolute('database'),
    data_directory: absolute('data_directory'),
    attempt_id: text('attempt_id'),
    service_unit: text('service_unit'),
    health_url,
    health_timeout_seconds: timeout,
    node_executable: absolute('node_executable'),
    admitted_legacy_null_sha: legacy,
  };
}
function stderr(text: string): void {
  try {
    writeSync(2, text);
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'EPIPE')) throw error;
  }
}
async function main(): Promise<0 | 1 | 2> {
  const child_processes = new ChildProcesses(),
    controller = new AbortController();
  let interrupted: NodeJS.Signals | undefined, request: InstallerRequest | undefined;
  const on_signal = (signal: NodeJS.Signals) => {
    if (interrupted) return;
    interrupted = signal;
    child_processes.seal();
    try {
      child_processes.force_kill(performance.now() / 1000);
    } catch (error) {
      // Cleanup and original-signal delivery must survive a closed stderr pipe.
      try {
        writeSync(2, error_detail(error).message + '\n');
      } catch {
        /* The original interruption still owns process termination. */
      }
    } finally {
      controller.abort();
    }
  };
  const term = () => on_signal('SIGTERM'),
    interrupt = () => on_signal('SIGINT');
  process.on('SIGTERM', term);
  process.on('SIGINT', interrupt);
  try {
    const argv = process.argv.slice(2);
    if (argv.length !== 1) throw new Error('Expected one installer request file');
    controller.signal.throwIfAborted();
    request = read_request(argv[0]!);
    const effects = create_installer_effects(request, {
      child_processes,
      signal: controller.signal,
      environment: { ...process.env },
    });
    return await install(
      request,
      effects,
      {
        stderr: (text) => {
          stderr(text);
        },
      },
      controller.signal,
    );
  } catch (error) {
    if (controller.signal.aborted) return 1;
    const original = error_detail(error),
      detail = `The update stopped unexpectedly (${original.name}: ${original.message}).`;
    if (request) {
      try {
        new InstallationRecord(request).record('failed', 'failed', detail);
      } catch (failure) {
        stderr(`Could not record the failed update: ${error_detail(failure).message}\n`);
      }
    }
    stderr(detail + '\n');
    return 1;
  } finally {
    child_processes.seal();
    try {
      await child_processes.settled();
    } finally {
      process.removeListener('SIGTERM', term);
      process.removeListener('SIGINT', interrupt);
      if (interrupted) process.kill(process.pid, interrupted);
    }
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await main();
