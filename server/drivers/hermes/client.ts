/** What the apps show for Hermes. */
import type { DriverClientDefinition } from '../core/client.ts';

export const hermes_client: DriverClientDefinition = {
  label: 'Hermes',
  icon: { file: new URL('./icon.png', import.meta.url), monochrome: false },
  accent_color: '#a78bfa',
};
