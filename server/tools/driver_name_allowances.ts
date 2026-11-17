/** Every place outside a driver package that still names a driver, and the stage that removes it. */
import type { DriverImportAllowance, DriverNameAllowance } from './check_driver_names.ts';

export const DRIVER_NAME_ALLOWANCES: readonly DriverNameAllowance[] = [
  {
    path: 'server/environments/registry/store.ts',
    driver_kind: 'hermes',
    reason:
      'Stored environments that still carry the retired hermes_home key are refused by that name.',
    until: 'Permanent',
  },
  {
    path: 'server/tools/smoke/codex_peer.ts',
    driver_kind: 'codex',
    reason: 'The smoke drives a scripted Codex app-server peer.',
    until: 'Permanent',
  },
];
export const DRIVER_IMPORT_ALLOWANCES: readonly DriverImportAllowance[] = [];
