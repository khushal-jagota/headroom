/**
 * What the apps show for Antigravity. icon.png is T3 Code's Antigravity mark
 * (apps/mobile/assets/antigravity.png at b79d277c), and the accent is T3's Antigravity usage
 * colour. Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
import type { DriverClientDefinition } from '../core/client.ts';

export const antigravity_client: DriverClientDefinition = {
  label: 'Antigravity',
  icon: { file: new URL('./icon.png', import.meta.url), monochrome: false },
  accent_color: '#8c7bd1',
};
