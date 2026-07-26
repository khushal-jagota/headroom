import type { ToolKind } from '@agentclientprotocol/sdk';

/**
 * Kind glyphs for tool-call steps. Each entry is a list of `<path d>` strings
 * drawn as 24×24 stroke icons (see `.acp-step-icon svg` in app.css). Any kind
 * the runtime does not send — including `undefined` — falls back to `other`.
 */
const STEP_ICON_PATHS: Record<ToolKind, readonly string[]> = {
  read: ['M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z', 'M14 3v5h5'],
  edit: ['M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z'],
  delete: [
    'M4 7h16',
    'M9 7V4h6v3',
    'M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13',
    'M10 11v6',
    'M14 11v6',
  ],
  move: ['M9 7h8v8', 'M7 17 17 7'],
  search: ['M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14', 'm21 21-4.3-4.3'],
  execute: ['m5 7 5 5-5 5', 'M13 17h6'],
  think: ['M12 3a5 5 0 0 0-3 9c.6.5 1 1.2 1 2v1h4v-1c0-.8.4-1.5 1-2a5 5 0 0 0-3-9z', 'M10 20h4'],
  fetch: ['M12 3v12', 'm7 10 5 5 5-5', 'M5 21h14'],
  switch_mode: ['M4 9h13', 'm14 6 3 3-3 3', 'M20 15H7', 'm10 12-3 3 3 3'],
  other: ['M6 12h.01', 'M12 12h.01', 'M18 12h.01'],
};

export function stepIconPaths(kind: ToolKind | undefined): readonly string[] {
  if (kind && kind in STEP_ICON_PATHS) return STEP_ICON_PATHS[kind];
  return STEP_ICON_PATHS.other;
}
