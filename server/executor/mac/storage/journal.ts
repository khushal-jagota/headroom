/** Production journal construction has one shared codec and one fixed worker entry. */
import * as protocol from '../../protocol.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type {
  AsyncCommandJournal,
  JournalDependencies,
  JournalSpoolOwner,
} from './journal_contracts.ts';
import { open_journal_service } from './journal_service.ts';
export type * from './journal_contracts.ts';
export function open_command_journal(
  owner: JournalSpoolOwner,
  incarnation: string,
  dependencies: JournalDependencies,
  options: StopOptions,
): Promise<AsyncCommandJournal> {
  return open_journal_service(
    owner,
    incarnation,
    {
      outputs_module: dependencies.outputs_module,
      now_ms: dependencies.now_ms,
      on_failure: dependencies.on_failure,
      protocol_module: new URL('../../protocol.ts', import.meta.url),
      worker_module: new URL('./journal_worker.ts', import.meta.url),
      error_types: protocol,
    },
    options,
  );
}
