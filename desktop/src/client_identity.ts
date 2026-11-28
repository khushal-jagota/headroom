import { app } from 'electron';
import { randomUUID } from 'node:crypto';
import { readJson, writeJson } from './stored_json';
import { DESKTOP_BRIDGE_VERSION } from './bridge_version';

/** The desktop wire shape of server/client_versions/contracts.ts. */
export interface DesktopClientIdentity {
  kind: 'desktop';
  device_id: string;
  version: string;
  bridge_version: number;
}

let deviceId: string | null = null;

/** This installation's identity: the one value behind every request and socket. */
export function desktopIdentity(): DesktopClientIdentity {
  if (deviceId === null) {
    const stored = readJson<{ device_id?: unknown }>('client-identity.json');
    deviceId = typeof stored?.device_id === 'string' && stored.device_id ? stored.device_id : randomUUID();
    if (stored?.device_id !== deviceId) writeJson('client-identity.json', { device_id: deviceId });
  }
  return {
    kind: 'desktop',
    device_id: deviceId,
    version: app.getVersion(),
    bridge_version: DESKTOP_BRIDGE_VERSION,
  };
}

/** Native identity replaces any identity that the renderer supplied. */
export function desktopIdentityHeaders(): Record<string, string> {
  return { 'X-Panels-Client': JSON.stringify(desktopIdentity()) };
}
