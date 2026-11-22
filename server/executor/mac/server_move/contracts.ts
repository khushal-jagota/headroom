/** The target machine's own record of one server move. Wire shapes stay in server_move/. */
import type { ServerMoveInstruction, ServerMovePackage } from '../../../server_move/contracts.ts';

/** The installation this machine started, so a failure or a restart can remove it. */
export interface ServerMoveInstallation {
  readonly root: string;
  readonly service: string;
  /** Null until the installer answered. */
  readonly executor_link: string | null;
}
/** `<executor root>/server-move/<move_id>/run.json`, replaced atomically at each step. */
export interface ServerMoveRunRecord {
  readonly instruction: ServerMoveInstruction & { readonly package: ServerMovePackage };
  /** The old server's address when the run started. The switch notice goes there. */
  readonly old_address: string;
  readonly installation: ServerMoveInstallation | null;
  /** When this machine sent `checked`. From then on the old server's phase decides the run. */
  readonly checked_at_ms: number | null;
  /** The old server answered commit: true. From here the run never removes the new server. */
  readonly committed: boolean;
}
/** A failure with a plain sentence for the owner. */
export class ServerMoveFailure extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ServerMoveFailure';
  }
}
