/** What the apps show for Codex. */
import type { DriverClientDefinition } from '../core/client.ts';

export const codex_client: DriverClientDefinition = {
  label: 'Codex',
  icon: { file: new URL('./icon.svg', import.meta.url), monochrome: true },
  accent_color: '#e6e6e6',
};
