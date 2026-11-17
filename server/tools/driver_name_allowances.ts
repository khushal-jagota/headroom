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
    path: 'server/conversation/backends/contracts.ts',
    driver_kind: 'claude',
    reason: 'The ClaudeInitializationTimeout alias the Mac runner still catches.',
    until: 'Executor',
  },
  {
    path: 'server/conversation/snapshot/environment.ts',
    driver_kind: 'claude',
    reason: 'claude_executable_override, kept with its signature for executor/mac/adapters.ts.',
    until: 'Executor',
  },
  {
    path: 'server/core/config.ts',
    driver_kind: 'claude',
    reason: 'provider_launch_settings, read only by executor/mac/adapters.ts.',
    until: 'Executor',
  },
  {
    path: 'server/core/config.ts',
    driver_kind: 'codex',
    reason: 'provider_launch_settings, read only by executor/mac/adapters.ts.',
    until: 'Executor',
  },
  {
    path: 'server/core/config.ts',
    driver_kind: 'hermes',
    reason: 'provider_launch_settings, read only by executor/mac/adapters.ts.',
    until: 'Executor',
  },
  {
    path: 'server/environments/linux_executor/join.ts',
    driver_kind: 'claude',
    reason: 'The Linux executor writes the three provider homes.',
    until: 'Executor',
  },
  {
    path: 'server/environments/linux_executor/join.ts',
    driver_kind: 'codex',
    reason: 'The Linux executor writes the three provider homes.',
    until: 'Executor',
  },
  {
    path: 'server/environments/linux_executor/join.ts',
    driver_kind: 'hermes',
    reason: 'The Linux executor writes the three provider homes.',
    until: 'Executor',
  },
  {
    path: 'server/environments/registry/store.ts',
    driver_kind: 'hermes',
    reason:
      'Stored environments that still carry the retired hermes_home key are refused by that name.',
    until: 'Permanent',
  },
  {
    path: 'server/executor/mac/',
    driver_kind: 'claude',
    reason: 'The Mac executor launches, catalogs, localizes and packages each backend itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/',
    driver_kind: 'codex',
    reason: 'The Mac executor launches, catalogs, localizes and packages each backend itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/',
    driver_kind: 'hermes',
    reason: 'The Mac executor launches, catalogs, localizes and packages each backend itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/machine_drivers.ts',
    driver_kind: 'claude',
    reason: 'The backend keys a protocol-26 Mac accepts, until a machine advertises its drivers.',
    until: 'Executor',
  },
  {
    path: 'server/executor/machine_drivers.ts',
    driver_kind: 'codex',
    reason: 'The backend keys a protocol-26 Mac accepts, until a machine advertises its drivers.',
    until: 'Executor',
  },
  {
    path: 'server/executor/machine_drivers.ts',
    driver_kind: 'hermes',
    reason: 'The backend keys a protocol-26 Mac accepts, until a machine advertises its drivers.',
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
export const DRIVER_IMPORT_ALLOWANCES: readonly DriverImportAllowance[] = [
  {
    path: 'server/executor/mac/adapters.ts',
    driver_kind: 'claude',
    reason: 'The Mac executor builds each backend launch itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/adapters.ts',
    driver_kind: 'codex',
    reason: 'The Mac executor builds each backend launch itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/adapters.ts',
    driver_kind: 'hermes',
    reason: 'The Mac executor builds each backend launch itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/catalog/production.ts',
    driver_kind: 'claude',
    reason: 'The Mac executor reads each backend catalog itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/catalog/production.ts',
    driver_kind: 'codex',
    reason: 'The Mac executor reads each backend catalog itself.',
    until: 'Executor',
  },
  {
    path: 'server/executor/mac/catalog/production.ts',
    driver_kind: 'hermes',
    reason: 'The Mac executor reads each backend catalog itself.',
    until: 'Executor',
  },
];
