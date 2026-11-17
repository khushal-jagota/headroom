/** The driver list the apps draw from: each driver's label, icon, colour and update flag. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type {
  DriverIconMediaType,
  DriverPresentation,
  DriversResponse,
} from '../../../web/src/lib/drivers/contracts.ts';
import type { CardDriver } from '../../conversation/snapshot/contracts.ts';
import { DEFAULT_BACKEND_INSTANCE_ID } from '../built_in.ts';

export interface DriverIconFile {
  readonly bytes: Buffer;
  readonly media_type: DriverIconMediaType;
  /** The first 12 hex digits of the bytes' SHA-256: the URL version and the ETag. */
  readonly version: string;
}
const MEDIA_TYPES: Readonly<Record<string, DriverIconMediaType>> = {
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};
/** Read once per process, on first use. Keyed by the icon file's URL. */
const icon_files = new Map<string, DriverIconFile>();

export function driver_icon_file(driver: CardDriver): DriverIconFile {
  const file = driver.client.icon.file;
  let icon = icon_files.get(file.href);
  if (icon === undefined) {
    const media_type = MEDIA_TYPES[/\.[^./]*$/.exec(file.pathname)?.[0] ?? ''];
    if (media_type === undefined)
      throw new Error(`The ${driver.driver_kind} icon is neither SVG nor PNG: ${file.pathname}`);
    const bytes = readFileSync(file);
    const version = createHash('sha256').update(bytes).digest('hex').slice(0, 12);
    icon = { bytes, media_type, version };
    icon_files.set(file.href, icon);
  }
  return icon;
}
function driver_presentation(driver: CardDriver): DriverPresentation {
  const icon = driver_icon_file(driver);
  const recipe = driver.manifest.status_recipe;
  return {
    driver_kind: driver.driver_kind,
    label: driver.client.label,
    icon: {
      url: `/api/drivers/${encodeURIComponent(driver.driver_kind)}/icon?v=${icon.version}`,
      media_type: icon.media_type,
      monochrome: driver.client.icon.monochrome,
    },
    accent_color: driver.client.accent_color,
    // The update advisory's own precondition.
    checks_for_updates:
      recipe.registry_package_name !== null || recipe.native_update_command !== null,
  };
}
export function drivers_json(drivers: readonly CardDriver[]): DriversResponse {
  return {
    drivers: drivers.map(driver_presentation),
    default_backend_instance_id: DEFAULT_BACKEND_INSTANCE_ID,
  };
}
