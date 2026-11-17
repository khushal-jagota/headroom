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
    path: 'ios/Panels/',
    driver_kind: 'hermes',
    reason: 'The iPhone app names, marks and colours each backend until it reads the drivers list.',
    until: 'Apps',
  },
  {
    path: 'server/environments/registry/store.ts',
    driver_kind: 'hermes',
    reason:
      'Stored environments that still carry the retired hermes_home key are refused by that name.',
    until: 'Permanent',
  },
  {
    path: 'server/executor/machine_drivers.ts',
    driver_kind: 'claude',
    reason: 'The fallback driver set for a machine that advertises no drivers (the Python Mac).',
    until: 'Executor',
  },
  {
    path: 'server/executor/machine_drivers.ts',
    driver_kind: 'codex',
    reason: 'The fallback driver set for a machine that advertises no drivers (the Python Mac).',
    until: 'Executor',
  },
  {
    path: 'server/executor/machine_drivers.ts',
    driver_kind: 'hermes',
    reason: 'The fallback driver set for a machine that advertises no drivers (the Python Mac).',
    until: 'Executor',
  },
  {
    path: 'server/executor/vps_remote_child.ts',
    driver_kind: 'claude',
    reason:
      'The protocol-26 Python Mac starts Claude with a fixed model and effort (claude_agent_sdk.py:1578-1588); the VPS refuses before mailbox placement.',
    until: 'Executor',
  },
  {
    path: 'server/tools/smoke/codex_peer.ts',
    driver_kind: 'codex',
    reason: 'The smoke drives a scripted Codex app-server peer.',
    until: 'Permanent',
  },
  {
    path: 'web/src/',
    driver_kind: 'claude',
    reason: 'The web app names, marks and colours each backend until it reads the drivers list.',
    until: 'Apps',
  },
  {
    path: 'web/src/',
    driver_kind: 'codex',
    reason: 'The web app names, marks and colours each backend until it reads the drivers list.',
    until: 'Apps',
  },
  {
    path: 'web/src/',
    driver_kind: 'hermes',
    reason: 'The web app names, marks and colours each backend until it reads the drivers list.',
    until: 'Apps',
  },
];
export const DRIVER_IMPORT_ALLOWANCES: readonly DriverImportAllowance[] = [];
