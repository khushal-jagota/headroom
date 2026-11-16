/** Local operational ports; every message remains a generated wire value. */
import type { MacRunningChild } from '../runner/contracts.ts';
import type { MacSourceWorkContract } from '../runner/retirement_contracts.ts';
import type { MacMaintenanceContract } from '../runner/maintenance_contracts.ts';
import type {
  ChildReference,
  ExecutorIdentity,
  LiveReadRequest,
  MacLocalStatus,
  JsonObject,
} from '../../wire.generated.ts';
import type { GatewayOutbox } from './outbox.ts';
export interface GatewayRunner
  extends Pick<MacSourceWorkContract, 'source_work'>, Pick<MacMaintenanceContract, 'quiesce'> {
  readonly admission_stopped: boolean;
  authenticate(child_id: string | null, capability: string | null): MacRunningChild;
  /** Fence starts before catalogs settle. This is not permanent runner shutdown. */
  seal_admission(): void;
}
export interface GatewayOptions {
  readonly runner: GatewayRunner;
  readonly outbox: GatewayOutbox;
  readonly presence: {
    readonly identity: ExecutorIdentity;
    status(admission_stopped: boolean): MacLocalStatus;
  };
  readonly client: {
    read(request: LiveReadRequest, signal?: AbortSignal): Promise<JsonObject>;
  } | null;
  readonly request_exit: () => void;
  readonly settle_catalogs: (signal: AbortSignal) => Promise<void>;
  readonly on_failure: (error: Error) => void;
}
export interface Publication {
  readonly child: ChildReference;
  readonly task: Promise<unknown>;
}
