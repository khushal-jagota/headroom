/** What the apps show for Claude. */
import type { DriverClientDefinition } from '../core/client.ts';

export const claude_client: DriverClientDefinition = {
  label: 'Claude',
  icon: { file: 'icon.svg', media_type: 'image/svg+xml', monochrome: true },
  accent_color: '#d97757',
};
