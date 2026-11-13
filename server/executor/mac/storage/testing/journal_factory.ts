/** Test-only trusted codec injection. Production never imports this factory. */
import * as protocol from '../../../protocol.ts';
import type { StopOptions } from '../../../../lifecycle/contracts.ts';
import type {
  AsyncCommandJournal,
  JournalDependencies,
  JournalSpoolOwner,
} from '../journal_contracts.ts';
import { open_journal_service } from '../journal_service.ts';
export interface TestJournalDependencies extends JournalDependencies {
  protocol_module: URL;
}
export function open_test_command_journal(
  owner: JournalSpoolOwner,
  incarnation: string,
  dependencies: TestJournalDependencies,
  options: StopOptions,
): Promise<AsyncCommandJournal> {
  return open_journal_service(
    owner,
    incarnation,
    {
      ...dependencies,
      worker_module: new URL('../journal_worker.ts', import.meta.url),
      error_types: protocol,
    },
    options,
  );
}
