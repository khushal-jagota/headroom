/** The retained inventory script owns catalog JSON. This owner returns command facts. */
import { fileURLToPath } from 'node:url';
import { copy_environment } from '../../../core/environment.ts';
import { constants } from 'node:os';
import type { Readable } from 'node:stream';
import type { CommandOutcome } from '../../snapshot/contracts.ts';
import type { HermesModelCatalogSource } from '../../snapshot/provider_contracts.ts';
import { MODEL_CATALOG_PROBE_TIMEOUT_SECONDS } from '../../snapshot/configuration.ts';
import { BackendAdapterError } from '../contracts.ts';
import { spawn_codex_process } from '../codex_app_server/process.ts';
import type { OwnedCodexProcess } from '../codex_app_server/process.ts';
import type { AcpChildLaunch } from './launch.ts';
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import { ModelCatalogCleanupFailed } from '../catalog_cleanup.ts';

export function hermes_model_catalog_launch(options: {
  hermes_python: string;
  hermes_home: string;
  hermes_python_source_root: string;
}): AcpChildLaunch {
  return {
    argv: [
      options.hermes_python,
      fileURLToPath(
        new URL(
          '../../../../src/planner/conversation/backends/hermes_model_catalog.py',
          import.meta.url,
        ),
      ),
    ],
    environment_overrides: [
      ['HERMES_HOME', options.hermes_home],
      ['HERMES_PYTHON_SRC_ROOT', options.hermes_python_source_root],
      ['PYTHONIOENCODING', 'utf-8'],
    ],
  };
}
interface Probe {
  owned: OwnedCodexProcess | null;
  task: Promise<CommandOutcome> | null;
  abort: Promise<never>;
  reject(reason: unknown): void;
  reason: unknown;
  finished: boolean;
  cleaned: boolean;
  cleanup_error: unknown;
  cleanup_reported: boolean;
  stopping: Promise<void> | null;
}
class CatalogTimeout extends Error {}
function collect(stream: Readable, chunks: Buffer[]): Promise<void> {
  return new Promise((resolve, reject) => {
    if (stream.readableEnded || stream.destroyed) {
      resolve();
      return;
    }
    stream.on('data', (chunk: Buffer) => chunks.push(chunk));
    stream.once('end', resolve);
    stream.once('close', resolve);
    stream.once('error', reject);
  });
}

