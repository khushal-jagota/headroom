import type {
  StopOptions,
  OwnedChildProcesses,
  OwnedOneShotChildren,
} from '../../../lifecycle/contracts.ts';
import type { MacAdapterHost } from '../adapters.ts';
import type { MacCatalog } from '../catalog/index.ts';
import type { ExecutorPaths } from '../configuration.ts';
import type { FilesService } from '../files.ts';
import type { MacRunner } from '../runner/index.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import type { MacHttpClient } from '../transport/http.ts';
import type { MacFileTransport } from '../transport/files.ts';
import type { MacPresence } from '../transport/presence.ts';
import type { MacHttpOptions } from '../transport/contracts.ts';
import type { ExecutorIdentity, MacUpdateCheck } from '../../wire.generated.ts';
import type { GatewayOutbox } from '../ipc/outbox.ts';
import type { mac_runtime_settings } from '../../../core/config.ts';

export interface MacOwnedService {
  stop(options: StopOptions): Promise<void>;
  settled(): Promise<void>;
}
export interface MacHostLoop extends MacOwnedService {
  start(): void;
}
export interface MacHostUpdates extends MacHostLoop {
  offer(update: MacUpdateCheck): void;
}
export interface MacAgentProcesses extends MacOwnedService {
  end_orphans(signal: AbortSignal): Promise<void>;
  record_children(): Promise<void>;
}
export interface MacInvocation {
  readonly child_processes: OwnedChildProcesses;
  readonly one_shot_children: OwnedOneShotChildren;
}
export interface MacHostContext extends MacInvocation {
  readonly identity: import('../../../execution_identity.ts').ExecutorIdentity;
  readonly runtime: ReturnType<typeof mac_runtime_settings>;
  readonly paths: ExecutorPaths;
  readonly client: MacHttpClient;
  readonly files: FilesService;
  readonly update_files: FilesService;
  readonly transfer: MacFileTransport;
  readonly outbox: OutboxStore;
  readonly gateway_outbox: GatewayOutbox;
  readonly journal: AsyncCommandJournal;
  readonly runner: MacRunner;
  readonly presence: MacPresence;
  request_exit(code: number): void;
  on_failure(error: Error): void;
}
/** Constructors stay inert. The host starts the resulting loops after journal replay. */
export interface MacHostBindings {
  adapters(invocation: MacInvocation): MacAdapterHost;
  catalog(
    adapters: MacAdapterHost,
    invocation: MacInvocation,
    on_failure: (error: Error) => void,
  ): MacCatalog;
  processes(
    context: MacInvocation & { readonly files: FilesService; readonly paths: ExecutorPaths },
  ): MacAgentProcesses | null;
  updates(context: MacHostContext): MacHostUpdates;
  client?(
    server_url: string,
    executor_id: string,
    identity: ExecutorIdentity,
    options: MacHttpOptions,
  ): MacHttpClient;
}
export interface MacHostOptions {
  readonly paths: ExecutorPaths;
  readonly revision: string;
  readonly executor_id: string;
  readonly panels_server_url: string;
  readonly identity?: ExecutorIdentity;
  readonly http?: MacHttpOptions;
  readonly runtime?: ReturnType<typeof mac_runtime_settings>;
}
