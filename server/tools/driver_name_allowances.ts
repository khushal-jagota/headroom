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
    path: 'server/conversation/tool_detail.ts',
    driver_kind: 'claude',
    reason: 'Claude-only tool call detail; it becomes a driver hook.',
    until: 'Core',
  },
  {
    path: 'server/conversation/views/data/storage.ts',
    driver_kind: 'hermes',
    reason: 'The hermes default for a view without a key; it becomes null.',
    until: 'Core',
  },
  {
    path: 'server/conversation/views/logic/bodies.ts',
    driver_kind: 'hermes',
    reason: 'The hermes default for a view without a key; it becomes null.',
    until: 'Core',
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
    reason: "Claude's start-fixed selection rule; the core's selection plan replaces it.",
    until: 'Core',
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
