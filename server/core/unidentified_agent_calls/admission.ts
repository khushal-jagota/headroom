import type {
  PeerLookup,
  UnidentifiedCallAdmission,
  UnidentifiedCallRequest,
} from './contracts.ts';
import { lookup_required } from './logic.ts';
import { resolve_loopback_peer } from './peer.ts';
export { http_refusal, websocket_refusal } from './logic.ts';

/** Trusted ingress is established before this boundary. Route authority follows it. */
export async function admit_unidentified_call(
  enabled: boolean,
  request: UnidentifiedCallRequest,
  lookup: PeerLookup = resolve_loopback_peer,
): Promise<UnidentifiedCallAdmission> {
  if (!lookup_required(enabled, request)) return { admitted: true, peer_lookup: null };
  const peer_lookup = await lookup(request.client, request.server);
  return { admitted: peer_lookup.is_owner, peer_lookup };
}