export class HermesModelCatalogOwner {
  readonly source: HermesModelCatalogSource;
  private readonly launch: AcpChildLaunch;
  private readonly child_processes: OwnedChildProcesses;
  private readonly timeout_seconds: number;
  private readonly log: (error: unknown) => void;
  private readonly active = new Set<Probe>();
  private sealed = false;
  private shutting_down: Promise<void> | null = null;
  constructor(
    launch: AcpChildLaunch,
    options: {
      child_processes: OwnedChildProcesses;
      timeout_seconds?: number;
      log?: (error: unknown) => void;
    },
  ) {
    this.launch = launch;
    this.child_processes = options.child_processes;
    this.timeout_seconds = options.timeout_seconds ?? MODEL_CATALOG_PROBE_TIMEOUT_SECONDS;
    this.log = options.log ?? (() => {});
    this.source = (options) => this.read(options.refresh);
  }
  private read(refresh: boolean): Promise<CommandOutcome> {
    if (this.sealed)
      return Promise.reject(new BackendAdapterError('Hermes catalog stopped at shutdown'));
    let reject!: (reason: unknown) => void;
    const abort = new Promise<never>((_, no) => {
      reject = no;
    });
    void abort.catch(() => {});
    const probe: Probe = {
      owned: null,
      task: null,
      abort,
      reject,
      reason: null,
      finished: false,
      cleaned: false,
      cleanup_error: null,
      cleanup_reported: false,
      stopping: null,
    };
    this.active.add(probe);
    const task = this.run(probe, refresh);
    probe.task = task;
    void task
      .finally(() => {
        probe.finished = true;
        if (probe.cleaned) this.active.delete(probe);
      })
      .catch(() => {});
    return task;
  }
  private async run(probe: Probe, refresh: boolean): Promise<CommandOutcome> {
    let primary: unknown;
    const argv = refresh ? [...this.launch.argv, '--refresh'] : [...this.launch.argv];
    const environment = copy_environment(process.env);
    Object.assign(environment, Object.fromEntries(this.launch.environment_overrides));
    const timeout = new CatalogTimeout(`${argv[0]} did not answer within ${this.timeout_seconds}s`);
    const timer = setTimeout(() => {
      if (probe.reason === null) {
        probe.reason = timeout;
        probe.reject(timeout);
      }
    }, this.timeout_seconds * 1000);
    try {
      try {
        probe.owned = await spawn_codex_process(argv, {
          child_processes: this.child_processes,
          environment,
          working_directory: process.cwd(),
          close_stdin: true,
        });
      } catch (error) {
        if (probe.reason !== null) throw probe.reason;
        const detail = error instanceof Error ? error.message : String(error);
        return {
          exit_code: -1,
          standard_output: '',
          standard_error: detail,
        };
      }
      const owned = probe.owned;
      if (probe.reason !== null) throw probe.reason;
      const output: Buffer[] = [];
      const error: Buffer[] = [];
      const completion = Promise.all([
        owned.exited,
        collect(owned.child.stdout, output),
        collect(owned.child.stderr, error),
      ]);
      await Promise.race([
        completion,
        probe.abort,
        owned.output_failed.then((failure) => {
          throw failure;
        }),
      ]);
      return {
        exit_code:
          owned.child.exitCode ??
          (owned.child.signalCode === null
            ? -1
            : -(constants.signals[owned.child.signalCode] ?? 1)),
        standard_output: Buffer.concat(output).toString('utf8'),
        standard_error: Buffer.concat(error).toString('utf8'),
      };
    } catch (error) {
      primary = error;
      if (error === timeout)
        return { exit_code: -1, standard_output: '', standard_error: timeout.message };
      throw error;
    } finally {
      clearTimeout(timer);
      await this.stop_probe(probe, primary);
    }
  }
  private async stop_probe(probe: Probe, primary?: unknown): Promise<void> {
    if (probe.cleaned) return;
    if (probe.stopping) return probe.stopping;
    const stopping = (async () => {
      try {
        await probe.owned?.stop();
        probe.cleaned = true;
        probe.cleanup_error = null;
        if (probe.finished) this.active.delete(probe);
      } catch (cause) {
        const error = new ModelCatalogCleanupFailed(
          primary,
          [cause],
          () => this.stop_probe(probe, primary),
          'the Hermes catalog process did not stop',
        );
        probe.cleanup_error = error;
        if (!probe.cleanup_reported) {
          probe.cleanup_reported = true;
          try {
            this.log(error);
          } catch {
            /* Diagnostics do not replace cleanup. */
          }
        }
        throw error;
      }
    })();
    probe.stopping = stopping;
    void stopping.catch(() => {
      if (probe.stopping === stopping) probe.stopping = null;
    });
    return stopping;
  }
  /** Cancel current reads. A later read remains permitted until shutdown seals it. */
  async cancel(): Promise<void> {
    const reason = new BackendAdapterError('Hermes catalog read cancelled');
    const probes = [...this.active];
    const already_finished = new Set(probes.filter((probe) => probe.finished));
    for (const probe of probes) {
      if (probe.reason === null) {
        probe.reason = reason;
        probe.reject(reason);
      }
    }
    const results = await Promise.allSettled(
      probes.map(async (probe) => {
        if (already_finished.has(probe)) await this.stop_probe(probe);
        else {
          try {
            await probe.task;
          } catch {
            /* Cancellation is returned to the source caller. */
          }
          if (probe.cleanup_error !== null) throw probe.cleanup_error;
        }
      }),
    );
    const failed = results.find((result) => result.status === 'rejected');
    if (failed?.status === 'rejected') throw failed.reason;
  }
  shutdown(): Promise<void> {
    this.sealed = true;
    if (!this.shutting_down) {
      const stopping = this.cancel();
      this.shutting_down = stopping;
      void stopping.catch(() => {
        if (this.shutting_down === stopping) this.shutting_down = null;
      });
    }
    return this.shutting_down;
  }
}
