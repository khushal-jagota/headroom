/** What the apps show for Claude. */
import type { DriverClientDefinition } from '../core/client.ts';

export const claude_client: DriverClientDefinition = {
  label: 'Claude',
  icon: { file: new URL('./icon.svg', import.meta.url), monochrome: true },
  accent_color: '#d97757',
};
