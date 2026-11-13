/** Trusted implementation/test dependencies, absent from the public construction API. */
import type * as protocol from '../../protocol.ts';
import type { JournalDependencies } from './journal_contracts.ts';
export type JournalProtocol = Pick<typeof protocol, 'decode_json' | 'encode_value'>;
export type JournalErrorTypes = Pick<
  typeof protocol,
  'ValidationError' | 'ValueError' | 'ProtocolRefused' | 'PydanticSerializationError' | 'KeyError'
>;
export interface JournalServiceDependencies extends JournalDependencies {
  protocol_module: URL;
  worker_module: URL;
  error_types: JournalErrorTypes;
}
