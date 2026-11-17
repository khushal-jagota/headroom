/** Every place outside a driver package that still names a driver, and the stage that removes it. */
import type { DriverImportAllowance, DriverNameAllowance } from './check_driver_names.ts';

export const DRIVER_NAME_ALLOWANCES: readonly DriverNameAllowance[] = [
  {
    path: 'ios/Panels/',
    driver_kind: 'claude',
    reason: 'The iPhone app names, marks and colours each backend until it reads the drivers list.',
    until: 'Apps',
  },
  {
    path: 'ios/Panels/',
    driver_kind: 'codex',
    reason: 'The iPhone app names, marks and colours each backend until it reads the drivers list.',
    until: 'Apps',
  },
  {
    path: 'server/tools/smoke/codex_peer.ts',
    driver_kind: 'codex',
    reason: 'The smoke drives a scripted Codex app-server peer.',
    until: 'Permanent',
  },
];
export const DRIVER_IMPORT_ALLOWANCES: readonly DriverImportAllowance[] = [];
