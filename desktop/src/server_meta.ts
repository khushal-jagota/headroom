import { net } from 'electron';
import { desktopIdentityHeaders } from './client_identity';

/** The updater reads the server's shell requirement through this one boundary. */
export async function fetchDesktopCompatibility(
  serverAddress: string,
): Promise<{ minimum_bridge_version: number }> {
  const response = await net.fetch(`${serverAddress}/api/meta`, {
    headers: desktopIdentityHeaders(),
    signal: AbortSignal.timeout(5_000),
    cache: 'no-store',
  });
  if (!response.ok) throw new Error(`Server metadata unavailable (${response.status}).`);
  const body = await response.json() as { compatibility?: { desktop_shell?: { minimum_bridge_version?: unknown } } };
  const floor = body?.compatibility?.desktop_shell?.minimum_bridge_version;
  if (typeof floor !== 'number' || !Number.isSafeInteger(floor) || floor < 1)
    throw new Error('The server did not supply a valid desktop bridge version.');
  return { minimum_bridge_version: floor };
}
