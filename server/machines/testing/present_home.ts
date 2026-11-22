/**
 * Test-only: the home's machine code is present and runs every built-in backend. Production
 * runs every conversation through machine code, so tests that run the home's conversations on
 * a local child need this row.
 */
import type { TransactionSql } from '../../core/database/contracts.ts';
import { PROTOCOL_VERSION } from '../../execution_configuration.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { native_wire_fixtures } from '../../executor/testing/native_protocol_fixtures.ts';
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';

export function seed_present_home_machine(tx: TransactionSql): void {
  const fixture = native_wire_fixtures().LeaseRequest;
  const identity = { ...fixture.identity, protocol_version: PROTOCOL_VERSION };
  const lease = {
    ...fixture,
    identity,
    queue_depth: 0,
    oldest_item_age_ms: null,
    waiting_conversations: [],
    working_turns: [],
    going_to_sleep: false,
    driver_kinds: BUILT_IN_MANIFESTS.map((driver) => driver.driver_kind),
    cleanup_uncertainties: [],
    legacy_cleanup_notice: null,
  };
  // A lease time beyond any test clock keeps the machine present for the whole test.
  tx.run(
    'INSERT INTO mac_executor_presence(machine_id,executor_incarnation,identity_json,lease_json,last_lease_at_ms) VALUES(?,?,?,?,?) ON CONFLICT(machine_id) DO NOTHING',
    [
      HOME_MACHINE_ID,
      identity.executor_incarnation,
      JSON.stringify(identity),
      JSON.stringify(lease),
      Number.MAX_SAFE_INTEGER,
    ],
  );
}
