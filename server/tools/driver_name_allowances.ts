/** Every place outside a driver's own package that names it, and the stage that removes it. */
import type { DriverImportAllowance, DriverNameAllowance } from './check_driver_names.ts';

export const DRIVER_NAME_ALLOWANCES: readonly DriverNameAllowance[] = [
  {
    path: 'server/tools/smoke/codex_peer.ts',
    driver_kind: 'codex',
    reason: 'The smoke drives a scripted Codex app-server peer.',
    until: 'Permanent',
  },
  {
    path: 'server/drivers/antigravity/runtime/allowance_usage.ts',
    driver_kind: 'claude',
    reason:
      "Google's Antigravity quota has a 'Claude and GPT' model-family window. That is the model vendor's family name, not the claude driver.",
    until: 'Permanent',
  },
  {
    path: 'server/drivers/antigravity/runtime/transcript_parser.ts',
    driver_kind: 'claude',
    reason:
      'agy records the Claude models Google serves by name and numeric model id, and the parser maps them to pricing names. These are model names, not the claude driver.',
    until: 'Permanent',
  },
];
export const DRIVER_IMPORT_ALLOWANCES: readonly DriverImportAllowance[] = [];
