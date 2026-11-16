/** A started service is not proof that the home accepted its credential. */
import { is_record } from '../../../core/records.ts';
import { JOIN_REQUIRED_PROTOCOL_VERSION } from '../configuration.ts';

export function accepted_lease(status: unknown, revision: string, started_ms: number): boolean {
  return (
    is_record(status) &&
    is_record(status.identity) &&
    status.identity.application_revision === revision &&
    status.identity.protocol_version === JOIN_REQUIRED_PROTOCOL_VERSION &&
    status.last_lease_failure == null &&
    status.going_to_sleep === false &&
    status.admission_stopped === false &&
    Number.isSafeInteger(status.lease_accepted_at_mac_ms) &&
    (status.lease_accepted_at_mac_ms as number) >= started_ms &&
    status.refusal == null
  );
}
