import {
  MIN_SUPPORTED_PROTOCOL_VERSION,
  PROTOCOL_VERSION,
  SPOOL_FORMAT_VERSION,
} from './execution_configuration.ts';
export interface ExecutorIdentity {
  protocol_version: number;
  application_revision: string;
  spool_format_version: number;
  executor_incarnation: string;
  connection_generation: number;
}
export function supports_executor_versions(
  protocol_version: number,
  spool_format_version: number,
): boolean {
  return (
    Number.isInteger(protocol_version) &&
    Number.isInteger(spool_format_version) &&
    MIN_SUPPORTED_PROTOCOL_VERSION <= protocol_version &&
    protocol_version <= PROTOCOL_VERSION &&
    spool_format_version === SPOOL_FORMAT_VERSION
  );
}
